import pytest

from app.services.latex import Edit, EditError, apply_edits, extract_structure

SAMPLE_TEX = r"""\documentclass{article}
\usepackage[margin=1in]{geometry}
\begin{document}
\section{Experience}
\begin{itemize}
\item Developed a web scraper that collected data from 100 sites.
\item Led a team of 4 students to build a mobile app.
\end{itemize}
\section{Skills}
Python, Java, and SQL.
\end{document}
"""


def test_applies_single_wording_edit():
    result = apply_edits(
        SAMPLE_TEX,
        [
            Edit(
                original="Developed a web scraper that collected data from 100 sites.",
                replacement="Engineered a Python web scraper aggregating data from 100+ sources.",
            )
        ],
    )
    assert "Engineered a Python web scraper" in result
    assert "Developed a web scraper" not in result


def test_rejects_edit_when_original_not_found():
    with pytest.raises(EditError, match="not found"):
        apply_edits(SAMPLE_TEX, [Edit(original="This text does not exist.", replacement="x")])


def test_rejects_ambiguous_original():
    tex = SAMPLE_TEX.replace(
        r"Python, Java, and SQL.",
        "Led a team of 4 students to build a mobile app.",
    )
    with pytest.raises(EditError, match="ambiguous"):
        apply_edits(
            tex,
            [Edit(original="Led a team of 4 students to build a mobile app.", replacement="x")],
        )


def test_rejects_edit_that_deletes_structural_command():
    with pytest.raises(EditError, match="structur"):
        apply_edits(
            SAMPLE_TEX,
            [
                Edit(
                    original=r"\item Led a team of 4 students to build a mobile app.",
                    replacement="Led a team of 4 students.",
                )
            ],
        )


def test_rejects_replacement_introducing_new_command():
    with pytest.raises(EditError, match="structur"):
        apply_edits(
            SAMPLE_TEX,
            [
                Edit(
                    original="Led a team of 4 students to build a mobile app.",
                    replacement=r"Led a team.\vspace{-2em}",
                )
            ],
        )


def test_allows_whitelisted_inline_formatting_changes():
    result = apply_edits(
        SAMPLE_TEX,
        [
            Edit(
                original="Led a team of 4 students to build a mobile app.",
                replacement=r"Led a team of \textbf{4 students} to ship a mobile app.",
            )
        ],
    )
    assert r"\textbf{4 students}" in result


def test_rejects_edit_inside_preamble():
    with pytest.raises(EditError, match="preamble"):
        apply_edits(
            SAMPLE_TEX,
            [Edit(original="[margin=1in]{geometry}", replacement="[margin=0.5in]{geometry}")],
        )


def test_rejects_unescaped_special_characters_in_replacement():
    with pytest.raises(EditError, match="unescaped"):
        apply_edits(
            SAMPLE_TEX,
            [
                Edit(
                    original="Led a team of 4 students to build a mobile app.",
                    replacement="Improved throughput by 40% for the team.",
                )
            ],
        )


def test_allows_escaped_special_characters_in_replacement():
    result = apply_edits(
        SAMPLE_TEX,
        [
            Edit(
                original="Led a team of 4 students to build a mobile app.",
                replacement=r"Improved throughput by 40\% for the team.",
            )
        ],
    )
    assert r"40\%" in result


def test_structure_identical_after_valid_edits():
    result = apply_edits(
        SAMPLE_TEX,
        [
            Edit(
                original="Developed a web scraper that collected data from 100 sites.",
                replacement="Engineered a distributed crawler indexing 100+ sources daily.",
            ),
            Edit(original="Python, Java, and SQL.", replacement="Python, TypeScript, and SQL."),
        ],
    )
    assert extract_structure(result) == extract_structure(SAMPLE_TEX)


def test_compile_tex_produces_single_page_pdf(tmp_path):
    from app.services.latex import compile_tex, pdf_page_count

    pdf_path = compile_tex(SAMPLE_TEX, tmp_path, "sample")
    assert pdf_path.exists()
    assert pdf_path.suffix == ".pdf"
    assert pdf_page_count(pdf_path) == 1


def test_compile_tex_raises_on_invalid_latex(tmp_path):
    from app.services.latex import CompileError, compile_tex

    with pytest.raises(CompileError):
        compile_tex(r"\documentclass{article}\begin{document}\undefinedcmd", tmp_path, "bad")


FONTAWESOME_TEX = r"""\documentclass{article}
\usepackage{fontawesome5}
\begin{document}
GitHub: \faGithub\ resume test.
\end{document}
"""


def test_compile_supports_pdflatex_only_templates(tmp_path):
    """User resume templates (Jake's template) use fontawesome5, which crashes
    Tectonic/XeTeX — compile must use pdfLaTeX when available."""
    from app.services.latex import compile_tex, pdf_page_count

    pdf_path = compile_tex(FONTAWESOME_TEX, tmp_path, "fa")
    assert pdf_page_count(pdf_path) == 1
