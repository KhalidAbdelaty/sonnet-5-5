"""Rivermark reconciliation agent: one append-only Messages API conversation.

run_reconciliation() is a generator. It yields events as the run progresses
and finishes with a "done" event that holds the full run record. The CLI
(run_experiment.py) and the Streamlit app consume the same events.
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic

from rivermark.cost import MODEL, call_cost
from rivermark.harness import Gate
from rivermark.tools import READ_TOOLS, TOOLS, ReadTools, dumps

ROOT = Path(__file__).resolve().parent.parent
RAW, BANK = ROOT / "data" / "raw", ROOT / "data" / "bank"
OUTPUTS = ROOT / "outputs"

BETAS = [
    "mid-conversation-output-config-2026-07-01",  # per-message effort
    "inline-tools-2026-09-15",                    # tool_addition / tool_removal
    "thinking-display-updates-2026-08-18",        # readable progress notes between tool calls
]
START_EFFORT, ESCALATED_EFFORT = "medium", "high"
MAX_TOKENS = 16_000
MAX_CALLS, MAX_SECONDS, STOP_USD = 40, 900, 3.00  # app limits, checked before each request

SYSTEM_PROMPT = """You are the reconciliation analyst for Rivermark Subscriptions, a small SaaS company that \
bills monthly plans (Starter $29, Team $79, Business $149) through a card processor. You are helping finance \
close September 2026.

How this engagement works:
- You start with read-only tools over the close data: orders, refunds, processor payout transactions, the \
close policy, and Rivermark's internal reconciliation check.
- Investigate before you conclude. Base every claim on records you retrieved in this conversation and cite \
their IDs (order_id, refund_id, txn_id, payout_id). Do not guess record IDs or amounts.
- When you have an evidence-backed explanation, call submit_plan. Each adjustment names one policy rule, the \
processor transaction lines it accounts for, and a signed amount in integer cents applied to expected payout. \
Proposing a plan writes nothing. The harness reviews it and decides whether to grant write access.
- Source exports are never edited. Corrections exist only as entries in the close adjustments ledger.
- The harness, not you, decides when the month is reconciled. It runs its own checks after you finish.
- Amounts in the data are integer cents. Show dollar figures with two decimals when you explain them.

Write short progress notes between tool calls so finance can follow along: what you found and what you are \
checking next."""

TASK = ("September's close does not reconcile: Rivermark's internal net revenue and the processor payout "
        "disagree. Investigate the discrepancy, explain its cause with evidence from the sources, and submit a "
        "remediation plan.")

UNLOCK_TEXT = ("The harness approved {plan_id}. Remediation authority is granted for the approved items only. "
               "Record each approved item with create_adjustment, using the same evidence_txn_ids, rule, and "
               "amount_cents as the plan. The tool appends to the close adjustments ledger and cannot touch "
               "source exports. When the approved items are recorded, reply with a short summary.")
REAPPROVE_TEXT = ("The harness approved {plan_id}. Record its new items with create_adjustment, then reply with "
                  "a short summary.")
REPORT_TEXT = ("Remediation is closed. create_adjustment has been withdrawn. submit_report is now available: "
               "call it once with the final report.")


def _cents(v: int) -> str:
    return f"${v / 100:,.2f}"


def serialize(messages: list[dict]) -> list[dict]:
    """Messages as plain JSON, content blocks included, so a run can be resumed exactly."""
    return [{**m, "content": [b.model_dump(exclude_none=True) if hasattr(b, "model_dump") else b
                              for b in m["content"]] if isinstance(m["content"], list) else m["content"]}
            for m in messages]


def run_reconciliation(client: anthropic.Anthropic | None = None, run_id: str | None = None,
                       escalate: bool = True, resume: dict | None = None):
    """escalate=False with resume=<fork> is the matched control: the same conversation, continued from
    its failed check without the effort change."""
    client = client or anthropic.Anthropic()
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    t0 = time.monotonic()
    gate = Gate(RAW, BANK, OUTPUTS / run_id)
    reads = ReadTools(RAW, gate)
    messages: list[dict] = [{"role": "user", "content": TASK}]

    stage, effort, phase = "investigate", START_EFFORT, "investigate"
    calls, events, verifications, gate_log, retrieved = [], [], [], [], {}
    effort_timeline = [{"call": 1, "effort": START_EFFORT, "reason": "starting level (per Anthropic guidance)"}]
    counts = {"read": 0, "direct": 0, "write": 0, "rejected": 0}
    report, stop, nudges, policy_read, failed_once = None, None, 0, False, False
    total = 0.0

    def ev(kind, **data):
        e = {"type": kind, "t": round(time.monotonic() - t0, 2), **data}
        if kind != "done":
            events.append(e)
        return e

    def append_system(content, output_config=None):
        msg = {"role": "system", "content": content}
        if output_config:
            msg["output_config"] = output_config
        messages.append(msg)

    def handle_failure(result):
        """First failed check: optionally raise effort, then send the harness result as the next user turn."""
        nonlocal effort, stage, phase, failed_once
        if escalate:
            append_system([], output_config={"effort": ESCALATED_EFFORT})  # effort-only: accepted anywhere
        ids = ", ".join(a["adjustment_id"] for a in gate.adjustments()) or "none"
        messages.append({"role": "user", "content": (
            f"The harness's independent check failed. Expected payout after adjustments: "
            f"{_cents(result['expected_after_adjustments_cents'])}. Processor deposits for September (bank "
            f"record): {_cents(result['processor_deposits_cents'])}. Residual: {_cents(result['residual_cents'])}. "
            f"Recorded adjustments ({ids}) stay in the ledger. Investigate what the residual is, using the same "
            f"tools, and submit an amended plan that contains only new adjustments.")})
        if escalate:
            effort_timeline.append({"call": len(calls) + 1, "effort": ESCALATED_EFFORT,
                                    "reason": "independent check failed"})
            yield ev("effort", frm=effort, to=ESCALATED_EFFORT, reason="independent check failed")
            effort = ESCALATED_EFFORT
        stage, phase, failed_once = "investigate", "escalate" if escalate else "control", True
        yield ev("phase", phase=phase, effort=effort)

    fork = None
    if resume:  # matched control: continue a saved conversation from its failed check
        messages[:] = resume["messages"]
        gate.observed.update(resume["observed"])
        gate.plans.extend(resume["plans"])
        for item in resume["approved"]:
            gate.approved[Gate.key(item)] = item
        gate.out_dir.mkdir(parents=True, exist_ok=True)
        for entry in resume["written"]:
            gate.written[Gate.key(entry)] = entry
            with open(gate.out_dir / "adjustments.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        gate.write_unlocked = True
        verifications.append({"after_call": 0, **resume["verify"]})
        yield from handle_failure(resume["verify"])
    else:
        yield ev("phase", phase=phase, effort=effort)
    while stop is None:
        if len(calls) >= MAX_CALLS or time.monotonic() - t0 > MAX_SECONDS or total >= STOP_USD:
            stop = "app limit reached"
            break
        started = time.monotonic()
        response = client.beta.messages.create(
            model=MODEL, max_tokens=MAX_TOKENS, system=SYSTEM_PROMPT, tools=TOOLS,
            cache_control={"type": "ephemeral"},               # automatic caching, breakpoint moves forward
            thinking={"type": "adaptive", "display": "updates"},
            output_config={"effort": START_EFFORT},             # never changes: per-message changes do that
            messages=messages, betas=BETAS,
        )
        latency = time.monotonic() - started
        cost = call_cost(response.usage)
        total += cost["cost"]
        call = {"call": len(calls) + 1, "phase": phase, "effort": effort, "stop_reason": response.stop_reason,
                "latency_s": round(latency, 2), **cost}
        calls.append(call)
        yield ev("usage", **call, running_total=total)

        messages.append({"role": "assistant", "content": response.content})  # thinking blocks go back unchanged
        for block in response.content:
            text = getattr(block, "thinking", None) if block.type == "thinking" else getattr(block, "text", None)
            if text and text.strip():
                yield ev("progress", text=text.strip(), block=block.type)

        if response.stop_reason == "tool_use":
            results, approved_plan = [], None
            for block in response.content:
                if block.type != "tool_use":
                    continue
                name, args = block.name, block.input
                path = "read" if name in READ_TOOLS else ("write" if name == "create_adjustment" else "direct")
                yield ev("tool_call", name=name, input=args, path=path, id=block.id)
                ok, content = True, None
                try:
                    if name in READ_TOOLS:
                        out = reads.run(name, args)
                        policy_read |= name == "read_policy"
                        for row in out.get("rows", []):
                            key = next(k for k in row if k.endswith("_id") and k != "customer_id")
                            retrieved[row[key]] = {"source": args["source"], **row}
                        counts["read"] += 1
                        content = dumps(out)
                    elif name == "submit_plan":
                        counts["direct"] += 1
                        ok, problems, fresh = gate.review_plan(args, stage)
                        gate_log.append({"call": call["call"], "approved": ok, "problems": problems,
                                         "plan": args, "plan_id": gate.plans[-1]["plan_id"] if ok else None})
                        yield ev("gate", approved=ok, problems=problems,
                                 plan_id=gate.plans[-1]["plan_id"] if ok else None, items=fresh)
                        if ok:
                            approved_plan = gate.plans[-1]["plan_id"]
                            content = f"{approved_plan} approved: {len(fresh)} new adjustment(s)."
                        else:
                            content = "Plan rejected. Write access stays locked.\n- " + "\n- ".join(problems)
                    elif name == "create_adjustment":
                        ok, detail = gate.write_adjustment(args)
                        counts["write" if ok else "rejected"] += 1
                        if ok:
                            yield ev("adjustment", entry=gate.written[Gate.key(args)])
                            content = f"Recorded {detail}."
                        else:
                            content = f"Rejected: {detail}"
                    elif name == "submit_report" and stage == "report":
                        counts["direct"] += 1
                        report = args
                        gate.out_dir.mkdir(parents=True, exist_ok=True)
                        (gate.out_dir / "report.json").write_text(json.dumps(args, indent=2), encoding="utf-8")
                        yield ev("report", report=args)
                        content = "Report received."
                    else:
                        ok, content = False, f"{name} is not available at this point in the run."
                except Exception as exc:  # malformed arguments or unknown columns go back as tool errors
                    ok, content = False, f"{type(exc).__name__}: {exc}"
                if not ok and name != "submit_plan":
                    counts["rejected"] += name != "create_adjustment"
                    yield ev("tool_result", name=name, ok=False, error=content)
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": content,
                                **({"is_error": True} if not ok else {})})
            messages.append({"role": "user", "content": results})

            if report is not None:
                stop = "report accepted"
                break
            if approved_plan and not gate.write_unlocked:
                # Placement rule: a content-carrying system message right after the tool_result turn.
                text = UNLOCK_TEXT.format(plan_id=approved_plan)
                append_system([{"type": "text", "text": text},
                               {"type": "tool_addition", "tool": {"type": "tool_reference",
                                                                  "name": "create_adjustment"}}])
                gate.write_unlocked = True
                stage, phase = "remediate", "remediate" if effort == START_EFFORT else phase
                yield ev("unlock", plan_id=approved_plan, text=text, tool="create_adjustment")
                yield ev("phase", phase=phase, effort=effort)
            elif approved_plan:
                append_system([{"type": "text", "text": REAPPROVE_TEXT.format(plan_id=approved_plan)}])
                stage = "remediate"
                yield ev("unlock", plan_id=approved_plan, text=REAPPROVE_TEXT.format(plan_id=approved_plan),
                         tool=None)
            continue

        if response.stop_reason == "pause_turn":
            continue
        if response.stop_reason != "end_turn":
            stop = f"stopped: {response.stop_reason}"  # refusal, max_tokens, or context limit
            break

        # end_turn: Claude replied in text. The harness decides what happens next.
        pending = gate.pending()
        if stage == "investigate" or (stage == "remediate" and pending):
            if nudges >= 2:
                stop = "no plan submitted" if stage == "investigate" else "approved items not recorded"
                break
            nudges += 1
            text = ("Submit your plan with submit_plan." if stage == "investigate"
                    else f"Approved items not yet recorded: {', '.join(pending)}.")
            messages.append({"role": "user", "content": text})
            yield ev("nudge", text=text)
            continue
        if stage == "report":
            stop = "report not submitted"
            break

        result = gate.verify()
        verifications.append({"after_call": call["call"], **result})
        yield ev("verify", result=result)
        if result["passed"]:
            append_system([], output_config={"effort": START_EFFORT})  # effort-only: accepted anywhere
            messages.append({"role": "user", "content": (
                "The harness ran its independent checks and all passed: numbers, provenance, raw files "
                "unchanged, write scope. Write the final report for finance.")})
            append_system([{"type": "text", "text": REPORT_TEXT},
                           {"type": "tool_removal", "tool": {"type": "tool_reference", "name": "create_adjustment"}},
                           {"type": "tool_addition", "tool": {"type": "tool_reference", "name": "submit_report"}}])
            if effort != START_EFFORT:
                effort_timeline.append({"call": len(calls) + 1, "effort": START_EFFORT, "reason": "checks passed"})
                yield ev("effort", frm=effort, to=START_EFFORT, reason="checks passed; report at medium")
            effort, stage, phase = START_EFFORT, "report", "report"
            yield ev("tool_change", removed=["create_adjustment"], added=["submit_report"])
            yield ev("phase", phase=phase, effort=effort)
            continue
        if failed_once:
            stop = "verification failed twice"
            break
        fork = {"run_id": run_id, "messages": serialize(messages), "observed": sorted(gate.observed),
                "plans": [dict(p) for p in gate.plans],
                "approved": list(gate.approved.values()), "written": gate.adjustments(), "verify": result}
        yield from handle_failure(result)

    elapsed = time.monotonic() - t0
    by_phase = {}
    for c in calls:
        p = by_phase.setdefault(c["phase"], {"api_calls": 0, "output_tokens": 0, "thinking_tokens": 0,
                                             "cache_read_tokens": 0, "cost": 0.0})
        p["api_calls"] += 1
        for k in ("output_tokens", "thinking_tokens", "cache_read_tokens", "cost"):
            p[k] += c[k]
    totals = {k: sum(c[k] for c in calls) for k in ("input_tokens", "cache_write_tokens", "cache_read_tokens",
                                                    "output_tokens", "thinking_tokens", "cost_input",
                                                    "cost_cache_write", "cost_cache_read", "cost_output", "cost")}
    record = {
        "run_id": run_id, "model": MODEL, "betas": BETAS, "stop": stop, "elapsed_s": round(elapsed, 1),
        "final_phase": phase, "calls": calls, "cost": {**totals, "by_phase": by_phase},
        "tool_counts": counts, "effort_timeline": effort_timeline, "gate_log": gate_log,
        "plans": gate.plans, "adjustments": gate.adjustments(), "verifications": verifications,
        "report": report, "retrieved_records": list(retrieved.values()), "policy_read": policy_read,
        "observed_ids": sorted(gate.observed), "events": events,
        "passed": bool(verifications and verifications[-1]["passed"]), "escalate": escalate,
        "fork": fork, "parent_run_id": resume["run_id"] if resume else None,
    }
    yield ev("done", record=record)
