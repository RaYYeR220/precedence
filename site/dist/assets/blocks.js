/* Blocks page: one record per batch, each landed block drawn as a strip of every
   transaction in ledger order. Data: window.PRECEDENCE_BATCHES. */
(function () {
  "use strict";
  const D = window.PRECEDENCE_BATCHES;
  const P = window.PREC;
  const { esc, int, pct, short, isNum } = P;
  if (!D) return;

  const PAGE = 20;
  const PROBE_W = 18;
  const PAD = 8;
  const H = 66;
  const Y = { tickTop: 9, tickH: 22, probeTop: 2, probeH: 36, entryTop: 41, entryH: 7, axis: 51 };
  const KIND = { ladder: "Ladder", tie: "Tie", sweep: "Sweep", format: "Format" };
  const ARM = { distinct: "distinct accounts", shared: "shared account" };

  const enc = D.level_encoding || { alphabet: "0123456789abcdefghijklmnopqrstuvwxyz", floor_log10: 3, per_decade: 3, unknown: "-" };
  const blocks = D.blocks || {};
  const batches = D.batches || [];

  const $ = (id) => document.getElementById(id);
  const sel = { family: $("f-family"), validator: $("f-validator"), kind: $("f-kind"), arm: $("f-arm"), landed: $("f-landed"), zoom: $("f-zoom") };

  /* -- filter options --------------------------------------------------------------- */
  (D.families || []).forEach((f) => sel.family.insertAdjacentHTML("beforeend", `<option value="${esc(f)}">${esc(f)}</option>`));
  (D.validators || []).forEach((v) => sel.validator.insertAdjacentHTML("beforeend", `<option value="${esc(v.id)}">${esc(short(v.id))} (${esc(v.family)})</option>`));

  /* -- state in the URL hash -------------------------------------------------------- */
  const state = { family: "", validator: "", kind: "", arm: "", landed: "", zoom: "3", page: 1 };
  function readHash() {
    const h = new URLSearchParams(location.hash.replace(/^#/, ""));
    for (const k of Object.keys(state)) if (h.has(k)) state[k] = k === "page" ? Math.max(1, parseInt(h.get(k), 10) || 1) : h.get(k);
    for (const k of Object.keys(sel)) {
      if ([...sel[k].options].some((o) => o.value === state[k])) sel[k].value = state[k];
      else if (k !== "zoom") state[k] = "";
    }
  }
  function writeHash() {
    const h = new URLSearchParams();
    for (const k of Object.keys(state)) {
      const v = String(state[k]);
      if (v && !(k === "page" && v === "1") && !(k === "zoom" && v === "3")) h.set(k, v);
    }
    const s = h.toString();
    try {
      history.replaceState(null, "", s ? "#" + s : location.pathname + location.search);
    } catch (e) {
      /* Some browsers refuse history changes on file:// pages; the filters still work. */
    }
  }

  function matches(b) {
    if (state.kind && b.kind !== state.kind) return false;
    if (state.arm && b.arm !== state.arm) return false;
    if (state.landed === "landed" && !b.blocks.length) return false;
    if (state.landed === "none" && b.blocks.length) return false;
    if (state.family && !(b.blocks.some((x) => x.family === state.family) || (!b.blocks.length && b.target_family === state.family))) return false;
    if (state.validator && !(b.blocks.some((x) => x.producer === state.validator) || b.target === state.validator)) return false;
    return true;
  }

  /* -- text helpers ----------------------------------------------------------------- */
  function cadence(b) {
    const s = b.spacing_us;
    if (!isNum(s) || s === 0) return "burst";
    return P.dec(s / 1000, s % 1000 ? 1 : 0) + " ms apart";
  }
  function anchorText(a) {
    if (!a || !isNum(a.slots)) return "";
    const n = a.slots;
    if (n === 0) return "first send at the window start";
    const word = n === 1 ? "slot" : "slots";
    return a.type === "before_window" ? `first send ${n} ${word} before the window` : `first send ${n} ${word} into the window`;
  }
  function statusText(s) {
    if (!s || s === "sent") return "";
    if (s.startsWith("skipped:")) return "Skipped: " + s.slice(8).replace(/_/g, " ");
    return s === "dry" ? "Dry run: nothing sent" : s;
  }
  function priorityText(ch) {
    if (ch === enc.unknown) return "priority unknown";
    const lvl = enc.alphabet.indexOf(ch);
    const exp = enc.floor_log10 + lvl / enc.per_decade;
    const mant = Math.pow(10, exp - Math.floor(exp));
    return `priority ≈ ${mant.toFixed(1)} × 10<sup>${Math.floor(exp)}</sup>`;
  }
  function curlFor(slot) {
    const body = JSON.stringify({ jsonrpc: "2.0", id: 1, method: "getBlock", params: [slot, { encoding: "json", maxSupportedTransactionVersion: 1, transactionDetails: "signatures", rewards: true }] });
    return `curl -s https://api.mainnet-beta.solana.com -X POST -H 'Content-Type: application/json' -d '${body}'`;
  }

  /* -- one strip ---------------------------------------------------------------------- */
  function layout(blk, probeAt, fx) {
    const n = blk.n_tx;
    const x = new Float64Array(n + 1);
    let cur = PAD;
    for (let i = 0; i < n; i++) {
      x[i] = cur;
      cur += probeAt.has(i) ? PROBE_W : fx;
    }
    x[n] = cur;
    return { x, width: cur + PAD };
  }
  function entryStarts(blk) {
    if (!blk.entries) return null;
    const starts = [];
    let s = blk.entries_start || 0;
    for (const c of blk.entries) {
      starts.push(s);
      s += c;
    }
    return starts;
  }
  function entryOf(starts, i) {
    if (!starts) return null;
    let lo = 0, hi = starts.length - 1, k = -1;
    while (lo <= hi) {
      const m = (lo + hi) >> 1;
      if (starts[m] <= i) {
        k = m;
        lo = m + 1;
      } else hi = m - 1;
    }
    return k < 0 ? null : k;
  }

  function stripSvg(b, blk, entry, fx) {
    const probeAt = new Map(entry.probes.map((p) => [p.i, p]));
    const { x, width } = layout(blk, probeAt, fx);
    const levels = blk.levels || "";
    const tw = Math.max(1, fx - 1);
    // Terciles of the other transactions' priority levels, for shading.
    const lv = [];
    for (let i = 0; i < blk.n_tx; i++) if (!probeAt.has(i) && levels[i] !== enc.unknown) lv.push(enc.alphabet.indexOf(levels[i]));
    lv.sort((a, c) => a - c);
    const q1 = lv.length ? lv[Math.floor(lv.length / 3)] : 0, q2 = lv.length ? lv[Math.floor((2 * lv.length) / 3)] : 0;
    const paths = ["", "", "", ""];
    for (let i = 0; i < blk.n_tx; i++) {
      if (probeAt.has(i)) continue;
      const ch = levels[i];
      const l = ch === enc.unknown || ch === undefined ? 0 : enc.alphabet.indexOf(ch);
      const cls = ch === enc.unknown ? 0 : l < q1 ? 0 : l < q2 ? 1 : l === lv[lv.length - 1] ? 3 : 2;
      paths[cls] += `M${x[i]} ${Y.tickTop}h${tw}v${Y.tickH}h-${tw}z`;
    }
    let out = `<g class="foreign">` + paths.map((d, k) => (d ? `<path class="l${k}" d="${d}"/>` : "")).join("") + `</g>`;
    const starts = entryStarts(blk);
    if (starts) {
      let d = "";
      for (const s of starts) if (s > 0 && s < blk.n_tx) d += `M${Math.round(x[s] - 0.5) + 0.5} ${Y.entryTop}v${Y.entryH}`;
      if (d) out += `<path class="entries" d="${d}"/>`;
    }
    // Axis: a label every 100 (or more) transactions.
    const step = [50, 100, 200, 500, 1000].find((s) => s * fx >= 90) || 1000;
    let ax = `<path class="axis-l" d="M${PAD} ${Y.axis}H${width - PAD}`;
    let at = "";
    for (let i = 0; i <= blk.n_tx; i += step) {
      const xi = x[i];
      ax += `M${Math.round(xi) + 0.5} ${Y.axis}v3`;
      if (xi + 8 + 6.2 * String(i).length <= width) at += `<text class="axis-t" x="${xi + 2}" y="${Y.axis + 11}">${i}</text>`;
    }
    out += ax + `"/>` + at;
    // Our probes, drawn above the hover layer of the other transactions.
    let probes = "";
    const ramp = P.ramp(Math.max(1, b.n_ranks || 1));
    for (const p of entry.probes) {
      const r = ramp[Math.max(0, Math.min(ramp.length - 1, (p.fee_rank || 1) - 1))];
      const k = entryOf(starts, p.i);
      const tip = `<b>Our probe ${p.idx}</b><br>fee <span class="num">${int(p.fee)}</span> lamports, fee rank ${p.fee_rank} of ${b.n_ranks}<br>${p.dt_us ? `sent ${ordinal(p.send_rank)}, <span class="num">${P.dec(p.dt_us / 1000, 2)} ms</span> after the first send` : `sent ${p.send_rank === 1 ? "first" : ordinal(p.send_rank) + ", together with the first"}`}<br>position <span class="num">${int(p.i)}</span> of ${int(blk.n_tx)}${k !== null ? `, entry ${int(k + 1)} of ${int(starts.length)}` : ""}<br><span class="muted">${esc(p.format || "")} ${esc(short(p.sig))}</span>`;
      const cx = x[p.i] + (PROBE_W - 1) / 2;
      probes += `<g class="probe" data-tip="${esc(tip)}"><rect x="${x[p.i]}" y="${Y.probeTop}" width="${PROBE_W - 1}" height="${Y.probeH}" fill="${r.fill}"/><text x="${cx}" y="${Y.probeTop + 15}" fill="${r.ink}">${p.fee_rank}</text><text x="${cx}" y="${Y.probeTop + 31}" fill="${r.ink}" opacity="0.8">${p.send_rank}</text><rect class="ring" x="${x[p.i] - 1.5}" y="${Y.probeTop - 1.5}" width="${PROBE_W + 2}" height="${Y.probeH + 3}" rx="3"/></g>`;
    }
    const label = `Block ${blk.slot}: ${blk.n_tx} transactions; our probes at positions ${entry.probes.map((p) => p.i).join(", ")} with fee ranks ${entry.probes.map((p) => p.fee_rank).join(", ")} and send order ${entry.probes.map((p) => p.send_rank).join(", ")}.`;
    const overlay = `<rect class="hit over" x="${PAD}" y="${Y.tickTop}" width="${width - 2 * PAD}" height="${Y.tickH}" data-tip=""/>`;
    return { html: `<svg width="${width}" height="${H}" viewBox="0 0 ${width} ${H}" role="img" aria-label="${esc(label)}">${out}${overlay}${probes}</svg>`, x, width, starts, probeAt };
  }

  function mainnet(b) {
    return !b.mode || b.mode === "live" || b.mode === "dry";
  }

  function ordinal(n) {
    const s = ["th", "st", "nd", "rd"], v = n % 100;
    return n + (s[(v - 20) % 10] || s[v] || s[0]);
  }

  function pairsText(pairs) {
    const hp = pairs.higher_priority_first || [0, 0], es = pairs.earlier_sent_first || [0, 0];
    const parts = [];
    if (hp[1]) parts.push(`higher priority first in <span class="num">${hp[0]}</span> of ${hp[1]} pairs`);
    if (es[1]) parts.push(`earlier sent first in <span class="num">${es[0]}</span> of ${es[1]}`);
    return parts.join("; ");
  }

  function blockHtml(b, entry, bi) {
    const blk = blocks[String(entry.slot)];
    const who = entry.producer ? `<span class="who" title="${esc(entry.producer)}">${esc(short(entry.producer))}</span> ${esc(entry.family)}` : "unknown producer";
    const where = isNum(entry.slot_in_window) ? (entry.slot_in_window >= 0 && entry.slot_in_window <= 3 ? `slot ${entry.slot_in_window + 1} of the window` : entry.slot_in_window < 0 ? `${-entry.slot_in_window} slot(s) before the window` : `${entry.slot_in_window - 3} slot(s) after the window`) : "";
    // Explorer links and the public RPC only make sense for mainnet runs; a local cluster's slots are its own.
    const links = mainnet(b)
      ? `<span class="links"><a class="btn" href="https://solscan.io/block/${entry.slot}" target="_blank" rel="noopener">Solscan</a><button class="btn" type="button" data-curl="${entry.slot}">Copy getBlock</button></span>`
      : `<span class="links muted-note">${esc(b.mode || "local")} run: no public explorer</span>`;
    const head = `<div class="blk-head"><span class="slot">Slot ${int(entry.slot)}</span><span>produced by ${who}</span>${where ? `<span>${esc(where)}</span>` : ""}${entry.spilled ? `<span class="spill">not the targeted leader</span>` : ""}${links}</div>`;
    if (!blk) return `<div class="b-block">${head}<p class="nothing">Block file not stored.</p></div>`;
    const pos = entry.probes.map((p) => p.i);
    const where2 = pos.length === 1 ? `1 of our probes at position <span class="num">${int(pos[0])}</span>` : `<span class="num">${pos.length}</span> of our probes at positions <span class="num">${int(Math.min(...pos))}–${int(Math.max(...pos))}</span>`;
    const sum = `<div class="blk-sum"><span>${where2} of ${int(blk.n_tx)}</span>${pairsText(entry.pairs) ? `<span>${pairsText(entry.pairs)}</span>` : ""}${blk.entries ? "" : `<span>no entry data</span>`}</div>`;
    const starts = entryStarts(blk);
    const rowsP = entry.probes
      .map((p) => {
        const k = entryOf(starts, p.i);
        return `<tr><td>${int(p.i)}</td><td>${p.fee_rank}</td><td>${p.send_rank}</td><td>${int(p.fee)}</td><td>${P.dec(p.dt_us / 1000, 2)}</td><td>${k === null ? "–" : int(k + 1) + " (" + blk.entries[k] + " tx)"}</td><td>${esc(p.format || "")}</td><td>${mainnet(b) ? `<a href="https://solscan.io/tx/${esc(p.sig)}" target="_blank" rel="noopener" title="${esc(p.sig)}">${esc(short(p.sig))}</a>` : `<span title="${esc(p.sig)}">${esc(short(p.sig))}</span>`}</td></tr>`;
      })
      .join("");
    const more = `<details class="more"><summary>${mainnet(b) ? "Probe details and getBlock command" : "Probe details"}</summary><div class="ptable"><table><thead><tr><th>Position</th><th>Fee rank</th><th>Send order</th><th>Fee (lamports)</th><th>Sent at (ms)</th><th>Entry</th><th>Format</th><th>Signature</th></tr></thead><tbody>${rowsP}</tbody></table></div>${mainnet(b) ? `<div class="curl">${esc(curlFor(entry.slot))}</div>` : ""}</details>`;
    return `<div class="b-block" data-b="${bi}" data-slot="${entry.slot}">${head}<div class="mini" aria-hidden="true"></div><div class="strip shade" tabindex="0" aria-label="Block ${entry.slot}, scroll sideways"></div>${sum}${more}</div>`;
  }

  function batchHtml(b, bi) {
    const title = `${KIND[b.kind] || b.kind}, ${ARM[b.arm] || b.arm}`;
    const facts = [
      `target <span class="num" title="${esc(b.target || "")}">${esc(short(b.target))}</span> ${esc(b.target_family || "")}`,
      `window from slot <span class="num">${int(b.window_first_slot)}</span>`,
      `${esc(b.path || "")}, ${esc(cadence(b))}`,
      esc(anchorText(b.anchor)),
      `<span class="num">${b.n_landed}</span> of ${b.n_planned} in a block`,
    ].filter(Boolean);
    let html = `<article class="batch"><div class="b-head"><h2 class="b-title">${esc(title)}<span class="num">#${b.batch}</span></h2><div class="b-facts">${facts.map((f) => `<span>${f}</span>`).join("")}</div><span class="b-run num">${esc(b.run)}</span></div>`;
    const st = statusText(b.status);
    if (st) html += `<p class="nothing">${esc(st)}</p>`;
    b.blocks.forEach((entry) => (html += blockHtml(b, entry, bi)));
    if (b.unlanded && b.unlanded.length) {
      const why = {};
      // With one fee level (tie batches) the send order is what tells the probes apart.
      const byFee = (b.n_ranks || 1) > 1;
      b.unlanded.forEach((u) => (why[u.why] = (why[u.why] || []).concat(byFee ? u.fee_rank : u.send_rank)));
      html += `<p class="nothing">Not in a stored block: ${Object.entries(why).map(([w, ranks]) => `${P.plural(ranks.length, "probe")} ${esc(w)} (${byFee ? "fee rank" : "send order"} ${ranks.sort((a, c) => a - c).join(", ")})`).join("; ")}.</p>`;
    } else if (!b.blocks.length && !st) html += `<p class="nothing">No probe of this batch is in a stored block.</p>`;
    return html + `</article>`;
  }

  /* -- mount strips after layout -------------------------------------------------------- */
  function mount(root, list) {
    const fx = parseInt(state.zoom, 10) + 1;
    root.querySelectorAll(".b-block[data-slot]").forEach((node) => {
      const b = list[parseInt(node.dataset.b, 10)];
      const slot = parseInt(node.dataset.slot, 10);
      const entry = b.blocks.find((e) => e.slot === slot);
      const blk = blocks[String(slot)];
      const strip = node.querySelector(".strip");
      const mini = node.querySelector(".mini");
      const s = stripSvg(b, blk, entry, fx);
      strip.innerHTML = s.html;
      const total = s.width;
      mini.innerHTML = entry.probes.map((p) => `<i style="left:${((s.x[p.i] + PROBE_W / 2) / total) * 100}%"></i>`).join("") + `<span class="view"></span>`;
      const view = mini.querySelector(".view");
      const sync = () => {
        const w = strip.clientWidth;
        view.style.left = (strip.scrollLeft / total) * 100 + "%";
        view.style.width = Math.min(100, (w / total) * 100) + "%";
        mini.style.display = w >= total ? "none" : "";
      };
      strip.addEventListener("scroll", sync, { passive: true });
      mini.addEventListener("click", (ev) => {
        const r = mini.getBoundingClientRect();
        strip.scrollLeft = ((ev.clientX - r.left) / r.width) * total - strip.clientWidth / 2;
      });
      const first = Math.min(...entry.probes.map((p) => s.x[p.i]));
      const last = Math.max(...entry.probes.map((p) => s.x[p.i])) + PROBE_W;
      strip.scrollLeft = Math.max(0, (first + last) / 2 - strip.clientWidth / 2);
      sync();
      // Hover over other transactions: describe the one under the pointer.
      const over = strip.querySelector(".over");
      over.addEventListener("pointermove", (ev) => {
        const r = strip.querySelector("svg").getBoundingClientRect();
        const px = ev.clientX - r.left;
        let lo = 0, hi = blk.n_tx - 1;
        while (lo < hi) {
          const m = (lo + hi + 1) >> 1;
          if (s.x[m] <= px) lo = m;
          else hi = m - 1;
        }
        const k = entryOf(s.starts, lo);
        over.setAttribute("data-tip", `Transaction <span class="num">${int(lo)}</span> of ${int(blk.n_tx)}<br>${priorityText(blk.levels[lo])}${k !== null ? `<br>entry ${int(k + 1)} of ${int(s.starts.length)} (${blk.entries[k]} tx)` : ""}`);
      });
    });
  }

  /* -- page ------------------------------------------------------------------------------ */
  const root = $("batches");
  const pager = $("pager");
  const count = $("f-count");
  let filtered = [];

  function render() {
    P.hideTip();
    filtered = batches.filter(matches);
    const pages = Math.max(1, Math.ceil(filtered.length / PAGE));
    state.page = Math.min(state.page, pages);
    const start = (state.page - 1) * PAGE;
    const list = filtered.slice(start, start + PAGE);
    const nb = filtered.reduce((a, b) => a + b.blocks.length, 0);
    count.textContent = filtered.length
      ? `${int(filtered.length)} of ${int(batches.length)} batches, ${int(nb)} blocks; showing ${int(start + 1)}–${int(start + list.length)}`
      : `No batch matches these filters.`;
    root.innerHTML = list.map((b, i) => batchHtml(b, i)).join("") || `<p class="empty">${batches.length ? "No batch matches these filters." : "No batch recorded yet."}</p>`;
    mount(root, list);
    renderPager(pages);
    writeHash();
  }

  function renderPager(pages) {
    if (pages <= 1) {
      pager.innerHTML = "";
      return;
    }
    const cur = state.page;
    const nums = new Set([1, pages, cur - 1, cur, cur + 1, cur - 2, cur + 2].filter((n) => n >= 1 && n <= pages));
    const sorted = [...nums].sort((a, b) => a - b);
    let html = `<button type="button" data-page="${cur - 1}" ${cur === 1 ? "disabled" : ""} aria-label="Previous page">Prev</button>`;
    let prev = 0;
    for (const n of sorted) {
      if (n - prev > 1) html += `<span class="gap">…</span>`;
      html += `<button type="button" data-page="${n}" ${n === cur ? 'aria-current="page"' : ""}>${n}</button>`;
      prev = n;
    }
    html += `<button type="button" data-page="${cur + 1}" ${cur === pages ? "disabled" : ""} aria-label="Next page">Next</button>`;
    pager.innerHTML = html;
  }

  pager.addEventListener("click", (ev) => {
    const btn = ev.target.closest("button[data-page]");
    if (!btn || btn.disabled) return;
    state.page = parseInt(btn.dataset.page, 10);
    render();
    root.scrollIntoView({ block: "start" });
  });

  for (const k of Object.keys(sel)) {
    sel[k].addEventListener("change", () => {
      state[k] = sel[k].value;
      if (k !== "zoom") state.page = 1;
      render();
    });
  }

  root.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("button[data-curl]");
    if (!btn) return;
    const text = curlFor(parseInt(btn.dataset.curl, 10));
    let ok = false;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        ok = true;
      }
    } catch (e) {
      ok = false;
    }
    if (!ok) {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try {
        ok = document.execCommand("copy");
      } catch (e) {
        ok = false;
      }
      ta.remove();
    }
    btn.textContent = ok ? "Copied" : "Copy failed: open details";
    btn.classList.toggle("done", ok);
    setTimeout(() => {
      btn.textContent = "Copy getBlock";
      btn.classList.remove("done");
    }, 1600);
  });

  function legend() {
    const lg = $("lg-ramp");
    if (lg) lg.innerHTML = P.ramp(8).map((r) => `<i style="background:${r.fill}"></i>`).join("");
  }

  window.addEventListener("hashchange", () => {
    readHash();
    render();
  });
  P.onScheme(() => {
    legend();
    render();
  });
  readHash();
  legend();
  render();
})();
