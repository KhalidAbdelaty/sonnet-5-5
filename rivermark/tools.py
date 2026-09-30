"""Tool definitions and the read-only executors Claude can call.

All tools are declared in the first request and the list never changes.
create_adjustment and submit_report start deferred; the harness surfaces them
later with tool_addition blocks.
"""
import json
from pathlib import Path

from rivermark.recon import internal_reconciliation, load

SOURCES = {
    "orders": ("orders.csv", "order_id", "order_date", "Subscription orders exported from billing."),
    "refunds": ("refunds.csv", "refund_id", "settled_date", "Refunds with request and processor settlement dates."),
    "payout_transactions": ("payout_transactions.csv", "txn_id", "settled_date",
                            "Processor balance transactions settled in September: signed amount, fee, and net "
                            "in cents, with payout_id."),
}
MAX_ROWS = 40
READ_TOOLS = {"list_sources", "query_records", "aggregate_records", "read_policy", "run_reconciliation_check"}
DATE = {"type": "string", "description": "Inclusive ISO date, YYYY-MM-DD."}


def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


ADJUSTMENT_ITEM = _schema({
    "finding": {"type": "string", "description": "One sentence: what is wrong and why."},
    "rule": {"type": "string", "description": "Policy rule ID, for example POL-3."},
    "evidence_txn_ids": {"type": "array", "items": {"type": "string"},
                         "description": "Processor txn_ids whose net_cents this adjustment accounts for."},
    "supporting_record_ids": {"type": "array", "items": {"type": "string"},
                              "description": "Other retrieved records (orders, refunds) that support the finding."},
    "amount_cents": {"type": "integer", "description": "Signed amount applied to expected payout."},
}, ["finding", "rule", "evidence_txn_ids", "supporting_record_ids", "amount_cents"])

TOOLS = [
    {"name": "list_sources", "strict": True,
     "description": "List the close data sources with their columns and row counts.",
     "input_schema": _schema({}, [])},
    {"name": "query_records", "strict": True,
     "description": f"Return up to {MAX_ROWS} rows from one source, optionally filtered by a column value "
                    "and a date range on the source's date column. Also returns the match count and amount total.",
     "input_schema": _schema({
         "source": {"type": "string", "enum": list(SOURCES)},
         "column": {"type": "string", "description": "Column to filter on."},
         "value": {"type": "string", "description": "Exact value to match in that column."},
         "date_from": DATE, "date_to": DATE,
     }, ["source"])},
    {"name": "aggregate_records", "strict": True,
     "description": "Count rows and total amount_cents in one source, grouped by a column.",
     "input_schema": _schema({
         "source": {"type": "string", "enum": list(SOURCES)},
         "group_by": {"type": "string"},
         "date_from": DATE, "date_to": DATE,
     }, ["source", "group_by"])},
    {"name": "read_policy", "strict": True,
     "description": "Read Rivermark's month-end close policy.", "input_schema": _schema({}, [])},
    {"name": "run_reconciliation_check", "strict": True,
     "description": "Run Rivermark's current internal reconciliation logic for September 2026, "
                    "including adjustments recorded so far.",
     "input_schema": _schema({}, [])},
    {"name": "submit_plan", "strict": True,
     "description": "Submit a diagnosis and remediation plan for the harness to review. Proposing does not "
                    "write anything; the harness approves or rejects the plan.",
     "input_schema": _schema({
         "diagnosis": {"type": "string"},
         "adjustments": {"type": "array", "items": ADJUSTMENT_ITEM},
         "remaining_uncertainty": {"type": "string"},
     }, ["diagnosis", "adjustments", "remaining_uncertainty"])},
    {"name": "create_adjustment", "strict": True, "defer_loading": True,
     "description": "Record one approved adjustment in the close adjustments ledger. Never edits source files.",
     "input_schema": _schema({
         "evidence_txn_ids": {"type": "array", "items": {"type": "string"}},
         "rule": {"type": "string"},
         "amount_cents": {"type": "integer"},
         "memo": {"type": "string"},
     }, ["evidence_txn_ids", "rule", "amount_cents", "memo"])},
    {"name": "submit_report", "strict": True, "defer_loading": True,
     "description": "Submit the final reconciliation report for finance.",
     "input_schema": _schema({
         "diagnosis": {"type": "string"},
         "findings": {"type": "array", "items": _schema({
             "finding": {"type": "string"}, "evidence": {"type": "array", "items": {"type": "string"}},
             "adjustment_id": {"type": "string"}}, ["finding", "evidence", "adjustment_id"])},
         "final_state": {"type": "string"},
         "remaining_uncertainty": {"type": "string"},
         "follow_ups": {"type": "array", "items": {"type": "string"}},
     }, ["diagnosis", "findings", "final_state", "remaining_uncertainty", "follow_ups"])},
]


class ReadTools:
    """Executes read tools and reports every record ID it returns to the gate."""

    def __init__(self, raw: Path, gate):
        self.raw, self.gate = raw, gate

    def _rows(self, source: str, date_from: str | None, date_to: str | None) -> tuple[list[dict], str, str]:
        name, key, date_col, _ = SOURCES[source]
        rows = load(self.raw, name)
        if date_from:
            rows = [r for r in rows if r[date_col] >= date_from]
        if date_to:
            rows = [r for r in rows if r[date_col] <= date_to]
        return rows, key, date_col

    def run(self, name: str, args: dict) -> dict:
        if name == "list_sources":
            out = {}
            for s, (fname, key, date_col, desc) in SOURCES.items():
                rows = load(self.raw, fname)
                out[s] = {"description": desc, "id_column": key, "date_column": date_col,
                          "columns": list(rows[0]), "rows": len(rows)}
            out["policy"] = {"description": "Month-end close policy, via read_policy."}
            out["close_check"] = {"description": "Internal reconciliation output, via run_reconciliation_check."}
            return out
        if name == "read_policy":
            return {"policy": (self.raw / "finance_policy.txt").read_text(encoding="utf-8")}
        if name == "run_reconciliation_check":
            return internal_reconciliation(self.raw, self.gate.adjustments())
        rows, key, _ = self._rows(args["source"], args.get("date_from"), args.get("date_to"))
        if name == "query_records":
            col, val = args.get("column"), args.get("value")
            if col:
                if rows and col not in rows[0]:
                    raise ValueError(f"Unknown column {col!r} for {args['source']}.")
                rows = [r for r in rows if str(r[col]) == str(val)]
            shown = rows[:MAX_ROWS]
            self.gate.observed.update(r[key] for r in shown)
            return {"source": args["source"], "match_count": len(rows),
                    "amount_total_cents": sum(r["amount_cents"] for r in rows),
                    "rows": shown, "truncated": len(rows) > MAX_ROWS}
        if name == "aggregate_records":
            col = args["group_by"]
            if rows and col not in rows[0]:
                raise ValueError(f"Unknown column {col!r} for {args['source']}.")
            groups: dict[str, dict] = {}
            for r in rows:
                g = groups.setdefault(str(r[col]), {"rows": 0, "amount_total_cents": 0})
                g["rows"] += 1
                g["amount_total_cents"] += r["amount_cents"]
            return {"source": args["source"], "group_by": col, "groups": groups}
        raise ValueError(f"Unknown tool {name}.")


def dumps(obj) -> str:
    return json.dumps(obj, separators=(",", ":"))
