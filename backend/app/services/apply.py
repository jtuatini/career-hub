"""One-click-apply pipeline: parse → classify → tailor → cover letter → ready.

run_pipeline is a RESUMABLE stage machine: each stage checks whether its
artifact already exists (job_id, state.base_resume_id, resume_doc_id,
cover_doc_id) and skips ahead, so a retry after an error re-runs only what's
missing. It runs in a daemon thread with its own DB session; the extension
polls the ApplySession row."""

import logging
import threading

from sqlalchemy.orm import Session

from app.config import settings
from app.db.base import SessionLocal
from app.db.models import ApplySession, DocType, GeneratedDoc, Job, Resume
from app.services import autofill as autofill_service
from app.services import jobparse as jobparse_service
from app.services import research as research_service
from app.services import resume_bank, tailor_flow
from app.services.coverletter import build_cover_letter_tex, draft_cover_letter
from app.services.engine import generate_json
from app.services.latex import compile_tex

logger = logging.getLogger(__name__)

STAGES = ["parsing", "classifying", "tailoring", "cover_letter", "ready"]
_PROGRESS = {"parsing": 0.1, "classifying": 0.3, "tailoring": 0.4, "cover_letter": 0.8, "ready": 1.0}

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {"job_type": {"type": "string"}},
    "required": ["job_type"],
    "additionalProperties": False,
}
CLASSIFY_SYSTEM = """\
Pick the single best-matching resume category for this job description.
Answer with one of the given categories VERBATIM — no new categories."""


def create_session(
    db: Session,
    url: str,
    page_text: str,
    fields: list[dict],
    buttons: list[dict],
    mode: str = "full",
    options: dict | None = None,
) -> ApplySession:
    """`options` are per-feature switches for a full-mode run:
    tailor_resume / cover_letter / answer_questions (absent = on)."""
    state = {
        "page_text": page_text[:30000], "fields": fields, "buttons": buttons,
        "qa_drafts": {}, "mode": mode, "options": options or {},
    }
    if mode == "tailor_only":
        # Hard preset: tailor runs, everything else is off, and the fill plan is
        # scoped to résumé file slots only (fillplan branches on fill_scope).
        # Deliberately overrides any passed options — the popup's saved toggles
        # must not re-enable cover letters or answers here.
        state["options"] = {
            "tailor_resume": True, "cover_letter": False, "answer_questions": False,
        }
        state["fill_scope"] = "resume_slot_only"
    session = ApplySession(url=url, state=state)
    db.add(session)
    db.commit()
    return session


def start_pipeline(session_id: int) -> None:
    threading.Thread(target=run_pipeline, args=(session_id,), daemon=True).start()


def _set(db: Session, s: ApplySession, stage: str) -> None:
    s.stage = stage
    s.progress = _PROGRESS[stage]
    db.commit()


def run_pipeline(session_id: int) -> None:
    """Runs the stage machine to completion or containment. Never raises: even
    opening the session (SessionLocal) and the failure-recording path itself
    (rollback/commit) are guarded, so a double fault (e.g. sqlite locked while
    the request thread and this daemon thread both write the row) logs and
    leaves the row as-is instead of crashing the daemon thread silently."""
    db = None
    try:
        db = SessionLocal()
        s = db.get(ApplySession, session_id)
        if s is None or s.status == "stopped":
            return

        if s.state.get("mode") == "fill_only":
            # Fill-only: no parse, no tailoring, no cover letter — the fill
            # plan runs off the saved profile (and drafts essays with no job
            # context). No documents exist, so file slots are left alone.
            _set(db, s, "ready")
            return

        if s.job_id is None:
            _set(db, s, "parsing")
            parsed = jobparse_service.parse_posting(s.state["page_text"], s.url or None)
            job = Job(
                company=parsed.get("company") or "Unknown",
                title=parsed.get("title") or "Unknown role",
                url=s.url,
                jd_text=parsed.get("jd_text") or s.state["page_text"],
            )
            db.add(job)
            db.flush()
            s.job_id = job.id
            db.commit()
        job = db.get(Job, s.job_id)

        opts = s.state.get("options") or {}

        if opts.get("tailor_resume", True) and s.state.get("base_resume_id") is None:
            _set(db, s, "classifying")
            override = s.state.get("resume_override")
            if override:
                base_id = override
            else:
                heads = [r for r in resume_bank.latest_versions(db) if r.tex_source is not None]
                if not heads:
                    raise RuntimeError("No LaTeX resume in the bank — add one in the Resumes tab first")
                types = sorted({r.job_type for r in heads})
                payload = generate_json(
                    CLASSIFY_SYSTEM,
                    f"CATEGORIES: {', '.join(types)}\n\nJOB DESCRIPTION:\n{job.jd_text[:6000]}",
                    CLASSIFY_SCHEMA,
                )
                job_type = payload.get("job_type", "")
                matches = [r for r in heads if r.job_type == job_type] or heads
                base_id = matches[0].id
            s.state = {**s.state, "base_resume_id": base_id}
            db.commit()

        if opts.get("tailor_resume", True) and s.resume_doc_id is None:
            _set(db, s, "tailoring")
            resume = db.get(Resume, s.state["base_resume_id"])
            if resume is None or resume.tex_source is None:
                raise RuntimeError("Chosen base resume is missing or PDF-only — pick another")
            try:
                outcome = tailor_flow.tailor_to_doc(db, resume, job, job.jd_text)
            except tailor_flow.PageOverflowError as e:
                # str(e) names the ACTUAL failure — bottom overflow ("overflows the
                # bottom of the page by Npt") or page count. Interpolating e.pages
                # alone reads as self-contradictory for overfull-only failures, where
                # the page count is still 1 ("couldn't fit on one page (1 pages)").
                raise RuntimeError(f"{e} Retry, or switch the base resume.") from e
            s = db.get(ApplySession, session_id)  # tailor_flow may have rolled back
            s.resume_doc_id = outcome.doc.id
            db.commit()

        if opts.get("cover_letter", True) and s.cover_doc_id is None:
            _set(db, s, "cover_letter")
            # Ground the letter in the tailored resume produced above — without
            # this the drafter has no factual source and leans on whatever
            # ambient context the engine CLI carries.
            tailored = db.get(GeneratedDoc, s.resume_doc_id) if s.resume_doc_id else None
            resume_context = (
                f"\n\nRESUME (.tex source):\n{tailored.tex_source}"
                if tailored is not None and tailored.tex_source
                else ""
            )
            body = draft_cover_letter(db, job, resume_context)
            doc = GeneratedDoc(
                job_id=job.id,
                doc_type=DocType.COVER_LETTER,
                tex_source=build_cover_letter_tex(job.company, body, autofill_service.load_profile(db)),
                draft_text=body,
                body_text=body,
                approved=True,
                vetted=False,  # pipeline letters never feed exemplars until amended
            )
            db.add(doc)
            db.flush()
            pdf = compile_tex(doc.tex_source, settings.files_dir / "docs", f"doc_{doc.id}")
            doc.pdf_path = str(pdf)
            s.cover_doc_id = doc.id
            db.commit()

        if opts.get("answer_questions", True):
            # Company research grounds the short answers drafted at fill time.
            # Cached per job; best-effort — a failed search never blocks apply.
            # Runs last so it can't delay the resume and cover letter.
            try:
                research_service.research_company(db, job)
            except Exception as e:
                logger.warning("company research skipped for session %s: %s", session_id, e)

        _set(db, s, "ready")
    except Exception as e:  # any stage failure is contained in the session row
        logger.warning("apply pipeline %s failed: %s", session_id, e)
        try:
            if db is not None:
                db.rollback()
                s = db.get(ApplySession, session_id)
                if s is not None:
                    s.status = "error"
                    s.error = str(e)
                    db.commit()
        except Exception as inner:
            # Recording the failure itself failed (e.g. sqlite locked) — log and
            # give up rather than let this propagate out of the daemon thread.
            # The row is left stuck at its last successfully-committed stage
            # instead of silently vanishing.
            logger.error("apply pipeline %s could not record error: %s", session_id, inner)
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass
