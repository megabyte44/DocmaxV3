"""Consent: the record that stands between a document and someone else's computer.

Two properties carry most of the weight here, and both are tested by trying to
break them:

* **It fails closed.** Corrupt, unreadable, or from the future all mean "no
  consent". Being wrong that way costs a prompt; being wrong the other way
  uploads a document nobody agreed to send.
* **It is scoped.** A grant is for one tool at one endpoint under one version of
  the terms. Change any of the three and it stops applying.

See ADR 0008 for why each rule is the way it is.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from docmax.core.consent import (
    CONSENT_TERMS_VERSION,
    SCHEMA_VERSION,
    ConsentGrant,
    ConsentStore,
)

ENDPOINT = "https://api.example.com"
OTHER_ENDPOINT = "https://other.example.com"


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "consent.json"


@pytest.fixture
def store(path: Path) -> ConsentStore:
    return ConsentStore(path, endpoint=ENDPOINT)


# ---------------------------------------------------------------------------
# Recording and asking
# ---------------------------------------------------------------------------


def test_nothing_is_consented_by_default(store: ConsentStore) -> None:
    assert store.has("ocr") is False
    assert store.granted_tools() == ()


def test_recording_grants_consent(store: ConsentStore) -> None:
    store.record("ocr")

    assert store.has("ocr") is True
    assert store.granted_tools() == ("ocr",)


def test_consent_is_per_tool(store: ConsentStore) -> None:
    """Agreeing to send a receipt to OCR is not agreeing to send every contract."""
    store.record("ocr")

    assert store.has("compress") is False


def test_the_record_survives_a_new_store(path: Path) -> None:
    """It is a file, not a session — the next invocation must see it."""
    ConsentStore(path, endpoint=ENDPOINT).record("ocr")

    assert ConsentStore(path, endpoint=ENDPOINT).has("ocr") is True


def test_recording_is_idempotent(store: ConsentStore) -> None:
    store.record("ocr")
    store.record("ocr")

    assert store.granted_tools() == ("ocr",)


def test_a_grant_records_what_was_agreed(store: ConsentStore) -> None:
    """A user asking "what did I agree to?" gets a real answer."""
    moment = datetime(2026, 8, 16, 12, 30, tzinfo=UTC)

    grant = store.record("ocr", now=moment)

    assert grant.tool == "ocr"
    assert grant.endpoint == ENDPOINT
    assert grant.terms_version == CONSENT_TERMS_VERSION
    assert grant.granted_at.startswith("2026-08-16T12:30:00")


def test_the_directory_is_created_on_first_write(tmp_path: Path) -> None:
    """Nothing exists until something is recorded — no import side effects."""
    nested = tmp_path / "config" / "docmax-like" / "consent.json"
    assert not nested.parent.exists()

    ConsentStore(nested, endpoint=ENDPOINT).record("ocr")

    assert nested.is_file()


def test_the_file_is_readable_by_a_human(store: ConsentStore, path: Path) -> None:
    """It is inspectable and deletable; that is the whole revocation story."""
    store.record("ocr")

    document = json.loads(path.read_text(encoding="utf-8"))

    assert document["version"] == SCHEMA_VERSION
    assert document["grants"][0]["tool"] == "ocr"


# ---------------------------------------------------------------------------
# Revocation
# ---------------------------------------------------------------------------


def test_revoking_withdraws_consent(store: ConsentStore) -> None:
    store.record("ocr")

    assert store.revoke("ocr") is True
    assert store.has("ocr") is False


def test_revoking_what_was_never_granted_reports_so(store: ConsentStore) -> None:
    assert store.revoke("ocr") is False


def test_revoking_one_tool_spares_the_others(store: ConsentStore) -> None:
    store.record("ocr")
    store.record("compress")

    store.revoke("ocr")

    assert store.granted_tools() == ("compress",)


def test_revoke_all_clears_everything(store: ConsentStore) -> None:
    store.record("ocr")
    store.record("compress")

    assert store.revoke_all() == 2
    assert store.granted_tools() == ()


def test_deleting_the_file_revokes_everything(store: ConsentStore, path: Path) -> None:
    """The documented no-command way to revoke."""
    store.record("ocr")
    path.unlink()

    assert store.has("ocr") is False


# ---------------------------------------------------------------------------
# Scope — what invalidates a grant
# ---------------------------------------------------------------------------


def test_a_grant_does_not_carry_to_another_endpoint(path: Path) -> None:
    """Agreeing to a box on the LAN is not agreeing to a service on the internet."""
    ConsentStore(path, endpoint=ENDPOINT).record("ocr")

    assert ConsentStore(path, endpoint=OTHER_ENDPOINT).has("ocr") is False


def test_returning_to_the_original_endpoint_finds_the_grant(path: Path) -> None:
    """Scoping must not silently destroy the grant, only decline to apply it."""
    ConsentStore(path, endpoint=ENDPOINT).record("ocr")
    assert ConsentStore(path, endpoint=OTHER_ENDPOINT).has("ocr") is False

    assert ConsentStore(path, endpoint=ENDPOINT).has("ocr") is True


def test_a_trailing_slash_does_not_make_a_different_endpoint(path: Path) -> None:
    ConsentStore(path, endpoint=ENDPOINT).record("ocr")

    assert ConsentStore(path, endpoint=ENDPOINT + "/").has("ocr") is True


def test_bumping_the_terms_version_invalidates_older_grants(path: Path) -> None:
    ConsentStore(path, endpoint=ENDPOINT, terms_version=1).record("ocr")

    assert ConsentStore(path, endpoint=ENDPOINT, terms_version=2).has("ocr") is False


def test_agreeing_again_satisfies_the_new_terms(path: Path) -> None:
    ConsentStore(path, endpoint=ENDPOINT, terms_version=1).record("ocr")

    newer = ConsentStore(path, endpoint=ENDPOINT, terms_version=2)
    newer.record("ocr")

    assert newer.has("ocr") is True


def test_a_newer_stored_terms_version_is_honoured(path: Path) -> None:
    """An older DocMax reading a newer record: they agreed to at least as much."""
    ConsentStore(path, endpoint=ENDPOINT, terms_version=5).record("ocr")

    assert ConsentStore(path, endpoint=ENDPOINT, terms_version=2).has("ocr") is True


def test_the_stale_grant_is_still_visible_for_the_prompt(path: Path) -> None:
    """ "You agreed on this date, for a different endpoint" beats asking blankly."""
    ConsentStore(path, endpoint=ENDPOINT).record("ocr")

    store = ConsentStore(path, endpoint=OTHER_ENDPOINT)

    assert store.has("ocr") is False
    stale = store.grant_for("ocr")
    assert stale is not None
    assert stale.endpoint == ENDPOINT


# ---------------------------------------------------------------------------
# Failing closed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scenario", "body"),
    [
        ("not json", "this is not json at all"),
        ("json but not an object", "[1, 2, 3]"),
        ("no version", '{"grants": []}'),
        ("grants is not a list", '{"version": 1, "grants": {}}'),
        ("empty file", ""),
    ],
)
def test_a_corrupt_record_means_no_consent(path: Path, scenario: str, body: str) -> None:
    path.write_text(body, encoding="utf-8")

    assert ConsentStore(path, endpoint=ENDPOINT).has("ocr") is False, scenario


def test_a_schema_from_the_future_means_no_consent(path: Path) -> None:
    """We cannot interpret it, and must not guess in the permissive direction."""
    path.write_text(
        json.dumps(
            {
                "version": SCHEMA_VERSION + 1,
                "grants": [
                    {
                        "tool": "ocr",
                        "endpoint": ENDPOINT,
                        "terms_version": CONSENT_TERMS_VERSION,
                        "granted_at": "2026-08-16T00:00:00+00:00",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert ConsentStore(path, endpoint=ENDPOINT).has("ocr") is False


def test_a_directory_where_the_file_should_be_means_no_consent(tmp_path: Path) -> None:
    blocked = tmp_path / "consent.json"
    blocked.mkdir()

    assert ConsentStore(blocked, endpoint=ENDPOINT).has("ocr") is False


@pytest.mark.parametrize(
    "entry",
    [
        {"tool": "ocr"},
        {"tool": 42, "endpoint": ENDPOINT, "terms_version": 1, "granted_at": "x"},
        {"tool": "ocr", "endpoint": ENDPOINT, "terms_version": "1", "granted_at": "x"},
        {"tool": "ocr", "endpoint": ENDPOINT, "terms_version": True, "granted_at": "x"},
        "not even a mapping",
    ],
)
def test_a_malformed_entry_is_dropped(path: Path, entry: object) -> None:
    """One bad record costs its own tool a prompt, not everyone else's grant."""
    good = {
        "tool": "compress",
        "endpoint": ENDPOINT,
        "terms_version": CONSENT_TERMS_VERSION,
        "granted_at": "2026-08-16T00:00:00+00:00",
    }
    path.write_text(
        json.dumps({"version": SCHEMA_VERSION, "grants": [entry, good]}), encoding="utf-8"
    )

    store = ConsentStore(path, endpoint=ENDPOINT)

    assert store.has("ocr") is False
    assert store.has("compress") is True, "the valid grant beside it survived"


def test_a_partial_write_cannot_corrupt_the_record(store: ConsentStore, path: Path) -> None:
    """Writes go through core.atomic, so no staged file is left beside it."""
    store.record("ocr")
    store.record("compress")

    leftovers = [p.name for p in path.parent.glob(".*")]
    assert leftovers == []
    assert store.granted_tools() == ("compress", "ocr")


# ---------------------------------------------------------------------------
# ConsentGrant
# ---------------------------------------------------------------------------


def test_a_grant_round_trips(path: Path) -> None:
    grant = ConsentGrant(
        tool="ocr", endpoint=ENDPOINT, terms_version=1, granted_at="2026-08-16T00:00:00+00:00"
    )

    assert ConsentGrant.from_dict(grant.to_dict()) == grant


def test_covers_requires_both_endpoint_and_terms() -> None:
    grant = ConsentGrant(
        tool="ocr", endpoint=ENDPOINT, terms_version=2, granted_at="2026-08-16T00:00:00+00:00"
    )

    assert grant.covers(endpoint=ENDPOINT, terms_version=2) is True
    assert grant.covers(endpoint=OTHER_ENDPOINT, terms_version=2) is False
    assert grant.covers(endpoint=ENDPOINT, terms_version=3) is False
