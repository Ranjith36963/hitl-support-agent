"""Render the detailed HITL flow as a PNG image.

Matches the detailed Mermaid flow in docs/architecture.md:
- Policy Risk Check + Confidence Check as separate gates
- Elapsed > 15min check before Revalidate
- Finalize Action separate from Send (idempotency boundary)
- Manual Queue terminal for rejection path
- Stale-context loopback to Draft
"""
import os
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Polygon

fig, ax = plt.subplots(figsize=(14, 19), dpi=110)
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
GREEN_PANEL = "#b2f2bb"
GREEN_ARROW = "#22c55e"
RED_ARROW = "#ef4444"
PURPLE_ARROW = "#8b5cf6"


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
ax.text(50, 95.5, "Policy + Confidence gates, elapsed-time revalidation, idempotent send",
        ha="center", va="center", fontsize=11, color="#757575")

CX = 38  # main column center
W_BOX, H_BOX = 22, 3.5

# Top sequential nodes
box(CX, 91, W_BOX, H_BOX, "1. Ticket In", BLUE_FILL, BLUE_STROKE)
box(CX, 86, W_BOX, H_BOX, "2. PII Redact (middleware)",
    PURPLE_FILL, PURPLE_STROKE, fs=9)
box(CX, 81, W_BOX, H_BOX, "3. Classify Intent", BLUE_FILL, BLUE_STROKE)
box(CX, 76, W_BOX, H_BOX, "4. Retrieve Context", BLUE_FILL, BLUE_STROKE)
box(CX, 71, W_BOX, H_BOX, "5. Draft Response", BLUE_FILL, BLUE_STROKE)

# Routing diamonds
diamond(CX, 64, 26, 6, "6. Policy Risk Check",
        YELLOW_FILL, YELLOW_STROKE, fs=10)
diamond(CX, 55, 26, 6, "7. Confidence Check\n(>= 0.85)",
        YELLOW_FILL, YELLOW_STROKE, fs=9)

# Convergence: Finalize, Send, Audit
box(CX, 23, W_BOX, 4.5,
    "12. Finalize Action\n(PII restore + payload)",
    BLUE_FILL, BLUE_STROKE, fs=9)
box(CX, 16, W_BOX, 4.5,
    "13. Send Response\n(idempotent on key)",
    BLUE_FILL, BLUE_STROKE, fs=9)
box(CX, 9, W_BOX, 4.5,
    "14. Audit log + trace metadata",
    GREEN_FILL, GREEN_STROKE, fs=9)

# HITL branch column
CX_HITL = 72
box(CX_HITL, 64, 22, 4.5, "8. Interrupt Gate\n(execution pauses)",
    RED_FILL, RED_STROKE, fs=9)
box(CX_HITL, 55, 22, 4.5,
    "9. Approval UI\n(risk breakdown shown)",
    ORANGE_FILL, ORANGE_STROKE, fs=9)
diamond(CX_HITL, 46, 22, 6,
        "10. Elapsed > 15min?",
        YELLOW_FILL, YELLOW_STROKE, fs=9)
diamond(CX_HITL, 37, 22, 6,
        "11. Revalidate\ncontext_hash",
        YELLOW_FILL, YELLOW_STROKE, fs=9)

# Manual Queue (rejection terminal)
box(91, 55, 14, 4.5, "Manual Queue\n(human agent)",
    GREEN_FILL, GREEN_STROKE, fs=8)

# MCP panel
panel = FancyBboxPatch((4, 71), 22, 13,
                       boxstyle="round,pad=0.3,rounding_size=0.8",
                       linewidth=1.5, edgecolor=GREEN_STROKE,
                       facecolor=GREEN_PANEL, alpha=0.5)
ax.add_patch(panel)
ax.text(15, 82.5, "Custom MCP Server", ha="center", va="center",
        fontsize=10, fontweight="bold", color=GREEN_STROKE)
box(15, 79, 18, 2.2, "get_customer_history", "white", GREEN_STROKE, fs=8)
box(15, 76, 18, 2.2, "get_kb_article", "white", GREEN_STROKE, fs=8)
box(15, 73, 18, 2.2, "send_email", "white", GREEN_STROKE, fs=8)

# Main flow arrows
arrow(CX, 89.25, CX, 87.75)
arrow(CX, 84.25, CX, 82.75)
arrow(CX, 79.25, CX, 77.75)
arrow(CX, 74.25, CX, 72.75)
arrow(CX, 69.25, CX, 67)

arrow(CX, 61, CX, 58, label="no risk", label_pos=(CX + 4, 59.5))

# Policy → Interrupt (risk)
arrow(CX + 13, 64, CX_HITL - 11, 64,
      color=RED_ARROW, label="risk", label_pos=(55, 65.5))

# Confidence → Interrupt (low conf) - elbow
arrow(CX + 13, 55, CX_HITL - 11, 62,
      color=RED_ARROW, label="low conf", label_pos=(55, 60),
      connectionstyle="angle3,angleA=0,angleB=90")

# Confidence → Finalize (above threshold) - long down
arrow(CX, 52, CX, 25.25,
      color=GREEN_ARROW, label="above threshold",
      label_pos=(CX - 7, 38))

# Interrupt → UI
arrow(CX_HITL, 61.75, CX_HITL, 57.25)

# UI → Manual Queue (reject)
arrow(CX_HITL + 11, 55, 84, 55,
      color=RED_ARROW, label="reject", label_pos=(80, 56.5))

# UI → Elapsed (approve/edit)
arrow(CX_HITL, 52.75, CX_HITL, 49,
      label="approve/edit", label_pos=(CX_HITL + 7, 51))

# Elapsed → Finalize (no — fast approval)
arrow(CX_HITL - 11, 46, CX + 11, 25,
      label="no (fast)", label_pos=(58, 37))

# Elapsed → Revalidate (yes)
arrow(CX_HITL, 43, CX_HITL, 40,
      label="yes", label_pos=(CX_HITL + 4, 41.5))

# Revalidate → Finalize (unchanged)
arrow(CX_HITL - 11, 37, CX + 11, 22,
      label="unchanged", label_pos=(56, 28))

# Revalidate → Draft (loopback - hash changed) — long sweep
arrow(CX_HITL + 11, 37, CX, 71,
      color=PURPLE_ARROW, dashed=True,
      label="hash changed → redraft",
      label_pos=(85, 56),
      connectionstyle="arc3,rad=0.5")

# Finalize → Send → Audit
arrow(CX, 20.75, CX, 18.25)
arrow(CX, 13.75, CX, 11.25)

# MCP arrows
arrow(CX - 11, 76, 26, 76,
      color=GREEN_ARROW, dashed=True,
      label="calls", label_pos=(31, 77))

arrow(CX - 11, 16, 24, 73,
      color=GREEN_ARROW, dashed=True,
      label="calls", label_pos=(20, 30),
      connectionstyle="arc3,rad=-0.4")

# Bottom legend
ax.text(50, 3,
        "Step 6 = policy gate.  Step 7 = confidence gate.  "
        "Step 10 controls revalidation.  "
        "Step 11 loops stale-context drafts back to Step 5.\n"
        "Reject path → Manual Queue (customer notified).  "
        "Idempotency on Step 13 prevents double-send across retries.",
        ha="center", va="center", fontsize=9, color="#444",
        bbox=dict(boxstyle="round,pad=0.6",
                  facecolor="#f5f5f5", edgecolor="#ddd"))

plt.tight_layout()
out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "hitl-flow.png")
plt.savefig(out_path, dpi=120, bbox_inches="tight", facecolor="white")
print(f"Saved {out_path}")
