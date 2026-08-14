"""Build one wizard page's ordered fill plan from a field/button snapshot.

Server layer of the submit guard: click_nav is emitted ONLY for buttons
navguard classifies as nav, and ONLY when the page has at least one field
action to fill — many real ATS final/review pages label their actual submit
button "Continue" (which navguard's allowlist treats as nav-like), so a
fieldless page is treated as a likely review/confirm step and handed to the
user instead. Deny-listed buttons always become the final await_user handoff.
Engine failures degrade (fewer actions), never raise."""

import hashlib
import logging
import re
from datetime import datetime

from sqlalchemy.orm import Session

from app.db.models import ApplySession, MemoryEntry
from app.services import answers as answers_service
from app.services import autofill as autofill_service
from app.services import memory as memory_service
from app.services import navguard
from app.services.engine import generate_json

logger = logging.getLogger(__name__)

SELECT_SCHEMA = {
    "type": "object",
    "properties": {
        "values": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"index": {"type": "integer"}, "value": {"type": ["string", "null"]}},
                "required": ["index", "value"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["values"],
    "additionalProperties": False,
}
SELECT_SYSTEM = """\
Choose values for these application-form fields from the applicant's profile,
his notes, and job context. Only answer fields you are confident about; use
null otherwise. For fields with an options list the value MUST be one of the
options verbatim. Never invent facts about the applicant."""

_COVER_HINTS = ("cover",)
_RESUME_HINTS = ("resume", "cv", "curriculum")

_FIELD_ACTION_KINDS = ("fill", "select", "combobox", "check", "attach")

# Native date/month inputs silently reject anything but ISO values, but the
# profile stores dates the way the user wrote them ("May 1, 2028"). Coerce at
# plan time so the browser accepts the write; unparseable values pass through
# unchanged (a plain-text date field still wants the human phrasing).
_DAY_FORMATS = ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%m/%d/%Y", "%m/%d/%y")
_MONTH_FORMATS = ("%Y-%m", "%B, %Y", "%B %Y", "%b, %Y", "%b %Y", "%m/%Y")


def _coerce_temporal(field_type: str | None, value: str) -> str:
    if field_type not in ("date", "month"):
        return value
    v = re.sub(r"\s+", " ", value.strip())
    for fmt in _DAY_FORMATS + _MONTH_FORMATS:
        try:
            parsed = datetime.strptime(v, fmt)
        except ValueError:
            continue
        return parsed.strftime("%Y-%m-%d" if field_type == "date" else "%Y-%m")
    return value


# Workday splits a date into MM / DD / YYYY spinner inputs whose only reliable
# marker is the data-automation-id (dateSectionMonth-input, …). Each segment
# gets just its slice of the mapped date; unparseable values pass through.
_DATE_SEGMENTS = {"datesectionmonth": "month", "datesectionday": "day", "datesectionyear": "year"}


def _resume_slot_plan(db: Session, session: ApplySession, fields: list[dict]) -> list[dict]:
    """tailor_only scope: attach the tailored résumé into résumé-hinted file
    slots and touch NOTHING else — no profile fill, no essays, no selects, no
    nav clicks. With no slot on the page, tell the user once where the PDF is."""
    profile = autofill_service._derive(autofill_service.load_profile(db))
    actions = [
        {
            "kind": "attach", "index": f["index"], "doc_kind": "resume",
            "doc_id": session.resume_doc_id,
            "filename": _attach_filename(profile, "resume"),
            "review": True, "label": f.get("label", ""),
        }
        for f in fields
        if f.get("type") == "file"
        and session.resume_doc_id
        and any(h in _hay(f) for h in _RESUME_HINTS)
    ]
    if not actions and not session.state.get("tailor_only_notified"):
        session.state = {**session.state, "tailor_only_notified": True}
        return [{
            "kind": "await_user", "button_index": None, "terminal": False,
            "reason": "Tailored résumé saved to the resume bank — no résumé upload "
                      "slot found on this page. Download the PDF from the extension popup.",
        }]
    return actions


def _date_segment(field: dict) -> str | None:
    hay = f"{field.get('automation_id') or ''} {field.get('id') or ''}".lower()
    return next((seg for marker, seg in _DATE_SEGMENTS.items() if marker in hay), None)


def _segment_value(value: str, seg: str) -> str | None:
    v = re.sub(r"\s+", " ", value.strip())
    for fmt in _DAY_FORMATS + _MONTH_FORMATS:
        try:
            parsed = datetime.strptime(v, fmt)
        except ValueError:
            continue
        return {"month": f"{parsed.month:02d}", "day": f"{parsed.day:02d}", "year": str(parsed.year)}[seg]
    return None


def _hay(f: dict) -> str:
    return " ".join(
        str(f.get(k, "")) for k in ("label", "name", "id", "aria_label", "placeholder", "automation_id")
    ).lower()


def _page_signature(fields: list[dict]) -> str:
    """Stable identity for a wizard page: the shape of its fields, independent
    of values. Used to recognize a replan of the page we just planned."""
    parts = sorted(
        "|".join(str(f.get(k) or "") for k in ("type", "label", "name", "id"))
        for f in fields
    )
    return hashlib.sha1("\n".join(parts).encode("utf-8", "replace")).hexdigest()


# Open-ended cues that make a question-labeled TEXT input essay-worthy
# (textareas always are). Short-fact hints veto: "What is your expected
# salary?" is question-shaped but wants a number, not prose.
_QUESTION_CUES = ("why", "what", "how", "describe", "tell us", "tell me", "excite")
_SHORT_FACT_HINTS = (
    "salary", "rate", "compensation", "name", "email", "phone", "date", "school",
    "university", "gpa", "address", "city", "zip", "state", "country", "linkedin",
    "github", "website", "url", "referr", "pronoun",
)


def _wants_drafted_answer(f: dict) -> bool:
    if f.get("type") == "textarea":
        return True
    if f.get("type") != "text":
        return False
    q = (f.get("label") or f.get("placeholder") or "").lower()
    if "?" not in q or not any(cue in q for cue in _QUESTION_CUES):
        return False
    return not any(hint in q for hint in _SHORT_FACT_HINTS)


def _select_memory_context(db: Session, questions: list[str]) -> str:
    """Brain context for the select/radio engine call, so preference-shaped
    questions (working style, relocation) can be answered from what the user has
    actually said about themselves. Empty brain skips the embedding model; any
    failure degrades to no context."""
    if db.query(MemoryEntry.id).filter(MemoryEntry.embedding.is_not(None)).first() is None:
        return ""
    try:
        return memory_service.retrieve_context(db, "\n".join(questions)[:2000], k_seeds=4).markdown
    except Exception as e:
        logger.warning("select memory context failed: %s", e)
        return ""


def _attach_filename(profile: dict[str, str], doc_kind: str) -> str:
    """Last_First_kind.pdf when the profile knows the name (recruiters see the
    filename); the generic kind.pdf otherwise."""
    first = (profile.get("first_name") or "").strip()
    last = (profile.get("last_name") or "").strip()
    if not (first and last):
        return f"{doc_kind}.pdf"
    stem = re.sub(r"[^A-Za-z0-9]+", "_", f"{last}_{first}").strip("_")
    return f"{stem}_{doc_kind}.pdf"


def build_plan(db: Session, session: ApplySession, fields: list[dict], buttons: list[dict]) -> list[dict]:
    # Same-page replan guard: if this snapshot is the page we JUST planned, the
    # wizard didn't advance (nav click rejected by validation) or the DOM merely
    # mutated under the user's hands. Re-planning would re-run the engine,
    # overwrite manual corrections, and — worst case — click Next in a loop.
    # After a failed advance, hand off once; otherwise stay silent. A retry
    # clears the stored signature (api/apply.py) so a deliberate refill works.
    sig = _page_signature(fields)
    if fields and sig == session.state.get("last_page_sig"):
        stalled_nav = session.state.get("nav_clicked_sig") == sig
        already_notified = session.state.get("stall_notified") == sig
        session.state = {**session.state, "stall_notified": sig}
        if stalled_nav and not already_notified:
            return [{
                "kind": "await_user", "button_index": None, "terminal": False,
                "reason": "The page didn't advance — a required field may need fixing. Continue manually when ready.",
            }]
        return []

    if session.state.get("fill_scope") == "resume_slot_only":
        actions = _resume_slot_plan(db, session, fields)
        # Mirror the general path's tail write (bottom of this function) so the
        # same-page replan guard above also suppresses an identical repeat
        # snapshot here — otherwise every replan of an unchanged page (Workday
        # re-rendering the DOM without a real page change) re-emits the attach
        # action. resume_slot_only never emits click_nav, so nav_clicked_sig
        # always clears.
        session.state = {**session.state, "last_page_sig": sig, "stall_notified": None, "nav_clicked_sig": None}
        return actions

    actions: list[dict] = []
    handled: set[int] = set()
    by_index = {f["index"]: f for f in fields}

    # Profile fields: heuristic (+ optional AI) mapping from autofill_service.
    # map_fields returns POSITIONAL indexes over the list it was given (`plain`,
    # files excluded) — resolve against `plain` itself, bounds-checked, then
    # emit the snapshot's own index (f["index"]) in the action. Resolving
    # against `by_index` (keyed by snapshot index over ALL fields, including
    # files) here would misalign every mapping whenever a file input sits
    # before other fields on the page.
    # Profile is loaded once: mapping context, select prompt, and attach
    # filenames all use it.
    profile = autofill_service._derive(autofill_service.load_profile(db))

    # AI assist ON: heuristics alone leave every unmatched short answer empty.
    # The assist is strictly profile-grounded (skip-if-unsure in its prompt).
    plain = [f for f in fields if f.get("type") not in ("file", "radio", "checkbox")]
    try:
        mapped = autofill_service.map_fields(db, plain, True) if plain else []
    except Exception as e:
        logger.warning("profile mapping failed: %s", e)
        mapped = []
    for m in mapped:
        pos = m["index"]
        if pos < 0 or pos >= len(plain):
            continue
        f = plain[pos]
        kind = {"select": "select", "combobox": "combobox"}.get(f.get("type"), "fill")
        idx = f["index"]
        value = _coerce_temporal(f.get("type"), m["value"])
        seg = _date_segment(f)
        if seg:
            value = _segment_value(m["value"], seg) or value
        actions.append({
            "kind": kind, "index": idx, "value": value,
            "review": m.get("confidence") != "high", "label": f.get("label", ""),
        })
        handled.add(idx)

    # Essays: unmapped textareas — plus question-shaped text inputs ("What
    # excites you about robotics?") that heuristics and profile mapping left
    # empty. Switchable via the session's options (extension popup toggle).
    if (session.state.get("options") or {}).get("answer_questions", True):
        qa_drafts = dict(session.state.get("qa_drafts", {}))
        for f in fields:
            if f["index"] in handled or not _wants_drafted_answer(f):
                continue
            question = (f.get("label") or f.get("placeholder") or "").strip()
            if len(question) < 8:
                continue
            try:
                draft = answers_service.draft_answer(db, question, job_id=session.job_id)["draft"]
            except Exception as e:
                logger.warning("essay draft failed for %r: %s", question, e)
                continue
            actions.append({
                "kind": "fill", "index": f["index"], "value": draft,
                "review": True, "essay": True, "label": question,
            })
            qa_drafts[question] = draft
            handled.add(f["index"])
        session.state = {**session.state, "qa_drafts": qa_drafts}

    # Remaining selects/comboboxes + radio groups: one engine call, degrade on
    # failure. A radio group rides along as a pseudo-select — its question is
    # the shared group_label, its options are the members' own labels, and its
    # prompt index is the first member's (the "anchor"). Checkboxes are
    # deliberately excluded: consent/marketing boxes stay with the human.
    leftovers = [
        f for f in fields
        if f["index"] not in handled and f.get("type") in ("select", "combobox")
    ]
    radio_groups: dict[str, list[dict]] = {}
    for f in fields:
        if f.get("type") == "radio" and f["index"] not in handled:
            radio_groups.setdefault(str(f.get("name") or f["index"]), []).append(f)
    group_by_anchor = {
        g[0]["index"]: g
        for g in radio_groups.values()
        if (g[0].get("group_label") or "").strip()
        and all((m.get("label") or "").strip() for m in g)
    }
    if leftovers or group_by_anchor:
        try:
            prompt_lines = [
                f"- index {f['index']}: {f.get('label') or f.get('name')} "
                f"(options: {', '.join(f.get('options', [])[:30]) or 'free text'})"
                for f in leftovers
            ] + [
                f"- index {anchor}: {g[0]['group_label'].strip()} "
                f"(options: {', '.join(m['label'].strip() for m in g)})"
                for anchor, g in group_by_anchor.items()
            ]
            questions = [f.get("label") or str(f.get("name") or "") for f in leftovers] + [
                g[0]["group_label"] for g in group_by_anchor.values()
            ]
            notes = _select_memory_context(db, [q for q in questions if q])
            notes_block = (
                f"\n\nAPPLICANT NOTES (from his memory bank — use only where directly relevant):\n{notes}"
                if notes
                else ""
            )
            payload = generate_json(
                SELECT_SYSTEM,
                f"PROFILE:\n{profile}{notes_block}\n\nFIELDS:\n" + "\n".join(prompt_lines),
                SELECT_SCHEMA,
            )
            for v in payload.get("values", []):
                if not v.get("value"):
                    continue
                value = v["value"].strip()
                group = group_by_anchor.get(v.get("index"))
                if group is not None:
                    # The matching radio (not the anchor) receives the check:
                    # exact label first, then the closest one — engines answer
                    # "Yes" where the label reads "Yes, I am authorized…".
                    member = next(
                        (m for m in group if m["label"].strip().lower() == value.lower()), None
                    )
                    if member is None:
                        best, _score = autofill_service.best_option(
                            value, [m["label"].strip() for m in group]
                        )
                        if best is not None:
                            member = next(m for m in group if m["label"].strip() == best)
                    if member is None:
                        logger.warning("radio answer %r matches no option in group %s", value, v.get("index"))
                        continue
                    actions.append({
                        "kind": "check", "index": member["index"], "value": member["label"].strip(),
                        "review": True, "label": group[0]["group_label"].strip(),
                    })
                    for m in group:
                        handled.add(m["index"])
                    continue
                f = by_index.get(v.get("index"))
                if f is None:
                    continue
                options = f.get("options") or []
                # Defense-in-depth: the prompt asks for a verbatim option match, but
                # nothing enforces that on the model's side — never trust it blind.
                # Case drift and short-form answers snap to the closest real option;
                # values resembling nothing are still dropped. Comboboxes have no
                # fixed options list (the executor live-picks in the DOM), so they
                # keep today's unvalidated behavior.
                if options:
                    stripped = [o.strip() for o in options]
                    exact = next((o for o in stripped if o.lower() == value.lower()), None)
                    if exact is None:
                        exact, _score = autofill_service.best_option(value, stripped)
                    if exact is None:
                        logger.warning("select value %r matches no option for field %s; skipping", value, f["index"])
                        continue
                    value = exact
                kind = "combobox" if f.get("type") == "combobox" else "select"
                actions.append({
                    "kind": kind, "index": f["index"], "value": value,
                    "review": True, "label": f.get("label", ""),
                })
                handled.add(f["index"])
        except Exception as e:
            logger.warning("select mapping degraded: %s", e)

    # File inputs -> attach the pipeline's own documents, but only into slots
    # that name what they want. Unrecognized file slots (transcript, portfolio,
    # writing sample) stay with the human — attaching a resume PDF into a
    # transcript slot is worse than leaving it empty.
    for f in fields:
        if f.get("type") != "file":
            continue
        if any(h in _hay(f) for h in _COVER_HINTS):
            if session.cover_doc_id:
                actions.append({
                    "kind": "attach", "index": f["index"], "doc_kind": "cover_letter",
                    "doc_id": session.cover_doc_id,
                    "filename": _attach_filename(profile, "cover_letter"),
                    "review": True, "label": f.get("label", ""),
                })
        elif session.resume_doc_id and any(h in _hay(f) for h in _RESUME_HINTS):
            actions.append({
                "kind": "attach", "index": f["index"], "doc_kind": "resume",
                "doc_id": session.resume_doc_id,
                "filename": _attach_filename(profile, "resume"),
                "review": True, "label": f.get("label", ""),
            })

    # Buttons: exactly one nav click at the end, or the submit/no-fields handoff.
    nav = None
    submit = None
    for b in buttons:
        cls = navguard.classify_button(b.get("text", ""), b.get("aria_label", ""), b.get("value", ""), b.get("name", ""))
        if cls == "nav" and nav is None:
            nav = b
        elif cls == "submit" and submit is None:
            submit = b

    has_field_actions = any(a["kind"] in _FIELD_ACTION_KINDS for a in actions)

    # Posting page in front of the application: many jobs show the description
    # with an "Apply now" button and keep the form behind it. With nothing
    # fillable on this page AND nothing filled yet this session, that click
    # cannot submit user data — it opens the form, and the next page_changed
    # brings the real fill plan. Review pages are excluded twice over: their
    # final buttons use end-of-flow verbs (submit/send/... — is_apply_start
    # rejects them), and by the time a review page appears this session has
    # recorded fill results.
    if not has_field_actions and not session.state.get("results"):
        for b in buttons:
            label = (b.get("text") or b.get("aria_label") or b.get("value") or "").strip()
            if navguard.is_apply_start(label):
                return [{
                    "kind": "click_start", "button_index": b["index"],
                    "expect_text": label, "label": label,
                }]

    if nav is not None:
        expect_text = (nav.get("text") or nav.get("aria_label") or nav.get("value") or "").strip()
        if has_field_actions and expect_text:
            actions.append({
                "kind": "click_nav", "button_index": nav["index"],
                "expect_text": expect_text,
                "label": (nav.get("text") or "").strip(),
            })
        elif has_field_actions:
            # Nav-classified by name alone (e.g. name="continue") with no visible
            # text/aria/value: content.js's click-time text re-verification
            # compares the live button against expect_text, so an empty value
            # would silently skip that check. Hand off rather than click blind.
            # Non-terminal: this is a wizard-page handoff, not the final submit.
            actions.append({
                "kind": "await_user", "button_index": nav["index"], "terminal": False,
                "reason": "Continue button has no visible label to verify — advance manually.",
            })
        else:
            # Nav-classified but nothing on the page to fill: likely a review/confirm
            # step (see module docstring) — hand off rather than click it ourselves.
            # Non-terminal: more fields may appear after the user advances.
            actions.append({
                "kind": "await_user", "button_index": nav["index"], "terminal": False,
                "reason": "Nothing to fill here — advance manually after reviewing.",
            })
    elif submit is not None:
        # Terminal: this is the final handoff — the session ends here, the user
        # reviews and submits by hand.
        actions.append({
            "kind": "await_user", "button_index": submit["index"], "terminal": True,
            "reason": "Submit button — review everything, then click it yourself. I never click this.",
        })

    # Remember what we just planned so a same-page replan is recognized (guard
    # at the top). nav_clicked_sig marks pages we tried to ADVANCE from — only
    # those earn a stall notice when they come back unchanged.
    session.state = {
        **session.state,
        "last_page_sig": sig,
        "stall_notified": None,
        "nav_clicked_sig": sig if any(a["kind"] == "click_nav" for a in actions) else None,
    }
    return actions
