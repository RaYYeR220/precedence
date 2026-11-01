/* Overview page: stats, verdict table, dose-response small multiples, ties, inclusion,
   fingerprint and predictions, all drawn from window.PRECEDENCE_OVERVIEW. */
(function () {
  "use strict";
  const D = window.PRECEDENCE_OVERVIEW;
  const P = window.PREC;
  const { esc, pct, ci, int, theta, isNum } = P;
  if (!D) return;

  /* -- stats ------------------------------------------------------------------ */
  const stats = document.getElementById("stats");
  stats.innerHTML = (D.stats || [])
    .map((s) => `<div class="stat"><dt>${esc(s.value).replace(/ of /g, '<span class="of">of</span>')}</dt><dd>${esc(s.label)}</dd></div>`)
    .join("") || `<div class="stat"><dt>–</dt><dd>No results yet.</dd></div>`;

  /* -- verdict table ------------------------------------------------------------ */
  const LO = 0.5, HI = 1.0;
  const xs = (v) => Math.max(0, Math.min(100, ((v - LO) / (HI - LO)) * 100));

  function scale(u) {
    if (!isNum(u.concordance)) return `<span class="conc-val">–</span>`;
    const none = u.verdict !== "rule";
    const c = u.concordance_ci || [];
    const bar = isNum(c[0]) && isNum(c[1]) ? `<span class="ci" style="left:${xs(c[0])}%;width:${Math.max(0.5, xs(c[1]) - xs(c[0]))}%"></span>` : "";
    const tipText = `<b>${esc(u.name.length > 20 ? P.short(u.name) : u.name)}</b><br>concordance with ${esc(u.rule_text || u.reference_text)}: <span class="num">${pct(u.concordance)} ${ci(c)}</span><br><span class="muted">${int(u.n_pairs)} probe pairs; ${none ? "not distinguishable from random" : "p = " + (isNum(u.p_perm) ? (u.p_perm < 0.001 ? "< 0.001" : u.p_perm.toFixed(3)) : "–")}</span>`;
    return `<div class="conc"><span class="conc-val">${pct(u.concordance)}</span><span class="scale${none ? " none" : ""}" data-tip="${esc(tipText)}"><span class="mid" style="left:${xs(0.75)}%"></span>${bar}<span class="pt" style="left:${xs(u.concordance)}%"></span></span></div>`;
  }

  function ruleCell(u) {
    if (u.verdict === "insufficient_data") return `<span class="rule none">insufficient data</span>`;
    if (u.verdict !== "rule") return `<span class="rule none">none detectable</span><span class="rule-sub">closest: ${esc(u.reference_text)}</span>`;
    return `<span class="rule">${esc(u.rule_text)}</span>` + (u.component ? `<span class="rule-sub">${esc(u.component)}</span>` : "");
  }

  function verdictText(u) {
    if (u.verdict === "insufficient_data") return "Insufficient data";
    const p = isNum(u.p_perm) ? (u.p_perm < 0.001 ? "p < 0.001" : "p = " + u.p_perm.toFixed(3)) : "";
    const mix = u.validator_rules && Object.keys(u.validator_rules).length
      ? "; validators: " + Object.entries(u.validator_rules).sort((a, b) => b[1] - a[1]).map(([k, n]) => n + " " + k).join(", ")
      : "";
    return (u.verdict === "rule" ? "Rule, " : "No rule detectable, ") + p + mix;
  }

  const byName = {};
  (D.validators || []).forEach((v) => (byName[v.name] = v));
  const tbody = document.querySelector("#verdicts tbody");
  const chev = `<svg class="chev" viewBox="0 0 10 10" aria-hidden="true"><path d="M3 1.5L6.5 5 3 8.5"/></svg>`;
  const rows = [];
  (D.families || []).forEach((f, i) => {
    const id = "fam-" + i;
    const stake = isNum(f.stake_share) ? `, ${pct(f.stake_share)} of stake` : "";
    rows.push(`<tr class="fam-row">
      <td class="fam-cell"><button class="fam-toggle" type="button" aria-expanded="false" aria-controls="${id}" data-group="${id}">${chev}${esc(f.name)}</button><span class="fam-sub">${esc(f.orderer_text || "")}${esc(stake)}</span></td>
      <td class="r num hide-s">${int(f.n_validators)}</td>
      <td class="r num hide-s">${int(f.n_windows)}</td>
      <td class="r num hide-s">${int(f.n_landed)}</td>
      <td class="rule-cell">${ruleCell(f)}</td>
      <td>${scale(f)}</td>
      <td class="r num hide-s">${theta(f.theta)}</td>
      <td class="hide-m"><span class="verdict-text">${esc(verdictText(f))}</span></td>
    </tr>`);
    (f.validators || []).forEach((name) => {
      const v = byName[name];
      if (!v) return;
      rows.push(`<tr class="val-row" data-group="${id}" hidden>
        <td><a href="blocks.html#validator=${encodeURIComponent(v.name)}" title="${esc(v.name)}">${esc(P.short(v.name))}</a></td>
        <td class="r num hide-s"></td>
        <td class="r num hide-s">${int(v.n_windows)}</td>
        <td class="r num hide-s">${int(v.n_landed)}</td>
        <td class="rule-cell">${ruleCell({ ...v, component: null })}</td>
        <td>${scale(v)}</td>
        <td class="r num hide-s">${theta(v.theta)}</td>
        <td class="hide-m"><span class="verdict-text">${esc(verdictText({ ...v, validator_rules: null }))}</span></td>
      </tr>`);
    });
  });
  tbody.innerHTML = rows.join("") || `<tr><td colspan="8" class="empty">No results yet.</td></tr>`;
  tbody.addEventListener("click", (ev) => {
    const b = ev.target.closest(".fam-toggle");
    if (!b) return;
    const open = b.getAttribute("aria-expanded") !== "true";
    b.setAttribute("aria-expanded", String(open));
    tbody.querySelectorAll(`tr.val-row[data-group="${b.dataset.group}"]`).forEach((r) => (r.hidden = !open));
  });
  // The first validator row of each group carries the group id for aria-controls.
  (D.families || []).forEach((f, i) => {
    const first = tbody.querySelector(`tr.val-row[data-group="fam-${i}"]`);
    if (first) first.id = "fam-" + i;
  });

  /* -- dose-response small multiples ------------------------------------------- */
  const dose = D.dose || {};
  const buckets = dose.buckets || [];
  const short = { hp_sent_earlier_or_same: "≤ 0", "<1ms": "< 1", "1-5ms": "1–5", "5-20ms": "5–20", "20-50ms": "20–50", "50-150ms": "50–150", ">150ms": "> 150" };
  const narrowMq = window.matchMedia("(max-width: 640px)");
  let W = 280, H = 168;
  const M = { l: 30, r: 8, t: 8, b: 26 };
  const famRule = {};
  (D.families || []).forEach((f) => (famRule[f.name] = f.verdict === "rule" ? f.rule_text : "no rule detectable"));

  function panel(unit) {
    const rowsU = (dose.units || {})[unit] || [];
    const iw = W - M.l - M.r, ih = H - M.t - M.b;
    const x = (i) => M.l + (buckets.length > 1 ? (i / (buckets.length - 1)) * iw : iw / 2);
    const y = (v) => M.t + (1 - v) * ih;
    let g = `<g class="grid">`;
    [0, 0.5, 1].forEach((v) => (g += `<line x1="${M.l}" x2="${W - M.r}" y1="${y(v)}" y2="${y(v)}" class="${v === 0.5 ? "half" : ""}"/>`));
    g += `</g><g class="axis">`;
    [0, 0.5, 1].forEach((v) => (g += `<text x="${M.l - 6}" y="${y(v) + 3}" text-anchor="end">${v * 100}</text>`));
    buckets.forEach((b, i) => {
      if (i % 2 === 0 || buckets.length < 6) g += `<text x="${x(i)}" y="${H - 8}" text-anchor="middle">${esc(short[b] || b)}</text>`;
    });
    g += `</g><g class="series">`;
    let path = "";
    let pen = false;
    const marks = [];
    rowsU.forEach((r, i) => {
      if (!isNum(r.rate) || !r.n_pairs) {
        pen = false;
        return;
      }
      path += (pen ? "L" : "M") + x(i).toFixed(1) + " " + y(r.rate).toFixed(1);
      pen = true;
      const c = r.ci || [];
      if (isNum(c[0]) && isNum(c[1])) marks.push(`<line class="whisk" x1="${x(i)}" x2="${x(i)}" y1="${y(c[0])}" y2="${y(c[1])}"/>`);
      const thin = r.n_pairs < 20 || (r.n_batches || 0) < 3;
      const label = dose.labels && dose.labels[r.bucket] ? dose.labels[r.bucket] : r.bucket;
      const t = `<b>${esc(unit === "all" ? "All families" : unit)}</b><br>Higher-priority probe ${esc(label)}<br><span class="num">${pct(r.rate)} ${ci(c)}</span> came first<br><span class="muted">${int(r.n_pairs)} pairs in ${int(r.n_batches)} batches${thin ? "; few pairs" : ""}</span>`;
      marks.push(`<circle cx="${x(i)}" cy="${y(r.rate)}" r="${thin ? 3.5 : 4}" class="${thin ? "thin" : ""}"/>`);
      marks.push(`<rect class="hit" x="${x(i) - 14}" y="${M.t}" width="28" height="${ih}" data-tip="${esc(t)}"/>`);
    });
    g += `<path d="${path}"/>` + marks.join("") + `</g>`;
    const title = unit === "all" ? "All families" : unit;
    const sub = unit === "all" ? "pooled" : famRule[unit] || "";
    const desc = rowsU.filter((r) => isNum(r.rate) && r.n_pairs).map((r) => `${(dose.labels || {})[r.bucket] || r.bucket}: ${pct(r.rate)}`).join("; ");
    return `<figure class="panel" style="margin:0"><h3>${esc(title)}<span>${esc(sub)}</span></h3><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(title + ". Higher priority first, by send gap. " + desc)}">${g}</svg></figure>`;
  }
  const doseEl = document.getElementById("dose");
  const order = (dose.order || []).filter((u) => (dose.units || {})[u]);
  function drawDose() {
    // One column on phones, drawn at its real width so the axis text stays legible.
    [W, H] = narrowMq.matches ? [340, 150] : [280, 168];
    doseEl.innerHTML = order.map(panel).join("") || `<p class="empty">No probe pairs yet.</p>`;
  }
  drawDose();
  if (narrowMq.addEventListener) narrowMq.addEventListener("change", drawDose);
  if (order.length) doseEl.insertAdjacentHTML("afterend", `<p class="x-title">Horizontal axis: how many milliseconds later the higher-priority probe was sent (≤ 0: sent first or together). Vertical: % of pairs where it still came first; dashed line at 50%. Hollow points rest on fewer than 20 pairs.</p>`);

  /* -- dot rows ------------------------------------------------------------------ */
  function dotRow(name, sub, rate, interval, valHtml, tipHtml, tickAt) {
    const c = interval || [];
    const bar = isNum(c[0]) && isNum(c[1]) ? `<span class="ci" style="left:${c[0] * 100}%;width:${Math.max(0.4, (c[1] - c[0]) * 100)}%"></span>` : "";
    const tick = isNum(tickAt) ? `<span class="tick" style="left:${tickAt * 100}%"></span>` : "";
    const pt = isNum(rate) ? `<span class="pt" style="left:${rate * 100}%"></span>` : "";
    return `<div class="dot-row"><span class="name">${esc(name)}${sub ? `<small>${esc(sub)}</small>` : ""}</span><span class="track" data-tip="${esc(tipHtml)}"><span class="mid"></span>${bar}${tick}${pt}</span><span class="val">${valHtml}</span></div>`;
  }

  const tiesEl = document.getElementById("ties");
  const tieRows = (D.families || []).filter((f) => f.tie && f.tie.n_pairs);
  if (tieRows.length) {
    tiesEl.innerHTML =
      tieRows
        .map((f) => {
          const t = f.tie, c = t.ci || [];
          const dir = isNum(c[0]) && c[0] > 0.5 ? "oldest first" : isNum(c[1]) && c[1] < 0.5 ? "newest first" : "no reliable order";
          return dotRow(f.name, dir, t.rate, c, `${pct(t.rate)}<small>${int(t.n_pairs)} pairs</small>`, `<b>${esc(f.name)}</b>: earlier-sent probe first in <span class="num">${pct(t.rate)} ${ci(c)}</span> of ${int(t.n_pairs)} tie pairs (${int(t.n_batches)} batches)`);
        })
        .join("") + `<div class="scale-ends"><span></span><span class="ends"><span>newest first</span><span>50%</span><span>oldest first</span></span><span></span></div>`;
  } else tiesEl.innerHTML = `<p class="empty">No tie batch had two probes in one block.</p>`;

  const incEl = document.getElementById("inclusion");
  const inc = (D.inclusion || {}).families || [];
  if (inc.length) {
    incEl.innerHTML =
      inc
        .map((r) =>
          dotRow(
            r.family,
            int(r.n) + " sent",
            r.rate,
            r.ci,
            `${pct(r.rate)}<small>${pct(r.spill)} spilled</small>`,
            `<b>${esc(r.family)}</b>: <span class="num">${pct(r.rate)} ${ci(r.ci)}</span> of ${int(r.n)} probes landed; <span class="num">${pct(r.spill)}</span> of those in another leader's block`,
            isNum(r.spill) ? r.spill : null
          )
        )
        .join("") + `<div class="scale-ends"><span></span><span class="ends"><span>0%</span><span>50%</span><span>100%</span></span><span></span></div>`;
  } else incEl.innerHTML = `<p class="empty">No probe sent yet.</p>`;

  /* -- fingerprint ------------------------------------------------------------------ */
  const fp = D.fingerprint || {};
  const fpEl = document.getElementById("fingerprint");
  const groups = [
    ["validator_level", "Per validator"],
    ["window_level", "Per window"],
  ]
    .filter(([k]) => fp[k] && isNum(fp[k].probe_accuracy))
    .map(([k, label]) => {
      const f = fp[k];
      const row = (name, v, cls) => `<div class="bar-row"><span>${esc(name)}</span><span class="track" data-tip="${esc(`<b>${label}</b>, ${name}: <span class='num'>${pct(v)}</span> correct over ${int(f.n_rows)} ${k === "validator_level" ? "validators" : "windows"}`)}"><span class="fill ${cls}" style="width:${isNum(v) ? v * 100 : 0}%"></span></span><span class="val">${pct(v)}</span></div>`;
      return `<div class="bar-group"><h3>${label}</h3>${row("Our probes", f.probe_accuracy, "")}${row("Whole-block baseline", f.baseline_accuracy, "base")}${row("Chance (majority class)", f.chance_majority, "chance")}</div>`;
    });
  fpEl.innerHTML = groups.join("") || `<p class="empty">Not enough validators to score yet.</p>`;

  /* -- predictions ---------------------------------------------------------------- */
  const icons = {
    confirmed: `<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2 6.5l2.5 2.5L10 3"/></svg>`,
    refuted: `<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M3 3l6 6M9 3l-6 6"/></svg>`,
    inconclusive: `<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 6h7"/></svg>`,
  };
  document.querySelector("#predictions tbody").innerHTML = (D.predictions || [])
    .map((p) => `<tr><td>${esc(p.id)}</td><td>${esc(p.prediction)}<div class="ev show-s">${esc(p.evidence)}</div></td><td><span class="outcome ${esc(p.outcome)}">${icons[p.outcome] || ""}${esc(p.outcome)}</span></td><td class="ev hide-s">${esc(p.evidence)}</td></tr>`)
    .join("");
})();
