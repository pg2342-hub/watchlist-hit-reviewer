"""Build the GitHub Pages demo (docs/index.html): the agent working one customer at a time, plus the backtest.

python scripts/build_demo.py              # runs the agent on all 40 customers and both gates (replay, no API key)
python scripts/build_demo.py --no-run     # reuse reports/recorded and reports/baseline for the backtest section

The agent runs for real here: a signed webhook per customer, the Footprint client over HTTP against the
stand-in (watchlist/sandbox.py), the SDN screen, the recorded Claude review, the guardrails and the writes.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "evals"), str(ROOT / "scripts")]

import backtest_parts as bt  # noqa: E402
import yaml  # noqa: E402

from watchlist import agent  # noqa: E402
from watchlist.footprint import FootprintClient  # noqa: E402
from watchlist.sandbox import Sandbox  # noqa: E402
from watchlist.svix import sign, verify  # noqa: E402

REPO = bt.REPO
SECRET = "whsec_ZGVtby13ZWJob29rLXNlY3JldC0wMQ=="  # demo only


def run_agent(cases: list[dict]) -> list[dict]:
    from agent import REVIEW_TAPE, fingerprint

    from watchlist.recorded import replay_reviewer

    review = replay_reviewer(REVIEW_TAPE, fingerprint())
    rows = []
    with Sandbox(cases) as sb:
        for c in cases:
            i = c["input"]
            event = {
                "event_kind": "footprint.onboarding.completed",
                "fp_id": i["fp_id"],
                "onboarding_id": i["onboarding_id"],
                "status": "fail",
                "requires_manual_review": True,
                "is_live": False,
                "playbook_key": "pb_test_kyc",
            }
            body = json.dumps(event).encode()
            ts = int(time.time())
            verify(
                SECRET,
                {"svix-id": f"msg_{c['id']}", "svix-timestamp": str(ts), "svix-signature": sign(SECRET, f"msg_{c['id']}", ts, body)},
                body,
            )
            fp = FootprintClient("sk_test_standin", sb.url)
            r = agent.handle(event, fp, review)
            out = r["review"]
            hits = []
            for h in out["hits"]:
                want = c["expected"]["calls"][h["uid"]]
                e = h["list_entry"]
                hits.append(
                    {
                        "uid": h["uid"],
                        "name": e["name"],
                        "score": h["name_score"],
                        "programs": e["programs"],
                        "listed": {"dob": e["dob"], "gender": e["gender"], "nationality": e["nationality"], "pob": e["place_of_birth"]},
                        "proposed": h["proposed"],
                        "final": h["final"],
                        "guardrail": h["guardrail"],
                        "comparisons": h["comparisons"],
                        "deciding_fact": h["deciding_fact"],
                        "narrative": h["narrative"],
                        "next_step": h["next_step"],
                        "analyst": {"call": want["call"], "kind": want["kind"]},
                    }
                )
            trace = []
            for t in r["trace"]:
                body_ = t["body"]
                resp = None
                if t["path"].endswith("/risk_signals"):
                    resp = sb.store.users[i["fp_id"]]["risk_signals"]
                elif t["path"].endswith("/vault/decrypt"):
                    vault = sb.store.users[i["fp_id"]]["vault"]
                    resp = {f: vault.get(f) for f in body_["fields"]}
                trace.append({"method": t["method"], "path": t["path"], "status": t["status"], "body": body_, "resp": resp})
            meta = out["_meta"]
            rows.append(
                {
                    "id": c["id"],
                    "group": c["group"],
                    "title": c["title"],
                    "name": out["screened_name"],
                    "event": event,
                    "action": r["action"],
                    "hits": hits,
                    "trace": trace,
                    "brief": r["brief"],
                    "meta": {k: meta.get(k) for k in ("model", "latency_s", "cost_usd")},
                }
            )
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-run", action="store_true")
    args = ap.parse_args()
    if not args.no_run:
        bt.run_gates()
    rec, base = bt.load("reports/recorded"), bt.load("reports/baseline")
    bt.CHECKS = rec["checks"]
    case_list = yaml.safe_load((ROOT / "evals/cases.yml").read_text())["cases"]
    cases = {c["id"]: c for c in case_list}
    rows = run_agent(case_list)
    manifest = json.loads((ROOT / "data/ofac/manifest.json").read_text())
    outcomes = bt.hit_outcomes(rec, cases)
    n_hits = len(outcomes)
    false_clear = sum(1 for r in outcomes if bt.outcome(r) == "false_clear")
    blocked = sum(1 for r in outcomes if bt.outcome(r) == "blocked")
    posted = sum(1 for r in rows if r["action"] == "posted_pass")
    metas = [r["meta"] for r in rows if r["meta"].get("latency_s")]
    model = metas[0]["model"] if metas else ""
    mean_cost = sum(m["cost_usd"] for m in metas) / max(len(metas), 1)
    mean_lat = sum(m["latency_s"] for m in metas) / max(len(metas), 1)
    base_fc = sum(
        1
        for c in base["cases"]
        for r in c["runs"][:1]
        for h in r["output"]["hits"]
        if h["final"]["call"] == "clear" and cases[c["id"]]["expected"]["calls"][h["uid"]]["call"] == "escalate"
    )
    rec_date = time.strftime("%B %-d, %Y", time.gmtime((ROOT / "recordings" / "reviewer.jsonl").stat().st_mtime))
    start = next((r["id"] for r in rows if r["action"] == "posted_pass" and len(r["hits"]) == 1), rows[0]["id"])
    data = json.dumps({"rows": rows, "start": start, "model": model, "recorded": rec_date}, separators=(",", ":")).replace("</", "<\\/")

    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Watchlist Hit Reviewer</title>
<meta name="description" content="An agent that reviews OFAC watchlist hits from Footprint, clears only what code confirms, and sends the rest to an analyst. {posted} of 40 customers cleared with zero wrong clears in the backtest.">
<style>{bt.CSS}{CSS}</style></head><body><main>
<div class="head">
  <h1>Watchlist Hit Reviewer</h1>
  <p class="lead">An agent for KYC teams on Footprint. When an onboarding lands in manual review with an OFAC hit, it reads the risk signals, decrypts only the fields the review needs, has Claude compare each hit with the customer under a written procedure, and posts a pass only when code confirms the hit is not the customer. Everything else goes to an analyst with a case brief.</p>
</div>

<section class="desk" id="tool" aria-label="Review a customer">
  <div class="compose">
    <div class="lab" id="pick-l">Pick a customer</div>
    <div class="seg" role="radiogroup" aria-label="Filter customers by outcome">
      <button type="button" role="radio" aria-checked="true" data-f="all">All&nbsp; <span>40</span></button>
      <button type="button" role="radio" aria-checked="false" data-f="posted_pass">Pass posted&nbsp; <span>{posted}</span></button>
      <button type="button" role="radio" aria-checked="false" data-f="sent_to_analyst">To analyst&nbsp; <span>{40 - posted}</span></button>
    </div>
    <div class="plist" id="plist" role="listbox" aria-labelledby="pick-l"></div>
  </div>

  <article class="doc" id="doc" aria-live="polite"></article>

  <div class="rec" id="rec">
    <h2>What Footprint sent and what the agent read</h2>
    <p>The signed webhook, then the vault fields it decrypted with a logged reason. Empty fields come back as null.</p>
    <dl class="kv" id="kv"></dl>
  </div>
</section>

<div class="layout">
<div class="content">

<section id="how">
  <h2>How it works</h2>
  <p class="point">Footprint flags the name hit; the agent does the analyst's first pass. Claude proposes a call for every hit and writes the reasoning. Code then checks the one fact the call rests on before anything is written back, and the agent can only ever post a pass. A fail, or anything it cannot settle, stays with a person.</p>
  <div class="steps">
    <div class="step"><h3><span>1</span>Receive</h3><p><b>A signed webhook.</b> <code>footprint.onboarding.completed</code> with <code>requires_manual_review</code>, checked against its Svix signature. Anything else is ignored.</p></div>
    <div class="step"><h3><span>2</span>Read</h3><p><b>Risk signals, then a scoped decrypt.</b> It stops unless a signal is <code>watchlist_hit_ofac</code>, then decrypts name, DOB, nationality, address and the ID document's DOB and gender, with a reason Footprint logs. No SSN.</p></div>
    <div class="step"><h3><span>3</span>Review</h3><p><b>Every hit against the SDN record.</b> Candidate entries come from the OFAC SDN list. Claude compares DOB, gender, nationality and aliases under the written procedure and proposes clear or escalate.</p></div>
    <div class="step"><h3><span>4</span>Guard and write</h3><p><b>Code confirms, then writes.</b> A clear stands only if code confirms the deciding fact; a listed DOB equal to the customer's always escalates. A pass is posted only when every hit is cleared.</p></div>
  </div>
</section>

<section id="backtest">
  <h2>Backtest before it touches a queue</h2>
  <p class="point">The same agent ran on 40 past reviews, 3 times each, and every call was compared with the analyst's. It agreed with the analyst on hits it cleared, with <b>{false_clear}</b> wrong clears across {n_hits} hits on run 1, and posted a pass for <b>{posted} of 40</b> customers. A name-score cutoff, the obvious shortcut, cleared <b>{base_fc}</b> listed people who onboarded under another spelling.</p>
  <div class="verdict" aria-labelledby="verdict-h">
    <div class="vhead"><h2 id="verdict-h">Same 40 customers, same gate</h2><p>Gate: <b>zero</b> wrong clears or critical errors, <b>37 of 40</b> customers, every listed person, on all 3 runs</p></div>
    {bt.vrow("This agent", f"{escape(model)}, low effort, code guardrails on every clear", rec)}
    {bt.vrow("Name-score cutoff", "Clears every hit whose name score is under 0.97", base)}
    <div class="legend"><span><i class="pass"></i>Passed</span><span><i class="fail"></i>Failed</span><span><i class="crit"></i>Critical error</span><span>Each square is one customer over 3 runs. Select one to open it above.</span></div>
  </div>
  <h3 class="sub">Where every hit landed</h3>
  <p class="note" style="margin:0 0 12px">All {n_hits} hits on run 1, grouped by the analyst's call. A hatched block is a clear Claude proposed and code blocked ({blocked} on run 1).</p>
  {bt.flow_svg(outcomes)}
  <p class="note">Recorded live run, {escape(rec_date)}: {escape(model)}, one call per customer, mean ${mean_cost:.4f} and {mean_lat:.1f} s per customer. CI replays the recorded replies through the current code, so it needs no API key.</p>
</section>

<section id="limits">
  <h2>Limits</h2>
  <ul class="lim">
    <li>Footprint's sandbox needs company approval, so Footprint is not called here. The client sends the requests Footprint's public API reference describes, and a local stand-in answers them in the same shapes. The document field names (<code>document.passport.dob</code> and similar) should be confirmed on an account.</li>
    <li>Customers and the analysts' past calls are constructed; the list entries are real. A bank's own past reviews would replace the 40 cases before any pilot.</li>
    <li>Candidate list entries come from this repo's own screen of the SDN list, at the same threshold that raised the hit. Footprint's own match details, where an account exposes them, would replace that step.</li>
  </ul>
</section>
</div>

<aside class="side" aria-label="About this build">
  <nav aria-label="On this page"><a href="#tool">Review a customer</a><a href="#how">How it works</a><a href="#backtest">Backtest</a><a href="#limits">Limits</a></nav>
  <div><h3>Data</h3><p>List entries are real: the public OFAC SDN list from the U.S. Treasury Sanctions List Service, fetched {escape(manifest["fetched_utc"][:10])} by this repo's own workflow. Customers, their records, the analysts' past calls and the review procedure are constructed. Footprint requests follow its public API reference and are answered by a local stand-in.</p></div>
  <div><h3>Run it yourself</h3><p>Python 3.10+.</p><pre>pip install -e ".[dev]"
pytest -q
python scripts/build_demo.py</pre><p style="margin-top:8px">Against Footprint: set FOOTPRINT_API_KEY, FOOTPRINT_WEBHOOK_SECRET and ANTHROPIC_API_KEY, then <code>python -m watchlist.server</code>.</p></div>
  <div><a class="pill" href="https://github.com/pg2342-hub/{REPO}">Code on GitHub</a></div>
</aside>
</div>
</main>
<script id="data" type="application/json">{data}</script>
<script>{JS}</script></body></html>
"""
    for ch in ("—", "–"):
        if ch in html:
            raise SystemExit("an em or en dash slipped into the page")
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "index.html").write_text(html, encoding="utf-8")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "demo_chrome.py"), str(docs / "index.html"), "--repo", REPO], check=True)
    (docs / ".nojekyll").write_text("")
    print(f"wrote docs/index.html: {posted} passes posted, {40 - posted} to analyst")


CSS = """
.head h1{margin:24px 0 12px}
.head .lead{max-width:56em;margin:0}
.desk{display:grid;grid-template-columns:minmax(0,4fr) minmax(0,7fr);grid-template-areas:"in doc" "rec doc";grid-template-rows:auto 1fr;gap:20px 24px;align-items:start;margin-top:28px;scroll-margin-top:16px}
.compose{grid-area:in;border:1px solid var(--rule);border-radius:16px;background:#fbfaf6;padding:18px 20px}
.lab{font:500 15px/1.3 var(--sans);margin:0 0 10px}
.compose .seg{margin:0 0 12px}
.compose .seg button{padding:0 12px;font-size:13.5px}
.plist{display:grid;gap:4px;max-height:430px;overflow:auto;margin:0 -6px;padding:0 6px}
.pi{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:2px 10px;align-items:center;text-align:left;width:100%;padding:8px 10px;border:1px solid transparent;border-radius:10px;background:transparent;font:inherit;color:var(--ink);cursor:pointer}
.pi:hover{background:#fff;border-color:var(--rule)}
.pi[aria-selected=true]{background:#fff;border-color:var(--ink)}
.pi b{font-weight:500;font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pi small{grid-column:1;font-size:12.5px;color:var(--ink-2)}
.pi .dot{grid-row:1/3;grid-column:2;width:10px;height:10px;border-radius:3px}
.dot.posted_pass{background:var(--green)}
.dot.sent_to_analyst{background:var(--paper);border:1.5px solid var(--ink)}
.rec{grid-area:rec;border:1px solid var(--rule);border-radius:16px;padding:18px 20px;min-width:0}
.rec h2,.doc h2,.doc h3{all:unset;display:block}
.rec h2{font:500 15px/1.3 var(--sans)}
.rec p{margin:4px 0 12px;font-size:13.5px;color:var(--ink-2)}
.kv{display:grid;grid-template-columns:auto minmax(0,1fr);gap:3px 14px;margin:0;font:12px/1.55 var(--mono)}
.kv dt{color:var(--ink-2)}
.kv dd{margin:0;overflow-wrap:anywhere}
.kv .nul{color:var(--ink-2);opacity:.65}
.kv .gap{grid-column:1/-1;height:8px;border-bottom:1px solid var(--rule-soft);margin-bottom:6px}
.doc{grid-area:doc;border:1px solid var(--rule);border-radius:16px;background:#fff;box-shadow:0 1px 0 rgb(27 26 23/.04),0 24px 48px -32px rgb(27 26 23/.35);min-width:0;overflow:hidden}
.dhead{display:flex;justify-content:space-between;align-items:flex-start;gap:12px 20px;padding:22px 26px 16px;border-bottom:1px solid var(--rule)}
.dhead>div{min-width:0}
.doc h2{font:400 28px/1.15 var(--serif)}
.dsub{font:12.5px/1.5 var(--mono);color:var(--ink-2);margin-top:6px;overflow-wrap:anywhere}
.status{display:inline-flex;align-items:center;min-height:30px;padding:0 14px;border-radius:999px;font:500 12.5px/1 var(--mono);letter-spacing:.04em;border:1px solid var(--ink);white-space:nowrap}
.s-posted_pass{background:var(--green-wash);border-color:var(--green-wash);color:var(--green)}
.s-sent_to_analyst{background:var(--ink);color:var(--paper)}
.meta{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px 20px;margin:0;padding:14px 26px;border-bottom:1px solid var(--rule);background:#fbfaf6}
.meta dt{font-size:12.5px;color:var(--ink-2)}
.meta dd{margin:2px 0 0;font-size:14.5px;overflow-wrap:anywhere}
.dpad{padding:6px 26px 22px}
.hit{margin-top:18px;padding-bottom:16px;border-bottom:1px solid var(--rule)}
.hit:last-child{border-bottom:0;padding-bottom:0}
.hh{display:flex;justify-content:space-between;align-items:baseline;gap:8px 14px;flex-wrap:wrap}
.hh h3{font:500 15px/1.35 var(--sans)}
.hh .code{font:12px/1.4 var(--mono);color:var(--ink-2)}
.call{display:inline-block;font:500 11.5px/1.7 var(--mono);padding:0 9px;border-radius:999px;border:1px solid var(--ink);white-space:nowrap}
.call.clear{background:var(--green-wash);border-color:var(--green-wash);color:var(--green)}
.call.escalate{background:var(--ink);color:var(--paper)}
.cmp{width:100%;margin:10px 0 0;font-size:13.5px;border-collapse:collapse}
.cmp th{font:500 12px/1.3 var(--sans);color:var(--ink-2);text-align:left;padding:4px 8px 4px 0;border-bottom:1px solid var(--rule)}
.cmp td{padding:6px 8px 6px 0;border-bottom:1px solid var(--rule-soft);vertical-align:top;overflow-wrap:anywhere}
.cmp td.r{font:12px/1.5 var(--mono);white-space:nowrap}
.cmp td.r.mismatch{color:var(--green)}
.cmp td.r.match{font-weight:600}
.nar{margin:10px 0 0;font-size:14px}
.nar b{font-weight:500}
.gr{margin:10px 0 0;border:1.5px dashed var(--ink);border-radius:10px;padding:8px 12px;font-size:13.5px}
.an{margin:8px 0 0;font-size:12.5px;color:var(--ink-2)}
.calls{border-top:1px solid var(--rule);background:#fbfaf6;padding:14px 26px 16px}
.calls h3{font:500 14px/1.35 var(--sans);margin-bottom:6px}
.calls ol{list-style:none;margin:0;padding:0;display:grid;gap:6px}
.calls li{font:12.5px/1.5 var(--mono);margin:0}
.calls .m{display:inline-block;min-width:3.6em;font-weight:600}
.calls .st{color:var(--green);margin-left:6px}
.calls details{margin-top:2px}
.calls summary{cursor:pointer;color:var(--ink-2);font:12px var(--sans)}
.calls pre{font-size:11.5px;margin:6px 0 0;max-height:240px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere}
.brief{margin:12px 0 0}
.brief pre{font-size:12px;white-space:pre-wrap;overflow-wrap:anywhere;margin:6px 0 0}
.prov{display:flex;gap:8px;align-items:baseline;padding:12px 26px;border-top:1px solid var(--rule);font-size:12.5px;color:var(--ink-2);margin:0}
.prov i{flex:none;width:7px;height:7px;border-radius:50%;background:var(--green);transform:translateY(-1px)}
.content section{scroll-margin-top:16px}
.steps{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-top:18px}
.step{border:1px solid var(--rule);border-radius:16px;background:#fbfaf6;padding:18px 20px;min-width:0}
.step h3{margin:0 0 6px;font:400 21px/1.25 var(--serif);display:flex;gap:10px;align-items:baseline}
.step h3 span{font:400 15px/1 var(--serif);color:var(--green);min-width:12px}
.step p{margin:0;font-size:14.5px;color:var(--ink-2)}
.step p b{color:var(--ink)}
#backtest .verdict{margin-top:20px}
h3.sub{font:400 21px/1.25 var(--serif);margin:28px 0 6px}
ul.lim{margin:0;padding-left:20px;max-width:46em}
ul.lim li{margin-bottom:10px}
.strip{grid-template-columns:repeat(20,minmax(0,1fr))}
@media (max-width:860px){.desk{grid-template-columns:minmax(0,1fr);grid-template-areas:"in" "doc" "rec"}.plist{max-height:260px}}
@media (max-width:640px){
 .dhead{padding:18px 18px 14px}.doc h2{font-size:24px}
 .meta{grid-template-columns:repeat(2,minmax(0,1fr));padding:12px 18px}
 .dpad{padding:4px 18px 18px}.calls,.prov{padding-left:18px;padding-right:18px}
 .steps{grid-template-columns:minmax(0,1fr)}
 .cmp th:nth-child(4),.cmp td:nth-child(4){display:none}
 .strip{grid-template-columns:repeat(10,minmax(0,1fr))}
}
"""

JS = r"""
const D=JSON.parse(document.getElementById('data').textContent);
const by=Object.fromEntries(D.rows.map(r=>[r.id,r]));
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const LABEL={posted_pass:'PASS POSTED',sent_to_analyst:'TO ANALYST'};
const KIND={likely_match:'likely match',need_info:'need more information',dob:'DOB rules it out',gender:'gender rules it out'};
let filter='all',cur=D.start;
function list(){
  const el=document.getElementById('plist');
  el.innerHTML=D.rows.filter(r=>filter==='all'||r.action===filter).map(r=>
    `<button type="button" class="pi" role="option" aria-selected="${r.id===cur}" data-id="${r.id}"><b>${esc(r.name)}</b><span class="dot ${r.action}" aria-hidden="true"></span><small>${r.hits.length} hit${r.hits.length>1?'s':''}, ${r.action==='posted_pass'?'pass posted':'sent to analyst'}</small></button>`).join('');
  el.querySelectorAll('.pi').forEach(b=>b.addEventListener('click',()=>show(b.dataset.id)));
}
function kv(r){
  const ev=Object.entries(r.event).map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(typeof v==='string'?v:JSON.stringify(v))}</dd>`).join('');
  const dec=r.trace.find(t=>t.path.endsWith('/vault/decrypt'));
  const f=dec?Object.entries(dec.resp).map(([k,v])=>`<dt>${esc(k)}</dt><dd${v==null?' class="nul"':''}>${v==null?'null':esc(v)}</dd>`).join(''):'';
  document.getElementById('kv').innerHTML=ev+'<div class="gap"></div>'+f;
}
function hit(h){
  const rows=(h.comparisons||[]).map(c=>`<tr><td>${esc(c.attribute)}</td><td>${esc(c.customer_value)}</td><td>${esc(c.list_value)}</td><td class="r ${esc(c.result)}">${esc(c.result)}</td></tr>`).join('');
  const prop=h.proposed.call!==h.final.call||h.proposed.kind!==h.final.kind;
  return `<div class="hit"><div class="hh"><h3>SDN ${esc(h.uid)} ${esc(h.name)} <span class="code">name score ${h.score.toFixed(2)}, ${esc(h.programs.join(', '))}</span></h3><span class="call ${h.final.call}">${h.final.call.toUpperCase()}: ${esc(KIND[h.final.kind])}</span></div>
  ${rows?`<table class="cmp"><thead><tr><th>Attribute</th><th>Customer</th><th>List entry</th><th>Result</th></tr></thead><tbody>${rows}</tbody></table>`:''}
  ${prop?`<div class="gr"><b>Code changed the call.</b> Claude proposed ${esc(h.proposed.call)} (${esc(KIND[h.proposed.kind]||h.proposed.kind)}): ${esc(h.guardrail||'')}</div>`:''}
  <p class="nar"><b>Claude's note.</b> ${esc(h.narrative)}</p>
  ${h.final.call==='escalate'&&h.next_step?`<p class="nar"><b>Next step.</b> ${esc(h.next_step)}</p>`:''}
  <p class="an">The analyst's past call on this hit: ${esc(h.analyst.call)} (${esc(KIND[h.analyst.kind])}).</p></div>`;
}
function show(id){
  cur=id;const r=by[id];
  document.querySelectorAll('.pi').forEach(b=>b.setAttribute('aria-selected',String(b.dataset.id===id)));
  const calls=r.trace.map(t=>{
    const body=t.body?`<details><summary>request</summary><pre>${esc(JSON.stringify(t.body,null,1))}</pre></details>`:'';
    const resp=t.resp?`<details><summary>response</summary><pre>${esc(JSON.stringify(t.resp,null,1))}</pre></details>`:'';
    return `<li><span class="m">${t.method}</span>${esc(t.path)}<span class="st">${t.status}</span>${body}${resp}</li>`}).join('');
  const dec=r.action==='posted_pass'?'pass posted':'none, review left open';
  document.getElementById('doc').innerHTML=`<div class="dhead"><div><h2>${esc(r.name)}</h2><div class="dsub">${esc(r.event.fp_id)} / ${esc(r.event.onboarding_id)}</div></div><span class="status s-${r.action}">${LABEL[r.action]}</span></div>
  <dl class="meta"><div><dt>OFAC hits</dt><dd>${r.hits.length}</dd></div><div><dt>Decision written</dt><dd>${dec}</dd></div><div><dt>Claude review</dt><dd>${r.meta.latency_s?r.meta.latency_s.toFixed(1)+' s, $'+r.meta.cost_usd.toFixed(4):'n/a'}</dd></div></dl>
  <div class="dpad">${r.hits.map(hit).join('')}
  ${r.brief?`<div class="brief"><details><summary>Case brief left for the analyst</summary><pre>${esc(r.brief)}</pre></details></div>`:''}</div>
  <div class="calls"><h3>Footprint API calls, in order</h3><ol>${calls}</ol></div>
  <p class="prov"><i aria-hidden="true"></i><span>Claude review recorded ${esc(D.recorded)} (${esc(D.model)}) and replayed through the current code. Footprint answered by the local stand-in.</span></p>`;
  kv(r);
}
document.querySelectorAll('.compose .seg button').forEach(b=>b.addEventListener('click',()=>{
  document.querySelectorAll('.compose .seg button').forEach(x=>x.setAttribute('aria-checked',String(x===b)));
  filter=b.dataset.f;list();
}));
document.querySelectorAll('.cell').forEach(c=>c.addEventListener('click',e=>{
  e.preventDefault();filter='all';document.querySelector('.compose .seg button').click();show(c.dataset.case);
  document.getElementById('tool').scrollIntoView({behavior:'smooth'});
}));
list();show(cur);
"""

if __name__ == "__main__":
    main()
