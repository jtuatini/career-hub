"""Short-trailing-line bullet detection from PDF text geometry."""

from app.services.latex import compile_tex
from app.services.tailor_flow import _short_tail_bullets

# The long bullet is calibrated (10pt, 1in margins) so it wraps to a second
# visual line whose trailing text is "graceful backpressure" (2 words, <= the
# 3-word threshold) — verified against the compiled PDF's text geometry.
TEX = r"""
\documentclass[10pt]{article}
\usepackage[margin=1in]{geometry}
\begin{document}
\begin{itemize}
\item Implemented a distributed ingestion pipeline handling four hundred thousand events per minute with graceful backpressure
\item Wrote tests
\end{itemize}
\end{document}
"""


def test_detects_bullet_with_short_trailing_line(tmp_path):
    pdf = compile_tex(TEX, tmp_path, "shorttail")
    bullets = _short_tail_bullets(pdf)
    # Exactly the one wrapping bullet is returned. This pins down the y-gap
    # footer guard: without it, the page-number footer ("1", drawn by
    # article's default \pagestyle{plain} far below the body text) gets
    # folded into the single-line "Wrote tests" bullet as a spurious
    # continuation, turning it into a second (bogus) 2-line/short-tail match.
    assert len(bullets) == 1
    # The long bullet wraps; its final line carries only a few words.
    assert "backpressure" in bullets[0]
    # The one-line bullet is NOT a target (single line == its own last line
    # with <=3 words would match; the rule only considers bullets spanning
    # 2+ visual lines) -- and it must not appear even with a footer digit
    # glommed onto it.
    assert not any(b.strip() in ("Wrote tests", "Wrote tests 1") for b in bullets)


def test_failure_returns_empty(tmp_path):
    assert _short_tail_bullets(tmp_path / "nonexistent.pdf") == []
