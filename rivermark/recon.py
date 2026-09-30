"""Rivermark's existing month-end reconciliation logic, plus CSV loading.

This is the logic the finance team already runs. The agent can call it through
run_reconciliation_check. It is deliberately left as written: it is part of
what the agent investigates, not a trusted verifier.
"""
import csv
import json
from pathlib import Path

PERIOD = ("2026-09-01", "2026-09-30")


def load(raw: Path, name: str) -> list[dict]:
    with open(raw / name, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k in list(r):
            if k.endswith("_cents") or k == "line_count":
                r[k] = int(r[k])
    return rows


def in_period(day: str) -> bool:
    return PERIOD[0] <= day <= PERIOD[1]


def internal_reconciliation(raw: Path, adjustments: list[dict]) -> dict:
    orders = {o["order_id"]: o for o in load(raw, "orders.csv")}
    gross = sum(o["amount_cents"] for o in orders.values() if in_period(o["order_date"]))
    # Refunds follow the original order into its month.
    refunds = sum(r["amount_cents"] for r in load(raw, "refunds.csv")
                  if in_period(orders[r["order_id"]]["order_date"]))
    lines = load(raw, "payout_transactions.csv")
    fees = sum(ln["fee_cents"] for ln in lines if ln["type"] == "charge")
    disputes = sum(ln["amount_cents"] for ln in lines if ln["type"] == "adjustment")
    processor = sum(ln["net_cents"] for ln in lines if ln["type"] in ("charge", "refund")) + disputes
    adj = sum(a["amount_cents"] for a in adjustments)
    expected = gross - refunds - fees
    return {
        "period": "2026-09",
        "gross_order_revenue_cents": gross,
        "refunds_recognized_cents": refunds,
        "net_revenue_cents": gross - refunds,
        "processing_fees_cents": fees,
        "dispute_adjustments_cents": disputes,
        "expected_payout_cents": expected,
        "adjustments_applied_cents": adj,
        "processor_payout_cents": processor,
        "variance_cents": expected + adj - processor,
    }


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
