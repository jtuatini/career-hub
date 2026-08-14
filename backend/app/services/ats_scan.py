"""ATS scan orchestration.

Four scan kinds against a tailored doc (generated_docs) or a bank resume:
- keyword: local PDF-parse + JD keyword coverage (atscheck). No AI, instant,
  runs synchronously on the request thread.
- jd_match: JD-vs-resume scoring through the engine facade.
- deep: hiring-agent-style resume-quality rubric through the engine facade.
- hiring_agent: optional subprocess wrap of the external hiring-agent repo
  (settings.ats_repo_path, Ollama-backed). Hidden when unconfigured.

AI kinds run in a daemon thread that owns its own SessionLocal session and
writes status/report/error back onto the AtsScan row (same pattern as
apply.run_pipeline / resume_import.run_import — conftest redirects
SessionLocal for tests)."""

import subprocess
import threading
from pathlib import Path

import httpx
from sqlalchemy import select

from app.config import settings
from app.db.base import SessionLocal
from app.db.models import AtsScan, GeneratedDoc, Resume
from app.services.atscheck import ats_report, pdf_text
from app.services.autofill import load_profile
from app.services.engine import generate_json
from app.services.github_sync import _fetch_repos, _username

HIRING_AGENT_TIMEOUT_SECONDS = 900  # qwen-class local models take minutes per stage
OLLAMA_URL = "http://127.0.0.1:11434"

JD_MATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "match_score": {"type": "integer", "minimum": 0, "maximum": 100},
        "missing_keywords": {"type": "array", "items": {"type": "string"}},
        "weak_areas": {"type": "array", "items": {"type": "string"}},
        "suggestions": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["match_score", "missing_keywords", "weak_areas", "suggestions", "summary"],
}

_CATEGORY_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 0, "maximum": 10},
        "evidence": {"type": "string"},
    },
    "required": ["score", "evidence"],
}

DEEP_SCHEMA = {
    "type": "object",
    "properties": {
        "open_source": _CATEGORY_SCHEMA,
        "self_projects": _CATEGORY_SCHEMA,
        "production": _CATEGORY_SCHEMA,
        "technical_skills": _CATEGORY_SCHEMA,
        "bonus": {"type": "array", "items": {"type": "string"}},
        "deductions": {"type": "array", "items": {"type": "string"}},
        "overall_score": {"type": "integer", "minimum": 0, "maximum": 40},
        "summary": {"type": "string"},
    },
    "required": [
        "open_source", "self_projects", "production", "technical_skills",
        "bonus", "deductions", "overall_score", "summary",
    ],
}

_JD_MATCH_SYSTEM = """\
You are a strict applicant-tracking-system (ATS) reviewer. Compare the resume
against the job description exactly as written. match_score (0-100): how well
the resume's wording and evidence cover the posting's requirements.
missing_keywords: concrete terms from the posting absent from the resume.
weak_areas: requirements only thinly evidenced. suggestions: specific,
wording-level improvements — never invent experience the resume doesn't show.
summary: 2-3 plain sentences. Judge only what is on the page."""

_DEEP_SYSTEM = """\
You are a rigorous, fair technical resume evaluator. Score each category 0-10
and quote concrete evidence from the resume:
- open_source: contributions to public or open-source work.
- self_projects: personal projects with real depth (not tutorials).
- production: shipped/production or internship experience with impact.
- technical_skills: breadth and depth of stack, judged against what the
  projects actually demonstrate.
Fairness rules: judge only what is written; no credit for brand-name prestige;
no penalty for formatting. bonus: notable extras (awards, publications,
leadership). deductions: red flags (vague claims, buzzword stuffing, claims
with no supporting evidence). overall_score is the sum of the four category
scores. summary: 3-4 plain sentences the candidate could act on.

When the candidate's actual GitHub repo list is provided, weigh open_source and
self_projects against it: confirmed matching repos strengthen the evidence;
resume claims with no corresponding public repo are noted (not auto-penalized —
private work exists) in the category evidence text."""


def _target(db, scan: AtsScan) -> tuple[str | None, str | None, str | None]:
    """(pdf_path, tex_source, jd_text) for the scan's subject."""
    if scan.doc_id is not None:
        doc = db.get(GeneratedDoc, scan.doc_id)
        if doc is None:
            raise ValueError("document not found")
        return doc.pdf_path, doc.tex_source, doc.job.jd_text if doc.job else None
    resume = db.get(Resume, scan.resume_id)
    if resume is None:
        raise ValueError("resume not found")
    return resume.pdf_path, resume.tex_source, None


def _resume_text(pdf_path: str | None, tex_source: str | None) -> str:
    """Prefer the compiled PDF's extracted text (what a real ATS parses);
    fall back to the LaTeX source."""
    if pdf_path and Path(pdf_path).exists():
        text = pdf_text(pdf_path)
        if text.strip():
            return text
    if tex_source:
        return tex_source
    raise ValueError("no resume content available (no PDF, no LaTeX source)")


def hiring_agent_available() -> bool:
    if not settings.ats_repo_path:
        return False
    repo = Path(settings.ats_repo_path)
    return (repo / "score.py").exists() and (repo / ".venv" / "bin" / "python").exists()


def ollama_running() -> bool:
    """Local liveness probe — the hiring-agent wrap needs a running Ollama."""
    try:
        return httpx.get(f"{OLLAMA_URL}/api/tags", timeout=2).status_code == 200
    except Exception:
        return False


def capabilities(db, doc_id: int | None, resume_id: int | None) -> dict:
    """Which scan kinds make sense for this target — drives the UI buttons."""
    pdf_path, tex, jd = _target(db, AtsScan(doc_id=doc_id, resume_id=resume_id))
    has_pdf = bool(pdf_path and Path(pdf_path).exists())
    has_content = has_pdf or bool(tex)
    return {
        "keyword": has_pdf,
        "jd_match": bool(jd) and has_content,
        "deep": has_content,
        "hiring_agent": hiring_agent_available() and has_pdf,
    }


def run_keyword(db, scan: AtsScan) -> None:
    """Local scan; completes synchronously on the request thread."""
    pdf_path, _tex, jd = _target(db, scan)
    if not pdf_path or not Path(pdf_path).exists():
        scan.status, scan.error = "error", "No compiled PDF for this target"
    else:
        # Broad on purpose: pdf_text/pypdf can raise on a corrupt PDF, and the
        # row must still land in a terminal state instead of staying "running"
        # forever (same pattern as run_scan below).
        try:
            scan.report = ats_report(pdf_path, jd)
            scan.status = "done"
        except Exception as e:
            scan.status, scan.error = "error", str(e)[:2000]
    db.commit()


def _run_jd_match(pdf_path, tex, jd) -> dict:
    return generate_json(
        _JD_MATCH_SYSTEM,
        f"JOB DESCRIPTION:\n{jd}\n\nRESUME:\n{_resume_text(pdf_path, tex)}",
        JD_MATCH_SCHEMA,
    )


def _github_evidence(db) -> str:
    """The candidate's actual public repos, for verifying resume claims.
    Best-effort: no handle / network failure => empty string."""
    try:
        username = _username(load_profile(db))
        repos = _fetch_repos(username)[:10]
    except Exception:
        return ""
    lines = []
    for r in repos:
        bits = [f"### {r.get('full_name')} ({r.get('html_url', '')})"]
        if r.get("description"):
            bits.append(r["description"])
        if r.get("language"):
            bits.append(f"Language: {r['language']}.")
        if r.get("topics"):
            bits.append("Topics: " + ", ".join(r["topics"]) + ".")
        if r.get("stargazers_count"):
            bits.append(f"Stars: {r['stargazers_count']}.")
        lines.append(" ".join(bits))
    return "\n".join(lines)


def _run_deep(pdf_path, tex, db) -> dict:
    github = _github_evidence(db)
    evidence = (
        f"CANDIDATE'S ACTUAL PUBLIC GITHUB (verify open-source/self-project claims "
        f"against this):\n{github}"
        if github
        else "CANDIDATE'S GITHUB: not available for verification — judge from the resume alone."
    )
    return generate_json(
        _DEEP_SYSTEM, f"RESUME:\n{_resume_text(pdf_path, tex)}\n\n{evidence}", DEEP_SCHEMA
    )


def _run_hiring_agent(pdf_path) -> dict:
    if not hiring_agent_available():
        raise ValueError("hiring-agent repo not configured (set ATS_REPO_PATH in backend/.env)")
    if not pdf_path or not Path(pdf_path).exists():
        raise ValueError("hiring-agent needs a compiled PDF")
    repo = Path(settings.ats_repo_path)
    proc = subprocess.run(
        [str(repo / ".venv" / "bin" / "python"), "score.py", str(Path(pdf_path).resolve())],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=HIRING_AGENT_TIMEOUT_SECONDS,
    )
    if proc.returncode != 0:
        raise ValueError(
            f"hiring-agent exited {proc.returncode}: {(proc.stderr or proc.stdout)[-800:]}"
        )
    return {"raw_output": proc.stdout[-20000:]}


def run_scan(scan_id: int) -> None:
    """Body of the background thread (tests call it synchronously).

    Cancellation (POST /api/ats/scan/{id}/cancel) is best-effort: it flips
    the row to "cancelled" from the request thread while this thread may be
    off doing engine or hiring-agent-subprocess work it has no way to
    interrupt (no process registry to kill the subprocess mid-flight — its
    result is simply discarded when this catches up). Two checks stop a
    late-finishing thread from resurrecting an already-cancelled row:
    (a) right after fetching the scan, before any engine work starts, and
    (b) via a fresh db.refresh() right after the report is computed, before
    it's written. A cancellation landing in the narrow window between that
    refresh and the commit below is still possible and is accepted as a
    documented gap: the row is already terminal ("cancelled") either way, so
    a lost race there is harmless, not a correctness bug."""
    db = SessionLocal()
    try:
        scan = db.get(AtsScan, scan_id)
        if scan is None:
            return
        if scan.status != "running":
            return
        try:
            pdf_path, tex, jd = _target(db, scan)
            if scan.kind == "jd_match":
                if not jd:
                    raise ValueError("no job description stored for this target")
                report = _run_jd_match(pdf_path, tex, jd)
            elif scan.kind == "deep":
                report = _run_deep(pdf_path, tex, db)
            elif scan.kind == "hiring_agent":
                report = _run_hiring_agent(pdf_path)
            else:
                raise ValueError(f"unknown scan kind {scan.kind!r}")
        # Broad on purpose: engine errors, subprocess timeouts, and validation
        # all land on the row for the UI instead of dying silently in a thread.
        except Exception as e:
            db.refresh(scan)
            if scan.status == "cancelled":
                return
            scan.status, scan.error = "error", str(e)[:2000]
            db.commit()
            return
        # This refresh only sees a concurrent cancel because nothing above has
        # written through `db` since the initial fetch — no transaction is open,
        # so the refresh reads the latest committed row. If run_scan ever
        # performs a DB write between the fetch and here, that write opens a
        # transaction and this refresh would read a stale snapshot instead,
        # reintroducing the resurrect risk this guard exists to close.
        db.refresh(scan)
        if scan.status == "cancelled":
            return
        scan.report = report
        scan.status = "done"
        db.commit()
    finally:
        db.close()


def start_scan(scan_id: int) -> None:
    threading.Thread(target=run_scan, args=(scan_id,), daemon=True).start()


def scan_guidance(db, doc_id: int) -> str | None:
    """Latest done scan per kind for a doc, formatted as tailoring guidance."""
    scans = db.scalars(
        select(AtsScan)
        .where(AtsScan.doc_id == doc_id, AtsScan.status == "done")
        .order_by(AtsScan.created_at.desc(), AtsScan.id.desc())
    ).all()
    latest: dict[str, AtsScan] = {}
    for s in scans:
        latest.setdefault(s.kind, s)
    if not latest:
        return None
    lines = ["ATS SCAN FINDINGS — address these while tailoring. Wording-only edits; "
             "NEVER invent experience to cover a missing keyword:"]
    for kind, s in latest.items():
        r = s.report or {}
        if kind == "keyword":
            missing = ", ".join(r.get("missing_keywords", [])) or "none"
            lines.append(f"[keyword] JD-term coverage gaps: {missing}")
        elif kind == "jd_match":
            lines.append(
                f"[jd_match] {r.get('match_score', '?')}/100. "
                f"Missing: {', '.join(r.get('missing_keywords', [])) or 'none'}. "
                f"Weak: {'; '.join(r.get('weak_areas', [])) or 'none'}. "
                f"Suggestions: {'; '.join(r.get('suggestions', [])) or 'none'}"
            )
        elif kind == "deep":
            lines.append(f"[deep] {r.get('summary', '')} "
                         f"Deductions: {'; '.join(r.get('deductions', [])) or 'none'}")
        elif kind == "hiring_agent":
            lines.append(f"[hiring_agent] {str(r.get('raw_output', ''))[-1500:]}")
    return "\n".join(lines)
