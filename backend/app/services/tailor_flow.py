"""Tailor-to-document flow shared by the API route and the apply pipeline:
tailor, compile, page-guarantee tighten loop, hard-fail cleanup, divergence."""

import logging
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import DocType, GeneratedDoc, Job, MemoryEntry, Resume
from app.services import fit
from app.services import memory as memory_service
from app.services.latex import CompileError, CompileInfo, compile_tex_info
from app.services.tailor import compute_divergence, tailor_resume

logger = logging.getLogger(__name__)

SHORT_TAIL_MAX_WORDS = 3


class PageOverflowError(Exception):
    def __init__(self, pages: int, cap: int, detail: str | None = None):
        self.pages, self.cap = pages, cap
        if detail:
            message = (
                f"Tailored resume still {detail} even after "
                f"{settings.tailor_max_tighten_rounds} tighten rounds — trim the base resume or retry."
            )
        else:
            message = (
                f"Tailored resume is {pages} pages but must fit in {cap} even after "
                f"{settings.tailor_max_tighten_rounds} tighten rounds — trim the base resume or retry."
            )
        super().__init__(message)


@dataclass
class TailorOutcome:
    doc: GeneratedDoc
    pages: int
    warnings: list[str] = field(default_factory=list)
    applied: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    divergence: float = 0.0


def _memory_context(db: Session, jd_text: str) -> str:
    """Graph-expanded brain context for this JD, formatted for the tailor prompt.
    Skips even loading the embedding model when the brain is empty."""
    if db.query(MemoryEntry.id).filter(MemoryEntry.embedding.is_not(None)).first() is None:
        return ""
    return memory_service.retrieve_context(db, jd_text[:2000], k_seeds=5).markdown


def _compile_doc_info(doc: GeneratedDoc) -> CompileInfo:
    pdf_path, info = compile_tex_info(doc.tex_source, settings.files_dir / "docs", f"doc_{doc.id}")
    doc.pdf_path = str(pdf_path)
    return info


def _overflows(info: CompileInfo, base_pages: int) -> bool:
    return info.page_count > base_pages or info.overfull_vbox_pt >= settings.tailor_max_overfull_pt


def _describe_overflow(info: CompileInfo, base_pages: int) -> str:
    if info.page_count > base_pages:
        return f"compiles to {info.page_count} pages but must fit in {base_pages}"
    return f"overflows the bottom of the page by {info.overfull_vbox_pt:.0f}pt"


def _describe_overflow_with_fit(tex: str, info: CompileInfo, base_pages: int) -> str:
    """Wraps _describe_overflow with the fit estimator's section-level diagnosis
    (which section is biggest) when it predicts overflow. The compiled page
    count from `info` remains the authoritative overflow signal driving the
    tighten loop; this only adds detail to the prompt sent back to the AI."""
    desc = _describe_overflow(info, base_pages)
    report = fit.estimate(tex)
    if not report.fits:
        desc += f" {fit.describe(report)}"
    return desc


# Generous upper bound on the y-gap between consecutive visual lines of the
# same wrapped bullet (single-line-spaced 9-12pt body text lands well under
# this). A gap past it means we've left the bullet's prose -- most commonly
# the page-number footer that `article`'s default \pagestyle{plain} draws
# many hundred points below the last line of body text.
MAX_LINE_GAP_PT = 24.0


def _short_tail_bullets(pdf_path: Path | str) -> list[str]:
    """Bullets whose last wrapped line holds <= SHORT_TAIL_MAX_WORDS words --
    trimming a few words there recovers a whole line. Best-effort: [] on any
    failure. Geometry via pypdf's text visitor (x = indent, y = line)."""
    try:
        from pypdf import PdfReader

        fragments: list[tuple[float, float, str]] = []  # (y, x, text)

        def visitor(text, cm, tm, font_dict, font_size):
            if text and text.strip():
                fragments.append((tm[5], tm[4], text))

        reader = PdfReader(str(pdf_path))
        reader.pages[0].extract_text(visitor_text=visitor)

        # Group fragments into visual lines by y (descending). pdflatex emits
        # one text-showing operator per typeset line, so a bullet marker and
        # the first line of its prose share a y and get joined into one
        # "line" here; wrapped continuation lines land at their own, lower y.
        lines: dict[float, list[tuple[float, str]]] = {}
        for y, x, text in fragments:
            key = round(y, 1)
            lines.setdefault(key, []).append((x, text))
        # Sort each line's fragments by x only: a bullet marker and its first
        # line of prose commonly share the exact same x, and sorting on the
        # full (x, text) tuple would then tie-break on text, which can put a
        # unicode bullet glyph (e.g. U+2022) after ASCII prose lexically and
        # scramble the marker out of leading position. Sorting on x alone
        # keeps ties in original (reading) order via Python's stable sort.
        ordered = [
            (y, " ".join(t for _, t in sorted(parts, key=lambda p: p[0])).strip())
            for y, parts in sorted(lines.items(), reverse=True)
        ]

        # Group lines into bullets: a line starting with a bullet glyph opens
        # a new bullet; each following line is a continuation of it as long
        # as the y-gap from the previous line stays within one line height.
        # A bigger jump (footer, section break) closes out whatever bullet
        # was open without folding in the unrelated line.
        bullets: list[list[str]] = []
        current: list[str] | None = None
        prev_y: float | None = None
        for y, text in ordered:
            is_marker = text.lstrip().startswith(("•", "-", "–", "*"))
            if is_marker:
                if current:
                    bullets.append(current)
                current = [text.lstrip("•-–* ")]
            elif current is not None and prev_y is not None and prev_y - y <= MAX_LINE_GAP_PT:
                current.append(text)
            else:
                if current:
                    bullets.append(current)
                current = None
            prev_y = y
        if current:
            bullets.append(current)

        return [
            " ".join(bullet)
            for bullet in bullets
            if len(bullet) >= 2 and len(bullet[-1].split()) <= SHORT_TAIL_MAX_WORDS
        ]
    except Exception as e:  # noqa: BLE001 -- analysis is contractually best-effort
        logger.warning("short-tail bullet analysis failed: %s", e)
        return []


def tailor_to_doc(
    db: Session, resume: Resume, job: Job, jd_text: str, guidance: str | None = None
) -> TailorOutcome:
    """Raises PageOverflowError (after rollback + PDF cleanup) or CompileError
    (also after rollback + PDF cleanup). Assumes `job` is already committed.

    `guidance` (e.g. formatted ATS scan findings) is threaded into the FIRST
    tailor pass as `extra_instruction` — same mechanism the tighten loop below
    already uses, so it flows through the existing wording-only prompt/validator
    machinery unchanged."""
    memory_context = _memory_context(db, jd_text)
    result = tailor_resume(
        resume.tex_source, jd_text, memory_context=memory_context, extra_instruction=guidance or ""
    )
    warnings: list[str] = []

    doc = GeneratedDoc(
        job_id=job.id,
        base_resume_id=resume.id,
        doc_type=DocType.RESUME,
        tex_source=result.tex,
    )
    db.add(doc)
    db.flush()

    deleted_edits: list[dict] = []
    try:
        info = _compile_doc_info(doc)
        base_pages = resume.page_count or info.page_count
        rounds = 0
        while _overflows(info, base_pages) and rounds < settings.tailor_max_tighten_rounds:
            # Exempt from the divergence budget: compute_divergence is checked against
            # `result.tex` (the already-tailored intermediate), and a legitimate
            # page-fitting shrink can look like near-total churn on that smaller base
            # even though it's a small change relative to the original resume. The
            # base-vs-final divergence warning below still reports total drift from
            # the untailored resume, so runaway edits are still surfaced to the user.
            rounds += 1
            last_round = rounds == settings.tailor_max_tighten_rounds
            overflow_desc = _describe_overflow_with_fit(result.tex, info, base_pages)
            if not last_round:
                targets = _short_tail_bullets(doc.pdf_path) if doc.pdf_path else []
                target_block = (
                    "\nThese bullets waste a nearly-empty last line — shorten EACH by a few "
                    "words so it fits on one line fewer:\n"
                    + "\n".join(f"- {t}" for t in targets)
                    if targets
                    else ""
                )
                extra = (
                    f"The tailored resume now {overflow_desc}. Shorten the wording of the "
                    f"longest bullets (wording-only) until it fits.{target_block}"
                )
                allow_delete = False
            else:
                # The override sentence is load-bearing: TAILOR_SYSTEM tells the model an
                # edit's original is "prose text only ... Never include LaTeX commands".
                # Obeying that here yields a prose-only original with an empty replacement,
                # which passes DEFAULT validation (removing prose changes no structural
                # tokens) and ships a dangling, empty "\item " glyph.
                extra = (
                    f"The tailored resume still {overflow_desc} after wording compression. "
                    "Compress further where possible, and as a LAST resort you MAY delete "
                    "whole bullet lines: return an edit whose replacement is an empty string "
                    "and whose original is the COMPLETE bullet line (including its \\item or "
                    "\\resumeItem command). For deletion edits ONLY, this overrides the "
                    "prose-only rule: the original MUST be the complete bullet line beginning "
                    "with its \\item or \\resumeItem command. Delete the bullets LEAST "
                    "relevant to this job, one at a time, fewest deletions that fit. Judge "
                    "relevance against the JOB DESCRIPTION provided above. Never delete "
                    "sections or headers."
                )
                allow_delete = True
            tighten = tailor_resume(
                result.tex,
                jd_text,
                extra_instruction=extra,
                budget=1.0,
                allow_item_deletion=allow_delete,
            )
            if allow_delete:
                deleted_edits += [
                    e for e in tighten.applied if not (e.get("replacement") or "").strip()
                ]
            result.applied += tighten.applied
            result.rejected += tighten.rejected
            result.tex = tighten.tex
            doc.tex_source = result.tex
            info = _compile_doc_info(doc)
        if _overflows(info, base_pages):
            # Recompute from the FINAL info: the loop-local overflow_desc (if any)
            # describes the state BEFORE the last tighten round's compile, and is
            # stale once we get here.
            raise PageOverflowError(
                info.page_count, base_pages, detail=_describe_overflow(info, base_pages)
            )
    except (CompileError, PageOverflowError):
        # Clean up the flushed doc row and any stale PDF on compile failure or overflow.
        stale_pdf = doc.pdf_path
        db.rollback()
        if stale_pdf:
            Path(stale_pdf).unlink(missing_ok=True)
        raise

    divergence = compute_divergence(resume.tex_source, result.tex)
    doc.divergence = divergence
    if divergence > settings.tailor_max_divergence:
        warnings.append(
            f"Tailored resume changed {divergence:.0%} of the base wording "
            f"(budget {settings.tailor_max_divergence:.0%}) — review before approving."
        )

    if deleted_edits:
        n = len(deleted_edits)
        warnings.append(
            f"Page-fit: {n} bullet{'s' if n != 1 else ''} cut (least job-relevant) — review before approving."
        )

    doc.edits = result.applied
    db.commit()
    return TailorOutcome(
        doc=doc,
        pages=info.page_count,
        warnings=warnings,
        applied=result.applied,
        rejected=result.rejected,
        divergence=divergence,
    )
