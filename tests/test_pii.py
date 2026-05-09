"""TDD tests for src/pii.py — redact/restore round-trip.

TDD discipline: tests written before implementation.
Each block: one failing test → minimal code to pass → next test.
"""

from src.pii import redact, restore

# ---------------------------------------------------------------------------
# Basic email redaction
# ---------------------------------------------------------------------------

def test_redact_single_email():
    """Redacting a plain email replaces it with a token."""
    text = "Contact me at alice@example.com for details."
    redacted, token_map = redact(text)
    assert "alice@example.com" not in redacted
    assert "[EMAIL_1]" in redacted


def test_restore_single_email():
    """Restore brings back the original email from the token map."""
    text = "Contact me at alice@example.com for details."
    redacted, token_map = redact(text)
    restored = restore(redacted, token_map)
    assert restored == text


def test_round_trip_preserves_full_string():
    """restore(redact(t)) == t for any string containing an email."""
    original = "Hello, please email bob@acme.io or call us."
    redacted, token_map = redact(original)
    assert restore(redacted, token_map) == original


# ---------------------------------------------------------------------------
# Token stability — same email → same token within one redact call
# ---------------------------------------------------------------------------

def test_same_email_same_token():
    """If the same email appears twice, it must get the same token."""
    text = "alice@example.com wrote to alice@example.com again."
    redacted, token_map = redact(text)
    # Both occurrences should be the same token
    assert redacted.count("[EMAIL_1]") == 2


# ---------------------------------------------------------------------------
# Multiple distinct emails → distinct tokens
# ---------------------------------------------------------------------------

def test_two_distinct_emails_distinct_tokens():
    """Two different emails must get different tokens."""
    text = "From alice@example.com to bob@corp.net about billing."
    redacted, token_map = redact(text)
    assert "[EMAIL_1]" in redacted
    assert "[EMAIL_2]" in redacted
    assert "alice@example.com" not in redacted
    assert "bob@corp.net" not in redacted


# ---------------------------------------------------------------------------
# Phone number redaction
# ---------------------------------------------------------------------------

def test_redact_phone_us_format():
    """US-format phone numbers are replaced with [PHONE_1]."""
    text = "Call us at 555-867-5309 or text us."
    redacted, token_map = redact(text)
    assert "555-867-5309" not in redacted
    assert "[PHONE_1]" in redacted


def test_restore_phone():
    """Restored text returns the original phone number."""
    text = "Call us at 555-867-5309 anytime."
    redacted, token_map = redact(text)
    assert restore(redacted, token_map) == text


def test_redact_phone_dotted_format():
    """Phones in 555.867.5309 dot-format are also redacted."""
    text = "Reach me at 555.867.5309."
    redacted, token_map = redact(text)
    assert "555.867.5309" not in redacted
    assert "[PHONE_1]" in redacted


def test_redact_phone_parentheses_format():
    """Phones in (555) 867-5309 format are also redacted."""
    text = "My number is (555) 867-5309."
    redacted, token_map = redact(text)
    assert "(555) 867-5309" not in redacted
    assert "[PHONE_1]" in redacted


# ---------------------------------------------------------------------------
# Credit card redaction
# ---------------------------------------------------------------------------

def test_redact_credit_card():
    """16-digit card numbers are replaced with [CC_1]."""
    text = "My card is 4111 1111 1111 1111 please charge it."
    redacted, token_map = redact(text)
    assert "4111 1111 1111 1111" not in redacted
    assert "[CC_1]" in redacted


def test_restore_credit_card():
    """Restored text recovers the original card number."""
    text = "Charge card 4111 1111 1111 1111 for $99."
    redacted, token_map = redact(text)
    assert restore(redacted, token_map) == text


def test_redact_credit_card_no_spaces():
    """16-digit card without spaces is also redacted."""
    text = "Card: 4111111111111111."
    redacted, token_map = redact(text)
    assert "4111111111111111" not in redacted
    assert "[CC_1]" in redacted


# ---------------------------------------------------------------------------
# Mixed PII — all three types together
# ---------------------------------------------------------------------------

def test_mixed_pii_all_types():
    """Redact handles email, phone, and CC in one pass."""
    text = (
        "Email alice@example.com, call 555-867-5309, "
        "charge card 4111 1111 1111 1111."
    )
    redacted, token_map = redact(text)
    assert "alice@example.com" not in redacted
    assert "555-867-5309" not in redacted
    assert "4111 1111 1111 1111" not in redacted
    assert "[EMAIL_1]" in redacted
    assert "[PHONE_1]" in redacted
    assert "[CC_1]" in redacted


def test_mixed_pii_round_trip():
    """Full round-trip on mixed PII text."""
    text = (
        "Email alice@example.com or bob@corp.net. "
        "Call 555-867-5309. Card: 4111 1111 1111 1111."
    )
    redacted, token_map = redact(text)
    assert restore(redacted, token_map) == text


# ---------------------------------------------------------------------------
# No PII — text passes through unchanged
# ---------------------------------------------------------------------------

def test_no_pii_text_unchanged():
    """Text with no PII is returned as-is with an empty token map."""
    text = "Hello, I need help with my account settings."
    redacted, token_map = redact(text)
    assert redacted == text
    assert token_map == {}


def test_empty_string():
    """Empty string is a valid input — returns empty, empty map."""
    redacted, token_map = redact("")
    assert redacted == ""
    assert token_map == {}
    assert restore(redacted, token_map) == ""


# ---------------------------------------------------------------------------
# Token uniqueness — token map keys are all unique
# ---------------------------------------------------------------------------

def test_token_map_keys_unique():
    """Each PII item gets a unique token key in the map."""
    text = (
        "alice@example.com, bob@corp.net, 555-867-5309, "
        "4111 1111 1111 1111"
    )
    _, token_map = redact(text)
    keys = list(token_map.keys())
    assert len(keys) == len(set(keys)), "Token map keys must be unique"


def test_token_map_values_are_originals():
    """Token map values are the original PII strings."""
    text = "Email me at alice@example.com."
    _, token_map = redact(text)
    assert "alice@example.com" in token_map.values()


# ---------------------------------------------------------------------------
# Two identical emails + one CC + one phone — advisor's stress test
# ---------------------------------------------------------------------------

def test_two_same_emails_plus_cc_plus_phone():
    """
    Same email twice + CC + phone: tokens stable, no collisions, round-trip
    correct. This is the advisor's required composite test.
    """
    text = (
        "Customer alice@example.com (also CC alice@example.com) "
        "paid with 4111 1111 1111 1111. "
        "Phone: (555) 867-5309."
    )
    redacted, token_map = redact(text)
    restored = restore(redacted, token_map)

    assert restored == text  # full round-trip
    # alice appears twice but should map to one token
    assert redacted.count("[EMAIL_1]") == 2
    # exactly one CC token and one phone token
    assert "[CC_1]" in redacted
    assert "[PHONE_1]" in redacted
    # No raw PII in redacted
    assert "alice@example.com" not in redacted
    assert "4111 1111 1111 1111" not in redacted
    assert "(555) 867-5309" not in redacted
