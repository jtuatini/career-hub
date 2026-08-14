from app.services.tailor import tailor_resume
from tests.test_latex import SAMPLE_TEX


def fake_generator(edits):
    def generate(system, user_content, schema, max_tokens=16000):
        return {"edits": edits}

    return generate


def test_tailor_applies_generated_edits():
    generate = fake_generator(
        [
            {
                "original": "Developed a web scraper that collected data from 100 sites.",
                "replacement": "Built a web scraper that collected data from 100+ sources.",
            }
        ]
    )
    result = tailor_resume(SAMPLE_TEX, "JD: data engineering intern", generate=generate)
    assert "100+" in result.tex
    assert len(result.applied) == 1
    assert result.rejected == []


def test_tailor_rejects_invalid_edits_but_applies_valid_ones():
    generate = fake_generator(
        [
            {
                "original": "Python, Java, and SQL.",
                "replacement": "Python, Rust, and SQL.",
            },
            {
                "original": r"\item Led a team of 4 students to build a mobile app.",
                "replacement": "Led a team.",  # deletes \item — structural
            },
            {
                "original": "This span does not exist anywhere.",
                "replacement": "whatever",
            },
        ]
    )
    result = tailor_resume(SAMPLE_TEX, "JD", generate=generate)
    assert "Python, Rust, and SQL." in result.tex
    assert len(result.applied) == 1
    assert len(result.rejected) == 2
    reasons = " ".join(r["reason"] for r in result.rejected)
    assert "structur" in reasons
    assert "not found" in reasons


def test_tailor_passes_jd_and_resume_to_generator():
    captured = {}

    def generate(system, user_content, schema, max_tokens=16000):
        captured["system"] = system
        captured["user"] = user_content
        return {"edits": []}

    tailor_resume(SAMPLE_TEX, "Acme SWE internship JD text", generate=generate)
    assert "Acme SWE internship JD text" in captured["user"]
    assert SAMPLE_TEX in captured["user"]
    assert "never invent" in captured["system"].lower()
