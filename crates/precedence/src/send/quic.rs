//! Direct QUIC to a leader's advertised TPU, the way any unstaked client
//! reaches it: ALPN `solana-tpu`, a throwaway self-signed ed25519 client
//! certificate (so the server sees an unknown, unstaked peer), and one
//! unidirectional stream per transaction.

use super::{PathInfo, SendRecord};
use crate::clock::TimeBase;
use anyhow::{Context, Result};
use quinn::{ClientConfig, Connection, Endpoint, TransportConfig};
use rustls::client::danger::{HandshakeSignatureValid, ServerCertVerified, ServerCertVerifier};
use rustls::pki_types::{CertificateDer, PrivateKeyDer, PrivatePkcs8KeyDer, ServerName, UnixTime};
use rustls::{DigitallySignedStruct, SignatureScheme};
use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;

pub const ALPN_TPU: &[u8] = b"solana-tpu";

/// Validator TPU certificates are self-signed; identity is not what we check.
#[derive(Debug)]
struct AcceptAnyServer(Arc<rustls::crypto::CryptoProvider>);

impl ServerCertVerifier for AcceptAnyServer {
    fn verify_server_cert(
        &self,
        _: &CertificateDer<'_>,
        _: &[CertificateDer<'_>],
        _: &ServerName<'_>,
        _: &[u8],
        _: UnixTime,
    ) -> Result<ServerCertVerified, rustls::Error> {
        Ok(ServerCertVerified::assertion())
    }
    fn verify_tls12_signature(
        &self,
        m: &[u8],
        c: &CertificateDer<'_>,
        d: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, rustls::Error> {
        rustls::crypto::verify_tls12_signature(m, c, d, &self.0.signature_verification_algorithms)
    }
    fn verify_tls13_signature(
        &self,
        m: &[u8],
        c: &CertificateDer<'_>,
        d: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, rustls::Error> {
        rustls::crypto::verify_tls13_signature(m, c, d, &self.0.signature_verification_algorithms)
    }
    fn supported_verify_schemes(&self) -> Vec<SignatureScheme> {
        self.0.signature_verification_algorithms.supported_schemes()
    }
}

pub fn client_config() -> Result<ClientConfig> {
    let provider = Arc::new(rustls::crypto::ring::default_provider());
    let key = rcgen::KeyPair::generate_for(&rcgen::PKCS_ED25519)?;
    let cert = rcgen::CertificateParams::new(vec!["solana".to_string()])?.self_signed(&key)?;
    let key_der = PrivateKeyDer::Pkcs8(PrivatePkcs8KeyDer::from(key.serialize_der()));
    let mut tls = rustls::ClientConfig::builder_with_provider(provider.clone())
        .with_protocol_versions(&[&rustls::version::TLS13])?
        .dangerous()
        .with_custom_certificate_verifier(Arc::new(AcceptAnyServer(provider)))
        .with_client_auth_cert(vec![cert.der().clone()], key_der)?;
    tls.alpn_protocols = vec![ALPN_TPU.to_vec()];
    tls.enable_early_data = false;
    let mut cfg = ClientConfig::new(Arc::new(quinn::crypto::rustls::QuicClientConfig::try_from(tls)?));
    let mut transport = TransportConfig::default();
    transport.keep_alive_interval(Some(Duration::from_secs(1)));
    transport.max_idle_timeout(Some(Duration::from_secs(10).try_into().unwrap()));
    cfg.transport_config(Arc::new(transport));
    Ok(cfg)
}

pub struct QuicSender {
    endpoint: Endpoint,
    conn: Option<Connection>,
    tb: TimeBase,
    dry: bool,
}

impl QuicSender {
    pub fn new(tb: TimeBase, dry: bool) -> Result<Self> {
        let mut endpoint = Endpoint::client("0.0.0.0:0".parse().unwrap())?;
        endpoint.set_default_client_config(client_config()?);
        Ok(QuicSender { endpoint, conn: None, tb, dry })
    }

    /// Open the connection ahead of the send so the handshake is not part of
    /// what the leader's scheduler sees.
    pub async fn connect(&mut self, addr: SocketAddr) -> Result<PathInfo> {
        let t0 = self.tb.now_us();
        let connecting = self.endpoint.connect(addr, "solana").context("quic connect")?;
        let conn = tokio::time::timeout(Duration::from_secs(3), connecting)
            .await
            .context("quic handshake timed out")?
            .context("quic handshake")?;
        let handshake_us = self.tb.now_us() - t0;
        let rtt_us = conn.rtt().as_micros() as u64;
        self.conn = Some(conn);
        Ok(PathInfo { addr: addr.to_string(), handshake_us: Some(handshake_us), rtt_us: Some(rtt_us) })
    }

    pub fn rtt_us(&self) -> Option<u64> {
        self.conn.as_ref().map(|c| c.rtt().as_micros() as u64)
    }

    /// Send one transaction on a fresh unidirectional stream. In dry-run the
    /// stream is never opened and nothing is written.
    pub async fn send(&self, wire: &[u8]) -> SendRecord {
        let t_open = self.tb.now_us();
        if self.dry {
            return SendRecord { t_open_us: t_open, t_done_us: t_open, err: None, dry: true };
        }
        let Some(conn) = &self.conn else {
            return SendRecord { t_open_us: t_open, t_done_us: t_open, err: Some("not connected".into()), dry: false };
        };
        let res: Result<()> = async {
            let mut s = conn.open_uni().await?;
            s.write_all(wire).await?;
            s.finish()?;
            Ok(())
        }
        .await;
        SendRecord { t_open_us: t_open, t_done_us: self.tb.now_us(), err: res.err().map(|e| e.to_string()), dry: false }
    }

    pub async fn close(&mut self) {
        if let Some(c) = self.conn.take() {
            c.close(0u32.into(), b"done");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use quinn::ServerConfig;

    fn server() -> (Endpoint, SocketAddr) {
        let key = rcgen::KeyPair::generate_for(&rcgen::PKCS_ED25519).unwrap();
        let cert = rcgen::CertificateParams::new(vec!["solana".into()]).unwrap().self_signed(&key).unwrap();
        let provider = Arc::new(rustls::crypto::ring::default_provider());
        let mut tls = rustls::ServerConfig::builder_with_provider(provider)
            .with_protocol_versions(&[&rustls::version::TLS13])
            .unwrap()
            .with_client_cert_verifier(Arc::new(AcceptAnyClient))
            .with_single_cert(vec![cert.der().clone()], PrivateKeyDer::Pkcs8(PrivatePkcs8KeyDer::from(key.serialize_der())))
            .unwrap();
        tls.alpn_protocols = vec![ALPN_TPU.to_vec()];
        let cfg = ServerConfig::with_crypto(Arc::new(quinn::crypto::rustls::QuicServerConfig::try_from(tls).unwrap()));
        let ep = Endpoint::server(cfg, "127.0.0.1:0".parse().unwrap()).unwrap();
        let addr = ep.local_addr().unwrap();
        (ep, addr)
    }

    #[derive(Debug)]
    struct AcceptAnyClient;
    impl rustls::server::danger::ClientCertVerifier for AcceptAnyClient {
        fn root_hint_subjects(&self) -> &[rustls::DistinguishedName] {
            &[]
        }
        fn verify_client_cert(
            &self,
            _: &CertificateDer<'_>,
            _: &[CertificateDer<'_>],
            _: UnixTime,
        ) -> Result<rustls::server::danger::ClientCertVerified, rustls::Error> {
            Ok(rustls::server::danger::ClientCertVerified::assertion())
        }
        fn verify_tls12_signature(
            &self,
            _: &[u8],
            _: &CertificateDer<'_>,
            _: &DigitallySignedStruct,
        ) -> Result<HandshakeSignatureValid, rustls::Error> {
            Ok(HandshakeSignatureValid::assertion())
        }
        fn verify_tls13_signature(
            &self,
            _: &[u8],
            _: &CertificateDer<'_>,
            _: &DigitallySignedStruct,
        ) -> Result<HandshakeSignatureValid, rustls::Error> {
            Ok(HandshakeSignatureValid::assertion())
        }
        fn supported_verify_schemes(&self) -> Vec<SignatureScheme> {
            vec![SignatureScheme::ED25519]
        }
    }

    async fn collect(ep: Endpoint, n: usize) -> Vec<Vec<u8>> {
        let conn = ep.accept().await.unwrap().await.unwrap();
        let mut out = Vec::new();
        for _ in 0..n {
            let mut s = conn.accept_uni().await.unwrap();
            out.push(s.read_to_end(4096).await.unwrap());
        }
        out
    }

    #[tokio::test]
    async fn streams_arrive_in_send_order_with_monotonic_timestamps() {
        let (ep, addr) = server();
        let srv = tokio::spawn(collect(ep, 5));
        let tb = TimeBase::new();
        let mut s = QuicSender::new(tb, false).unwrap();
        let info = s.connect(addr).await.unwrap();
        assert!(info.handshake_us.unwrap() > 0);
        let mut last = 0;
        for i in 0..5u8 {
            let r = s.send(&[i; 10]).await;
            assert!(r.err.is_none(), "{:?}", r.err);
            assert!(r.t_open_us >= last && r.t_done_us >= r.t_open_us);
            last = r.t_done_us;
        }
        let got = srv.await.unwrap();
        assert_eq!(got, (0..5u8).map(|i| vec![i; 10]).collect::<Vec<_>>());
    }

    #[tokio::test]
    async fn dry_run_writes_nothing() {
        let (ep, addr) = server();
        let accept = tokio::spawn(async move {
            let conn = ep.accept().await.unwrap().await.unwrap();
            tokio::time::timeout(Duration::from_millis(300), conn.accept_uni()).await.is_err()
        });
        let mut s = QuicSender::new(TimeBase::new(), true).unwrap();
        s.connect(addr).await.unwrap();
        let r = s.send(&[1, 2, 3]).await;
        assert!(r.dry && r.err.is_none());
        assert!(accept.await.unwrap(), "server saw a stream in dry-run");
    }
}
