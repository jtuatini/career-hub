"""AI job-posting parsing: raw page text (or a screenshot) in, clean structured
posting out. Powers the extension's posting card and the Tailor tab's
paste-anything box."""

from app.services.engine import generate_json, generate_json_with_image

PARSE_SCHEMA = {
    "type": "object",
    "properties": {
        "company": {"type": "string"},
        "title": {"type": "string"},
        "location": {"type": "string", "description": "Empty string if not stated"},
        "jd_text": {
            "type": "string",
            "description": "The complete job description: responsibilities, "
            "qualifications, comp, everything relevant — wording preserved",
        },
        "confidence": {
            "type": "number",
            "description": "0-1: how confident this page is actually a job posting "
            "and the fields are right",
        },
    },
    "required": ["company", "title", "location", "jd_text", "confidence"],
    "additionalProperties": False,
}

SYSTEM = """\
You extract job postings from raw web-page text or screenshots. Return the
company (the employer, not the job board), exact role title, location, and the
COMPLETE job description with original wording — responsibilities,
qualifications, compensation, benefits, deadlines. Drop navigation menus,
cookie banners, footers, EEO boilerplate, and "similar jobs" listings. If the
content clearly isn't a job posting, set confidence below 0.3 and explain
nothing — just extract what you can.\
"""

MAX_TEXT = 30_000


def parse_posting(text: str, url: str | None = None) -> dict:
    content = f"Page URL: {url}\n\n{text[:MAX_TEXT]}" if url else text[:MAX_TEXT]
    return _clamp(generate_json(SYSTEM, content, PARSE_SCHEMA))


def parse_posting_image(png_bytes: bytes) -> dict:
    return _clamp(generate_json_with_image(SYSTEM, png_bytes, PARSE_SCHEMA))


def _clamp(result: dict) -> dict:
    try:
        result["confidence"] = max(0.0, min(1.0, float(result.get("confidence", 0))))
    except (TypeError, ValueError):
        result["confidence"] = 0.0
    return result
