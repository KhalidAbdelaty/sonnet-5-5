"""Generate Rivermark Subscriptions' September 2026 close data.

    python build_data.py

data/raw/ holds the sources Claude can read. data/bank/ holds the processor
deposits the harness uses as its acceptance input; no tool exposes it.
The output is deterministic. The two seeded causes are marked in comments
below; nothing in the data labels them.
"""
import csv
import json
import random
from datetime import date, timedelta
from pathlib import Path

from rivermark.recon import internal_reconciliation

ROOT = Path(__file__).resolve().parent
RAW, BANK = ROOT / "data" / "raw", ROOT / "data" / "bank"
PLANS = {"Starter": 2900, "Team": 7900, "Business": 14900}


def fee(amount_cents: int) -> int:
    """Processor fee: 2.9% + 30 cents, rounded half up."""
    return (amount_cents * 29 + 500) // 1000 + 30


def write(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    rng = random.Random(7)
    RAW.mkdir(parents=True, exist_ok=True)
    BANK.mkdir(parents=True, exist_ok=True)

    # Orders: Aug 25-31 for context, Sep 1-29 for the close. Charges settle on the order date.
    orders, n = [], 20800
    days = [date(2026, 8, 25) + timedelta(d) for d in range(7)] + \
           [date(2026, 9, 1) + timedelta(d) for d in range(29)]
    for day in days:
        for _ in range(2):
            plan = rng.choices(list(PLANS), weights=[5, 4, 2])[0]
            orders.append({"order_id": f"ORD-{n}", "customer_id": f"CUS-{rng.randint(1000, 9999)}",
                           "order_date": day.isoformat(), "plan": plan, "amount_cents": PLANS[plan]})
            n += 1
    by_id = {o["order_id"]: o for o in orders}
    by_id["ORD-20813"].update(plan="Business", amount_cents=14900)  # placed Aug 31

    refunds = [
        ("RF-1038", "ORD-20803", "2026-08-27", "2026-08-28", "duplicate signup"),
        ("RF-1041", "ORD-20810", "2026-08-30", "2026-08-31", "cancelled in trial"),
        ("RF-1043", "ORD-20813", "2026-08-31", "2026-09-02", "downgrade requested"),  # seeded cause 1
        ("RF-1044", "ORD-20819", "2026-09-04", "2026-09-05", "cancelled in trial"),
        ("RF-1045", "ORD-20833", "2026-09-10", "2026-09-11", "billing error"),
        ("RF-1046", "ORD-20850", "2026-09-19", "2026-09-20", "duplicate signup"),
        ("RF-1047", "ORD-20861", "2026-09-24", "2026-09-25", "cancelled in trial"),
    ]
    refund_rows = [{"refund_id": r, "order_id": o, "requested_date": q, "settled_date": s,
                    "amount_cents": by_id[o]["amount_cents"], "reason": why} for r, o, q, s, why in refunds]

    # Processor balance transactions settled in September: amount, fee, and net per line.
    events = []
    for o in orders:
        if o["order_date"] >= "2026-09-01":
            events.append((o["order_date"], "charge", "charge", o["order_id"], o["amount_cents"],
                           fee(o["amount_cents"]), f"Charge {o['order_id']}"))
    for r in refund_rows:
        if r["settled_date"] >= "2026-09-01":
            events.append((r["settled_date"], "refund", "refund", r["refund_id"], -r["amount_cents"], 0,
                           f"Refund {r['refund_id']}"))
    # Seeded cause 2: a won dispute. The debit carries a $15.00 dispute fee that is not returned,
    # so the two amounts net to zero while the two nets do not.
    disputed = by_id["ORD-20842"]["amount_cents"]
    events.append(("2026-09-16", "adjustment", "dispute", "DSP-0077", -disputed, 1500, "Dispute DSP-0077 on ORD-20842"))
    events.append(("2026-09-23", "adjustment", "dispute_reversal", "DSP-0077", disputed, 0, "Dispute DSP-0077 won"))

    lines = []
    for i, (day, kind, cat, related, amount, fee_c, desc) in enumerate(sorted(events, key=lambda e: e[0])):
        lines.append({"txn_id": f"TXN-{50001 + i}", "settled_date": day, "type": kind, "reporting_category": cat,
                      "related_id": related, "amount_cents": amount, "fee_cents": fee_c,
                      "net_cents": amount - fee_c, "description": desc})

    # Weekly payouts. Deposits are the bank record the harness checks against.
    weeks = [("PO-2609-1", "2026-09-01", "2026-09-07", "2026-09-08"),
             ("PO-2609-2", "2026-09-08", "2026-09-14", "2026-09-15"),
             ("PO-2609-3", "2026-09-15", "2026-09-21", "2026-09-22"),
             ("PO-2609-4", "2026-09-22", "2026-09-30", "2026-10-01")]
    deposits = []
    for pid, start, end, deposit_date in weeks:
        members = [ln for ln in lines if start <= ln["settled_date"] <= end]
        for ln in members:
            ln["payout_id"] = pid
        deposits.append({"payout_id": pid, "period_start": start, "period_end": end, "deposit_date": deposit_date,
                         "net_deposit_cents": sum(ln["net_cents"] for ln in members)})

    write(RAW / "orders.csv", orders)
    write(RAW / "refunds.csv", refund_rows)
    cols = ("txn_id", "payout_id", "settled_date", "type", "reporting_category", "related_id",
            "amount_cents", "fee_cents", "net_cents", "description")
    write(RAW / "payout_transactions.csv", [{k: ln[k] for k in cols} for ln in lines])
    write(BANK / "processor_deposits.csv", deposits)
    report = internal_reconciliation(RAW, adjustments=[])
    (RAW / "close_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print("bank deposits:", sum(d["net_deposit_cents"] for d in deposits))


if __name__ == "__main__":
    main()
