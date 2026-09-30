"""Draw the Rivermark trust-boundary diagram to assets/rivermark_architecture.png.

    python build_architecture.py
"""
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

OUT = Path(__file__).resolve().parent / "assets" / "rivermark_architecture.png"
PAPER, WHITE, BORDER, INK, MUTED, ACCENT = "#F4F3EE", "#FFFFFF", "#E5E2D9", "#191919", "#55534E", "#B35F44"
GREEN, AMBER = ("#E3F1E6", "#1E5E2E"), ("#FFF1D6", "#7A4B00")


def box(ax, x, y, w, h, title, lines, fill=WHITE, title_color=INK):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.12",
                                fc=fill, ec=BORDER, lw=1.2))
    ax.text(x + 0.18, y + h - 0.3, title, fontsize=11, fontweight="bold", color=title_color, va="top")
    for i, line in enumerate(lines):
        ax.text(x + 0.18, y + h - 0.75 - i * 0.36, line, fontsize=9, color=MUTED, va="top")


def arrow(ax, a, b, label=""):
    ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=12, color=ACCENT, lw=1.4))
    if label:
        ax.text((a[0] + b[0]) / 2, (a[1] + b[1]) / 2 + 0.14, label, fontsize=8.5, color=ACCENT, ha="center")


fig, ax = plt.subplots(figsize=(11, 7), dpi=160)
fig.patch.set_facecolor(WHITE)
ax.set_xlim(0, 11)
ax.set_ylim(0, 7)
ax.axis("off")

# Two zones: what Claude can see, and what only the harness sees.
for x0, label in ((0.2, "Model-visible"), (6.3, "Harness-only")):
    ax.add_patch(FancyBboxPatch((x0, 0.25), 4.5, 6.45, boxstyle="round,pad=0,rounding_size=0.15",
                                fc=PAPER, ec=BORDER, lw=1.2))
    ax.text(x0 + 0.2, 6.52, label, fontsize=12, fontweight="bold", color=INK, va="top")

box(ax, 0.45, 4.2, 4.0, 1.95, "Claude Sonnet 5.5", ["One append-only conversation",
                                                    "Starts at medium effort, read-only",
                                                    "Per-message effort after a failed check"])
box(ax, 0.45, 2.1, 1.9, 1.85, "Read tools", ["sources, records,", "aggregates, policy,", "internal check"],
    fill=GREEN[0], title_color=GREEN[1])
box(ax, 2.55, 2.1, 1.9, 1.85, "Deferred tools", ["create_adjustment", "submit_report", "via tool_addition"],
    fill=AMBER[0], title_color=AMBER[1])
box(ax, 0.45, 0.45, 4.0, 1.4, "data/raw (read-only)", ["orders, refunds, processor", "transactions, close policy"])

box(ax, 6.55, 4.2, 4.0, 1.95, "Plan gate", ["Cited records were retrieved", "Amount = net of cited lines",
                                            "Allowed rule, no duplicates"])
box(ax, 6.55, 2.1, 4.0, 1.85, "Write executor", ["Approved plan items only", "Appends to the adjustments",
                                                 "ledger in outputs/<run>/"])
box(ax, 6.55, 0.45, 4.0, 1.4, "Independent verifier", ["Adjusted payout = bank deposits",
                                                       "provenance, raw hashes, write scope"])

arrow(ax, (4.45, 5.75), (6.55, 5.75), "submit_plan")
arrow(ax, (6.55, 4.9), (4.45, 4.9), "plan approved")
ax.text(5.5, 4.62, "system msg + tool_addition", fontsize=8.5, color=ACCENT, ha="center")
arrow(ax, (4.45, 3.0), (6.55, 3.0), "create_adjustment")
arrow(ax, (6.55, 1.25), (4.45, 4.25))
ax.text(4.75, 1.55, "failed check:", fontsize=8.5, color=ACCENT, ha="left")
ax.text(4.75, 1.3, "residual + effort", fontsize=8.5, color=ACCENT, ha="left")
fig.savefig(OUT, bbox_inches="tight", facecolor=WHITE)
print(f"saved {OUT}")
