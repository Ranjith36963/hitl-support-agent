"""Generate a detailed .excalidraw flow chart matching architecture.md."""
import json
import random
import time

random.seed(42)
NOW = int(time.time() * 1000)


def seed():
    return random.randint(1, 2_000_000_000)


def base(elem_id, elem_type, x, y, w, h, **kw):
    return {
        "id": elem_id,
        "type": elem_type,
        "x": x,
        "y": y,
        "width": w,
        "height": h,
        "angle": 0,
        "strokeColor": kw.get("strokeColor", "#1e1e1e"),
        "backgroundColor": kw.get("backgroundColor", "transparent"),
        "fillStyle": kw.get("fillStyle", "solid"),
        "strokeWidth": kw.get("strokeWidth", 2),
        "strokeStyle": kw.get("strokeStyle", "solid"),
        "roughness": 1,
        "opacity": kw.get("opacity", 100),
        "groupIds": [],
        "frameId": None,
        "roundness": kw.get("roundness"),
        "seed": seed(),
        "versionNonce": seed(),
        "isDeleted": False,
        "boundElements": kw.get("boundElements"),
        "updatedAt": NOW,
        "link": None,
        "locked": False,
    }


def shape(elem_id, elem_type, x, y, w, h, label=None, label_size=16, **kw):
    elements = []
    bound = []
    if label:
        bound.append({"type": "text", "id": f"{elem_id}_lbl"})
    kw["boundElements"] = bound if bound else None
    if elem_type == "rectangle":
        kw.setdefault("roundness", {"type": 3})
    elements.append(base(elem_id, elem_type, x, y, w, h, **kw))
    if label:
        text_w = max(len(label) * label_size * 0.5, 50)
        text_h = label_size * 1.25
        t = base(
            f"{elem_id}_lbl", "text",
            x + (w - text_w) / 2, y + (h - text_h) / 2,
            text_w, text_h,
            strokeColor="#1e1e1e",
        )
        t.update({
            "fontSize": label_size,
            "fontFamily": 1,
            "text": label,
            "textAlign": "center",
            "verticalAlign": "middle",
            "containerId": elem_id,
            "originalText": label,
            "lineHeight": 1.25,
            "baseline": int(label_size * 0.85),
        })
        elements.append(t)
    return elements


def text(elem_id, x, y, content, size=16, color="#1e1e1e"):
    text_w = max(len(content) * size * 0.55, 50)
    t = base(elem_id, "text", x, y, text_w, size * 1.25, strokeColor=color)
    t.update({
        "fontSize": size,
        "fontFamily": 1,
        "text": content,
        "textAlign": "left",
        "verticalAlign": "top",
        "containerId": None,
        "originalText": content,
        "lineHeight": 1.25,
        "baseline": int(size * 0.85),
    })
    return [t]


def arrow(elem_id, x1, y1, x2, y2, color="#1e1e1e",
          label=None, dashed=False):
    dx, dy = x2 - x1, y2 - y1
    elements = []
    bound = [{"type": "text", "id": f"{elem_id}_lbl"}] if label else None
    a = base(
        elem_id, "arrow",
        x1, y1,
        max(abs(dx), 1), max(abs(dy), 1),
        strokeColor=color,
        strokeStyle="dashed" if dashed else "solid",
        boundElements=bound,
    )
    a.update({
        "points": [[0, 0], [dx, dy]],
        "lastCommittedPoint": None,
        "startBinding": None,
        "endBinding": None,
        "startArrowhead": None,
        "endArrowhead": "arrow",
        "elbowed": False,
    })
    elements.append(a)
    if label:
        size = 13
        text_w = max(len(label) * size * 0.55, 30)
        text_h = size * 1.25
        t = base(
            f"{elem_id}_lbl", "text",
            x1 + dx / 2 - text_w / 2,
            y1 + dy / 2 - text_h / 2,
            text_w, text_h,
            strokeColor=color,
        )
        t.update({
            "fontSize": size,
            "fontFamily": 1,
            "text": label,
            "textAlign": "center",
            "verticalAlign": "middle",
            "containerId": elem_id,
            "originalText": label,
            "lineHeight": 1.25,
            "baseline": int(size * 0.85),
        })
        elements.append(t)
    return elements


elements = []

# Title
elements += text("title", 350, 20,
                 "HITL Customer Support Agent — Detailed Flow", size=24)
elements += text("subtitle", 320, 55,
                 "Policy + Confidence gates, elapsed-time revalidation, "
                 "idempotent send",
                 size=14, color="#757575")

# MCP server panel (left)
elements += shape("mcp_box", "rectangle", 40, 410, 230, 200,
                  backgroundColor="#b2f2bb", strokeColor="#15803d",
                  opacity=40)
elements += text("mcp_title", 80, 425, "Custom MCP Server",
                 size=18, color="#15803d")
elements += shape("mcp1", "rectangle", 60, 460, 190, 38,
                  backgroundColor="#ffffff", strokeColor="#15803d",
                  label="get_customer_history", label_size=14)
elements += shape("mcp2", "rectangle", 60, 510, 190, 38,
                  backgroundColor="#ffffff", strokeColor="#15803d",
                  label="get_kb_article", label_size=14)
elements += shape("mcp3", "rectangle", 60, 560, 190, 38,
                  backgroundColor="#ffffff", strokeColor="#15803d",
                  label="send_email", label_size=14)

# Main flow nodes
CX = 460  # x of left edge of main column
W_BOX, H_BOX = 240, 50

elements += shape("n1", "rectangle", CX, 100, W_BOX, H_BOX,
                  backgroundColor="#a5d8ff", strokeColor="#2563eb",
                  label="1. Ticket In", label_size=18)
elements += arrow("a1", CX + W_BOX / 2, 150, CX + W_BOX / 2, 175)

elements += shape("n2", "rectangle", CX, 175, W_BOX, H_BOX,
                  backgroundColor="#d0bfff", strokeColor="#8b5cf6",
                  label="2. PII Redact (middleware)", label_size=16)
elements += arrow("a2", CX + W_BOX / 2, 225, CX + W_BOX / 2, 250)

elements += shape("n3", "rectangle", CX, 250, W_BOX, H_BOX,
                  backgroundColor="#a5d8ff", strokeColor="#2563eb",
                  label="3. Classify Intent", label_size=18)
elements += arrow("a3", CX + W_BOX / 2, 300, CX + W_BOX / 2, 325)

elements += shape("n4", "rectangle", CX, 325, W_BOX, H_BOX,
                  backgroundColor="#a5d8ff", strokeColor="#2563eb",
                  label="4. Retrieve Context", label_size=18)
# arrow to MCP get_customer_history
elements += arrow("mcp_a1", CX, 350, 270, 479,
                  color="#15803d", dashed=True, label="calls")
elements += arrow("a4", CX + W_BOX / 2, 375, CX + W_BOX / 2, 400)

elements += shape("n5", "rectangle", CX, 400, W_BOX, H_BOX,
                  backgroundColor="#a5d8ff", strokeColor="#2563eb",
                  label="5. Draft Response", label_size=18)
elements += arrow("a5", CX + W_BOX / 2, 450, CX + W_BOX / 2, 475)

# Policy Risk Check diamond
elements += shape("n6", "diamond", CX, 475, W_BOX, 90,
                  backgroundColor="#fff3bf", strokeColor="#f59e0b",
                  label="6. Policy Risk Check", label_size=16)

# Policy → Interrupt (risk)
elements += arrow("a6_risk", CX + W_BOX, 520, 720, 520,
                  color="#ef4444", label="risk")
# Policy → Confidence (no risk)
elements += arrow("a6_no_risk", CX + W_BOX / 2, 565, CX + W_BOX / 2, 595,
                  label="no risk")

# Confidence Check diamond
elements += shape("n7", "diamond", CX, 595, W_BOX, 90,
                  backgroundColor="#fff3bf", strokeColor="#f59e0b",
                  label="7. Confidence Check (>=0.85)", label_size=14)

# Confidence → Interrupt (low conf)
elements += arrow("a7_low", CX + W_BOX, 640, 720, 540,
                  color="#ef4444", label="low conf")
# Confidence → Finalize (above threshold) - long down
elements += arrow("a7_pass", CX + W_BOX / 2, 685, CX + W_BOX / 2, 1100,
                  color="#22c55e", label="above threshold")

# HITL branch column
HX = 720
elements += shape("h1", "rectangle", HX, 495, W_BOX, 50,
                  backgroundColor="#ffc9c9", strokeColor="#dc2626",
                  label="8. Interrupt Gate", label_size=18)
elements += text("h1_note", HX + 25, 548,
                 "execution pauses (state already persisted)",
                 size=12, color="#dc2626")

elements += arrow("ha1", HX + W_BOX / 2, 568, HX + W_BOX / 2, 595)

elements += shape("h2", "rectangle", HX, 595, W_BOX, 50,
                  backgroundColor="#ffd8a8", strokeColor="#d97706",
                  label="9. Approval UI", label_size=18)
elements += text("h2_note", HX + 30, 648,
                 "shows risk breakdown + Approve/Edit/Reject",
                 size=12, color="#d97706")

# UI → Manual Queue (reject)
elements += arrow("h_reject", HX + W_BOX, 620, 1010, 620,
                  color="#ef4444", label="reject")

# Manual Queue terminal
elements += shape("mq", "rectangle", 1010, 595, 170, 50,
                  backgroundColor="#c3fae8", strokeColor="#15803d",
                  label="Manual Queue", label_size=15)
elements += text("mq_note", 1015, 648,
                 "(human agent, customer notified)",
                 size=11, color="#15803d")

# UI → Elapsed (approve/edit)
elements += arrow("h_approve", HX + W_BOX / 2, 668, HX + W_BOX / 2, 700,
                  label="approve / edit")

# Elapsed > 15min diamond
elements += shape("h3", "diamond", HX, 700, W_BOX, 90,
                  backgroundColor="#fff3bf", strokeColor="#f59e0b",
                  label="10. Elapsed > 15min?", label_size=14)

# Elapsed → Finalize (no - fast)
elements += arrow("h_fast", HX, 745, CX + W_BOX, 1110,
                  label="no (fast)")

# Elapsed → Revalidate (yes)
elements += arrow("h_yes", HX + W_BOX / 2, 790, HX + W_BOX / 2, 820,
                  label="yes")

# Revalidate diamond
elements += shape("h4", "diamond", HX, 820, W_BOX, 90,
                  backgroundColor="#fff3bf", strokeColor="#f59e0b",
                  label="11. Revalidate context_hash", label_size=13)

# Revalidate → Finalize (unchanged)
elements += arrow("h_unchanged", HX, 865, CX + W_BOX, 1135,
                  label="unchanged")

# Revalidate → Draft (loopback - hash changed)
elements += arrow("h_stale", HX + W_BOX, 865, CX + W_BOX, 425,
                  color="#8b5cf6", dashed=True,
                  label="hash changed -> redraft")

# Convergence column - Finalize, Send, Audit
elements += shape("n12", "rectangle", CX, 1100, W_BOX, 60,
                  backgroundColor="#a5d8ff", strokeColor="#2563eb",
                  label="12. Finalize Action (PII restore)",
                  label_size=14)
elements += arrow("a12", CX + W_BOX / 2, 1160, CX + W_BOX / 2, 1185)

elements += shape("n13", "rectangle", CX, 1185, W_BOX, 60,
                  backgroundColor="#a5d8ff", strokeColor="#2563eb",
                  label="13. Send Response (idempotent)",
                  label_size=14)
# Send → MCP send_email
elements += arrow("mcp_a2", CX, 1215, 270, 579,
                  color="#15803d", dashed=True, label="calls")
elements += arrow("a13", CX + W_BOX / 2, 1245, CX + W_BOX / 2, 1270)

elements += shape("n14", "rectangle", CX, 1270, W_BOX, 60,
                  backgroundColor="#c3fae8", strokeColor="#15803d",
                  label="14. Audit log + trace",
                  label_size=15)


doc = {
    "type": "excalidraw",
    "version": 2,
    "source": "hitl-support-agent",
    "elements": elements,
    "appState": {
        "gridSize": None,
        "viewBackgroundColor": "#ffffff",
    },
    "files": {},
}

with open("hitl-flow.excalidraw", "w", encoding="utf-8") as f:
    json.dump(doc, f, indent=2)

print(f"Generated hitl-flow.excalidraw with {len(elements)} elements")
