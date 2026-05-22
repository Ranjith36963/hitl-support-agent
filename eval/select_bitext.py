"""Bitext row selectors — pick one real Bitext message per intent.

Two eval sets are produced (both reproducible, deterministic — first clean row
in dataset order):

  data/bitext_eval_10.csv  — 10 SaaS-mappable intents (the focused first batch)
  data/bitext_eval_27.csv  — all 27 Bitext intents, one each (breadth set)

HONEST NOTE: Bitext is an e-commerce / order-support dataset. ~10 of its 27
intents map cleanly to a SaaS support agent; the rest (order/shipping/delivery)
are out-of-domain and are mapped best-effort — several land on the "other"
intent. Rows for those intents test the classifier on input it was never
designed for; read the 27-intent results with that in mind. The per-row
`mapping_rationale` column flags the e-commerce intents with "[e-commerce]".

Run once:  python -m eval.select_bitext
"""

from __future__ import annotations

import csv
from pathlib import Path

from datasets import load_dataset

# intent -> (project intent label, expected outcome, one-line rationale).
# All mappings are hand-authored judgement calls — disclosed, not hidden.

SELECTED_10: dict[str, tuple[str, str, str]] = {
    "recover_password": ("FAQ", "auto_send", "password reset — safe self-service FAQ"),
    "newsletter_subscription": ("FAQ", "auto_send", "newsletter signup — safe FAQ"),
    "check_payment_methods": ("FAQ", "auto_send", "which payment methods — informational FAQ"),
    "create_account": ("basic_technical", "auto_send", "how to create an account — basic how-to"),
    "get_refund": ("refund", "escalated", "refund request — money, Gate 1 escalates"),
    "check_refund_policy": ("refund", "escalated", "refund-policy question — refund mention escalates"),
    "track_refund": ("refund", "escalated", "refund status — refund mention escalates"),
    "payment_issue": ("billing", "escalated", "payment problem — billing dispute escalates"),
    "complaint": ("complaint", "escalated", "complaint — angry/edge-case escalates"),
    "contact_human_agent": ("other", "escalated", "explicit human request — escalate by design"),
}

# All 27 Bitext intents. The 8 clearly e-commerce / out-of-domain intents are
# tagged "[e-commerce]" in the rationale so the results are read honestly.
SELECTED_27: dict[str, tuple[str, str, str]] = {
    # --- cleanly SaaS-mappable ---
    "recover_password": ("FAQ", "auto_send", "password reset — self-service FAQ"),
    "newsletter_subscription": ("FAQ", "auto_send", "newsletter management — FAQ"),
    "check_payment_methods": ("info", "auto_send", "which payment methods — factual info"),
    "create_account": ("basic_technical", "auto_send", "account-creation how-to"),
    "edit_account": ("FAQ", "auto_send", "account-detail change — FAQ"),
    "switch_account": ("FAQ", "auto_send", "account switching — FAQ"),
    "delete_account": ("other", "escalated", "account deletion — sensitive, escalate"),
    "registration_problems": ("technical", "escalated", "signup is broken — technical"),
    "get_refund": ("refund", "escalated", "refund request — money"),
    "check_refund_policy": ("refund", "escalated", "refund-policy question — refund mention"),
    "track_refund": ("refund", "escalated", "refund status — refund mention"),
    "payment_issue": ("billing", "escalated", "payment failure — billing"),
    "check_invoice": ("billing", "escalated", "invoice query — billing"),
    "get_invoice": ("billing", "escalated", "invoice request — billing"),
    "complaint": ("complaint", "escalated", "complaint — edge-case escalates"),
    "review": ("other", "escalated", "leaving a review — edge-case 'other'"),
    "contact_human_agent": ("other", "escalated", "explicit human request"),
    "contact_customer_service": ("other", "escalated", "request to reach support"),
    # --- e-commerce / out-of-domain for a SaaS agent (best-effort mapping) ---
    "cancel_order": ("billing", "escalated", "order cancellation — money [e-commerce]"),
    "change_order": ("other", "escalated", "order change — no SaaS equivalent [e-commerce]"),
    "check_cancellation_fee": ("billing", "escalated", "cancellation fee — money [e-commerce]"),
    "place_order": ("other", "escalated", "placing an order — no SaaS equivalent [e-commerce]"),
    "track_order": ("other", "escalated", "order tracking — no SaaS equivalent [e-commerce]"),
    "delivery_options": ("info", "auto_send", "delivery options — info [e-commerce]"),
    "delivery_period": ("info", "auto_send", "delivery timing — info [e-commerce]"),
    "change_shipping_address": ("other", "escalated", "shipping address — no SaaS equivalent [e-commerce]"),
    "set_up_shipping_address": ("other", "escalated", "shipping setup — no SaaS equivalent [e-commerce]"),
}

_REPO_ROOT = Path(__file__).parent.parent


def _is_clean(text: str) -> bool:
    """A usable row: no template placeholders, reasonable length."""
    return "{{" not in text and "}}" not in text and 20 <= len(text) <= 220


def _select(ds, intent_map: dict[str, tuple[str, str, str]], out_path: Path) -> None:
    """Pick one clean row per intent in *intent_map*, write CSV to *out_path*."""
    picked: dict[str, dict[str, str]] = {}
    for row in ds:
        intent = row["intent"]
        if intent in intent_map and intent not in picked and _is_clean(row["instruction"]):
            picked[intent] = {
                "bitext_intent": intent,
                "category": row["category"],
                "instruction": row["instruction"].strip(),
            }
        if len(picked) == len(intent_map):
            break

    missing = set(intent_map) - set(picked)
    if missing:
        raise SystemExit(f"No clean row found for intents: {sorted(missing)}")

    # Deterministic dev/test split by SHA-256 of bitext_intent. Only the
    # 27-intent breadth set has a split column populated; the 10-row file
    # leaves it empty (caller treats absent column as "all"). Lowest 7 hashes
    # → dev, remaining → test. Reproducible from the intent name alone, so
    # `python -m eval.select_bitext` produces the same partition every time.
    import hashlib

    def _split_for(intent: str, dev_set: set[str]) -> str:
        return "dev" if intent in dev_set else "test"

    if len(intent_map) > 10:  # the 27 breadth set
        def _hash_rank(name: str) -> int:
            return int(hashlib.sha256(name.encode()).hexdigest(), 16)
        ranked = sorted(intent_map, key=_hash_rank)
        dev_set = set(ranked[:7])
    else:
        dev_set = set()  # no split column for the 10-row file

    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        header = ["bitext_intent", "category", "instruction",
                  "project_intent", "expected_outcome", "mapping_rationale"]
        if dev_set:
            header.append("split")
        writer.writerow(header)
        for intent in intent_map:  # stable order
            rec = picked[intent]
            proj, outcome, rationale = intent_map[intent]
            row = [rec["bitext_intent"], rec["category"], rec["instruction"],
                   proj, outcome, rationale]
            if dev_set:
                row.append(_split_for(intent, dev_set))
            writer.writerow(row)
    print(f"Wrote {len(picked)} rows -> {out_path}")


def main() -> None:
    ds = load_dataset(
        "bitext/Bitext-customer-support-llm-chatbot-training-dataset", split="train"
    )
    _select(ds, SELECTED_10, _REPO_ROOT / "data" / "bitext_eval_10.csv")
    _select(ds, SELECTED_27, _REPO_ROOT / "data" / "bitext_eval_27.csv")


if __name__ == "__main__":
    main()
