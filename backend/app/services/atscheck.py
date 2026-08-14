"""ATS-parse check + JD keyword match — fully local, no API calls.

Answers two questions before submitting: can a dumb parser actually read the
generated PDF, and does the wording cover the job description's vocabulary?
"""

import re
from collections import Counter
from pathlib import Path

STOPWORDS = frozenset(
    """a an and are as at be been but by for from has have if in into is it its of on
    or our that the their they this to was we were what when where which while who
    will with you your not can may must should would about more than other over
    such all any each per etc via using use used including experience experiences
    work working team teams strong ability skills required preferred qualifications
    responsibilities role position candidate candidates applicants applicant year
    years month months day days new one two three plus minimum maximum""".split()
)

WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z+#.]{1,}")


def pdf_text(path: str | Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _tokens(text: str) -> list[str]:
    return [w.lower().rstrip(".") for w in WORD_RE.findall(text)]


def jd_keywords(jd_text: str, limit: int = 25) -> list[str]:
    """Most frequent meaningful JD terms, frequency-ranked."""
    counts = Counter(t for t in _tokens(jd_text) if t not in STOPWORDS and len(t) > 2)
    return [term for term, _ in counts.most_common(limit)]


def ats_report(pdf_path: str | Path, jd_text: str | None) -> dict:
    text = pdf_text(pdf_path)
    words = _tokens(text)
    report: dict = {
        "parsed_words": len(words),
        "ats_readable": len(words) >= 50,
    }
    if jd_text:
        keywords = jd_keywords(jd_text)
        present = sorted(k for k in keywords if k in set(words))
        missing = [k for k in keywords if k not in set(words)]
        report |= {
            "keywords_checked": len(keywords),
            "present_keywords": present,
            "missing_keywords": missing,
            "keyword_score": round(len(present) / len(keywords), 3) if keywords else None,
        }
    else:
        report |= {
            "keywords_checked": 0,
            "present_keywords": [],
            "missing_keywords": [],
            "keyword_score": None,
        }
    return report
