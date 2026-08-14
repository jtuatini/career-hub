"""Submit-guard: shared deny-list classification for wizard buttons.

Layer 1 of two — the extension re-checks the vendored copy of the same list
at click time. type="submit" is never a signal (ATS Next buttons are
type=submit); only human-visible text signals count."""

import json
import re
from pathlib import Path

_DENYLIST_PATH = Path(__file__).with_name("submit_denylist.json")
DENYLIST: list[str] = json.loads(_DENYLIST_PATH.read_text())
NAV_ALLOWLIST = ["next", "continue", "save and continue", "save & continue"]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def is_submit_like(text: str) -> bool:
    t = _norm(text)
    return any(re.search(rf"\b{re.escape(term)}\b", t) for term in DENYLIST)


_APPLY_START = re.compile(r"^apply\b")


def is_apply_start(text: str) -> bool:
    """Start-of-flow 'Apply' / 'Apply now' / 'Apply for this job' labels — the
    posting-page button that OPENS an application form, never one that sends
    it. Anything mentioning submit/send/finish/complete stays out; those are
    end-of-flow verbs and remain deny-listed."""
    t = _norm(text)
    return bool(_APPLY_START.match(t)) and not any(
        w in t for w in ("submit", "send", "finish", "complete")
    )


def classify_button(*signals: str) -> str:
    """'submit' if ANY non-empty signal (text/aria-label/value/name) matches the
    deny-list; 'nav' if any equals the allow-list verbatim; else 'unknown'.
    Unknown and unlabeled buttons are never clicked."""
    texts = [_norm(s) for s in signals if s and s.strip()]
    if not texts:
        return "unknown"
    if any(is_submit_like(t) for t in texts):
        return "submit"
    if any(t in NAV_ALLOWLIST for t in texts):
        return "nav"
    return "unknown"
