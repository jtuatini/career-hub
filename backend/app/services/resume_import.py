"""PDF → LaTeX resume import: monitored stage machine (extract → convert →
compile → verify → fix loop → review) turning an uploaded PDF resume into a
tailorable Jake's-template LaTeX resume. Verification is three independent
checks per round — fidelity (nothing dropped/invented/altered), fit (page
count + overfull boxes), alignment (visual inspection of the rendered page).
Nothing reaches the resume bank until the user accepts the verified result."""

import logging
import threading
from io import BytesIO
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.db.base import SessionLocal
from app.db.models import ImportSession, Resume
from app.services import resume_bank
from app.services.engine import generate_json, generate_json_with_image, generate_text
from app.services.ingest import extract_text
from app.services.latex import CompileError, compile_tex_info, pdf_page_count

logger = logging.getLogger(__name__)

STAGES = ["extract", "convert", "compile", "verify", "review"]
_PROGRESS = {"extract": 0.1, "convert": 0.35, "compile": 0.55, "verify": 0.75, "review": 1.0}
MAX_FIX_ROUNDS = 3
MIN_TEXT_CHARS = 200

# Jake's-template skeleton, trimmed to packages that compile under a stock
# TinyTeX pdflatex (deliberately NO fontawesome/marvosym — those are why the
# owner's personal templates need pdfLaTeX-only; imports must compile anywhere).
JAKES_TEMPLATE = r"""
\documentclass[letterpaper,11pt]{article}
\usepackage[empty]{fullpage}
\usepackage{titlesec}
\usepackage{enumitem}
\usepackage[hidelinks]{hyperref}
\usepackage{fancyhdr}
\usepackage{tabularx}
\pagestyle{fancy}
\fancyhf{}
\renewcommand{\headrulewidth}{0pt}
\renewcommand{\footrulewidth}{0pt}
\addtolength{\oddsidemargin}{-0.5in}
\addtolength{\evensidemargin}{-0.5in}
\addtolength{\textwidth}{1in}
\addtolength{\topmargin}{-.5in}
\addtolength{\textheight}{1.0in}
\urlstyle{same}
\raggedbottom
\raggedright
\setlength{\tabcolsep}{0in}
\titleformat{\section}{\vspace{-4pt}\scshape\raggedright\large}{}{0em}{}[\titlerule \vspace{-5pt}]
\newcommand{\resumeItem}[1]{\item\small{#1 \vspace{-2pt}}}
\newcommand{\resumeSubheading}[4]{
  \vspace{-2pt}\item
    \begin{tabular*}{0.97\textwidth}[t]{l@{\extracolsep{\fill}}r}
      \textbf{#1} & #2 \\
      \textit{\small#3} & \textit{\small #4} \\
    \end{tabular*}\vspace{-7pt}
}
\newcommand{\resumeProjectHeading}[2]{
    \item
    \begin{tabular*}{0.97\textwidth}{l@{\extracolsep{\fill}}r}
      \small#1 & #2 \\
    \end{tabular*}\vspace{-7pt}
}
\newcommand{\resumeSubHeadingListStart}{\begin{itemize}[leftmargin=0.15in, label={}]}
\newcommand{\resumeSubHeadingListEnd}{\end{itemize}}
\newcommand{\resumeItemListStart}{\begin{itemize}}
\newcommand{\resumeItemListEnd}{\end{itemize}\vspace{-5pt}}
\begin{document}
% HEADER: name, phone | email | linkedin | github (only what the source has)
% SECTIONS in source order, using \section{...}, \resumeSubHeadingListStart,
% \resumeSubheading{org}{location}{role}{dates}, \resumeItemListStart,
% \resumeItem{...} for bullets, \resumeProjectHeading for projects.
% A final \section{Additional} catches content that fits nowhere else.
\end{document}
"""

CONVERT_SYSTEM = """\
You convert resume text extracted from a PDF into a complete LaTeX resume
using EXACTLY the provided template preamble and structural macros. Rules:
- Output ONLY the complete .tex file, starting with \\documentclass. No
  markdown fences, no commentary before or after.
- Use only content present in the extracted text. Never invent, embellish,
  or silently drop anything. Content that fits no section goes into a final
  Additional section.
- Preserve numbers, dates, titles, and technology names verbatim.
- Escape LaTeX special characters in content: & % $ # _ become \\& \\% \\$ \\# \\_.\
"""

FIDELITY_SCHEMA = {
    "type": "object",
    "properties": {
        "dropped": {"type": "array", "items": {"type": "string"}},
        "invented": {"type": "array", "items": {"type": "string"}},
        "altered": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["dropped", "invented", "altered"],
    "additionalProperties": False,
}
FIDELITY_SYSTEM = """\
Compare a resume's source text against the body content of a LaTeX conversion.
Report ONLY real content differences (facts, numbers, dates, titles, bullets):
- dropped: source content missing from the LaTeX
- invented: LaTeX content not present in the source
- altered: content whose numbers/dates/titles changed
Formatting, ordering, and LaTeX syntax are NOT differences. Empty arrays mean
a faithful conversion."""

ALIGNMENT_SCHEMA = {
    "type": "object",
    "properties": {"issues": {"type": "array", "items": {"type": "string"}}},
    "required": ["issues"],
    "additionalProperties": False,
}
ALIGNMENT_SYSTEM = """\
You are inspecting a rendered resume page image for LAYOUT defects only:
overlapping text, text running past the right margin, misaligned date/location
columns, broken glyphs, stray template placeholders. Report each defect as one
short issue string. An empty list means the page looks clean. Content quality
is NOT your concern."""


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def _render_page_png(pdf_path: Path) -> bytes:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        bitmap = doc[0].render(scale=2.0)
        buf = BytesIO()
        bitmap.to_pil().save(buf, format="PNG")
        return buf.getvalue()
    finally:
        doc.close()


def _imports_dir() -> Path:
    d = settings.files_dir / "imports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def create_import(db: Session, filename: str, data: bytes, name: str, job_type: str) -> ImportSession:
    """Extract-or-die up front so scanned/image PDFs fail fast with a clear
    message instead of burning an engine call."""
    text = extract_text(filename, data)
    if len(text.strip()) < MIN_TEXT_CHARS:
        raise ValueError(
            "This PDF has no extractable text — it looks like a scanned image. "
            "Export a text-based PDF from your editor and try again."
        )
    s = ImportSession(filename=filename, name=name, job_type=job_type, state={})
    db.add(s)
    db.flush()
    pdf_path = _imports_dir() / f"import_{s.id}.pdf"
    pdf_path.write_bytes(data)
    s.state = {
        "extracted_text": text[:60000],
        "pdf_path": str(pdf_path),
        "source_page_count": pdf_page_count(pdf_path),
        "rounds": 0,
    }
    db.commit()
    return s


def start_run(session_id: int) -> None:
    threading.Thread(target=run_import, args=(session_id,), daemon=True).start()


def _set(db: Session, s: ImportSession, stage: str) -> None:
    s.stage = stage
    s.progress = _PROGRESS[stage]
    db.commit()


def _convert(extracted: str) -> str:
    tex = generate_text(
        CONVERT_SYSTEM,
        f"TEMPLATE:\n{JAKES_TEMPLATE}\n\nEXTRACTED RESUME TEXT:\n{extracted}",
    )
    return _strip_fences(tex)


def _fix(extracted: str, tex: str, report: dict) -> str:
    issues = [f"- [{cat}] {i}" for cat, items in report.items() for i in items]
    fixed = generate_text(
        CONVERT_SYSTEM,
        "Your previous conversion has verification findings. Produce a corrected "
        "complete .tex file fixing EXACTLY these issues and changing nothing "
        "else:\n" + "\n".join(issues)
        + f"\n\nPREVIOUS .tex:\n{tex}\n\nEXTRACTED RESUME TEXT:\n{extracted}",
    )
    return _strip_fences(fixed)


def _verify(db: Session, s: ImportSession, tex: str) -> dict:
    """Compile + three independent checks. Returns the report; stores the
    candidate PDF path in state. Raises CompileError if the tex won't build."""
    pdf_path, info = compile_tex_info(tex, _imports_dir(), f"import_{s.id}_candidate")
    report: dict = {"fidelity": [], "fit": [], "alignment": []}

    source_pages = s.state["source_page_count"]
    if info.page_count > source_pages:
        report["fit"].append(
            f"generated {info.page_count} pages but the original is {source_pages}"
        )
    if info.overfull_vbox_pt > settings.tailor_max_overfull_pt:
        report["fit"].append(
            f"content overflows the page bottom by {info.overfull_vbox_pt:.0f}pt"
        )

    fidelity = generate_json(
        FIDELITY_SYSTEM,
        f"SOURCE TEXT:\n{s.state['extracted_text']}\n\nLATEX CONVERSION:\n{tex}",
        FIDELITY_SCHEMA,
    )
    for cat in ("dropped", "invented", "altered"):
        report["fidelity"].extend(f"{cat}: {i}" for i in fidelity.get(cat, []))

    try:
        png = _render_page_png(pdf_path)
        alignment = generate_json_with_image(ALIGNMENT_SYSTEM, png, ALIGNMENT_SCHEMA)
        report["alignment"] = list(alignment.get("issues", []))
    except Exception as e:  # rendering/vision is best-effort; fit+fidelity still gate
        logger.warning("import %s alignment check skipped: %s", s.id, e)

    s.state = {**s.state, "candidate_pdf_path": str(pdf_path)}
    db.commit()
    return report


def run_import(session_id: int) -> None:
    """Stage machine to review or containment — mirrors apply.run_pipeline's
    error discipline: any failure lands in the row, never escapes the thread."""
    db = None
    try:
        db = SessionLocal()
        s = db.get(ImportSession, session_id)
        if s is None:
            return
        extracted = s.state["extracted_text"]

        _set(db, s, "convert")
        tex = _convert(extracted)

        rounds = 0
        while True:
            _set(db, s, "compile")
            _set(db, s, "verify")
            try:
                report = _verify(db, s, tex)
            except CompileError as e:
                report = {"fidelity": [], "fit": [f"does not compile: {e}"], "alignment": []}
                # The candidate PDF on record (if any) is from an earlier,
                # different tex — never let a stale preview outlive its tex.
                s.state = {**s.state, "candidate_pdf_path": None}
                db.commit()
            clean = not any(report.values())
            if clean or rounds >= MAX_FIX_ROUNDS:
                break
            rounds += 1
            tex = _fix(extracted, tex, report)

        s.state = {**s.state, "tex": tex, "report": report, "rounds": rounds}
        s.status = "review"
        _set(db, s, "review")
    except Exception as e:
        logger.warning("import %s failed: %s", session_id, e)
        try:
            if db is not None:
                db.rollback()
                s = db.get(ImportSession, session_id)
                if s is not None:
                    s.status = "error"
                    s.error = str(e)
                    db.commit()
        except Exception as inner:
            logger.error("import %s could not record error: %s", session_id, inner)
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


def accept(db: Session, s: ImportSession) -> Resume:
    """Save the verified tex as a NEW ROOT resume (its own family) — fully
    tailorable from here on. Used for both the clean case and save-with-warnings
    (the API layer decides whether to allow the latter)."""
    if s.status != "review" or not s.state.get("tex"):
        raise ValueError("Import has no reviewed LaTeX to accept")
    report = s.state.get("report") or {}
    if any(issue.startswith("does not compile") for issue in report.get("fit", [])):
        raise ValueError("Import's LaTeX does not compile — it can't be saved; re-run the import")
    resume = Resume(name=s.name, job_type=s.job_type, tex_source=s.state["tex"], parent_id=None)
    db.add(resume)
    db.flush()
    resume_bank.compile_and_store(db, resume)
    s.resume_id = resume.id
    s.status = "done"
    db.commit()
    return resume
