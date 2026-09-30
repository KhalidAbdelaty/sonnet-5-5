"""Run one live Rivermark reconciliation and save the record to runs/.

    python run_experiment.py            # starts at medium, escalates to high after a failed check
    python run_experiment.py --control  # matched control: resumes the newest run from its failed check
                                        # and stays at medium

This calls the Claude API with the key in .env and is billed to it.
"""
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from rivermark.agent import run_reconciliation

load_dotenv()
RUNS = Path(__file__).resolve().parent / "runs"


def short(value, limit=160):
    text = value if isinstance(value, str) else json.dumps(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def main():
    control = "--control" in sys.argv
    resume = None
    if control:
        runs = sorted(RUNS.glob("run_*.json"))
        resume = json.loads(runs[-1].read_text(encoding="utf-8"))["fork"] if runs else None
        if not resume:
            sys.exit("No saved run with a failed check to resume. Run without --control first.")
    for e in run_reconciliation(escalate=not control, resume=resume):
        t, kind = f"{e['t']:7.1f}s", e["type"]
        if kind == "phase":
            print(f"{t}  == phase: {e['phase']} (effort {e['effort']})")
        elif kind == "usage":
            print(f"{t}  call {e['call']} [{e['effort']}] {e['stop_reason']} in={e['input_tokens']} "
                  f"write={e['cache_write_tokens']} read={e['cache_read_tokens']} out={e['output_tokens']} "
                  f"(thinking {e['thinking_tokens']}) {e['latency_s']}s ${e['cost']:.4f}")
        elif kind == "progress":
            print(f"{t}  note: {short(e['text'], 300)}")
        elif kind == "tool_call":
            print(f"{t}  [{e['path']}] {e['name']} {short(e['input'])}")
        elif kind == "tool_result":
            print(f"{t}  [rejected] {e['name']}: {short(e['error'])}")
        elif kind == "gate":
            state = f"APPROVED {e['plan_id']}" if e["approved"] else f"REJECTED {e['problems']}"
            print(f"{t}  gate: {state} items={short(e['items'], 300)}")
        elif kind == "unlock":
            print(f"{t}  unlock: system message" + (f" + tool_addition({e['tool']})" if e["tool"] else ""))
        elif kind == "adjustment":
            print(f"{t}  wrote {short(e['entry'], 300)}")
        elif kind == "verify":
            r = e["result"]
            print(f"{t}  verify: passed={r['passed']} residual={r['residual_cents']} checks={r['checks']}")
        elif kind in ("effort", "tool_change", "nudge"):
            print(f"{t}  {kind}: {short({k: v for k, v in e.items() if k not in ('type', 't')})}")
        elif kind == "report":
            print(f"{t}  report: {short(e['report'], 400)}")
        elif kind == "done":
            rec = e["record"]
            RUNS.mkdir(exist_ok=True)
            path = RUNS / ("control" if control else "") / f"run_{rec['run_id']}.json"
            path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps(rec, indent=2, default=str), encoding="utf-8")
            c = rec["cost"]
            print(f"\nstop={rec['stop']} passed={rec['passed']} elapsed={rec['elapsed_s']}s "
                  f"calls={len(rec['calls'])} cost=${c['cost']:.4f}\nsaved {path}")


if __name__ == "__main__":
    main()
