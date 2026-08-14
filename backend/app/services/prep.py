"""Interview prep pack: likely questions for a job + which brain stories to tell.

Sends the JD, cached company research (if any), and top brain stories; the model
may only reference the stories it was given — titles are validated on the way out.
"""

import threading

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import SessionLocal
from app.db.models import CompanyResearch, DocType, GeneratedDoc, Job, MemoryEntry, PrepSession
from app.services import memory as memory_service
from app.services.engine import generate_json, generate_search, generate_text
from app.services.github_sync import GITHUB_API

PREP_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "why_asked": {"type": "string"},
                    "story_titles": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Titles of provided memories that answer this well",
                    },
                    "talking_points": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["question", "why_asked", "story_titles", "talking_points"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["questions"],
    "additionalProperties": False,
}

SYSTEM = """\
You build interview prep packs for internship candidates. Produce 6-10 likely
questions for this specific role: a mix of behavioral, role-specific technical
themes, and company-motivation questions. For each: why they ask it, which of the
candidate's provided memories (by exact title) make the strongest STAR answers,
and 2-4 concrete talking points drawn ONLY from those memories and the job
description. Never invent experiences; if no memory fits, leave story_titles
empty and say what kind of story the candidate should think of.\
"""


def build_prep(db: Session, job: Job) -> dict:
    ctx = None
    if db.query(MemoryEntry.id).filter(MemoryEntry.embedding.is_not(None)).first() is not None:
        query = f"{job.title} {job.company}\n{(job.jd_text or '')[:1500]}"
        ctx = memory_service.retrieve_context(db, query, k_seeds=8)
    research = db.scalar(
        select(CompanyResearch)
        .where(CompanyResearch.job_id == job.id)
        .order_by(CompanyResearch.created_at.desc())
    )

    parts = [f"Role: {job.title} at {job.company}", f"Job description:\n{(job.jd_text or '')[:6000]}"]
    if research:
        parts.append(f"Company research:\n{research.findings[:3000]}")
    if ctx and ctx.entries:
        parts.append(f"Candidate memories (with their graph connections):\n{ctx.markdown}")
    else:
        parts.append("Candidate memories: none available yet.")

    result = generate_json(SYSTEM, "\n\n---\n\n".join(parts), PREP_SCHEMA)
    known_titles = {e.title for e in ctx.entries} if ctx else set()
    for q in result.get("questions", []):
        q["story_titles"] = [t for t in q["story_titles"] if t in known_titles]
    return result


INTERVIEWER_SYSTEM = """\
You are conducting a live mock interview for the role in the context below.
Ask exactly ONE question per turn — no preamble, no commentary, no feedback,
never answer for the candidate. Mix behavioral and role-technical questions
grounded in the job description and the candidate's resume. If the last
answer was vague or short, probe it with a follow-up before moving on.
Output only the question text."""

DEBRIEF_SYSTEM = """\
The mock interview is over. Write the candidate an honest debrief from the
transcript: strengths (what genuinely landed), gaps (weak or missing answers,
judged only on what was said), and for each main question the concrete points
a stronger answer would hit — drawn from the resume and memories provided,
never invented."""

DEBRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "strengths": {"type": "array", "items": {"type": "string"}},
        "gaps": {"type": "array", "items": {"type": "string"}},
        "suggested_answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "points": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["question", "points"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["strengths", "gaps", "suggested_answers"],
    "additionalProperties": False,
}


def _interview_context(db: Session, job: Job) -> str:
    parts = [
        f"Role: {job.title} at {job.company}",
        f"Job description:\n{(job.jd_text or 'not stored')[:6000]}",
    ]
    resume = db.scalar(
        select(GeneratedDoc)
        .where(
            GeneratedDoc.job_id == job.id,
            GeneratedDoc.doc_type == DocType.RESUME,
            GeneratedDoc.approved.is_(True),
        )
        .order_by(GeneratedDoc.created_at.desc())
    )
    if resume is not None:
        parts.append(f"Candidate's tailored resume (LaTeX source):\n{resume.tex_source[:8000]}")
    else:
        parts.append("Candidate's tailored resume: none tailored for this job yet.")
    if db.query(MemoryEntry.id).filter(MemoryEntry.embedding.is_not(None)).first() is not None:
        query = f"{job.title} {job.company}\n{(job.jd_text or '')[:1500]}"
        ctx = memory_service.retrieve_context(db, query, k_seeds=8)
        if ctx.entries:
            parts.append(f"Candidate memories (with graph connections):\n{ctx.markdown}")
    return "\n\n---\n\n".join(parts)


def _render_transcript(transcript: list[dict]) -> str:
    if not transcript:
        return "(nothing yet — open the interview with your first question)"
    return "\n\n".join(
        f"{'INTERVIEWER' if t['role'] == 'interviewer' else 'CANDIDATE'}: {t['text']}"
        for t in transcript
    )


def _get_session(db: Session, session_id: int, kind: str, status: str) -> PrepSession:
    session = db.get(PrepSession, session_id)
    if session is None:
        raise ValueError("prep session not found")
    if session.kind != kind or session.status != status:
        raise ValueError(f"not an {status} {kind} session")
    return session


def start_interview(db: Session, job_id: int) -> PrepSession:
    job = db.get(Job, job_id)
    if job is None:
        raise ValueError("job not found")
    session = PrepSession(job_id=job_id, kind="interview", status="active")
    db.add(session)
    db.flush()
    question = generate_text(
        INTERVIEWER_SYSTEM,
        f"{_interview_context(db, job)}\n\nTRANSCRIPT SO FAR:\n{_render_transcript([])}",
    )
    session.transcript = [{"role": "interviewer", "text": question.strip()}]
    db.commit()
    return session


def answer_turn(db: Session, session_id: int, answer: str) -> PrepSession:
    session = _get_session(db, session_id, "interview", "active")
    job = db.get(Job, session.job_id)
    transcript = list(session.transcript) + [{"role": "candidate", "text": answer.strip()}]
    question = generate_text(
        INTERVIEWER_SYSTEM,
        f"{_interview_context(db, job)}\n\nTRANSCRIPT SO FAR:\n{_render_transcript(transcript)}",
    )
    # JSON column: reassign, never append in place (mutations aren't tracked).
    session.transcript = transcript + [{"role": "interviewer", "text": question.strip()}]
    db.commit()
    return session


def finish_interview(db: Session, session_id: int) -> PrepSession:
    session = _get_session(db, session_id, "interview", "active")
    job = db.get(Job, session.job_id)
    session.report = generate_json(
        DEBRIEF_SYSTEM,
        f"{_interview_context(db, job)}\n\nFULL TRANSCRIPT:\n{_render_transcript(list(session.transcript))}",
        DEBRIEF_SCHEMA,
    )
    session.status = "done"
    db.commit()
    return session


OA_SEARCH_SYSTEM = """\
You research company-specific online assessments (OA) and first-round screens
for internship candidates. Find concrete, recent reports: which platform the
company uses (HackerRank, CodeSignal, HireVue, ...), question styles and
difficulty, actual past questions where public, and timing. Prefer LeetCode
discussion threads, Glassdoor interview pages, and public GitHub prep repos.
Report facts with their sources; say clearly when something is unverified."""

OA_SYSTEM = """\
Synthesize the research below into an OA prep report for this candidate.
topics: the recurring problem categories to drill. sample_questions: concrete
questions/prompts found in the research, each with its source URL (never
invent questions — omit if none were found). links: the most useful URLs to
study. summary: 3-5 plain sentences — platform, format, difficulty, and the
single best use of the candidate's remaining prep time."""

OA_REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "topics": {"type": "array", "items": {"type": "string"}},
        "sample_questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "source": {"type": "string"},
                },
                "required": ["question", "source"],
                "additionalProperties": False,
            },
        },
        "links": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "topics", "sample_questions", "links"],
    "additionalProperties": False,
}


def _github_oa_snippets(company: str) -> str:
    """Public GitHub prep repos for this company, via api.github.com only.
    Best-effort: any failure returns what we have (possibly nothing)."""
    try:
        resp = httpx.get(
            f"{GITHUB_API}/search/repositories",
            params={"q": f'{company} interview OR "online assessment"', "per_page": 3},
            headers={"Accept": "application/vnd.github+json"},
            timeout=30,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
    except Exception:
        return ""
    parts = []
    for repo in items:
        body = ""
        try:
            readme = httpx.get(
                f"{GITHUB_API}/repos/{repo['full_name']}/readme",
                headers={"Accept": "application/vnd.github.raw+json"},
                timeout=30,
            )
            if readme.status_code == 200:
                body = readme.text[:4000]
        except Exception:
            pass
        parts.append(
            f"### {repo['full_name']} ({repo.get('html_url', '')})\n"
            f"{repo.get('description') or ''}\n{body}"
        )
    return "\n\n".join(parts)


def start_oa(db: Session, job_id: int) -> PrepSession:
    job = db.get(Job, job_id)
    if job is None:
        raise ValueError("job not found")
    session = PrepSession(job_id=job_id, kind="oa", status="running")
    db.add(session)
    db.commit()
    return session


def run_oa_research(session_id: int) -> None:
    """Body of the background thread (tests call it synchronously)."""
    db = SessionLocal()
    try:
        session = db.get(PrepSession, session_id)
        if session is None:
            return
        try:
            job = db.get(Job, session.job_id)
            search_text, sources = generate_search(
                OA_SEARCH_SYSTEM,
                f"Find company-specific online-assessment and first-round interview "
                f"questions for {job.company} ({job.title}).",
            )
            user_content = (
                f"WEB SEARCH FINDINGS:\n{search_text}\n\nSOURCE URLS:\n" + "\n".join(sources)
            )
            github = _github_oa_snippets(job.company)
            if github:
                user_content += f"\n\nPUBLIC GITHUB PREP REPOS:\n{github}"
            session.report = generate_json(OA_SYSTEM, user_content, OA_REPORT_SCHEMA)
            session.status = "done"
        # Broad on purpose: engine/network errors land on the row for the UI
        # instead of dying silently in a thread.
        except Exception as e:
            session.status, session.error = "error", str(e)[:2000]
        db.commit()
    finally:
        db.close()


def start_oa_research(session_id: int) -> None:
    threading.Thread(target=run_oa_research, args=(session_id,), daemon=True).start()
