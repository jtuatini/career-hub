import difflib
import re
from dataclasses import dataclass, field

from app.config import settings
from app.services.latex import Edit, EditError, apply_edits

EDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "original": {
                        "type": "string",
                        "description": "Exact verbatim span copied from the .tex source, prose only",
                    },
                    "replacement": {
                        "type": "string",
                        "description": "New wording for that span",
                    },
                },
                "required": ["original", "replacement"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["edits"],
    "additionalProperties": False,
}

TAILOR_SYSTEM = """You tailor LaTeX resumes to job descriptions through wording-only edits.

Hard rules:
- NEVER invent experiences, skills, tools, metrics, dates, or facts that are not already in the resume. Rewording and re-emphasizing what exists is allowed; fabrication is not.
- Each edit's "original" must be an exact, verbatim, unique substring of the .tex source — prose text only (bullet text, summary lines, skill lists). Never include LaTeX commands, environments, or preamble in the span unless unavoidable, and never change them.
- Replacements are plain prose. Escape LaTeX specials (\\% \\& \\# \\_ \\$). The only formatting commands allowed in replacements are \\textbf, \\textit, \\emph.
- Keep replacements close to the original length so the document stays the same number of pages. Shorter is fine; meaningfully longer is not.
- Mirror the vocabulary of the job description where it is truthful to do so (keywords matter for ATS scanning).
- No AI-writing tells in replacements: never em-dashes (—), never three-item "A, B, and C" lists, no buzzwords like "leverage", "spearheaded", "seamless", "robust", "dynamic". Plain, specific verbs.
- Edit only what improves fit for this job; leave everything else untouched.
- Order the edits list most-important-first. A rewording budget caps total change; if it is hit, edits at the end of your list are dropped."""

_WS_RE = re.compile(r"\s+")


def _word_tokens(text: str) -> list[str]:
    """Prose words only: tokens starting with a backslash are LaTeX commands."""
    return [t for t in _WS_RE.split(text) if t and not t.startswith("\\")]


def compute_divergence(base_tex: str, new_tex: str) -> float:
    """Fraction of the base document's prose words that changed."""
    base, new = _word_tokens(base_tex), _word_tokens(new_tex)
    if not base:
        return 0.0
    matcher = difflib.SequenceMatcher(None, base, new, autojunk=False)
    changed = sum(
        max(i2 - i1, j2 - j1)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    )
    return min(1.0, changed / len(base))


@dataclass
class TailorResult:
    tex: str
    applied: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    divergence: float = 0.0


def _default_generate(system: str, user_content: str, schema: dict, max_tokens: int = 16000):
    from app.services import engine

    return engine.generate_json(system, user_content, schema, max_tokens)


def tailor_resume(
    tex: str,
    jd_text: str,
    *,
    generate=_default_generate,
    extra_instruction: str = "",
    memory_context: str = "",
    budget: float | None = None,
    allow_item_deletion: bool = False,
) -> TailorResult:
    """Ask the model for a wording-only edit list and apply each edit.

    Edits that fail validation (not found, ambiguous, structural) are rejected
    individually and reported; valid edits are still applied.

    `budget` caps the fraction of the base document's prose that may change,
    checked against `tex` (this call's starting point) — not any earlier
    baseline. Pass None (the default) to use `settings.tailor_max_divergence`.
    Callers doing a secondary pass over already-tailored text (e.g. the
    page-guard tighten round in generate.py) should pass a permissive value
    explicitly, since the budget here is relative to the intermediate `tex`,
    not the original resume.

    `allow_item_deletion` is threaded through to every `apply_edits` call
    below; it is the narrow, user-approved exception that lets an
    empty-replacement edit excise one complete bullet item (see
    `latex._deletion_allowed`). Default is False — off unless a caller opts in.
    """
    memory_block = (
        "\n\nAPPLICANT BACKGROUND (retrieved from his memory bank — use it to choose "
        "truthful emphasis and vocabulary; the no-fabrication rule still applies to "
        f"the resume itself):\n{memory_context}"
        if memory_context
        else ""
    )
    user_content = (
        f"JOB DESCRIPTION:\n{jd_text}\n\n"
        f"RESUME (.tex source):\n{tex}\n"
        f"{memory_block}\n\n"
        "Return the wording-only edit list that best tailors this resume to the job."
        + (f"\n\nADDITIONAL INSTRUCTION: {extra_instruction}" if extra_instruction else "")
    )
    payload = generate(TAILOR_SYSTEM, user_content, EDIT_SCHEMA)

    result = TailorResult(tex=tex)
    budget = settings.tailor_max_divergence if budget is None else budget
    items = payload.get("edits", [])
    for index, item in enumerate(items):
        edit = Edit(original=item["original"], replacement=item["replacement"])
        try:
            candidate = apply_edits(result.tex, [edit], allow_item_deletion=allow_item_deletion)
        except EditError as e:
            result.rejected.append({**item, "reason": str(e)})
            continue
        if compute_divergence(tex, candidate) > budget:
            reason = f"divergence budget ({budget:.0%}) reached"
            result.rejected.extend({**rest, "reason": reason} for rest in items[index:])
            break
        result.tex = candidate
        result.applied.append(item)
    result.divergence = compute_divergence(tex, result.tex)
    return result
