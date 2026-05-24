"""Render the detailed HITL flow as a PNG image.

Matches the detailed Mermaid flow in docs/architecture.md (v3 hero):
- Three capability-isolated MCP servers (Read / Email Write / Slack Write)
- Slack Notification node BEFORE Interrupt Gate (LangGraph correctness rule)
- Policy Risk Check + Confidence Check as separate gates, intent constraint on auto-send
- Reject loop bounded by human_rejection_count >= 3
- Stale-context revalidation posts a delta on the same Slack message
- Finalize Action separate from Send (idempotency boundary)
"""
import os

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Polygon

fig, ax = plt.subplots(figsize=(15, 20), dpi=110)
ax.set_xlim(0, 100)
ax.set_ylim(0, 100)
ax.axis("off")
fig.patch.set_facecolor("white")

BLUE_FILL, BLUE_STROKE = "#a5d8ff", "#2563eb"
PURPLE_FILL, PURPLE_STROKE = "#d0bfff", "#8b5cf6"
YELLOW_FILL, YELLOW_STROKE = "#fff3bf", "#f59e0b"
RED_FILL, RED_STROKE = "#ffc9c9", "#dc2626"
ORANGE_FILL, ORANGE_STROKE = "#ffd8a8", "#d97706"
GREEN_FILL, GREEN_STROKE = "#c3fae8", "#15803d"
CYAN_FILL, CYAN_STROKE = "#99e9f2", "#0891b2"
PINK_FILL, PINK_STROKE = "#fcc2d7", "#be185d"
GOLD_FILL, GOLD_STROKE = "#fde68a", "#a16207"
GREEN_ARROW = "#22c55e"
RED_ARROW = "#ef4444"
PURPLE_ARROW = "#8b5cf6"
CYAN_ARROW = "#0891b2"
PINK_ARROW = "#be185d"
GOLD_ARROW = "#a16207"


def box(cx, cy, w, h, text, fill, stroke, fs=10):
    rect = FancyBboxPatch(
        (cx - w / 2, cy - h / 2), w, h,
        boxstyle="round,pad=0.15,rounding_size=0.6",
        linewidth=1.5, edgecolor=stroke, facecolor=fill,
    )
    ax.add_patch(rect)
    ax.text(cx, cy, text, ha="center", va="center",
            fontsize=fs, fontweight="bold", color="#111")


def diamond(cx, cy, w, h, text, fill, stroke, fs=9):
    pts = [(cx, cy + h / 2), (cx + w / 2, cy),
           (cx, cy - h / 2), (cx - w / 2, cy)]
    poly = Polygon(pts, closed=True, linewidth=1.5,
                   edgecolor=stroke, facecolor=fill)
    ax.add_patch(poly)
    ax.text(cx, cy, text, ha="center", va="center",
            fontsize=fs, fontweight="bold", color="#111")


def arrow(x1, y1, x2, y2, color="#1e1e1e", label=None,
          label_pos=None, dashed=False,
          connectionstyle="arc3,rad=0"):
    a = FancyArrowPatch(
        (x1, y1), (x2, y2),
        arrowstyle="->,head_width=4,head_length=6",
        color=color, linewidth=1.5,
        linestyle="--" if dashed else "-",
        connectionstyle=connectionstyle,
    )
    ax.add_patch(a)
    if label:
        if label_pos is None:
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        else:
            mx, my = label_pos
        ax.text(mx, my, label, ha="center", va="center",
                fontsize=8, fontweight="bold", color=color,
                bbox=dict(boxstyle="round,pad=0.18",
                          facecolor="white", edgecolor="none"))


# Title
ax.text(50, 98, "HITL Customer Support Agent — Detailed Flow",
        ha="center", va="center", fontsize=17, fontweight="bold")
ax.text(50, 95.5,
        "v3 hero diagram — v4 adds Researcher + Drafter↔Critic inside the Retrieve / Draft nodes",
        ha="center", va="center", fontsize=10, color="#757575", style="italic")

CX = 38  # main column center
W_BOX, H_BOX = 22, 3.5

# Top sequential nodes (main column)
box(CX, 91, W_BOX, H_BOX, "Ticket In (Gmail IMAP)", BLUE_FILL, BLUE_STROKE, fs=10)
box(CX, 86, W_BOX, H_BOX, "PII Redact (middleware)", PURPLE_FILL, PURPLE_STROKE, fs=9)
box(CX, 81, W_BOX, H_BOX, "Classify Intent", BLUE_FILL, BLUE_STROKE)
box(CX, 76, W_BOX, H_BOX, "Retrieve Context", BLUE_FILL, BLUE_STROKE)
box(CX, 71, W_BOX, H_BOX, "Draft Response", BLUE_FILL, BLUE_STROKE)

# Routing diamonds
diamond(CX, 64, 26, 6, "Policy Risk Check", YELLOW_FILL, YELLOW_STROKE, fs=10)
diamond(CX, 55, 26, 6, "Confidence Check\n(>= 0.85)", YELLOW_FILL, YELLOW_STROKE, fs=9)

# Convergence (bottom of main column)
box(CX, 23, W_BOX, 4.5,
    "Finalize Action\n(PII restore + threading headers)",
    BLUE_FILL, BLUE_STROKE, fs=9)
box(CX, 16, W_BOX, 4.5,
    "Send Response\n(idempotent on send_idempotency_key)",
    BLUE_FILL, BLUE_STROKE, fs=8)
box(CX, 9, W_BOX, 4.5,
    "Audit log + LangSmith trace",
    GREEN_FILL, GREEN_STROKE, fs=9)

# HITL branch column (right side) — Slack Notification BEFORE Interrupt Gate
CX_HITL = 72
box(CX_HITL, 68, 24, 5,
    "Slack Notification\nchannel-routed: #refunds / #technical / #complaints",
    ORANGE_FILL, ORANGE_STROKE, fs=8)
box(CX_HITL, 60, 22, 4.5,
    "Interrupt Gate\nonly interrupt(), pauses execution",
    RED_FILL, RED_STROKE, fs=8)
box(CX_HITL, 52, 22, 4.5,
    "Approval Buttons\nApprove / Edit / Reject",
    ORANGE_FILL, ORANGE_STROKE, fs=9)
diamond(CX_HITL, 44, 22, 6, "Elapsed > 15min?", YELLOW_FILL, YELLOW_STROKE, fs=9)
diamond(CX_HITL, 36, 22, 6, "Revalidate\ncontext_hash", YELLOW_FILL, YELLOW_STROKE, fs=9)

# Manual Queue (rejection terminal — count >= 3)
box(91, 52, 14, 4.5,
    "Manual Queue\ncount >= 3",
    GREEN_FILL, GREEN_STROKE, fs=8)

# THREE capability-isolated MCP servers (left side, vertical stack)
# Read MCP
read_panel = FancyBboxPatch((3, 83), 22, 10,
                            boxstyle="round,pad=0.3,rounding_size=0.6",
                            linewidth=1.5, edgecolor=CYAN_STROKE,
                            facecolor=CYAN_FILL, alpha=0.3)
ax.add_patch(read_panel)
ax.text(14, 91.5, "MCP Read Server", ha="center", va="center",
        fontsize=9, fontweight="bold", color=CYAN_STROKE)
box(14, 89, 18, 1.7, "get_crm_profile", "white", CYAN_STROKE, fs=7)
box(14, 87, 18, 1.7, "get_customer_history", "white", CYAN_STROKE, fs=7)
box(14, 85, 18, 1.7, "get_kb_article", "white", CYAN_STROKE, fs=7)

# Slack Write MCP (placed near Slack Notification at y=68)
slack_panel = FancyBboxPatch((3, 71), 22, 10,
                             boxstyle="round,pad=0.3,rounding_size=0.6",
                             linewidth=1.5, edgecolor=GOLD_STROKE,
                             facecolor=GOLD_FILL, alpha=0.3)
ax.add_patch(slack_panel)
ax.text(14, 79.5, "MCP Slack Write Server", ha="center", va="center",
        fontsize=9, fontweight="bold", color=GOLD_STROKE)
box(14, 77, 18, 1.7, "post_approval_request", "white", GOLD_STROKE, fs=7)
box(14, 75, 18, 1.7, "update_message", "white", GOLD_STROKE, fs=7)
box(14, 73, 18, 1.7, "open_edit_modal", "white", GOLD_STROKE, fs=7)

# Isolation note
ax.text(14, 69,
        "Three capability-isolated MCP servers —\n"
        "each can only do its lane.\n"
        "Prompt injection cannot reach other I/O.",
        ha="center", va="center", fontsize=7, color="#555", style="italic")

# Email Write MCP (placed near Send Response at y=16)
email_panel = FancyBboxPatch((3, 13), 22, 6,
                             boxstyle="round,pad=0.3,rounding_size=0.6",
                             linewidth=1.5, edgecolor=PINK_STROKE,
                             facecolor=PINK_FILL, alpha=0.3)
ax.add_patch(email_panel)
ax.text(14, 17.5, "MCP Email Write Server", ha="center", va="center",
        fontsize=9, fontweight="bold", color=PINK_STROKE)
box(14, 15, 18, 1.7, "send_email", "white", PINK_STROKE, fs=7)

# Main flow arrows (vertical chain on main column)
arrow(CX, 89.25, CX, 87.75)
arrow(CX, 84.25, CX, 82.75)
arrow(CX, 79.25, CX, 77.75)
arrow(CX, 74.25, CX, 72.75)
arrow(CX, 69.25, CX, 67)

# Policy → Confidence (no risk)
arrow(CX, 61, CX, 58, label="no risk", label_pos=(CX + 5, 59.5))

# Policy → Slack Notification (risk)
arrow(CX + 13, 64, CX_HITL - 12, 68,
      color=RED_ARROW, label="risk",
      label_pos=(55, 66.5))

# Confidence → Slack Notification (low conf)
arrow(CX + 13, 55, CX_HITL - 12, 67,
      color=RED_ARROW, label="low conf",
      label_pos=(55, 62),
      connectionstyle="angle3,angleA=0,angleB=90")

# Confidence → Finalize (auto-send, both gates pass + safe intent)
arrow(CX, 52, CX, 25.25,
      color=GREEN_ARROW,
      label="above + safe intent\n(FAQ / info / basic_technical)",
      label_pos=(CX - 7, 38))

# Slack Notification → Interrupt Gate (vertical)
arrow(CX_HITL, 65.5, CX_HITL, 62.25)

# Interrupt → Approval Buttons (vertical)
arrow(CX_HITL, 57.75, CX_HITL, 54.25)

# Approval → Manual Queue (reject count >= 3)
arrow(CX_HITL + 11, 52, 84, 52,
      color=RED_ARROW,
      label="reject (>= 3)",
      label_pos=(82, 53.5))

# Approval → Draft loopback (reject < 3, redraft with reason)
arrow(CX_HITL - 11, 52, CX + 11, 71,
      color=RED_ARROW, dashed=True,
      label="reject (< 3)\n+ reason → redraft",
      label_pos=(48, 61),
      connectionstyle="arc3,rad=-0.3")

# Approval → Elapsed (approve/edit)
arrow(CX_HITL, 49.75, CX_HITL, 47,
      label="approve / edit", label_pos=(CX_HITL + 8, 48.5))

# Elapsed → Finalize (no, fast approval)
arrow(CX_HITL - 11, 44, CX + 11, 25,
      label="no (fast path)", label_pos=(57, 35))

# Elapsed → Revalidate (yes)
arrow(CX_HITL, 41, CX_HITL, 39,
      label="yes", label_pos=(CX_HITL + 4, 40))

# Revalidate → Finalize (unchanged)
arrow(CX_HITL - 11, 36, CX + 11, 22,
      label="unchanged", label_pos=(55, 28))

# Revalidate → Interrupt loop-back (changed — Summarize Changes posts delta on same msg)
arrow(CX_HITL + 11, 36, CX_HITL + 11, 58,
      color=PURPLE_ARROW, dashed=True,
      label="changed →\nSummarize delta\non same Slack msg,\nre-interrupt",
      label_pos=(92, 47),
      connectionstyle="arc3,rad=0.35")

# MCP call arrows
# Read MCP — called by Retrieve Context
arrow(CX - 11, 76, 25, 87,
      color=CYAN_ARROW, dashed=True,
      label="calls", label_pos=(30, 82))

# Email Write MCP — called by Send Response (now adjacent on the left)
arrow(CX - 11, 16, 25, 16,
      color=PINK_ARROW, dashed=True,
      label="calls", label_pos=(31, 17))

# Slack Write MCP — called by Slack Notification (post_approval_request, update_message)
arrow(CX_HITL - 12, 68, 25, 76,
      color=GOLD_ARROW, dashed=True,
      label="calls", label_pos=(48, 73))

# Bottom legend
ax.text(50, 3,
        "Two-gate routing: Policy Risk (refund / angry / policy match) > Confidence (>= 0.85). "
        "Auto-send only when both pass AND intent is safe.\n"
        "Slack post happens BEFORE Interrupt — LangGraph correctness rule. "
        "Three MCP servers split by capability — bounded blast radius for prompt injection.\n"
        "Reject < 3 → redraft with reason. Reject >= 3 → Manual Queue. "
        "Long pause + context drift → delta posted on same Slack message, re-interrupt.",
        ha="center", va="center", fontsize=8.5, color="#444",
        bbox=dict(boxstyle="round,pad=0.6",
                  facecolor="#f5f5f5", edgecolor="#ddd"))

plt.tight_layout()
out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "hitl-flow.png")
plt.savefig(out_path, dpi=120, bbox_inches="tight", facecolor="white")
print(f"Saved {out_path}")
