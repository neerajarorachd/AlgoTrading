"""Builds the pattern review page (HTML) from explain_patterns_day.py's JSON.

    .venv/Scripts/python.exe backend/scripts/pattern_review_page.py hits.json review.html

One section per pattern: its conditions with the required values, how often
it fired (by timeframe), how many hits formed with a condition failing (the
80% rule), and every hit with each condition's actual value.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

TYPE_ORDER = {"bullish": 0, "bearish": 1, "caution": 2, "avoid": 3}


def build(data: dict) -> str:
    hits = data["hits"]
    by_pattern = defaultdict(list)
    for h in hits:
        by_pattern[h["pattern"]].append(h)
    patterns = []
    for key, rows in by_pattern.items():
        first = rows[0]
        fail_counter = Counter()
        for r in rows:
            for i, c in enumerate(r["conditions"]):
                if not c["met"]:
                    fail_counter[i] += 1
        patterns.append({
            "key": key, "name": first["name"], "type": first["type"], "weight": first["weight"],
            "conditions": [{"formula": c["formula"], "required": c["required"]} for c in first["conditions"]],
            "hits": len(rows),
            "by_tf": dict(Counter(r["timeframe"] for r in rows)),
            "with_fail": sum(1 for r in rows if r["met"] < r["of"]),
            "fails": {str(i): n for i, n in fail_counter.items()},
            "stocks": len({r["symbol"] for r in rows}),
        })
    patterns.sort(key=lambda p: (-p["hits"], TYPE_ORDER.get(p["type"], 9)))
    payload = json.dumps({"day": data["day"], "note": data.get("note"), "patterns": patterns, "hits": hits}, separators=(",", ":"), default=str)
    return TEMPLATE.replace("__DATA__", payload.replace("</", "<\\/"))


TEMPLATE = r"""<title>Pattern Hit Review</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
/* Layout: summary table first (one row per pattern), then one collapsible section per pattern with its rule and every hit. */
:root {
  --bg: #f6f7fa; --surface: #ffffff; --surface-2: #eef0f6; --fg: #1d2233; --muted: #5d6478; --faint: #8a90a3;
  --border: #dde1ea; --accent: #4b45b8; --accent-soft: #e7e6fa;
  --good: #15803d; --good-soft: #dcf2e3; --bad: #b42318; --bad-soft: #fbe2df;
  --bull: #15803d; --bear: #b42318; --caution: #c0457a; --avoid: #7f1d1d;
  --sans: "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, "Cascadia Mono", Consolas, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #12141c; --surface: #1a1d28; --surface-2: #232736; --fg: #e6e8f0; --muted: #a3a9bb; --faint: #7a8094;
  --border: #2e3344; --accent: #9d97ff; --accent-soft: #2a2850;
  --good: #4ade80; --good-soft: #163423; --bad: #f87171; --bad-soft: #3a1b1b;
  --bull: #4ade80; --bear: #f87171; --caution: #f08bb6; --avoid: #fca5a5; color-scheme: dark } }
:root[data-theme="dark"] {
  --bg: #12141c; --surface: #1a1d28; --surface-2: #232736; --fg: #e6e8f0; --muted: #a3a9bb; --faint: #7a8094;
  --border: #2e3344; --accent: #9d97ff; --accent-soft: #2a2850;
  --good: #4ade80; --good-soft: #163423; --bad: #f87171; --bad-soft: #3a1b1b;
  --bull: #4ade80; --bear: #f87171; --caution: #f08bb6; --avoid: #fca5a5; color-scheme: dark }
* { box-sizing: border-box }
body { background: var(--bg); color: var(--fg); font: 14px/1.5 var(--sans); margin: 0;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); padding-block: 24px 48px }
.wrap { max-width: 1180px; margin: 0 auto; display: flex; flex-direction: column; gap: 20px }
h1 { font-size: 22px; margin: 0; text-wrap: balance }
h2 { font-size: 16px; margin: 0 }
.sub { color: var(--muted); margin: 4px 0 0; max-width: 72ch }
.facts { display: flex; flex-wrap: wrap; gap: 8px 22px; color: var(--muted); font-size: 13px }
.facts b { color: var(--fg); font-variant-numeric: tabular-nums }
.rule { background: var(--accent-soft); border-radius: 6px; padding: 10px 14px; font-size: 13px; max-width: 80ch }
.panel { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; overflow: hidden }
.scroll { overflow-x: auto }
table { border-collapse: collapse; width: 100%; font-size: 13px }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--border); vertical-align: top }
th { font-size: 11px; text-transform: uppercase; letter-spacing: .05em; color: var(--faint); font-weight: 600; background: var(--surface-2); white-space: nowrap }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap }
tr.pick { cursor: pointer } tr.pick:hover td { background: var(--surface-2) }
.mono { font-family: var(--mono); font-size: 12px }
.type { display: inline-block; font-size: 11px; font-weight: 600; padding: 1px 8px; border-radius: 99px; border: 1px solid currentColor; white-space: nowrap }
.t-bullish { color: var(--bull) } .t-bearish { color: var(--bear) } .t-caution { color: var(--caution) } .t-avoid { color: var(--avoid) }
.bar { display: inline-block; height: 6px; border-radius: 3px; background: var(--bad); vertical-align: middle; margin-left: 6px }
.controls { display: flex; flex-wrap: wrap; gap: 10px 16px; align-items: center; font-size: 13px }
.controls label { display: inline-flex; align-items: center; gap: 6px; color: var(--muted) }
select { font: inherit; padding: 4px 8px; border: 1px solid var(--border); border-radius: 5px; background: var(--surface); color: var(--fg) }
details.pat { background: var(--surface); border: 1px solid var(--border); border-radius: 8px }
details.pat > summary { list-style: none; cursor: pointer; padding: 12px 14px; display: flex; flex-wrap: wrap; gap: 6px 14px; align-items: baseline }
details.pat > summary::-webkit-details-marker { display: none }
details.pat > summary::before { content: "▸"; color: var(--faint) } details.pat[open] > summary::before { content: "▾" }
details.pat .body { padding: 0 14px 14px; display: flex; flex-direction: column; gap: 12px; min-width: 0 }
.conds td:first-child { width: 28px; color: var(--faint) }
.cell-ok { color: var(--good) } .cell-no { color: var(--bad); background: var(--bad-soft) }
.cell { white-space: nowrap }
.cell small { display: block; color: var(--faint); font-size: 11px; white-space: normal }
.empty { padding: 14px; color: var(--muted) }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px }
</style>

<div class="wrap">
  <header>
    <h1>Pattern Hit Review</h1>
    <p class="sub" id="sub"></p>
  </header>
  <div class="facts" id="facts"></div>
  <div class="rule"><b>How a pattern forms:</b> at least 80% of its conditions must hold on the candle. With 5 conditions, 4 is enough (80%); with 6, 5 is enough (83%); with 3 or 4 conditions, all must hold. Rows marked ✗ below are conditions that did <b>not</b> hold when the pattern still fired.</div>

  <section class="panel">
    <div class="scroll"><table id="summary">
      <thead><tr><th>Pattern</th><th>Type</th><th class="num">Weight</th><th class="num">Hits</th><th class="num">1m / 3m / 5m</th><th class="num">Stocks</th><th class="num">Fired with a failing condition</th><th>Most often failing</th></tr></thead>
      <tbody></tbody>
    </table></div>
  </section>

  <div class="controls">
    <label for="f-tf">Timeframe <select id="f-tf"><option value="">All</option><option>1min</option><option>3min</option><option>5min</option></select></label>
    <label for="f-sym">Stock <select id="f-sym"><option value="">All</option></select></label>
    <label for="f-fail"><input type="checkbox" id="f-fail"> Only hits with a failing condition</label>
  </div>
  <div id="patterns" style="display:flex;flex-direction:column;gap:10px"></div>
</div>

<script>
const DATA = __DATA__;
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const fmt = (v) => (v === null || v === undefined ? '—' : typeof v === 'number' ? (Math.abs(v) >= 1000 ? v.toLocaleString('en-IN', { maximumFractionDigits: 2 }) : +v.toFixed(4)) : v === true ? 'yes' : v === false ? 'no' : v);

const total = DATA.hits.length;
const withFail = DATA.hits.filter((h) => h.met < h.of).length;
const stocks = new Set(DATA.hits.map((h) => h.symbol));
$('#sub').textContent = `Every pattern hit on ${DATA.day} (IST), replayed read-only from the live candles with the live pattern definitions and strength rules. Each hit shows every condition: what it requires and the actual value on that candle.` + (DATA.note ? ' ' + DATA.note : '');
$('#facts').innerHTML = `<span><b>${total}</b> hits</span><span><b>${DATA.patterns.length}</b> patterns fired</span><span><b>${stocks.size}</b> stocks</span><span><b>${withFail}</b> (${Math.round(100 * withFail / Math.max(total, 1))}%) fired with a failing condition</span>`;
for (const s of [...stocks].sort()) $('#f-sym').insertAdjacentHTML('beforeend', `<option>${esc(s)}</option>`);

const maxFail = Math.max(1, ...DATA.patterns.map((p) => p.with_fail));
$('#summary tbody').innerHTML = DATA.patterns.map((p) => {
  const worst = Object.entries(p.fails).sort((a, b) => b[1] - a[1])[0];
  const worstTxt = worst ? `<span class="mono">${esc(p.conditions[+worst[0]].formula)}</span> <span style="color:var(--faint)">(${worst[1]}×)</span>` : '<span style="color:var(--faint)">—</span>';
  return `<tr class="pick" data-key="${esc(p.key)}" tabindex="0"><td>${esc(p.name)}</td><td><span class="type t-${p.type}">${p.type}</span></td><td class="num">${p.weight}</td><td class="num"><b>${p.hits}</b></td>
    <td class="num">${p.by_tf['1min'] ?? 0} / ${p.by_tf['3min'] ?? 0} / ${p.by_tf['5min'] ?? 0}</td><td class="num">${p.stocks}</td>
    <td class="num">${p.with_fail}<span class="bar" style="width:${Math.round(60 * p.with_fail / maxFail)}px"></span></td><td>${worstTxt}</td></tr>`;
}).join('');
document.querySelectorAll('tr.pick').forEach((tr) => {
  const go = () => { const d = document.getElementById('p-' + tr.dataset.key); d.open = true; d.scrollIntoView({ behavior: 'smooth', block: 'start' }) };
  tr.addEventListener('click', go); tr.addEventListener('keydown', (e) => { if (e.key === 'Enter') go() });
});

function render() {
  const tf = $('#f-tf').value, sym = $('#f-sym').value, onlyFail = $('#f-fail').checked;
  const openKeys = new Set([...document.querySelectorAll('details.pat[open]')].map((d) => d.dataset.key));
  $('#patterns').innerHTML = DATA.patterns.map((p) => {
    const rows = DATA.hits.filter((h) => h.pattern === p.key && (!tf || h.timeframe === tf) && (!sym || h.symbol === sym) && (!onlyFail || h.met < h.of))
      .sort((a, b) => a.symbol.localeCompare(b.symbol) || a.time.localeCompare(b.time) || a.timeframe.localeCompare(b.timeframe));
    const conds = p.conditions.map((c, i) => `<tr><td>${i + 1}</td><td class="mono">${esc(c.formula)}</td><td class="mono">${esc(c.required ?? 'true')}</td><td class="num">${p.fails[i] ?? 0} of ${p.hits} hits</td></tr>`).join('');
    const head = p.conditions.map((c, i) => `<th>${i + 1}. <span class="mono" style="text-transform:none;letter-spacing:0">${esc(c.formula)}</span></th>`).join('');
    const body = rows.map((h) => `<tr><td>${esc(h.symbol)}</td><td>${h.timeframe}</td><td class="num">${h.time}</td><td class="num">${h.met}/${h.of}</td><td class="num">${h.score}</td>${
      h.conditions.map((c) => `<td class="cell ${c.met ? 'cell-ok' : 'cell-no'}">${c.met ? '✓' : '✗'} ${esc(fmt(c.actual))}<small>${esc(c.required ?? '')}</small></td>`).join('')}</tr>`).join('');
    return `<details class="pat" id="p-${esc(p.key)}" data-key="${esc(p.key)}" ${openKeys.has(p.key) ? 'open' : ''}>
      <summary><h2>${esc(p.name)}</h2><span class="type t-${p.type}">${p.type}</span><span style="color:var(--muted)">weight ${p.weight} · ${p.hits} hits · ${p.with_fail} with a failing condition · showing ${rows.length}</span></summary>
      <div class="body">
        <div class="panel"><div class="scroll"><table class="conds"><thead><tr><th>#</th><th>Condition</th><th>Required</th><th class="num">Failed on</th></tr></thead><tbody>${conds}</tbody></table></div></div>
        <div class="panel"><div class="scroll">${rows.length ? `<table><thead><tr><th>Stock</th><th>TF</th><th class="num">Time</th><th class="num">Met</th><th class="num">Score</th>${head}</tr></thead><tbody>${body}</tbody></table>` : '<div class="empty">No hits match the filters.</div>'}</div></div>
      </div></details>`;
  }).join('');
}
['#f-tf', '#f-sym', '#f-fail'].forEach((s) => $(s).addEventListener('change', render));
render();
</script>
"""


def main():
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.write_text(build(json.loads(src.read_text(encoding="utf-8"))), encoding="utf-8")
    print("written", out)


if __name__ == "__main__":
    main()
