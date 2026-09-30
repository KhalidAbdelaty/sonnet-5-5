"""Harness-side authority: plan gate, write executor, and independent verifier.

Nothing here is sent to Claude except the short pass/fail messages the methods
return. The acceptance input (bank deposits) and raw-file hashes stay here.
"""
import hashlib
import json
from pathlib import Path

from rivermark.recon import in_period, internal_reconciliation, load

ALLOWED_RULES = {"POL-2", "POL-3", "POL-4"}  # rules that can justify a payout adjustment
ALLOWED_OUTPUTS = {"adjustments.jsonl", "report.json"}


def hash_dir(path: Path) -> dict:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(path.iterdir())}


def processor_deposits(bank: Path) -> int:
    """What Rivermark actually received for the month. Never exposed through a tool."""
    return sum(d["net_deposit_cents"] for d in load(bank, "processor_deposits.csv") if in_period(d["period_end"]))


class Gate:
    """Tracks what Claude has seen, which plan items are approved, and what was written."""

    def __init__(self, raw: Path, bank: Path, out_dir: Path):
        self.raw, self.bank, self.out_dir = raw, bank, out_dir
        self.lines = {ln["txn_id"]: ln for ln in load(raw, "payout_transactions.csv")}
        self.observed: set[str] = set()      # every record ID returned in a tool result
        self.approved: dict[tuple, dict] = {}  # evidence key -> approved plan item
        self.written: dict[tuple, dict] = {}   # evidence key -> adjustment entry
        self.plans: list[dict] = []
        self.write_unlocked = False
        self.hashes_before = hash_dir(raw)

    @staticmethod
    def key(item: dict) -> tuple:
        return tuple(sorted(item["evidence_txn_ids"]))

    def evidence_problems(self, item: dict) -> list[str]:
        """Provenance: every cited line was retrieved, and the lines net to the adjustment."""
        ids = item["evidence_txn_ids"]
        problems = [f"{t} was never returned by a read tool in this conversation."
                    for t in ids if t not in self.observed]
        unknown = [t for t in ids if t not in self.lines]
        if unknown or not ids:
            problems.append(f"Evidence must be processor txn_ids; not found: {', '.join(unknown) or 'none given'}.")
        elif sum(self.lines[t]["net_cents"] for t in ids) != item["amount_cents"]:
            problems.append(f"amount_cents {item['amount_cents']} is not the net_cents total of {', '.join(ids)}.")
        return problems

    # ------------------------------------------------------------ plan gate
    def review_plan(self, plan: dict, stage: str) -> tuple[bool, list[str], list[dict]]:
        problems, fresh, seen = [], [], set()
        for i, item in enumerate(plan.get("adjustments", []), 1):
            k, done = self.key(item), self.written.get(self.key(item))
            if done and done["amount_cents"] == item["amount_cents"] and done["rule"] == item["rule"]:
                continue  # already recorded exactly as proposed
            problems += [f"Item {i}: {p}" for p in self.evidence_problems(item)]
            missing = [s for s in item["supporting_record_ids"] if s not in self.observed]
            if missing:
                problems.append(f"Item {i}: supporting records not retrieved: {', '.join(missing)}.")
            if item["rule"] not in ALLOWED_RULES:
                problems.append(f"Item {i}: rule {item['rule']} cannot justify a payout adjustment.")
            if k in seen or done or any(set(k) & set(w) for w in self.written):
                problems.append(f"Item {i}: those transactions are already covered by an adjustment.")
            seen.add(k)
            fresh.append(item)
        if not fresh and not problems:
            problems.append("The plan proposes no new adjustments.")
        approved = not problems
        if approved:
            for item in fresh:
                self.approved[self.key(item)] = item
            self.plans.append({"plan_id": f"PLAN-{len(self.plans) + 1}", "stage": stage, **plan})
        return approved, problems, fresh

    # ------------------------------------------------------- write executor
    def write_adjustment(self, entry: dict) -> tuple[bool, str]:
        if not self.write_unlocked:
            return False, "Write authority has not been granted."
        k = self.key(entry)
        item = self.approved.get(k)
        if item is None:
            return False, "These transactions are not in an approved plan item."
        if entry["amount_cents"] != item["amount_cents"] or entry["rule"] != item["rule"]:
            return False, "Entry differs from the approved plan item."
        if k in self.written:
            return False, f"Already recorded as {self.written[k]['adjustment_id']}."
        record = {"adjustment_id": f"ADJ-{len(self.written) + 1:03d}", **entry}
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with open(self.out_dir / "adjustments.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        self.written[k] = record
        return True, record["adjustment_id"]

    def adjustments(self) -> list[dict]:
        return list(self.written.values())

    def pending(self) -> list[str]:
        return [" + ".join(k) for k in self.approved if k not in self.written]

    # ---------------------------------------------------- independent check
    def verify(self) -> dict:
        internal = internal_reconciliation(self.raw, self.adjustments())
        deposits = processor_deposits(self.bank)
        adjusted = internal["expected_payout_cents"] + internal["adjustments_applied_cents"]
        provenance = [a["adjustment_id"] for a in self.adjustments() if self.evidence_problems(a)]
        created = sorted(p.name for p in self.out_dir.rglob("*") if p.is_file()) if self.out_dir.exists() else []
        checks = {
            "numbers": adjusted == deposits,
            "provenance": not provenance,
            "raw_unchanged": hash_dir(self.raw) == self.hashes_before,
            "write_scope": set(created) <= ALLOWED_OUTPUTS,
        }
        return {"checks": checks, "passed": all(checks.values()),
                "expected_after_adjustments_cents": adjusted, "processor_deposits_cents": deposits,
                "residual_cents": adjusted - deposits, "provenance_failures": provenance,
                "files_created": created}
