import re
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import GeneratedDoc
from app.services import voice as voice_service
from app.services import writing
from app.services.engine import generate_text

COVER_LETTER_SYSTEM = (
    """You write concise, specific cover letters for internship applications.

Rules:
- Use only facts present in the resume; never invent experience, skills, or metrics.
- 250-350 words, 3-4 paragraphs, plain prose. No date/address header, no salutation, no
  closing — output the body paragraphs only.
- Connect the candidate's strongest relevant experience to what the job description asks for.
- Sound like a person, not a template: concrete, direct, zero filler phrases.

"""
    + writing.AI_WRITING_RULES
)


def draft_cover_letter(db: Session, job, resume_context: str) -> str:
    """Voice-aware draft: profile + exemplars + engine, then critique-refine.
    Returns plain prose body; caller stores it un-compiled."""
    system = COVER_LETTER_SYSTEM
    voice = voice_service.voice_context(db)
    if voice:
        system += f"\n\n{voice}"
    exemplars = db.scalars(
        select(GeneratedDoc)
        .where(
            GeneratedDoc.doc_type == "cover_letter",
            GeneratedDoc.approved.is_(True),
            GeneratedDoc.vetted.is_(True),
            GeneratedDoc.body_text.is_not(None),
        )
        .order_by(GeneratedDoc.created_at.desc())
        .limit(2)
    ).all()
    if exemplars:
        joined = "\n\n---\n\n".join(d.body_text for d in exemplars)
        system += f"\n\n## His past approved cover letters (match this voice)\n{joined}"
    body = generate_text(
        system,
        f"COMPANY: {job.company}\nROLE: {job.title}\n\nJOB DESCRIPTION:\n{job.jd_text}"
        + resume_context,
    ).strip()
    body = voice_service.critique_refine(db, body, f"{job.title} at {job.company}")
    return _remove_ai_tells(body)


def _remove_ai_tells(body: str) -> str:
    """Scrub dash-tells deterministically; if fuzzier tells remain, spend ONE
    revision round on them. Never fails the draft over polish."""
    body = writing.scrub(body)
    tells = writing.find_tells(body)
    if not tells:
        return body
    try:
        revised = generate_text(
            writing.REVISE_SYSTEM,
            "PROBLEMS TO FIX:\n"
            + "\n".join(f"- {t}" for t in tells)
            + f"\n\nDRAFT:\n{body}",
        ).strip()
        return writing.scrub(revised) if revised else body
    except Exception:
        return body


_UNESCAPED_SPECIAL = re.compile(r"(?<!\\)([&%#_$])")


def escape_latex(text: str) -> str:
    return _UNESCAPED_SPECIAL.sub(r"\\\1", text)


def _display_url(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", url).rstrip("/")


def build_cover_letter_tex(company: str, body: str, profile: dict[str, str] | None = None) -> str:
    """Full letter: letterhead from the user's profile fields (name, contact
    line), date, company block, salutation, body, signed closing. Missing
    profile fields degrade gracefully — absent lines are simply omitted."""
    profile = profile or {}
    name = profile.get("full_name", "").strip()

    contact_bits = [
        b
        for b in (
            profile.get("email", "").strip(),
            profile.get("phone", "").strip(),
            _display_url(profile.get("linkedin", "").strip()),
            _display_url(profile.get("github", "").strip()),
            ", ".join(p for p in (profile.get("city", "").strip(), profile.get("state", "").strip()) if p),
        )
        if b
    ]

    header = ""
    if name:
        header = f"{{\\LARGE\\scshape {escape_latex(name)}}}\\\\[3pt]\n"
        if contact_bits:
            header += f"{{\\small {' \\textbar\\ '.join(escape_latex(b) for b in contact_bits)}}}\\\\\n"
        header = (
            "\\begin{center}\n" + header + "\\end{center}\n"
            "\\vspace{-6pt}\\hrule\\vspace{18pt}\n"
        )

    today = date.today().strftime("%B %d, %Y").replace(" 0", " ")
    signature = f"Sincerely,\\\\[4pt]\n{escape_latex(name)}" if name else "Sincerely,"

    return (
        "\\documentclass[11pt]{article}\n"
        "\\usepackage[margin=1in]{geometry}\n"
        "\\setlength{\\parindent}{0pt}\n"
        "\\setlength{\\parskip}{9pt}\n"
        "\\pagestyle{empty}\n"
        "\\begin{document}\n"
        f"{header}"
        f"\\noindent {today}\n\n"
        "\\vspace{8pt}\n"
        f"\\noindent Hiring Team\\\\\n{escape_latex(company)}\n\n"
        "\\vspace{12pt}\n"
        "\\noindent Dear Hiring Team,\n\n"
        f"{escape_latex(body)}\n\n"
        "\\vspace{12pt}\n"
        f"\\noindent {signature}\n"
        "\\end{document}\n"
    )
