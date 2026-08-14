"""Supplemental-question answering: retrieve from the brain, draft in the
user's voice (few-shot from their past approved answers), return the draft plus
exactly what context was used so the UI can show its work."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import CompanyResearch, Job
from app.services import memory as memory_service
from app.services import voice as voice_service
from app.services import writing
from app.services.engine import generate_text

SYSTEM_CORE = """\
You draft answers to internship application questions as the applicant, in first person.
Use ONLY the memories provided — never invent experiences, numbers, or names.
Be brief: 40-90 words, two to four plain sentences. Go longer ONLY when the
question itself demands it (an essay prompt or a stated word count — then land
just past its minimum). Answer the question directly in the first sentence.
Ground the answer in ONE specific detail from the applicant's experience or
interests; one is enough — do not tour their background. When company research is provided,
tie the answer to one concrete fact from it. Recruiters skim: no preamble, no
restating the question, no closing summary sentence.

Your output is pasted VERBATIM into the form field. Never include
meta-commentary: no prefaces, no "here's a draft", no notes about missing
research or thin context, no explanations of what you could or couldn't
verify. If context is thin, silently write the best honest answer from what
you have — the word budget still applies. Return only the answer text.\
"""

GENERIC_VOICE = """\
Match the plain, direct voice of the example answers when given. Concrete beats
grand; no clichés ("passionate", "fast learner"), no flattery padding.\
"""


# "250-400 words", "250 to 400 words", "at least 250 words", "minimum of 250
# words", "250 words minimum" — the floor a question states explicitly.
# Testbed audit: the 40-90 default beat a stated 250-word minimum without this.
_WORD_FLOOR_RE = re.compile(
    r"(?:(?P<lo>\d{2,4})\s*(?:[-–—]|to)\s*\d{2,4}\s*words?)"
    r"|(?:at\s+least\s+(?P<min1>\d{2,4})\s*words?)"
    r"|(?:minimum(?:\s+of)?\s+(?P<min2>\d{2,4})\s*words?)"
    r"|(?:(?P<min3>\d{2,4})\s*[- ]?words?\s+minimum)",
    re.IGNORECASE,
)


def stated_word_floor(question: str) -> int | None:
    m = _WORD_FLOOR_RE.search(question)
    if m is None:
        return None
    value = m.group("lo") or m.group("min1") or m.group("min2") or m.group("min3")
    return int(value)


def _cached_research(db: Session, job: Job) -> str | None:
    """Latest research brief for this job, cache-only by design: drafting an
    answer must never trigger a live web search (the pipeline and the Jobs tab
    own that). Missing research just means the answer skips that grounding."""
    row = db.scalar(
        select(CompanyResearch)
        .where(CompanyResearch.job_id == job.id)
        .order_by(CompanyResearch.created_at.desc())
    )
    return row.findings if row else None


def draft_answer(db: Session, question: str, job_id: int | None = None) -> dict:
    job = db.get(Job, job_id) if job_id else None
    retrieval_query = question if job is None else f"{question}\n{job.company} {job.title}"
    ctx = memory_service.retrieve_context(db, retrieval_query, k_seeds=6)
    past = memory_service.search_qa(db, question, k=3)

    voice = voice_service.voice_context(db)
    system = SYSTEM_CORE + "\n\n" + writing.AI_WRITING_RULES + "\n\n" + (voice or GENERIC_VOICE)
    if past:
        examples = "\n\n".join(
            f"Q: {qa.question}\nA: {qa.answer}" for qa, _ in past
        )
        system += f"\n\nExamples of the applicant's approved past answers:\n\n{examples}"

    parts = [f"Question to answer:\n{question}"]
    floor = stated_word_floor(question)
    if floor:
        parts.append(
            f"LENGTH REQUIREMENT: this question demands at least {floor} words. "
            f"The 40-90 word default does NOT apply here — write at least {floor} "
            "words and land just above that floor. Count before you finish."
        )
    if job is not None:
        jd = (job.jd_text or "")[:6000]
        parts.append(f"The application is for {job.title} at {job.company}.\nJob description:\n{jd}")
        research = _cached_research(db, job)
        if research:
            parts.append(
                f"Company research (use at most one concrete fact):\n{research[:2500]}"
            )
    if ctx.entries:
        parts.append(f"The applicant's relevant memories (with their graph connections):\n{ctx.markdown}")
    else:
        parts.append(
            "No relevant memories were found. Say what kind of experience the answer "
            "needs and draft only what can be honestly generic."
        )

    draft = writing.scrub(generate_text(system, "\n\n---\n\n".join(parts)))
    return {
        "draft": draft,
        "memories_used": [
            {"id": e.id, "title": e.title, "score": round(s, 4)} for e, s in ctx.seeds
        ],
        "past_answers_used": [
            {"id": qa.id, "question": qa.question, "score": round(s, 4)} for qa, s in past
        ],
    }
