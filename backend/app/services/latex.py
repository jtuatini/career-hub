import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader


class EditError(Exception):
    pass


class CompileError(Exception):
    pass


@dataclass
class CompileInfo:
    page_count: int
    overfull_vbox_pt: float


_OVERFULL_VBOX = re.compile(r"Overfull \\vbox \(([0-9.]+)pt too high\)")


def _parse_overfull(log_text: str) -> float:
    """Largest overfull-vbox overflow reported by pdflatex, in points."""
    hits = [float(m) for m in _OVERFULL_VBOX.findall(log_text)]
    return max(hits, default=0.0)


@dataclass
class Edit:
    original: str
    replacement: str


DOC_MARKER = r"\begin{document}"

# Inline prose formatting that may legitimately change during rewording.
_INLINE_FORMATTING = re.compile(r"\\(?:textbf|textit|emph|underline)\{([^{}]*)\}")
# Escaped special characters allowed in prose: \% \& \# \_ \$
_ESCAPED_SPECIAL = re.compile(r"\\[%&#_$]")
# Everything that defines the template: control sequences, group/math/special chars.
_STRUCTURAL_TOKEN = re.compile(r"\\[A-Za-z@]+\*?|\\.|[{}\[\]$&~^_#%]")
# A control word: backslash + the LONGEST run of letters. `\resumeItemListEnd` must
# parse as its own name, never as the prefix `\resumeItem`.
_CONTROL_WORD = re.compile(r"\\([A-Za-z]+)")

# Page-fit trimming (the narrow, user-approved exception to wording-only):
# ONLY these bullet-item macros may head a deletable span.
DELETABLE_ITEM_COMMANDS = {"\\item", "\\resumeItem", "\\resumeSubItem"}
# ...and a deleted span may ONLY be followed by one of these: the closed set of macros
# that genuinely begin a new structural unit (a sibling bullet, or a list/environment
# closer). LaTeX's command vocabulary is open, so this is an allowlist, not a denylist:
# any unknown tail is prose until proven otherwise. Grow it if a real template needs it.
DELETION_BOUNDARY_COMMANDS = {
    "item", "resumeItem", "resumeSubItem", "end",
    "resumeItemListEnd", "resumeSubHeadingListEnd",
}
NEVER_DELETABLE = {
    "\\begin", "\\end", "\\section", "\\subsection",
    "\\documentclass", "\\usepackage", "\\newcommand",
}


def _deletion_allowed(span: str) -> bool:
    """A span may be excised iff it is one complete, balanced item macro.

    The head test is the mirror image of `_is_deletion_boundary`'s tail test, and
    for the same reason: leading prose contributes no structural tokens, so a span
    that OPENS with the previous bullet's trailing words still has `\\item` as its
    first *token*. Reading the RAW span settles it — the item macro must be the
    very first thing in it (indentation aside), or the deletion would silently
    truncate the previous bullet's wording.
    """
    raw = span.lstrip()
    head = _CONTROL_WORD.match(raw)
    # Exact membership on the full control word: `\itemsep` must not pass as `\item`.
    if head is None or "\\" + head.group(1) not in DELETABLE_ITEM_COMMANDS:
        return False
    tokens = _STRUCTURAL_TOKEN.findall(_normalize(span))
    if not tokens or tokens[0] not in DELETABLE_ITEM_COMMANDS:
        return False
    if any(t in NEVER_DELETABLE for t in tokens):
        return False
    depth = 0
    bracket_depth = 0
    dollar_count = 0
    for t in tokens:
        if t == "{":
            depth += 1
        elif t == "}":
            depth -= 1
            if depth < 0:
                return False
        elif t == "[":
            bracket_depth += 1
        elif t == "]":
            bracket_depth -= 1
            if bracket_depth < 0:
                return False
        elif t == "$":
            dollar_count += 1
    return depth == 0 and bracket_depth == 0 and dollar_count % 2 == 0


def _is_deletion_boundary(tail: str) -> bool:
    """True if the text right after a deleted span starts a new structural unit.

    Classifies the FIRST token of the RAW tail against a closed allowlist; anything
    else — unknown commands, escaped specials, comments, bare specials, prose — is
    rejected. Two rules make that fail-safe rather than merely strict:

    - Allowlist, not denylist: LaTeX's command vocabulary is open, so no list of
      prose-ish macros (`\\texttt`, `\\href`, `\\hfill`, ...) can ever be complete.
    - Raw, never normalized: `_normalize` deletes escaped specials and unwraps inline
      formatting, which would let the test anchor on a token *past* the seam — a tail
      of `\\%\\item Next` would read as `\\item` and hide the orphaned `\\%`.

    Over-rejection only costs a skipped trim (callers catch EditError); over-acceptance
    ships a document that pdflatex refuses or silently mangles.

    A row terminator (`\\\\`, `\\\\*`, `\\\\[6pt]`) is NOT a boundary on its own — it only
    separates entries, so what matters is what follows it. `\\\\ prose` is still orphaned
    prose: deleting a mid-list bullet leaves `\\begin{itemize}\\\\ prose` (pdflatex-fatal),
    and deleting the last one grafts the prose onto the previous bullet (compiles, ships
    mangled). So the terminator and its optional `*`/`[len]` argument are stripped and the
    same allowlist is re-applied to the remainder.
    """
    tail = tail.lstrip()
    if not tail:
        return True  # nothing follows: no prose can be orphaned
    if tail.startswith("\\\\"):
        rest = tail[2:]
        if rest.startswith("*"):
            rest = rest[1:]
        if rest.startswith("["):
            close = rest.find("]")
            if close == -1:
                return False  # unclosed optional argument: not parseable, so not a boundary
            rest = rest[close + 1 :]
        return _is_deletion_boundary(rest)  # terminates: `rest` is strictly shorter
    match = _CONTROL_WORD.match(tail)
    return match is not None and match.group(1) in DELETION_BOUNDARY_COMMANDS


def _normalize(text: str) -> str:
    prev = None
    while prev != text:
        prev = text
        text = _INLINE_FORMATTING.sub(r"\1", text)
    return _ESCAPED_SPECIAL.sub("", text)


def extract_structure(tex: str) -> list[str]:
    """The template fingerprint: every structural token, in order, ignoring
    whitelisted inline formatting and escaped specials. Wording-only edits
    must leave this sequence identical."""
    return _STRUCTURAL_TOKEN.findall(_normalize(tex))


def _validate_replacement(replacement: str) -> None:
    residue = _normalize(replacement)
    if "\\" in residue or "{" in residue or "}" in residue:
        raise EditError(
            "replacement introduces structural LaTeX (commands or braces): "
            f"{replacement[:80]!r}"
        )
    bare = [c for c in residue if c in "$&~^_#%"]
    if bare:
        raise EditError(
            f"replacement contains unescaped special character(s) {bare}; "
            f"escape them (e.g. \\%): {replacement[:80]!r}"
        )


def apply_edits(tex: str, edits: list[Edit], allow_item_deletion: bool = False) -> str:
    """Apply wording-only edits by exact string replacement.

    Raises EditError if an edit cannot be located unambiguously, touches the
    preamble, or would change the structural token stream of the document.

    `allow_item_deletion` is the one sanctioned exception: an empty-replacement
    edit may excise one complete, balanced item macro (see `_deletion_allowed`)
    when the page-fit guard needs to trim a bullet. Default mode is unchanged.
    """
    result = tex
    for edit in edits:
        count = result.count(edit.original)
        if count == 0:
            raise EditError(f"original text not found: {edit.original[:80]!r}")
        if count > 1:
            raise EditError(
                f"original text is ambiguous ({count} occurrences): {edit.original[:80]!r}"
            )
        pos = result.find(edit.original)
        marker_pos = result.find(DOC_MARKER)
        if marker_pos != -1 and pos < marker_pos:
            raise EditError(f"edit targets the preamble: {edit.original[:80]!r}")
        _validate_replacement(edit.replacement)

        candidate = result[:pos] + edit.replacement + result[pos + len(edit.original) :]
        if extract_structure(candidate) != extract_structure(result):
            is_deletion = allow_item_deletion and edit.replacement == ""
            if not (is_deletion and _deletion_allowed(edit.original)):
                raise EditError(
                    f"edit would alter document structure: {edit.original[:80]!r}"
                )
            if not _is_deletion_boundary(result[pos + len(edit.original) :]):
                raise EditError(
                    f"deletion would orphan prose after the item span: {edit.original[:80]!r}"
                )
            span_tokens = _STRUCTURAL_TOKEN.findall(_normalize(edit.original))
            before = extract_structure(result)
            after = extract_structure(candidate)
            # The only change must be this span's tokens leaving as one slice.
            removed_ok = False
            for i in range(len(before) - len(span_tokens) + 1):
                if before[i : i + len(span_tokens)] == span_tokens and before[:i] + before[i + len(span_tokens):] == after:
                    removed_ok = True
                    break
            if not removed_ok:
                raise EditError(
                    f"deletion changes more than the item span: {edit.original[:80]!r}"
                )
        result = candidate
    return result


def _find_pdflatex() -> str | None:
    tinytex = Path.home() / "Library/TinyTeX/bin/universal-darwin/pdflatex"
    if tinytex.exists():
        return str(tinytex)
    return shutil.which("pdflatex")


def compile_tex_info(tex: str, out_dir: Path, name: str) -> tuple[Path, CompileInfo]:
    """Compile LaTeX source to <out_dir>/<name>.pdf, returning page-fit data.

    Prefers pdfLaTeX (most resume templates target it — fontawesome5 et al.
    crash Tectonic's XeTeX engine); falls back to Tectonic if no pdflatex
    is installed. Overfull-vbox detection only runs on the pdflatex path
    (Tectonic reports 0.0); the page-count guard still applies either way.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    pdflatex = _find_pdflatex()
    with tempfile.TemporaryDirectory() as workdir:
        tex_file = Path(workdir) / f"{name}.tex"
        tex_file.write_text(tex)
        if pdflatex:
            cmd = [
                pdflatex,
                "-interaction=nonstopmode",
                "-halt-on-error",
                f"-output-directory={workdir}",
                str(tex_file),
            ]
        else:
            cmd = ["tectonic", "--outdir", str(workdir), str(tex_file)]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        built_pdf = Path(workdir) / f"{name}.pdf"
        if proc.returncode != 0 or not built_pdf.exists():
            raise CompileError(_extract_compile_error(proc.stdout, proc.stderr))
        overfull_pt = 0.0
        if pdflatex:
            log_text = proc.stdout or ""
            log_file = Path(workdir) / f"{name}.log"
            if log_file.exists():
                log_text += log_file.read_text(errors="ignore")
            overfull_pt = _parse_overfull(log_text)
        pdf_path = out_dir / f"{name}.pdf"
        shutil.move(str(built_pdf), pdf_path)
    return pdf_path, CompileInfo(page_count=pdf_page_count(pdf_path), overfull_vbox_pt=overfull_pt)


def compile_tex(tex: str, out_dir: Path, name: str) -> Path:
    """Compile LaTeX to <out_dir>/<name>.pdf. See compile_tex_info for overflow data."""
    path, _ = compile_tex_info(tex, out_dir, name)
    return path


def _extract_compile_error(stdout: str, stderr: str) -> str:
    """pdflatex reports errors as '!'-prefixed lines in stdout; tectonic uses stderr."""
    error_lines = [line for line in stdout.splitlines() if line.startswith("!")]
    if error_lines:
        return " ".join(error_lines[:4])
    return stderr.strip() or stdout.strip()[-500:] or "unknown LaTeX error"


def pdf_page_count(pdf_path: Path) -> int:
    return len(PdfReader(pdf_path).pages)
