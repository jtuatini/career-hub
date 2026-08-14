"""Deterministic overflow estimator: chars-per-line + per-section overhead math."""

from app.services import fit


def _doc(body: str) -> str:
    return "\\documentclass{article}\\begin{document}\n" + body + "\n\\end{document}"


def test_visible_chars_ignore_commands():
    assert fit._visible_chars("\\textbf{Built} a \\emph{fast} parser") == len("Built a fast parser")


def test_item_line_count_wraps_by_chars_per_line():
    short = "x" * (fit.CHARS_PER_LINE - 5)
    long = "x" * (fit.CHARS_PER_LINE * 2 + 5)
    assert fit._wrapped_lines(short) == 1
    assert fit._wrapped_lines(long) == 3


def test_sections_are_split_and_counted():
    body = (
        "\\section{Education}\n\\resumeItem{" + "e" * 50 + "}\n"
        "\\section{Projects}\n"
        + "\n".join("\\resumeItem{" + "p" * 120 + "}" for _ in range(3))
    )
    report = fit.estimate(_doc(body))
    names = [s["name"] for s in report.sections]
    assert names == ["Education", "Projects"]
    edu, proj = report.sections
    assert proj["lines"] > edu["lines"]
    assert report.lines == sum(s["lines"] for s in report.sections)


def test_fits_verdict_against_budget():
    small = fit.estimate(_doc("\\section{A}\n\\resumeItem{tiny}"))
    assert small.fits
    stuffed = fit.estimate(
        _doc("\\section{A}\n" + "\n".join("\\resumeItem{" + "w" * 200 + "}" for _ in range(80)))
    )
    assert not stuffed.fits


def test_describe_names_biggest_section():
    stuffed = fit.estimate(
        _doc("\\section{Tiny}\n\\resumeItem{x}\n\\section{Huge}\n"
             + "\n".join("\\resumeItem{" + "w" * 200 + "}" for _ in range(80)))
    )
    assert "Huge" in fit.describe(stuffed)


def test_estimate_never_raises_on_garbage_input():
    """estimate() is pure text math with no compile/AI step — it must be total
    over any input, including malformed or nonsensical tex."""
    garbage_inputs = [
        "",
        "not even latex",
        "\\begin{document}",  # unterminated
        "\\end{document}",  # no begin
        "\\section{}\\resumeItem{}",  # empty braces
        "\x00\x01\ufffd binary-ish garbage \\resumeItem{" + "\\" * 50,
        "\\documentclass{article}\\begin{document}" + ("\\section{" * 20) + "\\end{document}",
        None if False else "\n\n\n\n",  # blank-only body
    ]
    for tex in garbage_inputs:
        report = fit.estimate(tex)
        assert isinstance(report, fit.FitReport)
        assert isinstance(report.lines, int) and report.lines >= 0
        # describe() must also never raise, even on a degenerate report.
        assert isinstance(fit.describe(report), str)


# ---------------------------------------------------------------------------
# Calibration: the deterministic constants must agree with the REAL compiler.
#
# backend/tests/test_page_guarantee.py and test_overflow_guarantee.py stub
# every compile in their tests (they monkeypatch tailor_flow._compile_doc_info),
# so neither file contains an actual compilable resume fixture. The one real,
# compilable "Jake's-template-like skeleton that compiles on stock TinyTeX" in
# this test suite is `resume_import.JAKES_TEMPLATE`, exercised for real by
# backend/tests/test_resume_import.py::test_jakes_skeleton_compiles_with_real_pdflatex.
# We reuse that exact template (and that test's fill-in pattern) here, extended
# with Experience/Skills sections so there is a real "last section" to stuff.
# ---------------------------------------------------------------------------


def _page_guarantee_fixture() -> str:
    """A filled instance of resume_import.JAKES_TEMPLATE: header + Education +
    Experience (with two \\resumeItem bullets under a \\resumeSubheading) +
    Skills (a bare \\resumeItemListStart/\\resumeItemListEnd bullet list --
    the "last section" _add_long_bullets stuffs)."""
    from app.services.resume_import import JAKES_TEMPLATE

    body = (
        "\\textbf{\\Large Jane Doe} \\\\ jane@example.com | 555-0100\n"
        "\\section{Education}\n\\resumeSubHeadingListStart\n"
        "\\resumeSubheading{University of Somewhere}{Somewhere, ST}"
        "{BS Computer Science}{Aug 2024 -- May 2028}\n"
        "\\resumeSubHeadingListEnd\n"
        "\\section{Experience}\n\\resumeSubHeadingListStart\n"
        "\\resumeSubheading{Acme Corp}{Remote}{Software Intern}{May 2025 -- Aug 2025}\n"
        "\\resumeItemListStart\n"
        "\\resumeItem{Built a data pipeline in Python that cut processing time 40\\%.}\n"
        "\\resumeItem{Wrote integration tests covering the ingest service.}\n"
        "\\resumeItemListEnd\n"
        "\\resumeSubHeadingListEnd\n"
        "\\section{Skills}\n"
        "\\resumeItemListStart\n"
        "\\resumeItem{Python, Java, SQL, Git.}\n"
        "\\resumeItemListEnd\n"
    )
    return JAKES_TEMPLATE.replace("\\end{document}", body + "\\end{document}")


def _add_long_bullets(tex: str, n: int) -> str:
    """Append n x ~150-char \\resumeItem bullets (ordinary breakable prose, not
    one giant unbroken token -- Jake's-template itemize breaks across a page
    with a fatal "missing \\item" error on an unbreakable run) to the fixture's
    last section (Skills' \\resumeItemListStart/\\resumeItemListEnd)."""
    bullet_text = ("Delivered measurable results across cross-functional teams "
                    "and stakeholders while shipping production code ")[:150]
    bullets = "".join(f"\\resumeItem{{{bullet_text}}}\n" for _ in range(n))
    idx = tex.rfind("\\resumeItemListEnd")
    return tex[:idx] + bullets + tex[idx:]


def test_calibration_against_real_compile(tmp_path):
    """The estimator's verdict must match pdflatex's page count for a fitting
    and an overflowing variant of the real test template."""
    from app.services.latex import compile_tex_info

    base_tex = _page_guarantee_fixture()          # the fixture test_page_guarantee uses
    stuffed_tex = _add_long_bullets(base_tex, 40)  # append 40 x 150-char bullets to its last section

    _, base_info = compile_tex_info(base_tex, tmp_path, "base")
    _, stuffed_info = compile_tex_info(stuffed_tex, tmp_path, "stuffed")
    assert base_info.page_count == 1 and stuffed_info.page_count > 1  # fixture sanity

    assert fit.estimate(base_tex).fits
    assert not fit.estimate(stuffed_tex).fits


# ---------------------------------------------------------------------------
# Dense one-page calibration: the sparse fixture above estimates only ~17
# lines against a 58-line budget -- nowhere near the real page boundary, so
# it can't catch structural over-counting -- a bug could inflate every
# section 3x and that fixture would still "fit". The
# reported "~94/54 lines" bug (a REAL one-page resume flagged as overflowing)
# needed a fixture shaped like an actual full resume: multiple subheadings
# with multiple bullets each, formatted the way real resumes and AI-generated
# conversions actually write \resumeSubheading/\resumeProjectHeading calls --
# arguments split across physical lines with \vspace spacers -- not all
# jammed onto the command's own line the way _page_guarantee_fixture does.
# See fit.py's module docstring for the resulting root-cause diagnosis.
# ---------------------------------------------------------------------------


def _dense_bullet(n_chars: int, seed: str) -> str:
    """~n_chars of ordinary breakable prose (see _add_long_bullets's note on
    itemize's fatal "missing \\item" on an unbreakable run)."""
    text = (f"Delivered measurable impact {seed} across cross-functional teams, "
            "shipping production code that improved reliability and reduced "
            "latency for thousands of daily users while mentoring junior engineers ")
    while len(text) < n_chars:
        text += text
    return text[:n_chars]


def _pad(s: str, n: int) -> str:
    while len(s) < n:
        s += " et al."
    return s[:n]


def _build_dense_tex(exp_bullet_counts: list[int]) -> str:
    """JAKES_TEMPLATE filled like a REAL full one-page resume: 3 Experience
    subheadings (bullet count per subheading given by exp_bullet_counts) x
    ~110-char bullets, 2 Project subheadings x 2 x ~110-char bullets, one
    Education subheading, and a 4-line Skills section (~90 chars/line).
    \\resumeSubheading/\\resumeProjectHeading arguments are split across
    physical lines with a trailing \\vspace spacer -- the idiomatic
    multi-line style real resumes use (see data/ai-workspace's real tailored
    resume), which is what actually triggers the over-counting bug this
    fixture exists to catch."""
    from app.services.resume_import import JAKES_TEMPLATE

    companies = ["Acme Corp", "Globex Inc", "Initech LLC"]
    roles = ["Software Engineering Intern", "Backend Developer Intern", "Data Engineering Intern"]
    locations = ["Remote", "New York, NY", "Austin, TX"]
    dates = ["May 2025 -- Aug 2025", "May 2024 -- Aug 2024", "Jan 2024 -- May 2024"]

    exp_subheadings = []
    for i, count in enumerate(exp_bullet_counts):
        bullets = "".join(f"\\resumeItem{{{_dense_bullet(110, f'{i}-{j}')}}}\n" for j in range(count))
        exp_subheadings.append(
            "\\resumeSubheading\n"
            f"  {{{companies[i]}}}{{{locations[i]}}}\n"
            f"  {{{roles[i]}}}{{{dates[i]}}}\n"
            "  \\vspace{0.5pt}\n"
            f"\\resumeItemListStart\n{bullets}\\resumeItemListEnd\n"
        )
    exp_block = (
        "\\section{Experience}\n\\resumeSubHeadingListStart\n"
        + "".join(exp_subheadings)
        + "\\resumeSubHeadingListEnd\n"
    )

    proj_names = ["Personal Portfolio Site", "Distributed Task Queue"]
    proj_tools = ["Python, React, PostgreSQL", "Go, Redis, Docker"]
    proj_blocks = []
    for i in range(2):
        bullets = "".join(f"\\resumeItem{{{_dense_bullet(110, f'proj{i}-{j}')}}}\n" for j in range(2))
        proj_blocks.append(
            "\\resumeProjectHeading\n"
            f"  {{\\textbf{{{proj_names[i]}}} $|$ \\emph{{{proj_tools[i]}}}}}{{2025}}\n"
            "  \\vspace{0.2pt}\n"
            f"\\resumeItemListStart\n{bullets}\\resumeItemListEnd\n"
        )
    proj_block = (
        "\\section{Projects}\n\\resumeSubHeadingListStart\n"
        + "".join(proj_blocks)
        + "\\resumeSubHeadingListEnd\n"
    )

    edu_block = (
        "\\section{Education}\n\\resumeSubHeadingListStart\n"
        "\\resumeSubheading\n"
        "  {University of Somewhere}{Somewhere, ST}\n"
        "  {BS Computer Science, GPA 3.8}{Aug 2023 -- May 2027}\n"
        "\\resumeSubHeadingListEnd\n"
    )

    skills_lines = [
        _pad("Languages: Python, Java, C++, JavaScript, TypeScript, SQL, Go, Rust.", 90),
        _pad("Frameworks: React, FastAPI, Django, Spring Boot, Node.js, Flask.", 90),
        _pad("Tools: Git, Docker, Kubernetes, AWS, GCP, Jenkins, Terraform, Linux.", 90),
        _pad("Coursework: Data Structures, Algorithms, Operating Systems, Databases.", 90),
    ]
    skills_block = (
        "\\section{Skills}\n\\resumeItemListStart\n"
        + "".join(f"\\resumeItem{{{line}}}\n" for line in skills_lines)
        + "\\resumeItemListEnd\n"
    )

    header = ("\\textbf{\\Large Jane Doe} \\\\\n"
              "jane@example.com | 555-0100 | linkedin.com/in/janedoe | github.com/janedoe\n")
    body = header + edu_block + exp_block + proj_block + skills_block
    return JAKES_TEMPLATE.replace("\\end{document}", body + "\\end{document}")


def _dense_one_page_fixture(out_dir):
    """Build _build_dense_tex's 3x3/2x2-bullet resume and verify BY REAL
    COMPILE that it is exactly 1 page. If a future template/constant change
    ever pushes it to 2 pages, deterministically trims the last bullet off
    the last Experience subheading with >1 bullet (then re-tries) until it's
    back to 1 page, rather than silently testing a 2-page input as if it
    were the intended fixture.

    Returns (tex, info) -- the already-computed CompileInfo from the
    trim/verify compile, so callers don't need to compile the same tex a
    second time just to read its page_count."""
    from app.services.latex import compile_tex_info

    counts = [3, 3, 3]
    for _ in range(9):
        tex = _build_dense_tex(counts)
        _, info = compile_tex_info(tex, out_dir, "dense_trial")
        if info.page_count <= 1:
            return tex, info
        for i in range(len(counts) - 1, -1, -1):
            if counts[i] > 1:
                counts[i] -= 1
                break
        else:
            break  # nothing left to trim -- return whatever we have rather than loop forever
    return tex, info


def _add_extra_dense_bullets(tex: str, n: int) -> str:
    """Append n x ~90-char \\resumeItem bullets to the dense fixture's Skills
    section, the same style _add_long_bullets uses for the sparse fixture --
    ordinary breakable prose, not one giant unbreakable token."""
    bullets = "".join(f"\\resumeItem{{{_dense_bullet(90, f'extra{i}')}}}\n" for i in range(n))
    idx = tex.rfind("\\resumeItemListEnd")
    return tex[:idx] + bullets + tex[idx:]


def test_dense_one_page_resume_fits(tmp_path):
    """The reported bug: real one-page resumes were flagged as overflowing
    (e.g. "~94/54 lines") because the line-by-line scanner structurally
    over-counted dense, realistically-formatted documents -- see fit.py's
    module docstring for the full root-cause diagnosis (multi-line macro
    arguments, a misclassified \\resumeProjectHeading cost, \\vspace/\\begin/
    \\end phantom lines, and a double-charged header). This fixture
    reproduces that real shape and must both fit AND clear the budget with
    real headroom, not just barely squeak under it."""
    dense_tex, info = _dense_one_page_fixture(tmp_path)
    assert info.page_count == 1  # fixture sanity: this must be a REAL one-pager

    report = fit.estimate(dense_tex)
    assert report.fits
    # Against the raw, unslacked skeleton-measured budget (not
    # effective_budget) -- the dense fixture (52 lines / 58 budget) clears
    # this with ~10% headroom on its own, before FIT_SLACK is even applied.
    assert report.lines <= report.budget * 0.95  # >=5% headroom, not a bare pass


def test_dense_stuffed_variant_within_slack(tmp_path):
    """Pinned tradeoff (see fit.py's module docstring, "Accepted tradeoff"):
    FIT_SLACK's 10% cross-template tolerance is coarse enough that it also
    reclassifies a REAL, pdflatex-confirmed 2-page overflow as fits=True,
    because this particular stuffed variant of the dense fixture happens to
    estimate to exactly effective_budget (63 == 63). This is accepted as
    safe because fit.estimate() is advisory only -- both the tailor gate and
    the doc-approval gate act on the REAL compiled page count, never on
    `fits` alone (see fit.py). Pinning this exact case means any future
    constant/slack drift that changes the tie breaks this test visibly,
    instead of silently changing which real documents get a free pass."""
    from app.services.latex import compile_tex_info

    dense_tex, _ = _dense_one_page_fixture(tmp_path)
    stuffed_tex = _add_extra_dense_bullets(dense_tex, 11)  # measured tipping point -- see below

    _, info = compile_tex_info(stuffed_tex, tmp_path, "dense_stuffed")
    assert info.page_count == 2  # fixture sanity: this must be a REAL 2-pager

    report = fit.estimate(stuffed_tex)
    assert report.lines == report.effective_budget  # the exact tie driving the accepted tradeoff
    assert report.fits is True  # the accepted false negative, pinned
