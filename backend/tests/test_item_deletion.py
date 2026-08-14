"""allow_item_deletion: the narrow, user-approved exception to wording-only."""

import pytest

from app.services.latex import Edit, EditError, apply_edits

DOC = (
    "\\documentclass{article}\\begin{document}\\section{Work}"
    "\\begin{itemize}"
    "\\item Built the data pipeline for ingest"
    "\\item Wrote \\textbf{fast} parsers in Rust"
    "\\end{itemize}\\end{document}"
)

JAKE_DOC = (
    "\\documentclass{article}\\newcommand{\\resumeItem}[1]{#1}\\begin{document}"
    "\\resumeItem{Shipped the billing service}"
    "\\resumeItem{Cut infra costs 30\\%}"
    "\\end{document}"
)

BRACKET_DOC = (
    "\\documentclass{article}\\begin{document}"
    "\\begin{itemize}"
    "\\item[Label] Built the pipeline"
    "\\item Wrote parsers"
    "\\end{itemize}\\end{document}"
)

DOLLAR_DOC = (
    "\\documentclass{article}\\begin{document}"
    "\\begin{itemize}"
    "\\item Cost $x$ dollars saved"
    "\\item Wrote parsers"
    "\\end{itemize}\\end{document}"
)

ORPHAN_DOC = (
    "\\documentclass{article}\\begin{document}"
    "\\begin{itemize}"
    "\\item A \\& B and more"
    "\\item Wrote parsers"
    "\\end{itemize}\\end{document}"
)

ESCAPED_TAIL_DOC = (
    "\\documentclass{article}\\begin{document}"
    "\\begin{itemize}"
    "\\item Revenue was \\$5\\% higher this quarter"
    "\\item Wrote parsers"
    "\\end{itemize}\\end{document}"
)

BARE_DOLLAR_TAIL_DOC = (
    "\\documentclass{article}\\begin{document}"
    "\\begin{itemize}"
    "\\item Revenue was $5 higher this quarter"
    "\\item Wrote parsers"
    "\\end{itemize}\\end{document}"
)

BARE_PERCENT_TAIL_DOC = (
    "\\documentclass{article}\\begin{document}"
    "\\begin{itemize}"
    "\\item Revenue was %5 higher this quarter"
    "\\item Wrote parsers"
    "\\end{itemize}\\end{document}"
)

BARE_AMP_TAIL_DOC = (
    "\\documentclass{article}\\begin{document}"
    "\\begin{itemize}"
    "\\item Revenue was &5 higher this quarter"
    "\\item Wrote parsers"
    "\\end{itemize}\\end{document}"
)

INLINE_TAIL_DOC = (
    "\\documentclass{article}\\begin{document}"
    "\\begin{itemize}"
    "\\item Managed the budget growth \\textbf{and grew revenue}"
    "\\item Wrote parsers"
    "\\end{itemize}\\end{document}"
)

ESCAPED_BRACE_TAIL_DOC = (
    "\\documentclass{article}\\begin{document}"
    "\\begin{itemize}"
    "\\item Revenue was \\{5\\} higher this quarter"
    "\\item Wrote parsers"
    "\\end{itemize}\\end{document}"
)

ROW_TERMINATOR_DOC = (
    "\\documentclass{article}\\newcommand{\\resumeItem}[1]{#1}\\begin{document}"
    "\\resumeItem{Shipped the billing service}\\\\"
    "\\resumeItem{Cut infra costs 30\\%}"
    "\\end{document}"
)

BRACKET_ROW_TERMINATOR_DOC = (
    "\\documentclass{article}\\newcommand{\\resumeItem}[1]{#1}\\begin{document}"
    "\\resumeItem{Shipped the billing service}\\\\[6pt]"
    "\\resumeItem{Cut infra costs 30\\%}"
    "\\end{document}"
)

# "%s" is filled with the row-terminator form that precedes the orphaned prose.
ROW_TERMINATOR_PROSE_DOC = (
    "\\documentclass{article}\\begin{document}"
    "\\begin{itemize}"
    "\\item Built the data pipeline"
    "%s and then some trailing prose"
    "\\item Wrote parsers"
    "\\end{itemize}\\end{document}"
)

ROW_TERMINATOR_GRAFT_DOC = (
    "\\documentclass{article}\\begin{document}"
    "\\begin{itemize}"
    "\\item Built the data pipeline"
    "\\item Wrote parsers"
    "\\\\ and then some trailing prose"
    "\\end{itemize}\\end{document}"
)

HEAD_PROSE_DOC = (
    "\\documentclass{article}\\begin{document}"
    "\\begin{itemize}"
    "\\item Built the data pipeline for ingest"
    "\\item Wrote parsers in Rust"
    "\\end{itemize}\\end{document}"
)

WHITESPACE_TAIL_DOC = (
    "\\documentclass{article}\\begin{document}\n"
    "\\begin{itemize}\n"
    "  \\item Built the data pipeline\n"
    "  \\item Wrote parsers\n"
    "\\end{itemize}\n\\end{document}\n"
)

TRAILING_ITEM_DOC = (
    "\\documentclass{article}\\begin{document}"
    "\\begin{itemize}"
    "\\item Built the data pipeline"
    "\\item Wrote parsers\n"
)

ESCAPED_SPECIAL_SEAM_DOC = (
    "\\documentclass{article}\\begin{document}"
    "\\begin{itemize}"
    "\\item Cut infra costs 30\\%"
    "\\item Wrote parsers"
    "\\end{itemize}\\end{document}"
)

# "%s" is filled with the macro that follows the deleted bullet prefix.
UNLISTED_COMMAND_TAIL_DOC = (
    "\\documentclass{article}\\begin{document}"
    "\\begin{itemize}"
    "\\item Managed the budget growth %s"
    "\\item Wrote parsers"
    "\\end{itemize}\\end{document}"
)

LIST_END_DOC = (
    "\\documentclass{article}"
    "\\newcommand{\\resumeItem}[1]{#1}\\newcommand{\\resumeItemListEnd}{}"
    "\\begin{document}"
    "\\resumeItem{Shipped the billing service}"
    "\\resumeItem{Cut infra costs 30\\%}"
    "\\resumeItemListEnd"
    "\\end{document}"
)


def test_default_mode_still_rejects_item_deletion():
    with pytest.raises(EditError, match="structure"):
        apply_edits(DOC, [Edit(original="\\item Built the data pipeline for ingest", replacement="")])


def test_deletion_mode_accepts_complete_item():
    out = apply_edits(
        DOC,
        [Edit(original="\\item Built the data pipeline for ingest", replacement="")],
        allow_item_deletion=True,
    )
    assert "data pipeline" not in out
    assert "fast" in out  # sibling bullet untouched


def test_deletion_mode_accepts_resumeitem_with_braces():
    out = apply_edits(
        JAKE_DOC,
        [Edit(original="\\resumeItem{Cut infra costs 30\\%}", replacement="")],
        allow_item_deletion=True,
    )
    assert "infra costs" not in out and "billing service" in out


def test_deletion_mode_rejects_section_and_environment_spans():
    with pytest.raises(EditError):
        apply_edits(DOC, [Edit(original="\\section{Work}", replacement="")], allow_item_deletion=True)
    with pytest.raises(EditError):
        apply_edits(
            DOC,
            [Edit(original="\\begin{itemize}\\item Built the data pipeline for ingest", replacement="")],
            allow_item_deletion=True,
        )


def test_deletion_mode_rejects_partial_item_span():
    # Deleting only part of a bullet's braces would unbalance the document.
    with pytest.raises(EditError):
        apply_edits(
            JAKE_DOC,
            [Edit(original="\\resumeItem{Cut infra costs", replacement="")],
            allow_item_deletion=True,
        )


def test_deletion_mode_normal_edits_still_validated():
    with pytest.raises(EditError, match="structural"):
        apply_edits(
            DOC,
            [Edit(original="Built the data pipeline", replacement="\\newpage Built")],
            allow_item_deletion=True,
        )


def test_deletion_mode_rejects_unbalanced_bracket_span():
    # "\item[Label" leaves a dangling "]" behind — an unmatched optional-arg bracket.
    with pytest.raises(EditError):
        apply_edits(
            BRACKET_DOC,
            [Edit(original="\\item[Label", replacement="")],
            allow_item_deletion=True,
        )


def test_deletion_mode_rejects_unbalanced_dollar_span():
    # "\item Cost $x" leaves a dangling "$" behind — opens math mode and never closes it.
    with pytest.raises(EditError):
        apply_edits(
            DOLLAR_DOC,
            [Edit(original="\\item Cost $x", replacement="")],
            allow_item_deletion=True,
        )


def test_deletion_mode_rejects_prefix_orphaning_prose():
    # "\item A \& B" is a prefix of the bullet; "and more" would be orphaned onto
    # nothing (no \item precedes it any more), breaking the LaTeX list structure.
    with pytest.raises(EditError, match="orphan"):
        apply_edits(
            ORPHAN_DOC,
            [Edit(original="\\item A \\& B", replacement="")],
            allow_item_deletion=True,
        )


def test_deletion_mode_rejects_orphan_prose_starting_with_escaped_special():
    # The orphaned tail "\$5\% higher this quarter" *looks* like it starts with a
    # backslash command, but \$ and \% are escaped specials (plain prose once
    # normalized) — not a structural boundary. Must still be rejected.
    with pytest.raises(EditError, match="orphan"):
        apply_edits(
            ESCAPED_TAIL_DOC,
            [Edit(original="\\item Revenue was", replacement="")],
            allow_item_deletion=True,
        )


def test_deletion_mode_rejects_orphan_prose_starting_with_bare_dollar():
    # The orphaned tail "$5 higher this quarter" has no backslash at all — a bare
    # special character is not a command anchor and must not count as a boundary.
    with pytest.raises(EditError, match="orphan"):
        apply_edits(
            BARE_DOLLAR_TAIL_DOC,
            [Edit(original="\\item Revenue was", replacement="")],
            allow_item_deletion=True,
        )


def test_deletion_mode_rejects_orphan_prose_starting_with_bare_percent():
    with pytest.raises(EditError, match="orphan"):
        apply_edits(
            BARE_PERCENT_TAIL_DOC,
            [Edit(original="\\item Revenue was", replacement="")],
            allow_item_deletion=True,
        )


def test_deletion_mode_rejects_orphan_prose_starting_with_bare_ampersand():
    with pytest.raises(EditError, match="orphan"):
        apply_edits(
            BARE_AMP_TAIL_DOC,
            [Edit(original="\\item Revenue was", replacement="")],
            allow_item_deletion=True,
        )


def test_deletion_mode_rejects_orphan_prose_starting_with_inline_formatting():
    # "\textbf{and grew revenue}" is an inline prose wrapper, not a new structural
    # unit: normalization unwraps it to bare prose. Deleting the bullet prefix would
    # leave that prose governed by no \item at all.
    with pytest.raises(EditError, match="orphan"):
        apply_edits(
            INLINE_TAIL_DOC,
            [Edit(original="\\item Managed the budget growth ", replacement="")],
            allow_item_deletion=True,
        )


def test_deletion_mode_rejects_orphan_prose_starting_with_escaped_literal():
    # "\{" is an escaped literal brace — backslash + non-letter, i.e. prose. Only a
    # real command or the row terminator "\\" may follow a deleted span.
    with pytest.raises(EditError, match="orphan"):
        apply_edits(
            ESCAPED_BRACE_TAIL_DOC,
            [Edit(original="\\item Revenue was ", replacement="")],
            allow_item_deletion=True,
        )


def test_deletion_mode_accepts_item_followed_by_row_terminator():
    # Jake's-template style: entries separated by "\\" row terminators. The tail
    # "\\\resumeItem{...}" is a genuine structural boundary and must be accepted.
    out = apply_edits(
        ROW_TERMINATOR_DOC,
        [Edit(original="\\resumeItem{Shipped the billing service}", replacement="")],
        allow_item_deletion=True,
    )
    assert "billing service" not in out and "infra costs" in out


def test_deletion_mode_accepts_item_followed_by_bracketed_row_terminator():
    # "\\[6pt]\resumeItem{...}": the row terminator carries an optional vertical-skip
    # argument, and a real bullet still follows it. Genuine boundary — must be accepted.
    out = apply_edits(
        BRACKET_ROW_TERMINATOR_DOC,
        [Edit(original="\\resumeItem{Shipped the billing service}", replacement="")],
        allow_item_deletion=True,
    )
    assert "billing service" not in out and "infra costs" in out


@pytest.mark.parametrize("terminator", ["\\\\", "\\\\*", "\\\\[6pt]"])
def test_deletion_mode_rejects_prose_after_row_terminator(terminator):
    # A row terminator is only a boundary because of what FOLLOWS it. Here it is
    # followed by prose, so deleting the preceding bullet leaves
    # "\begin{itemize}\\ and then some trailing prose" — which pdflatex refuses
    # ("Something's wrong--perhaps a missing \item"). Accepting the tail on the
    # strength of its leading "\\" alone is exactly the bug.
    with pytest.raises(EditError, match="orphan"):
        apply_edits(
            ROW_TERMINATOR_PROSE_DOC % terminator,
            [Edit(original="\\item Built the data pipeline", replacement="")],
            allow_item_deletion=True,
        )


def test_deletion_mode_rejects_row_terminator_prose_grafting_onto_previous_bullet():
    # The silent variant: the deleted bullet is the LAST one, so the orphaned
    # "\\ and then some trailing prose" lands inside the still-open previous
    # bullet. It compiles cleanly and ships mangled content — worse than a crash.
    with pytest.raises(EditError, match="orphan"):
        apply_edits(
            ROW_TERMINATOR_GRAFT_DOC,
            [Edit(original="\\item Wrote parsers", replacement="")],
            allow_item_deletion=True,
        )


def test_deletion_mode_rejects_span_opening_with_previous_bullet_prose():
    # The span OPENS mid-bullet: "for ingest" belongs to the previous bullet.
    # Leading prose contributes no structural tokens, so the normalized tokens[0]
    # is still \item and the span looks like a clean bullet. Excising it would
    # silently truncate the previous bullet's wording.
    with pytest.raises(EditError, match="structure"):
        apply_edits(
            HEAD_PROSE_DOC,
            [Edit(original="for ingest\\item Wrote parsers in Rust", replacement="")],
            allow_item_deletion=True,
        )


def test_deletion_mode_accepts_span_with_leading_whitespace():
    # Indentation before the item macro is not prose — the head check lstrips first.
    out = apply_edits(
        WHITESPACE_TAIL_DOC,
        [Edit(original="  \\item Wrote parsers\n", replacement="")],
        allow_item_deletion=True,
    )
    assert "Wrote parsers" not in out and "data pipeline" in out


def test_deletion_mode_accepts_whitespace_before_next_item():
    out = apply_edits(
        WHITESPACE_TAIL_DOC,
        [Edit(original="\\item Built the data pipeline", replacement="")],
        allow_item_deletion=True,
    )
    assert "data pipeline" not in out and "Wrote parsers" in out


def test_deletion_mode_rejects_escaped_special_at_the_seam():
    # The tail is "\%\item Wrote parsers": the seam token is the escaped percent —
    # prose. Normalizing the tail would delete it and let the boundary test anchor on
    # the *next* token (\item), hiding the orphan. The seam test must read the raw tail.
    with pytest.raises(EditError, match="orphan"):
        apply_edits(
            ESCAPED_SPECIAL_SEAM_DOC,
            [Edit(original="\\item Cut infra costs 30", replacement="")],
            allow_item_deletion=True,
        )


@pytest.mark.parametrize(
    "tail_macro",
    [
        "\\texttt{and grew revenue}",
        "\\textsc{and grew revenue}",
        "\\href{https://example.test}{and grew revenue}",
        "\\mbox{and grew revenue}",
        "\\emph{}and grew revenue",  # empty-brace wrapper: normalization strips it whole
        "\\hfill and grew revenue",
        "\\itemsep and grew revenue",  # allowlisted "item" is a PREFIX of this name
        "\\ldots and grew revenue",
    ],
)
def test_deletion_mode_rejects_orphan_prose_after_unlisted_command(tail_macro):
    # LaTeX's command vocabulary is open, so a denylist of prose wrappers can never be
    # complete. Anything that is not a known deletion boundary must be rejected.
    with pytest.raises(EditError, match="orphan"):
        apply_edits(
            UNLISTED_COMMAND_TAIL_DOC % tail_macro,
            [Edit(original="\\item Managed the budget growth ", replacement="")],
            allow_item_deletion=True,
        )


def test_deletion_mode_accepts_item_followed_by_list_end():
    # Jake's-template list closers end a bullet list, so they are genuine boundaries.
    out = apply_edits(
        LIST_END_DOC,
        [Edit(original="\\resumeItem{Cut infra costs 30\\%}", replacement="")],
        allow_item_deletion=True,
    )
    assert "infra costs" not in out and "billing service" in out


def test_deletion_mode_accepts_item_at_end_of_input():
    out = apply_edits(
        TRAILING_ITEM_DOC,
        [Edit(original="\\item Wrote parsers", replacement="")],
        allow_item_deletion=True,
    )
    assert "Wrote parsers" not in out and "data pipeline" in out
