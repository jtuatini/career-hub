"""Autofill field mapping: match a page's form fields to profile values.

Heuristics first (deterministic, free, testable); Claude only for fields the
heuristics can't place, and only when the caller opts in. Confidence drives the
extension's highlight color: "high" fills green, "review" fills yellow —
the user always reviews and always clicks submit themselves.
"""

import difflib
import json
import re

from sqlalchemy.orm import Session

from app.db.models import ProfileField
from app.services.engine import generate_json

# Profile keys → phrases seen in form labels/names/placeholders. Order matters
# twice over: first match wins, so more specific keys come first — and a key
# whose phrases CONTAIN another key's phrase ("preferred first name" ⊃ "first
# name", "country phone code" ⊃ "country") must precede that key. The key set
# mirrors the default Greenhouse form and Workday apply wizard (see
# docs/design.md and the ProfileView groups).
SYNONYMS: dict[str, list[str]] = {
    "preferred_name": ["preferred name", "preferred first name", "chosen name", "nickname"],
    "middle_name": ["middle name", "middle initial"],
    "first_name": ["first name", "given name", "firstname", "fname", "legal first"],
    "last_name": ["last name", "family name", "surname", "lastname", "lname", "legal last"],
    "full_name": ["full name", "your name", "legal name", "name as it appears"],
    "email": ["email", "e-mail"],
    "phone_device_type": ["phone device type", "device type", "phone type"],
    "phone_country_code": ["country phone code", "phone code", "dialing code", "country code"],
    "phone_extension": ["phone extension"],
    "phone": ["phone", "mobile", "cell", "telephone"],
    "linkedin": ["linkedin"],
    "github": ["github", "git hub"],
    "website": ["website", "portfolio", "personal site", "url"],
    "address2": ["address line 2", "address2", "apt", "suite", "unit number"],
    "address": ["street address", "address line 1", "address1", "mailing address", "address"],
    "city": ["city", "town"],
    "state": ["state", "province", "region"],
    "zip": ["zip", "postal"],
    "country": ["country"],
    "school": ["school", "university", "college", "institution", "alma mater"],
    "degree": ["degree", "qualification"],
    "major": ["major", "field of study", "concentration", "discipline"],
    "gpa": ["gpa", "grade point"],
    "grad_date": ["graduation date", "grad date", "expected graduation", "completion date"],
    "start_date": ["available start", "start date", "earliest start"],
    "work_auth": [
        "authorized to work",
        "work authorization",
        "legally authorized",
        "right to work",
    ],
    "sponsorship": ["sponsorship", "require visa", "visa status", "immigration"],
    "over_18": ["18 years", "at least 18", "18 or older", "age of majority"],
    "security_clearance": ["security clearance", "clearance"],
    "salary": ["salary", "compensation", "expected pay", "desired pay", "pay rate", "hourly rate"],
    "relocation": ["relocation", "relocate", "willing to move"],
    "remote_preference": ["remote", "hybrid", "onsite", "work arrangement", "work location preference"],
    "notice_period": ["notice period"],
    "referral_name": ["referred by", "who referred", "referred you", "referrer"],
    "hear_about": ["hear about", "how did you find", "referral source"],
    "job_title": ["job title", "current title", "most recent job title"],
    "company": ["company name", "current employer", "current company", "most recent employer", "employer name"],
    # Voluntary self-identification: filled ONLY from values the user typed into
    # their profile — never guessed, never AI-mapped beyond stored values.
    "gender": ["gender"],
    "hispanic_latino": ["hispanic", "latino"],
    "race_ethnicity": ["race", "ethnicity"],
    "veteran_status": ["veteran"],
    "disability_status": ["disability"],
    "pronouns": ["pronouns"],
}

_norm_re = re.compile(r"[^a-z0-9 ]+")
_camel_re = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _norm(text: str | None) -> str:
    return _norm_re.sub(" ", (text or "").lower()).strip()


def _split_camel(text: str | None) -> str:
    """legalNameSection_firstName → legal Name Section_first Name, so camelCase
    field names/automation-ids (Workday's habit) reach the synonym matcher as
    separate words after _norm."""
    return _camel_re.sub(" ", text or "")


_digit_re = re.compile(r"\d+")


def _option_score(value: str, option: str) -> float:
    """0..1 similarity between a profile/engine value and a form option.
    Word-boundary containment (not raw substring — 'no' must never fire inside
    'now'), token overlap with anchored prefix credit ('co' completes to
    'colorado' only when another token matched exactly, so 'no' can never ride
    on 'not'), and character similarity; the max of them. Values whose numbers
    differ are capped below threshold — a silently wrong year is worse than an
    empty field."""
    v, o = _norm(value), _norm(option)
    if not v or not o:
        return 0.0
    if v == o:
        return 1.0
    shorter, longer = (v, o) if len(v) <= len(o) else (o, v)
    score = 0.0
    if re.search(rf"\b{re.escape(shorter)}\b", longer):
        score = 0.75 + 0.2 * (len(shorter) / len(longer))
    tv, to = set(v.split()), set(o.split())
    inter = tv & to
    score = max(score, len(inter) / len(tv | to))
    if len(tv) >= 2 and inter:
        cov = sum(
            1 for t in tv
            if t in to
            or (len(t) >= 2 and any(u.startswith(t) for u in to))
            or any(len(u) >= 2 and t.startswith(u) for u in to)
        )
        score = max(score, 0.75 * cov / len(tv))
    score = max(score, difflib.SequenceMatcher(None, v, o).ratio())
    if _digit_re.findall(v) and _digit_re.findall(o) and _digit_re.findall(v) != _digit_re.findall(o):
        return min(score, 0.3)
    return score


FUZZY_THRESHOLD = 0.6


def best_option(value: str, options: list[str], threshold: float = FUZZY_THRESHOLD) -> tuple[str | None, float]:
    """Closest option to value, or (None, best_score) when nothing plausible.
    Shared by the heuristic select path, the AI fallback, and fillplan's
    engine-answer validation — next-best beats skip, but only above threshold."""
    best, score = None, 0.0
    for opt in options:
        s = _option_score(value, opt)
        if s > score:
            best, score = opt, s
    if best is None or score < threshold:
        return None, score
    return best, score


def load_profile(db: Session) -> dict[str, str]:
    return {f.key: f.value for f in db.query(ProfileField).all()}


def _derive(profile: dict[str, str]) -> dict[str, str]:
    """Fill first/last/full name gaps from whichever form the user stored."""
    out = dict(profile)
    if "full_name" in out and ("first_name" not in out or "last_name" not in out):
        parts = out["full_name"].split()
        if len(parts) >= 2:
            out.setdefault("first_name", parts[0])
            out.setdefault("last_name", " ".join(parts[1:]))
    if "full_name" not in out and "first_name" in out and "last_name" in out:
        out["full_name"] = f"{out['first_name']} {out['last_name']}"
    return out


def _tier_keys(haystacks: list[str]) -> list[str]:
    candidates: list[str] = []
    for key, phrases in SYNONYMS.items():
        for phrase in phrases:
            pattern = re.compile(rf"\b{re.escape(phrase)}\b")
            if any(hay and pattern.search(hay) for hay in haystacks):
                candidates.append(key)
                break
    return candidates


def _match_keys(field: dict) -> list[str]:
    """Candidate profile keys for a field, best-first. The VISIBLE tier
    (label, aria-label, placeholder) outranks the attribute tier (name, id,
    automation-id): ids like "phone_country" contain words the human never
    sees, and the label is what the applicant actually answers. Whole-word
    matching so 'state' can't fire on 'United States'; every matching key is
    returned and the caller prefers one the profile actually has a value for."""
    label_tier = _tier_keys([
        _norm(field.get("label")),
        _norm(field.get("aria_label")),
        _norm(field.get("placeholder")),
    ])
    attr_tier = _tier_keys([
        _norm(_split_camel(field.get("name"))),
        _norm(_split_camel(field.get("id"))),
        _norm(_split_camel(field.get("automation_id"))),
    ])
    return label_tier + [k for k in attr_tier if k not in label_tier]


# US applications overwhelmingly store the 2-letter code while form selects
# list full names (and vice versa is handled by the fuzzy scorer). Expanded
# only for option-backed fields — plain text fields keep the user's own form.
_STATE_ABBREV = {
    "al": "Alabama", "ak": "Alaska", "az": "Arizona", "ar": "Arkansas",
    "ca": "California", "co": "Colorado", "ct": "Connecticut", "de": "Delaware",
    "fl": "Florida", "ga": "Georgia", "hi": "Hawaii", "id": "Idaho",
    "il": "Illinois", "in": "Indiana", "ia": "Iowa", "ks": "Kansas",
    "ky": "Kentucky", "la": "Louisiana", "me": "Maine", "md": "Maryland",
    "ma": "Massachusetts", "mi": "Michigan", "mn": "Minnesota", "ms": "Mississippi",
    "mo": "Missouri", "mt": "Montana", "ne": "Nebraska", "nv": "Nevada",
    "nh": "New Hampshire", "nj": "New Jersey", "nm": "New Mexico", "ny": "New York",
    "nc": "North Carolina", "nd": "North Dakota", "oh": "Ohio", "ok": "Oklahoma",
    "or": "Oregon", "pa": "Pennsylvania", "ri": "Rhode Island", "sc": "South Carolina",
    "sd": "South Dakota", "tn": "Tennessee", "tx": "Texas", "ut": "Utah",
    "vt": "Vermont", "va": "Virginia", "wa": "Washington", "wv": "West Virginia",
    "wi": "Wisconsin", "wy": "Wyoming", "dc": "District of Columbia",
}
_COUNTRY_ALIASES = {
    "us": "United States", "usa": "United States", "u s": "United States",
    "u s a": "United States", "america": "United States", "uk": "United Kingdom",
}


# EEO / voluntary self-identification: filled only from stored profile values
# and never shown as a confident green fill (ProfileView promises this).
_ALWAYS_REVIEW_KEYS = {"gender", "hispanic_latino", "race_ethnicity", "veteran_status", "disability_status"}


# Degree shorthand → the stem platform lists share ("Bachelor's Degree",
# "Bachelor's (BA/BS)" both contain "bachelor s"). Doctorates vary too much
# between platforms to expand safely.
_DEGREE_ALIASES = {
    v: "Bachelor's" for v in (
        "bs", "b s", "bse", "b s e", "bsc", "b s c", "ba", "b a", "beng", "b eng",
        "bachelor of science", "bachelor of arts", "bachelor of engineering",
        "bachelor of science in engineering", "bachelors",
    )
} | {
    v: "Master's" for v in (
        "ms", "m s", "msc", "m s c", "meng", "m eng", "mba", "m b a",
        "master of science", "master of arts", "master of engineering", "masters",
    )
}


def _expand_geo(key: str, value: str) -> str:
    if key == "state":
        return _STATE_ABBREV.get(_norm(value), value)
    if key == "country":
        return _COUNTRY_ALIASES.get(_norm(value), value)
    if key == "degree":
        return _DEGREE_ALIASES.get(_norm(value), value)
    return value


def _select_value(field: dict, value: str) -> tuple[str | None, str]:
    """Pick the select option matching the profile value. Returns (option, confidence)."""
    options = field.get("options") or []
    v = _norm(value)
    for opt in options:
        if _norm(opt) == v:
            return opt, "high"
    best, _score = best_option(value, options)
    if best is not None:
        return best, "review"
    return None, "review"


def map_fields(db: Session, fields: list[dict], use_ai: bool = False) -> list[dict]:
    profile = _derive(load_profile(db))
    mappings: list[dict] = []
    unmatched: list[dict] = []

    for index, field in enumerate(fields):
        key = next((k for k in _match_keys(field) if k in profile), None)
        if key is None:
            # Positional index must WIN over the snapshot's own "index" key —
            # this function speaks positions over `fields`, and callers
            # (fillplan) resolve them against the filtered list they passed in.
            unmatched.append({**field, "index": index})
            continue
        value = profile[key]
        if field.get("options") or field.get("type") == "combobox":
            value = _expand_geo(key, value)
        confidence = "high"
        if field.get("options"):
            option, confidence = _select_value(field, value)
            if option is None:
                unmatched.append({**field, "index": index})
                continue
            value = option
        # Self-identification answers always get the yellow review highlight,
        # however exact the match — the user gives these a final look.
        if key in _ALWAYS_REVIEW_KEYS:
            confidence = "review"
        mappings.append(
            {"index": index, "profile_key": key, "value": value, "confidence": confidence}
        )

    if use_ai and unmatched and profile:
        mappings.extend(_ai_map(unmatched, profile))
    return sorted(mappings, key=lambda m: m["index"])


AI_SCHEMA = {
    "type": "object",
    "properties": {
        "mappings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "profile_key": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["index", "profile_key", "value"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["mappings"],
    "additionalProperties": False,
}

AI_SYSTEM = """\
You map job-application form fields to an applicant's profile values. Only map a
field when a profile value genuinely answers it; skip fields you're unsure about.
For select fields, the value must be one of the given options, verbatim. Never
fabricate information that is not in the profile.\
"""


def _ai_map(unmatched: list[dict], profile: dict[str, str]) -> list[dict]:
    payload = json.dumps({"fields": unmatched, "profile": profile}, ensure_ascii=False)
    result = generate_json(AI_SYSTEM, payload, AI_SCHEMA)
    by_index = {f["index"]: f for f in unmatched}
    mappings: list[dict] = []
    for m in result.get("mappings", []):
        field = by_index.get(m.get("index"))
        if field is None:  # hallucinated index
            continue
        options = field.get("options") or []
        if options:
            # The prompt asks for a verbatim option but nothing enforces it —
            # snap near-matches to the closest real option, drop the implausible.
            value = m.get("value", "")
            exact = next((o for o in options if _norm(o) == _norm(value)), None)
            if exact is None:
                exact, _score = best_option(value, options)
            if exact is None:
                continue
            m = {**m, "value": exact}
        mappings.append({**m, "confidence": "review"})
    return mappings
