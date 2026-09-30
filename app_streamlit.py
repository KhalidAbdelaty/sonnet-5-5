"""Streamlit front end for the Rivermark guarded reconciliation agent.

    streamlit run app_streamlit.py

"Reconcile the month" starts a live run against the Claude Sonnet 5.5 API and is
billed to the key in .env. "Show the latest recorded run" opens the newest
runs/run_*.json with no API call (add ?run=<run_id> to the URL to open a specific run).
"""
from __future__ import annotations

import base64
import html
import json
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from rivermark.agent import (BETAS, ESCALATED_EFFORT, MAX_CALLS, MAX_SECONDS, RAW, START_EFFORT, STOP_USD,
                             run_reconciliation)
from rivermark.cost import (MODEL, PRICE_CACHE_READ, PRICE_CACHE_WRITE_1H, PRICE_CACHE_WRITE_5M, PRICE_INPUT,
                            PRICE_OUTPUT)

load_dotenv()
HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"
RUNS = HERE / "runs"
DATACAMP_URL = "https://www.datacamp.com/blog/claude-sonnet-5-5"

FEATURES = [
    (":material/psychology:", "Adaptive thinking and effort",
     "Thinking stays adaptive. The run starts at medium, the level Anthropic suggests for well-specified "
     "multistep tool work."),
    (":material/add_circle:", "Mid-conversation tool changes",
     "create_adjustment is declared with defer_loading and surfaced by tool_addition only after the plan "
     "gate approves."),
    (":material/tune:", "Per-message effort",
     "A failed independent check appends an effort-only system message that moves the next turns to high."),
    (":material/cached:", "Prompt-cache preservation",
     "tools and system never change. Unlocks and effort changes are appended, so cache reads keep growing."),
    (":material/gavel:", "Deterministic verification gate",
     "Python checks provenance, amounts, write scope, and bank deposits. Claude's confidence is not a pass."),
    (":material/receipt_long:", "Adjustment artifacts, not edits",
     "Corrections go to a per-run ledger file. Raw exports are hashed before and after and never edited."),
]

st.set_page_config(page_title="Rivermark Reconciliation Agent", page_icon=":material/account_balance:",
                   layout="wide")
st.logo(str(ASSETS / "datacamp-logo.png"), link=DATACAMP_URL, size="large")

st.markdown(
    """
    <style>
      .block-container { padding-top: 2rem; max-width: 1180px; }
      .brand { display: flex; align-items: center; gap: .75rem; flex-wrap: wrap; margin-bottom: .25rem; }
      .brand img { height: 26px; }
      .brand .model { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: .8rem;
        background: #F4F3EE; color: #191919; border: 1px solid #E5E2D9; border-radius: 999px;
        padding: .15rem .65rem; }
      .pill { display: inline-block; border-radius: 999px; padding: .2rem .8rem; font-weight: 700;
        font-size: .9rem; margin-right: .4rem; }
      .pill-ok { background: #E3F1E6; color: #1E5E2E; }
      .pill-warn { background: #FFF1D6; color: #7A4B00; }
      .pill-bad { background: #FBE3E0; color: #8A1F12; }
      .pill-neutral { background: #F4F3EE; color: #191919; border: 1px solid #E5E2D9; }
      .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: .6rem;
        margin: .6rem 0 1rem; }
      .stat { background: #F4F3EE; color: #191919; border: 1px solid #E5E2D9; border-radius: .6rem;
        padding: .55rem .8rem; }
      .stat .label { font-size: .78rem; color: #55534E; }
      .stat .value { font-size: 1.25rem; font-weight: 700; font-family: ui-monospace, Menlo, Consolas, monospace; }
    </style>
    """,
    unsafe_allow_html=True,
)


def b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


st.markdown(
    f"""
    <div class="brand">
      <img src="data:image/png;base64,{b64(ASSETS / 'claude-logo.png')}" alt="Claude logo"/>
      <span class="model">{MODEL}</span>
    </div>
    """,
    unsafe_allow_html=True,
)
st.title("Rivermark Reconciliation Agent")
st.markdown(
    "Claude Sonnet 5.5 investigates why September's internal net revenue and the processor payout disagree. "
    "It starts read-only at medium effort. Python grants write access only after its plan passes, and raises "
    "effort only if an independent check still fails."
)

# ----------------------------------------------------------------- sidebar
with st.sidebar:
    st.subheader("Run settings")
    st.markdown(
        f"- Model: `{MODEL}`\n"
        f"- Starting effort: `{START_EFFORT}`\n"
        f"- Escalation: per-message `{ESCALATED_EFFORT}` after a failed check\n"
        f"- Beta headers:\n" + "".join(f"  `{b}`\n" for b in BETAS) +
        f"- App limits: {MAX_CALLS} API calls, {MAX_SECONDS // 60} min, no new request after ${STOP_USD:.2f}"
    )
    st.subheader("Price per million tokens")
    st.markdown(
        f"- Input: ${PRICE_INPUT:.2f}\n"
        f"- Cache write (5 min): ${PRICE_CACHE_WRITE_5M:.2f}\n"
        f"- Cache write (1 hour): ${PRICE_CACHE_WRITE_1H:.2f}\n"
        f"- Cache read: ${PRICE_CACHE_READ:.2f}\n"
        f"- Output: ${PRICE_OUTPUT:.2f}"
    )
    if st.session_state.get("spend"):
        st.metric("Spent this session", f"${st.session_state.spend:.4f}")
    if (ASSETS / "rivermark_architecture.png").exists():
        st.image(str(ASSETS / "rivermark_architecture.png"), caption="Trust boundary", width="stretch")

# ---------------------------------------------------------------- features
st.subheader("What this run uses")
cols = st.columns(2)
for i, (icon, name, desc) in enumerate(FEATURES):
    with cols[i % 2], st.container(border=True):
        st.markdown(f"{icon} **{name}**")
        st.caption(desc)

# --------------------------------------------------------------- controls
st.write("")
left, right = st.columns([1, 1])
with left:
    live = st.button("Reconcile the month", type="primary", icon=":material/search:",
                     help="Starts a live run that calls the Claude API.", width="stretch")
with right:
    recorded = st.button("Show the latest recorded run", icon=":material/history:",
                         help="Opens the newest file in runs/ without calling the API.", width="stretch")
st.caption("A live run calls the Claude API and is billed to the key in your .env file.")


# ---------------------------------------------------------------- helpers
def money(cents: int | None) -> str:
    return "n/a" if cents is None else f"{'-' if cents < 0 else ''}${abs(cents) / 100:,.2f}"


def stats_html(items: list[tuple[str, str]]) -> str:
    cells = "".join(f"<div class='stat'><div class='label'>{html.escape(label)}</div>"
                    f"<div class='value'>{html.escape(value)}</div></div>" for label, value in items)
    return f"<div class='stats'>{cells}</div>"


def pill(text: str, kind: str) -> str:
    return f"<span class='pill pill-{kind}'>{html.escape(text)}</span>"


def code(value, limit=140) -> str:
    text = value if isinstance(value, str) else json.dumps(value)
    text = " ".join(text.split()).replace("`", "'")
    return text if len(text) <= limit else text[: limit - 3] + "..."


class LogState:
    """Counters shown in the stats strip while events stream in."""

    def __init__(self):
        self.t, self.phase, self.calls, self.tools, self.cost = 0.0, "investigate", 0, 0, 0.0
        self.authority = "Read-only"

    def strip(self) -> str:
        return stats_html([("Elapsed", f"{self.t:.1f} s"), ("Phase", self.phase), ("Turns", str(self.calls)),
                           ("Tool calls", str(self.tools)), ("Authority", self.authority),
                           ("Running cost", f"${self.cost:.4f}")])


def log_event(log, e: dict, s: LogState) -> None:
    """Render one agent event as a log line. Every badge also carries a text label."""
    kind, stamp = e["type"], f"`{e['t']:>5.1f}s`"
    s.t = e["t"]
    if kind == "phase":
        s.phase = e["phase"]
        log.markdown(f"{stamp} :material/flag: **{e['phase']}** at `{e['effort']}` effort")
    elif kind == "usage":
        s.calls, s.cost = e["call"], e["running_total"]
        cache = ":orange-badge[cache] " if e["cache_read_tokens"] else ""
        log.markdown(f"{stamp} {cache}:material/memory: call {e['call']} · `{e['effort']}` · "
                     f"{e['cache_read_tokens']:,} cache read · {e['cache_write_tokens']:,} written · "
                     f"{e['output_tokens']:,} out ({e['thinking_tokens']:,} thinking) · {e['latency_s']}s")
    elif kind == "progress":
        log.markdown(f"{stamp} :material/chat: {code(e['text'], 320)}")
    elif kind == "tool_call":
        s.tools += 1
        badge = {"read": ":blue-badge[read]", "direct": ":blue-badge[direct]", "write": ":orange-badge[write]"}
        log.markdown(f"{stamp} {badge[e['path']]} :material/build: `{e['name']}` `{code(e['input'])}`")
    elif kind == "tool_result":
        log.markdown(f"{stamp} :red-badge[rejected] `{e['name']}`: {code(e['error'], 220)}")
    elif kind == "gate":
        if e["approved"]:
            log.markdown(f"{stamp} :green-badge[approved] :material/fact_check: plan gate: {e['plan_id']}, "
                         f"{len(e['items'])} new adjustment(s)")
        else:
            log.markdown(f"{stamp} :red-badge[rejected] :material/fact_check: plan gate: "
                         f"{code('; '.join(e['problems']), 260)}")
    elif kind == "unlock":
        if e["tool"]:
            s.authority = f"Write: {e['plan_id']}"
            log.markdown(f"{stamp} :material/lock_open: mid-conversation system message + "
                         f"`tool_addition({e['tool']})`: {code(e['text'], 200)}")
        else:
            s.authority = f"Write: {e['plan_id']}"
            log.markdown(f"{stamp} :material/lock_open: system message: {code(e['text'], 200)}")
    elif kind == "adjustment":
        a = e["entry"]
        log.markdown(f"{stamp} :orange-badge[write] :material/receipt_long: {a['adjustment_id']} · "
                     f"{money(a['amount_cents'])} · {a['rule']} · {', '.join(a['evidence_txn_ids'])}")
    elif kind == "verify":
        r = e["result"]
        if r["passed"]:
            log.markdown(f"{stamp} :green-badge[approved] :material/verified: independent check "
                         f"{pill('Reconciled', 'ok')}", unsafe_allow_html=True)
        else:
            failed = ", ".join(k for k, v in r["checks"].items() if not v)
            log.markdown(f"{stamp} :red-badge[rejected] :material/rule: independent check "
                         f"{pill('Residual ' + money(r['residual_cents']), 'warn')} failed: {failed}",
                         unsafe_allow_html=True)
    elif kind == "effort":
        log.markdown(f"{stamp} :violet-badge[effort] :material/tune: `{e['frm']}` to `{e['to']}` "
                     f"({e['reason']}), same conversation")
    elif kind == "tool_change":
        s.authority = "Report only"
        log.markdown(f"{stamp} :material/swap_horiz: `tool_removal({', '.join(e['removed'])})` · "
                     f"`tool_addition({', '.join(e['added'])})`")
    elif kind == "report":
        s.authority = "Closed"
        log.markdown(f"{stamp} :material/description: report submitted")
    elif kind == "nudge":
        log.markdown(f"{stamp} :material/info: harness: {code(e['text'], 200)}")


def effort_path(record: dict) -> str:
    levels = [x["effort"] for x in record["effort_timeline"]]
    return " → ".join(levels)


def control_for(record: dict) -> dict | None:
    for path in sorted((RUNS / "control").glob("run_*.json"), reverse=True):
        rec = json.loads(path.read_text(encoding="utf-8"))
        if rec.get("parent_run_id") == record["run_id"]:
            return rec
    return None


def render_run(record: dict, show_log: bool) -> None:
    if show_log:
        with st.status(f"Recorded run {record['run_id']} ({record['stop']})", state="complete", expanded=False):
            log, s = st.container(height=420, border=True), LogState()
            for e in record["events"]:
                log_event(log, e, s)
    st.divider()
    ver, report, c = record["verifications"], record.get("report") or {}, record["cost"]
    final = ver[-1] if ver else None
    approved = sum(1 for g in record["gate_log"] if g["approved"])
    rejected = len(record["gate_log"]) - approved
    check = pill("Check: Reconciled", "ok") if record["passed"] else pill(
        f"Check: Residual {money(final['residual_cents'])}" if final else "Check: not run", "warn")
    st.markdown(
        pill(f"Phase: {record['final_phase']}", "neutral") + check +
        pill(f"Effort: {effort_path(record)}", "neutral") +
        pill(f"Plans: {approved} approved", "ok") + (pill(f"{rejected} rejected", "bad") if rejected else ""),
        unsafe_allow_html=True)
    st.markdown(stats_html([
        ("Wall-clock time", f"{record['elapsed_s']:.1f} s"),
        ("API calls", str(len(record["calls"]))),
        ("Tool calls", str(sum(1 for e in record["events"] if e["type"] == "tool_call"))),
        ("Adjustments", str(len(record["adjustments"]))),
        ("Final residual", money(final["residual_cents"]) if final else "n/a"),
        ("Cost", f"${c['cost']:.4f}"),
    ]), unsafe_allow_html=True)

    tabs = st.tabs([":material/description: Report", ":material/edit_note: Adjustments", ":material/tune: Effort",
                    ":material/payments: Cost", ":material/insights: Evidence", ":material/data_object: JSON"])
    with tabs[0]:
        first_fail = next((v for v in ver if not v["passed"]), None)
        blocks = [(":material/search:", "Diagnosis", report.get("diagnosis", "No report submitted.")),
                  (":material/rule:", "Residual",
                   (f"The first independent check left {money(first_fail['residual_cents'])} unexplained. "
                    if first_fail else "No residual after the first check. ") +
                   (f"Final residual: {money(final['residual_cents'])}." if final else "")),
                  (":material/verified:", "Final state", report.get("final_state", ""))]
        for icon, title, text in blocks:
            with st.container(border=True):
                st.markdown(f"{icon} **{title}**")
                st.write(text)
        left, right = st.columns(2)
        with left:
            st.markdown("**Contributing findings**")
            for f in report.get("findings", []):
                st.markdown(f"- {f['finding']} ({f['adjustment_id']}; evidence: {', '.join(f['evidence'])})")
        with right:
            st.markdown("**Remaining uncertainty**")
            st.write(report.get("remaining_uncertainty", ""))
            for item in report.get("follow_ups", []):
                st.markdown(f"- {item}")
    with tabs[1]:
        for plan in record["plans"]:
            with st.container(border=True):
                st.markdown(f":material/fact_check: **{plan['plan_id']}** ({plan['stage']})")
                st.caption(plan["diagnosis"])
                st.dataframe(pd.DataFrame([{"Rule": a["rule"], "Evidence lines": ", ".join(a["evidence_txn_ids"]),
                                            "Amount": money(a["amount_cents"]), "Finding": a["finding"]}
                                           for a in plan["adjustments"]]), hide_index=True, width="stretch")
        st.markdown("**Adjustment artifacts** (`outputs/<run>/adjustments.jsonl`)")
        if record["adjustments"]:
            st.dataframe(pd.DataFrame([{"ID": a["adjustment_id"], "Rule": a["rule"], "Amount": money(a["amount_cents"]),
                                        "Evidence lines": ", ".join(a["evidence_txn_ids"]), "Memo": a["memo"]}
                                       for a in record["adjustments"]]), hide_index=True, width="stretch")
        st.markdown("**Independent reconciliation** (harness)")
        st.dataframe(pd.DataFrame([{"After call": v["after_call"],
                                    "Expected after adjustments": money(v["expected_after_adjustments_cents"]),
                                    "Bank deposits": money(v["processor_deposits_cents"]),
                                    "Residual": money(v["residual_cents"]),
                                    "Result": "Reconciled" if v["passed"] else "Residual"} for v in ver]),
                     hide_index=True, width="stretch")
    with tabs[2]:
        calls = pd.DataFrame(record["calls"])
        left, right = st.columns(2)
        for col, level, note in ((left, START_EFFORT, "Production path: investigation, first plan, remediation, report."),
                                 (right, ESCALATED_EFFORT, "Only after the independent check failed.")):
            with col, st.container(border=True):
                st.markdown(f":material/tune: **{level.capitalize()} effort**")
                st.caption(note)
                part = calls[calls["effort"] == level] if not calls.empty else calls
                if part.empty:
                    st.write("Not used in this run.")
                    continue
                st.markdown(stats_html([("Calls", str(len(part))), ("Output tokens", f"{part['output_tokens'].sum():,}"),
                                        ("Thinking tokens", f"{part['thinking_tokens'].sum():,}"),
                                        ("Cost", f"${part['cost'].sum():.4f}")]), unsafe_allow_html=True)
        change = next((e for e in record["events"] if e["type"] == "effort" and e["to"] == ESCALATED_EFFORT), None)
        if change:
            st.markdown("**What changed in the conversation**")
            st.code(json.dumps({"role": "system", "content": [], "output_config": {"effort": ESCALATED_EFFORT}}),
                    language="json")
            st.caption("An effort-only system message is appended before the harness's failure message. "
                       "tools, system, and top-level output_config stay the same.")
        ctrl = control_for(record)
        if ctrl:
            v = ctrl["verifications"][-1] if ctrl["verifications"] else {}
            st.markdown("**Matched control** (same conversation resumed from the failed check, effort left at medium)")
            st.dataframe(pd.DataFrame([{"Run": ctrl["run_id"], "Result": "Reconciled" if ctrl["passed"] else "Residual",
                                        "API calls": len(ctrl["calls"]),
                                        "Output tokens": sum(x["output_tokens"] for x in ctrl["calls"]),
                                        "Thinking tokens": sum(x["thinking_tokens"] for x in ctrl["calls"]),
                                        "Cost": round(ctrl["cost"]["cost"], 4),
                                        "Final residual": money(v.get("residual_cents"))}]),
                         hide_index=True, width="stretch")
    with tabs[3]:
        k = st.columns(4)
        k[0].metric("Uncached input", f"{c['input_tokens']:,}", f"${c['cost_input']:.4f}", delta_color="off")
        k[1].metric("Cache read", f"{c['cache_read_tokens']:,}", f"${c['cost_cache_read']:.4f}", delta_color="off")
        k[2].metric("Cache write", f"{c['cache_write_tokens']:,}", f"${c['cost_cache_write']:.4f}", delta_color="off")
        k[3].metric("Output", f"{c['output_tokens']:,}", f"${c['cost_output']:.4f}", delta_color="off")
        st.dataframe(pd.DataFrame([{"Phase": p, "API calls": v["api_calls"], "Output tokens": v["output_tokens"],
                                    "Thinking tokens": v["thinking_tokens"], "Cache read": v["cache_read_tokens"],
                                    "Cost": round(v["cost"], 4)} for p, v in c["by_phase"].items()]),
                     hide_index=True, width="stretch")
        st.dataframe(calls[["call", "phase", "effort", "stop_reason", "input_tokens", "cache_read_tokens",
                            "cache_write_tokens", "output_tokens", "thinking_tokens", "latency_s", "cost"]],
                     hide_index=True, width="stretch")
        st.caption("All figures come from the usage object on each API response.")
    with tabs[4]:
        with st.container(border=True):
            st.markdown(":material/visibility: **Model-visible evidence**")
            rows = record["retrieved_records"]
            st.caption(f"{len(rows)} records returned by read tools. The plan gate only accepts IDs from this set.")
            if rows:
                st.dataframe(pd.DataFrame(rows).fillna("").astype(str), hide_index=True, width="stretch", height=260)
            with st.expander("Close policy text", expanded=False):
                st.text((RAW / "finance_policy.txt").read_text(encoding="utf-8"))
        with st.container(border=True):
            st.markdown(":material/shield: **Harness-only checks**")
            st.caption("Never sent to Claude: the bank deposit total, raw-file hashes, and the write-scope rule.")
            if final:
                st.dataframe(pd.DataFrame([{"Check": k, "Result": "Pass" if ok else "Fail"}
                                           for k, ok in final["checks"].items()]), hide_index=True, width="stretch")
                st.markdown(f"- Bank deposits: {money(final['processor_deposits_cents'])}\n"
                            f"- Files created: {', '.join(final['files_created'])}")
    with tabs[5]:
        st.json({k: record[k] for k in ("run_id", "model", "stop", "passed", "report", "plans", "adjustments",
                                        "verifications", "effort_timeline", "cost")}, expanded=False)


def latest_run() -> dict | None:
    """Newest runs/run_*.json, or the one named by ?run=<run_id> in the URL."""
    wanted = RUNS / f"run_{st.query_params.get('run', '')}.json"
    paths = [wanted] if wanted.exists() else sorted(RUNS.glob("run_*.json"))
    return json.loads(paths[-1].read_text(encoding="utf-8")) if paths else None


if live:
    s = LogState()
    strip = st.empty()
    strip.markdown(s.strip(), unsafe_allow_html=True)
    record = None
    with st.status("Reconciling Rivermark's September close...", expanded=True) as status:
        log = st.container(height=420, border=True)
        for event in run_reconciliation():
            if event["type"] == "done":
                record = event["record"]
                s.t, s.cost = record["elapsed_s"], record["cost"]["cost"]
                status.update(label=f"Run finished ({record['stop']})", state="complete", expanded=False)
            else:
                log_event(log, event, s)
            strip.markdown(s.strip(), unsafe_allow_html=True)
    if record is not None:
        RUNS.mkdir(exist_ok=True)
        (RUNS / f"run_{record['run_id']}.json").write_text(json.dumps(record, indent=2, default=str),
                                                          encoding="utf-8")
        st.session_state.spend = st.session_state.get("spend", 0.0) + record["cost"]["cost"]
        st.session_state.record = record
        st.rerun()  # redraw with the session spend in the sidebar and the log in a collapsed status

if recorded:
    st.session_state.record = latest_run()
    if st.session_state.record is None:
        st.info("No recorded runs yet. Run `python run_experiment.py` or start a live run first.")

if st.session_state.get("record"):
    render_run(st.session_state.record, show_log=True)
