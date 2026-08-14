"""Divergence guard: tailored resumes stay close to the base, by budget."""

import pytest

from app.config import settings
from app.services.tailor import compute_divergence, tailor_resume

BASE = r"""\documentclass{article}
\begin{document}
\section{Experience}
Built a robotics control system for the regional competition team.
Led weekly practice sessions for ten students across two schools.
\end{document}"""


def test_divergence_zero_for_identical():
    assert compute_divergence(BASE, BASE) == 0.0


def test_divergence_counts_changed_words_not_commands():
    new = BASE.replace("robotics control system", "autonomous robotics stack")
    d = compute_divergence(BASE, new)
    assert 0.0 < d < 0.5  # a few words out of ~20 prose words


def test_edits_are_trimmed_when_budget_exceeded(monkeypatch):
    # BASE has ~21 prose words; edit 1 changes 3 (~0.14, fits), edit 2 would
    # push past the budget.
    monkeypatch.setattr(settings, "tailor_max_divergence", 0.20)

    def fake_generate(system, user_content, schema, max_tokens=16000):
        return {
            "edits": [
                {"original": "robotics control system",
                 "replacement": "autonomous navigation stack"},
                {"original": "Led weekly practice sessions for ten students across two schools.",
                 "replacement": "Directed intensive daily coaching for dozens of pupils "
                                "spanning several partner institutions and mentors."},
            ]
        }

    result = tailor_resume(BASE, "a job description", generate=fake_generate)
    assert len(result.applied) == 1                      # first (most important) kept
    assert result.applied[0]["original"] == "robotics control system"
    assert result.rejected and "budget" in result.rejected[0]["reason"]
    assert result.divergence <= 0.20


def test_divergence_recorded_when_under_budget(monkeypatch):
    monkeypatch.setattr(settings, "tailor_max_divergence", 0.9)

    def fake_generate(system, user_content, schema, max_tokens=16000):
        return {"edits": [{"original": "regional competition", "replacement": "state championship"}]}

    result = tailor_resume(BASE, "jd", generate=fake_generate)
    assert len(result.applied) == 1
    assert 0.0 < result.divergence < 0.3
