"""Canary for the NVDA log speech parser.

Path B reads speech out of NVDA's own log, where sequences are
serialized with Python's repr(). That is an internal NVDA implementation
detail with no stability contract: it can change in any release, and
when it does the parser fails *silently* — an empty transcript, not an
exception.

This canary pins the parser against a captured excerpt from the NVDA
version named below. If NVDA changes its log shape, this test fails
loudly instead of the audit quietly reporting nothing.

When it fails: re-capture a log excerpt from the new NVDA, update the
fixture and NVDA_PINNED_VERSION, and confirm the parser still yields
one entry per utterance with locale codes stripped.
"""

from __future__ import annotations

from pathlib import Path

from audit.screen_reader import _parse_log_speech, _parse_log_speech_timed

# The NVDA release this fixture was captured from. docs/windows_worker.md
# documents this as the supported version for Path B.
NVDA_PINNED_VERSION = "2024.4.1"

FIXTURE = Path(__file__).parent / "fixtures" / "nvda_log_canary.txt"


def _log() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_parser_extracts_exactly_the_spoken_utterances():
    assert _parse_log_speech(_log()) == [
        "Place order button",
        "Email address edit blank",
        "Terms and conditions, it's required link",
        "heading level 2 Delivery options",
    ]


def test_locale_codes_are_not_treated_as_speech():
    """LangChangeCommand args are quoted strings but aren't spoken."""
    assert not any(
        u in ("en_US", "en") for u in _parse_log_speech(_log())
    )


def test_non_speech_log_lines_are_ignored():
    spoken = " ".join(_parse_log_speech(_log()))
    assert "Should not be parsed" not in spoken
    assert "kb(desktop):tab" not in spoken


def test_timestamps_are_attached_and_monotonic():
    timed = _parse_log_speech_timed(_log())
    stamps = [ts for ts, _ in timed]
    assert all(ts is not None for ts in stamps), "every utterance needs a timestamp"
    assert stamps == sorted(stamps), "timestamps must increase with the log"


def test_empty_log_yields_no_utterances():
    """The degenerate case the browse-mode guard depends on."""
    assert _parse_log_speech("") == []
    assert _parse_log_speech("INFO - x (00:00:00.000) - MainThread (1):\nnothing\n") == []
