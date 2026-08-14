"""Submit deny-list: classification + backend/extension parity."""

import json
import re
from pathlib import Path

from app.services import navguard

REPO = Path(__file__).resolve().parents[2]


def test_submit_like_matches_whole_words():
    for text in ["Submit", "  APPLY NOW ", "Send application", "Review and Submit",
                 "Finish", "Done", "Complete Application", "send"]:
        assert navguard.is_submit_like(text), text
    for text in ["Next", "Continue", "Save and continue", "Save & Continue", "Applying tips"]:
        assert not navguard.is_submit_like(text), text


def test_classify_button():
    assert navguard.classify_button("Next") == "nav"
    assert navguard.classify_button("Save and Continue") == "nav"
    assert navguard.classify_button("Submit Application") == "submit"
    assert navguard.classify_button("Next", "submit application") == "submit"  # any signal trips it
    assert navguard.classify_button("Weiter") == "unknown"
    assert navguard.classify_button("", "  ") == "unknown"  # unlabeled: never clicked


def test_denylist_parity_with_extension():
    py = json.loads((REPO / "backend/app/services/submit_denylist.json").read_text())
    js_src = (REPO / "extension/submit_denylist.js").read_text()
    match = re.search(r"SUBMIT_DENYLIST\s*=\s*(\[.*?\])", js_src, re.S)
    assert match, "extension/submit_denylist.js must define SUBMIT_DENYLIST = [...]"
    assert json.loads(match.group(1)) == py


def test_list_contents_are_pinned():
    """Pin exact deny-list and allow-list contents to catch content drift."""
    assert navguard.DENYLIST == ["submit", "apply", "apply now", "send application", "send my application", "submit application", "finish", "complete application", "confirm application", "send", "done"]
    assert navguard.NAV_ALLOWLIST == ["next", "continue", "save and continue", "save & continue"]
