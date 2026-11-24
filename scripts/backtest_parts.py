"""Backtest pieces of the demo page: the verdict rows, the hit-outcome chart and their CSS.

scripts/build_demo.py imports these; running this file alone writes the older backtest-only page to docs/backtest.html.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "watchlist-hit-reviewer"
GROUPS = {
    "true_match": "Listed person",
    "need_info": "Cannot rule out",
    "multi_dob": "Several listed DOBs",
    "dob_clear": "Clear on DOB",
    "gender": "Clear on gender",
    "many_hits": "Common name",
}
KINDS = {
    "likely_match": "escalate: likely match",
    "need_info": "escalate: need more information",
    "dob": "clear: DOB",
    "gender": "clear: gender",
}


def run_gates() -> None:
    for cfg, out in (("evals/golive.yml", "reports/recorded"), ("evals/golive.baseline.yml", "reports/baseline")):
        subprocess.run(["golive", "run", cfg, "--out", out], cwd=ROOT, check=False, stdout=subprocess.DEVNULL)


def load(path: str) -> dict:
    return json.loads((ROOT / path / "results.json").read_text())


def state(case: dict) -> tuple[str, str]:
    """Worst outcome over the runs: critical, failed, or passed on every run."""
    crit_ids = {c["id"] for c in CHECKS if c["critical"]}
    st = "pass"
    for r in case["runs"]:
        if not r["passed"]:
            if set(r["failed_checks"]) & crit_ids or r["error"]:
                return "crit", "failed with a critical error"
            st = "fail"
    return st, ("passed on all 3 runs" if st == "pass" else "failed on at least one run")


def strip(res: dict, who: str) -> str:
    cells = []
    for c in res["cases"]:
        st, words = state(c)
        cells.append(f'<a class="cell {st}" href="#case-{c["id"]}" data-case="{c["id"]}" aria-label="{who} {c["id"]} {words}"></a>')
    return f'<div class="strip" role="group" aria-label="{who}: every customer">{"".join(cells)}</div>'


def badge(decision: str) -> str:
    cls = {"go": "go", "no-go": "nogo", "partial": "part"}[decision]
    return f'<span class="gate {cls}">{decision.upper()}</span>'


def vrow(name: str, sub: str, res: dict) -> str:
    worst = min(r["passed"] for r in res["summary"]["by_run"])
    crit = sum(r["critical"] for r in res["summary"]["by_run"])
    return f"""<div class="vrow">
  <div class="vwho"><b>{name}</b><span>{sub}</span></div>
  {strip(res, name)}
  <div class="vscore"><b>{worst}</b><span>of 40 passed (worst run)<br>{crit} critical over 3 runs</span></div>
  {badge(res["decision"])}
</div>"""


def hit_outcomes(res: dict, cases: dict) -> list[dict]:
    """Every hit on run 1: the analyst's call, the reviewer's proposal and the final call."""
    rows = []
    for c in res["cases"]:
        out = c["runs"][0]["output"]
        for h in out["hits"]:
            want = cases[c["id"]]["expected"]["calls"][h["uid"]]
            rows.append(
                {
                    "case": c["id"],
                    "uid": h["uid"],
                    "want": want,
                    "proposed": h["proposed"],
                    "final": h["final"],
                    "guardrail": h["guardrail"],
                }
            )
    return rows


def outcome(h: dict) -> str:
    if h["final"]["call"] == "clear" and h["want"]["call"] == "escalate":
        return "false_clear"
    if h["guardrail"] and h["proposed"]["call"] == "clear":
        return "blocked"
    if h["final"]["call"] == h["want"]["call"]:
        return "agreed"
    return "over"


def flow_svg(rows: list[dict]) -> str:
    """Signature visual: every hit, grouped by the analyst's call, split by what happened to it. Drawn to scale."""
    order = ["dob", "gender", "likely_match", "need_info"]
    labels = {
        "dob": "Analyst cleared: DOB",
        "gender": "Analyst cleared: gender",
        "likely_match": "Analyst escalated: likely match",
        "need_info": "Analyst escalated: need info",
    }
    parts = [
        ("agreed", "Same call", "var(--green)", ""),
        ("blocked", "Clear blocked by code", "url(#hatch)", ""),
        ("over", "Escalated instead", "var(--paper)", "var(--ink)"),
        ("false_clear", "Wrongly cleared", "var(--ink)", ""),
    ]
    unit, x0, bar_h, gap, w = 16, 230, 26, 14, 760
    total_max = max(sum(1 for r in rows if r["want"]["kind"] == k) for k in order)
    unit = min(unit, (w - x0 - 40) / max(total_max, 1))
    y = 10
    out = [
        f'<svg class="flow" viewBox="0 0 {w} {len(order) * (bar_h + gap) + 60}" role="img" aria-label="Every screening hit by the analyst call and what the reviewer did">',
        '<defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
        '<rect width="6" height="6" fill="var(--paper)"/><line x1="0" y1="0" x2="0" y2="6" stroke="var(--ink)" stroke-width="2"/></pattern></defs>',
    ]
    for k in order:
        sub = [r for r in rows if r["want"]["kind"] == k]
        out.append(f'<text x="0" y="{y + bar_h / 2 + 5}" class="fl">{labels[k]}</text>')
        x = x0
        for key, _, fill, stroke in parts:
            n = sum(1 for r in sub if outcome(r) == key)
            if n:
                s = f' stroke="{stroke}" stroke-width="1.5"' if stroke else ""
                out.append(
                    f'<rect x="{x:.1f}" y="{y}" width="{n * unit - 2:.1f}" height="{bar_h}" rx="4" fill="{fill}"{s}><title>{n} hit(s)</title></rect>'
                )
                x += n * unit
        out.append(f'<text x="{x + 8:.1f}" y="{y + bar_h / 2 + 5}" class="fn">{len(sub)}</text>')
        y += bar_h + gap
    lx = 0
    for _, label, fill, stroke in parts:
        s = f' stroke="{stroke}" stroke-width="1.5"' if stroke else ""
        out.append(
            f'<rect x="{lx}" y="{y + 6}" width="12" height="12" rx="3" fill="{fill}"{s}/><text x="{lx + 18}" y="{y + 16}" class="fk">{label}</text>'
        )
        lx += 30 + len(label) * 7.2
    out.append("</svg>")
    return "".join(out)


def customer_cell(inp: dict) -> str:
    v, d = inp["customer"]["vault"], inp["customer"]["document"]
    name = " ".join(x for x in (v["id.first_name"], v.get("id.middle_name"), v["id.last_name"]) if x)
    dob = v.get("id.dob")
    bits = [f"DOB {dob}" if dob else "no DOB in the vault"]
    if d.get("dob") and not dob:
        bits.append(f"ID document DOB {d['dob']}")
    if d.get("gender"):
        bits.append(f"{d['gender'].lower()} on the ID document")
    if not d:
        bits.append("no ID document")
    return f"<b>{escape(name)}</b><div class='ch'>{escape(', '.join(bits))}</div>"


def hit_list(case: dict, res_case: dict) -> str:
    out = res_case["runs"][0]["output"]
    items = []
    for h in out["hits"]:
        want = case["expected"]["calls"][h["uid"]]
        e = h["list_entry"]
        mark = "ok" if h["final"]["call"] == want["call"] else "bad"
        g = f"<div class='why'>Code: {escape(h['guardrail'])}</div>" if h["guardrail"] else ""
        detail = ""
        if h.get("narrative"):
            nxt = f"<p><b>Next step.</b> {escape(h['next_step'])}</p>" if h.get("next_step") else ""
            detail = f"<details><summary>Narrative</summary><p>{escape(h['narrative'])}</p>{nxt}</details>"
        items.append(
            f"<li><span class='n'>SDN {h['uid']}</span> {escape(e['name'])}"
            f"<div class='ch'>listed DOB {escape('; '.join(e['dob']) or 'none')}{', ' + escape(e['gender'].lower()) if e.get('gender') else ''}"
            f", name score {h['name_score']:.2f}</div>"
            f"<div>Analyst: {KINDS[want['kind']]}. <span class='v {mark}'>Reviewer: {KINDS[h['final']['kind']]}</span></div>{g}{detail}</li>"
        )
    return f"<ul class='hits'>{''.join(items)}</ul>"


def verdict_cell(res_case: dict) -> str:
    fails = {}
    for r in res_case["runs"]:
        for cid in r["failed_checks"]:
            fails.setdefault(cid, r["verdicts"][cid]["reason"])
        if r["error"]:
            fails.setdefault("error", r["error"])
    if not fails:
        return "<td class='v ok'>Pass</td>"
    crit = any(c["critical"] and c["id"] in fails for c in CHECKS)
    why = "".join(f"<div class='why'>{escape(k)}: {escape(v[:160])}</div>" for k, v in fails.items())
    return f"<td class='v bad{' crit' if crit else ''}'>Fail{' (critical)' if crit else ''}{why}</td>"


CHECKS: list[dict] = []


def main() -> None:
    global CHECKS
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-run", action="store_true")
    args = ap.parse_args()
    if not args.no_run:
        run_gates()
    rec, base = load("reports/recorded"), load("reports/baseline")
    CHECKS = rec["checks"]
    import yaml

    cases = {c["id"]: c for c in yaml.safe_load((ROOT / "evals/cases.yml").read_text())["cases"]}
    manifest = json.loads((ROOT / "data/ofac/manifest.json").read_text())
    rows = hit_outcomes(rec, cases)
    n_hits = len(rows)
    blocked = sum(1 for r in rows if outcome(r) == "blocked")
    false_clear = sum(1 for r in rows if outcome(r) == "false_clear")
    agreed = sum(1 for r in rows if outcome(r) == "agreed")
    auto = sum(1 for r in rows if r["final"]["call"] == "clear")
    base_fc = sum(
        1
        for c in base["cases"]
        for r in c["runs"][:1]
        for h in r["output"]["hits"]
        if h["final"]["call"] == "clear" and cases[c["id"]]["expected"]["calls"][h["uid"]]["call"] == "escalate"
    )
    meta = [r["output"]["_meta"] for c in rec["cases"] for r in c["runs"] if r["output"]["_meta"].get("latency_s")]
    model = meta[0].get("model", "") if meta else ""
    mean_cost = sum(m["cost_usd"] for m in meta) / max(len(meta), 1)
    mean_lat = sum(m["latency_s"] for m in meta) / max(len(meta), 1)
    judge_cost = rec["summary"]["total_judge_cost_usd"]
    gate_reasons = "".join(f"<li>{escape(x)}</li>" for x in rec["gate_result"]["reasons"]) or "<li>Every rule held on all 3 runs.</li>"

    seg = '<button type="button" role="radio" aria-checked="true" data-t="all">All&nbsp; <span>40</span></button>' + "".join(
        f'<button type="button" role="radio" aria-checked="false" data-t="{g}">{label}&nbsp; <span>{sum(1 for c in cases.values() if c["group"] == g)}</span></button>'
        for g, label in GROUPS.items()
    )
    base_by = {c["id"]: c for c in base["cases"]}
    trs = []
    for c in rec["cases"]:
        case = cases[c["id"]]
        trs.append(
            f'<tr id="case-{c["id"]}" data-tier="{case["group"]}"><td class="n">{c["id"]}</td>'
            f'<td><span class="tag">{GROUPS[case["group"]]}</span><div class="ch">{escape(case["title"])}</div></td>'
            f"<td class='tx'>{customer_cell(case['input'])}</td><td class='hx'>{hit_list(case, c)}</td>"
            f"{verdict_cell(c)}{verdict_cell(base_by[c['id']])}</tr>"
        )
    SEV = {True: '<span class="sev sev-critical">Critical</span>', False: '<span class="sev">Standard</span>'}
    check_rows = "".join(
        f"<tr><td class='n'>{escape(c['id'])}</td><td>{escape(c['text'])}</td><td>{SEV[c['critical']]}</td></tr>" for c in CHECKS
    )

    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Watchlist Backtest Gate</title>
<meta name="description" content="A backtest and go-live gate for an AI reviewer of OFAC sanctions name hits: Claude with code guardrails is {rec["decision"].upper()} on 40 customers screened against the real SDN list; a name-score cutoff is {base["decision"].upper()}.">
<style>{CSS}</style></head><body><main>
<div class="head">
<h1>Watchlist Backtest Gate</h1>
<p class="lead">A backtest for an AI reviewer of sanctions screening hits. Constructed customers are screened against the real OFAC SDN list, Claude reviews every hit under a written procedure, code confirms the deciding fact before any clear is posted to Footprint, and the gate compares each call with the analyst's past call.</p>
</div>

<section class="verdict" aria-labelledby="verdict-h">
  <div class="vhead"><h2 id="verdict-h">Same 40 customers, same gate</h2><p>Gate: <b>zero</b> wrong clears or critical errors, <b>37 of 40</b> customers, every listed person, on all 3 runs</p></div>
  {vrow("Claude reviewer", f"{escape(model)}, medium effort, code guardrails on every clear", rec)}
  {vrow("Name-score cutoff", "Clears every hit whose name score is under 0.97", base)}
  <div class="legend"><span><i class="pass"></i>Passed</span><span><i class="fail"></i>Failed</span><span><i class="crit"></i>Critical error</span><span>Each square is one customer over 3 runs. Select one to read it.</span></div>
</section>

<div class="layout">
<div class="content">

<h2 id="why">Why a backtest comes first</h2>
<p class="point">Most sanctions name hits are not the listed person, so a reviewer that agrees with analysts most of the time looks ready long before it is. The question a bank asks is narrower: on our own past cases, did it ever clear someone our analyst escalated? Here the Claude reviewer agreed with the analyst on <b>{agreed} of {n_hits}</b> hits on run 1, with <b>{false_clear}</b> wrong clears, and <b>{auto}</b> hits could post a clear without an analyst. The name-score cutoff cleared <b>{base_fc}</b> listed people who onboarded under another spelling.</p>
<p class="note">Recorded live run: {escape(model)}, one call per customer, mean ${mean_cost:.4f} and {mean_lat:.1f} s per customer; judge ${judge_cost:.2f} in total for all 3 runs. CI replays the recorded replies through the current code, so it needs no API key.</p>

<h2 id="hits">Where every hit landed</h2>
<p class="note" style="margin:0 0 16px">All {n_hits} hits on run 1, grouped by the analyst's call. A hatched block is a clear the reviewer proposed and code blocked because it could not confirm the deciding fact.</p>
{flow_svg(rows)}
<p class="note">Guardrail blocks on run 1: {blocked}. Code posts a clear only when the customer's DOB differs from every listed DOB, or both genders are on file and differ with no listed DOB matching; a listed full DOB equal to the customer's always escalates.</p>

<h2 id="every-case">Every customer</h2>
<div class="seg" role="radiogroup" aria-label="Filter by kind of customer">{seg}</div>
<div class="scroll"><table class="t" id="cases"><thead><tr><th>#</th><th>Kind</th><th>Customer record</th><th>Hits on the SDN list, run 1</th><th>Claude reviewer</th><th>Name cutoff</th></tr></thead><tbody>
{"".join(trs)}
</tbody></table></div>

<h2 id="gate">The gate</h2>
<ul class="note">{gate_reasons}</ul>
<div class="scroll"><table class="t"><thead><tr><th>Check</th><th>What it asks</th><th>Severity</th></tr></thead><tbody>{check_rows}</tbody></table></div>
</div>

<aside class="side" aria-label="About this build">
  <nav aria-label="On this page"><a href="#why">Why a backtest comes first</a><a href="#hits">Where every hit landed</a><a href="#every-case">Every customer</a><a href="#gate">The gate</a></nav>
  <div><h3>Data</h3><p>List entries are real: the public OFAC SDN list from the U.S. Treasury Sanctions List Service, fetched {escape(manifest["fetched_utc"][:10])} by this repo's own workflow. Customers, their records and the analysts' past calls are constructed, and so is the review procedure. Footprint request shapes follow its public API reference; Footprint is not called.</p></div>
  <div><h3>Run it yourself</h3><p>Python 3.10+.</p><pre>pip install -e ".[dev]"
pytest -q
golive run evals/golive.yml
golive run evals/golive.baseline.yml</pre></div>
  <div><a class="pill" href="https://github.com/pg2342-hub/{REPO}">Code on GitHub</a></div>
</aside>
</div>
</main>
<script>
const seg=[...document.querySelectorAll('.seg button')];
seg.forEach(b=>b.addEventListener('click',()=>{{
 seg.forEach(x=>x.setAttribute('aria-checked',String(x===b)));
 const t=b.dataset.t;document.querySelectorAll('#cases tbody tr').forEach(r=>r.hidden=!(t==='all'||r.dataset.tier===t));
}}));
document.querySelectorAll('.cell').forEach(c=>{{
 const id=c.dataset.case;
 const lit=on=>document.querySelectorAll(`.cell[data-case="${{id}}"]`).forEach(x=>x.classList.toggle('lit',on));
 c.addEventListener('mouseenter',()=>lit(true));c.addEventListener('mouseleave',()=>lit(false));
 c.addEventListener('click',()=>{{
  seg[0].click();
  const row=document.getElementById('case-'+id);
  document.querySelectorAll('#cases tr.lit').forEach(r=>r.classList.remove('lit'));
  row&&row.classList.add('lit');
 }});
}});
</script></body></html>
"""
    for ch in ("—", "–"):
        if ch in html:
            raise SystemExit("an em or en dash slipped into the page")
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "backtest.html").write_text(html, encoding="utf-8")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "demo_chrome.py"), str(docs / "backtest.html"), "--repo", REPO], check=True)
    (docs / ".nojekyll").write_text("")
    print("wrote docs/backtest.html")


CSS = """
/* Shared demo layout, copied from the approved pilots. Keep it; add page-specific rules below it. */

.head{padding:8px 0 0}
.head h1{margin:40px 0 14px}
.head .lead{max-width:38em}
/* verdict: the same 25 cases under the same gate, one square per case */
.verdict{margin:36px 0 0;border:1px solid var(--rule);border-radius:16px;background:#fbfaf6;overflow:hidden}
.vhead{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:8px 24px;padding:18px 24px;border-bottom:1px solid var(--rule)}
.vhead h2{all:unset;font:400 22px/1.2 var(--serif)}
.vhead p{margin:0;color:var(--ink-2);font-size:15px}
.vhead p b{color:var(--ink)}
.vrow,.arow{display:grid;grid-template-columns:minmax(0,15em) minmax(0,1fr) 7.5em 5.5em;align-items:center;gap:20px;padding:22px 24px}
.vrow+.vrow{border-top:1px solid var(--rule-soft)}
.vwho b,.awho b{display:block;font:400 20px/1.2 var(--serif)}
.vwho span,.awho span{display:block;color:var(--ink-2);font-size:14px;line-height:1.4;margin-top:4px}
.strip{display:grid;grid-template-columns:repeat(25,minmax(0,1fr));gap:4px}
.cell{display:block;aspect-ratio:1;border-radius:4px;border:1.5px solid transparent;transition:transform .2s var(--ease),box-shadow .2s}
.cell.pass{background:var(--green)}
.cell.fail{background:var(--paper);border-color:var(--ink)}
.cell.crit{background:var(--ink)}
.cell:hover,.cell:focus-visible,.cell.lit{transform:translateY(-3px);box-shadow:0 6px 12px -6px rgb(27 26 23/.5)}
.vscore b{font:400 44px/1 var(--serif)}
.vscore span{display:block;color:var(--ink-2);font-size:13px;line-height:1.35;margin-top:4px}
.gate{justify-self:end;display:inline-flex;align-items:center;justify-content:center;min-height:32px;padding:0 14px;border-radius:999px;font:500 13px/1 var(--mono);letter-spacing:.06em}
.gate.go{background:var(--green-wash);color:var(--green)}
.gate.nogo{background:var(--ink);color:var(--paper)}
.legend{display:flex;flex-wrap:wrap;gap:6px 20px;padding:14px 24px;border-top:1px solid var(--rule);color:var(--ink-2);font-size:13px}
.legend span{display:inline-flex;align-items:center;gap:8px}
.legend i{width:12px;height:12px;border-radius:3px;border:1.5px solid transparent}
.legend .pass{background:var(--green)}.legend .fail{border-color:var(--ink)}.legend .crit{background:var(--ink)}
/* page body: content and a side column */
.layout{display:grid;grid-template-columns:minmax(0,1fr) 270px;gap:56px;align-items:start;margin-top:8px}
.side{position:sticky;top:24px;margin-top:64px;display:grid;gap:22px;font-size:14px;color:var(--ink-2)}
.side h3{margin:0 0 6px;font:500 13px/1.3 var(--sans);color:var(--ink)}
.side p{margin:0}
.side nav a{display:block;padding:5px 0;color:var(--ink-2);text-decoration:none;border-bottom:1px solid var(--rule-soft)}
.side nav a:hover{color:var(--ink)}
.side pre{font-size:12px;margin:6px 0 0}
.point{font-size:17px;max-width:40em;margin:0}
.point b{font-weight:500}
.seg{display:flex;width:fit-content;max-width:100%;flex-wrap:wrap;gap:2px;padding:3px;border:1px solid var(--rule);border-radius:999px;margin:0 0 16px;background:#fbfaf6}
.seg button{min-height:34px;padding:0 14px;border:0;border-radius:999px;background:transparent;color:var(--ink-2);font:500 14px/1 var(--sans);cursor:pointer}
.seg button span{font-weight:400;opacity:.8}
.seg button[aria-checked=true]{background:var(--ink);color:var(--paper)}
.n{font-family:var(--mono);font-size:13px}
.tag{font:400 12px/1 var(--mono);border:1px solid var(--rule);border-radius:999px;padding:4px 8px;white-space:nowrap}
.tx{max-width:340px}.ch{font-size:13px;color:var(--ink-2);margin-top:4px}
.v{white-space:nowrap;font-weight:500}
.v.ok{color:var(--green)}
.v.bad{white-space:normal;color:var(--ink)}
.v.bad.crit::before{content:"";display:inline-block;width:8px;height:8px;border-radius:2px;background:var(--ink);margin-right:6px;vertical-align:1px}
.why{font-weight:400;font-size:13px;color:var(--ink-2);margin-top:4px;max-width:240px}
#cases tr.lit td{background:var(--green-wash)}
#cases tr{scroll-margin-top:24px}
.abl{border:1px solid var(--rule);border-radius:16px;background:#fbfaf6;overflow:hidden}
.arow{grid-template-columns:minmax(0,13em) minmax(0,1fr) 6.5em}
.arow+.arow{border-top:1px solid var(--rule-soft)}
.ascore{font:400 15px/1 var(--sans);color:var(--ink-2)}
.ascore b{font:400 30px/1 var(--serif);color:var(--ink)}
.ascore span{display:block;font-size:13px;margin-top:4px}
.note{color:var(--ink-2);font-size:15px;max-width:44em;margin-top:14px}
.rule{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.rule div{border:1px solid var(--rule);border-radius:16px;padding:20px 22px;background:#fbfaf6}
.rule div:last-child{background:var(--ink);color:var(--paper);border-color:var(--ink)}
.rule h3{margin:0 0 6px;font:400 22px/1.2 var(--serif)}
.rule p{margin:0 0 10px;font-size:15px;opacity:.85}
.rule ul{margin:0;font-size:15px}
.rule div:last-child li::marker{color:rgb(244 242 236/.6)}
.sev{font:500 12px/1 var(--mono);padding:4px 8px;border-radius:999px;border:1px solid var(--rule)}
.sev-critical{background:var(--ink);color:var(--paper);border-color:var(--ink)}
.sev-low{color:var(--ink-2)}
@media (max-width:1000px){
 .layout{grid-template-columns:minmax(0,1fr);gap:0}
 .side{position:static;margin-top:48px;order:2}
 .vrow{grid-template-columns:minmax(0,1fr) auto;gap:14px 16px}
 .vrow .strip{grid-column:1/-1;grid-row:2}
 .vscore{grid-row:3}.vrow .gate{grid-row:3}
 .arow{grid-template-columns:minmax(0,1fr) auto;gap:12px 16px}
 .arow .strip{grid-column:1/-1;grid-row:2}
 .ascore{grid-column:2;grid-row:1}
 .rule{grid-template-columns:1fr}
}
@media (max-width:640px){.vrow,.arow{padding:18px}.vhead{padding:16px 18px}.strip{gap:3px}.tx{max-width:none}}
@media (prefers-reduced-motion:reduce){.cell{transition:none}}


/* page-specific */
.strip{grid-template-columns:repeat(20,minmax(0,1fr))}
.gate.part{background:repeating-linear-gradient(45deg,var(--paper) 0 4px,var(--rule) 4px 6px);color:var(--ink);border:1.5px solid var(--ink)}
.flow{width:100%;height:auto;display:block;margin:4px 0 8px}
.flow .fl{font:400 13px var(--sans);fill:var(--ink)}
.flow .fn,.flow .fk{font:400 12px var(--mono);fill:var(--ink-2);font-variant-numeric:tabular-nums}
.hits{list-style:none;margin:0;padding:0;display:grid;gap:10px;font-size:14px}
.hits li{border-bottom:1px solid var(--rule-soft);padding-bottom:8px}
.hits li:last-child{border-bottom:0}
.hx{min-width:300px}
details summary{cursor:pointer;color:var(--ink-2);font-size:13px;margin-top:4px}
details p{font-size:14px;margin:6px 0 0}
@media (max-width:640px){.strip{grid-template-columns:repeat(10,minmax(0,1fr))}.hx{min-width:220px}}
"""


if __name__ == "__main__":
    main()
