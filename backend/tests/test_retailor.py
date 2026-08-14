"""Re-tailor a doc using its ATS scan findings as extra guidance."""

import pytest

from app.db.models import AtsScan, DocType, GeneratedDoc, Job, Resume
from app.services import ats_scan as ats_service


@pytest.fixture
def doc(db_session):
    resume = Resume(name="base", job_type="software", tex_source="\\documentclass{article}x")
    job = Job(company="Umbra", title="SW Intern", jd_text="C++ and Python.")
    db_session.add_all([resume, job])
    db_session.flush()
    d = GeneratedDoc(job_id=job.id, base_resume_id=resume.id,
                     doc_type=DocType.RESUME, tex_source="x", approved=True)
    db_session.add(d)
    db_session.commit()
    return d


def test_scan_guidance_formats_latest_done_reports(db_session, doc):
    db_session.add(AtsScan(doc_id=doc.id, kind="jd_match", status="done",
                           report={"match_score": 61, "missing_keywords": ["Python"],
                                   "weak_areas": ["testing"], "suggestions": ["quantify"],
                                   "summary": "solid"}))
    db_session.add(AtsScan(doc_id=doc.id, kind="deep", status="running"))
    db_session.commit()
    g = ats_service.scan_guidance(db_session, doc.id)
    assert "61/100" in g and "Python" in g and "quantify" in g
    assert "deep" not in g  # running scans are not findings yet


def test_scan_guidance_none_without_done_scans(db_session, doc):
    assert ats_service.scan_guidance(db_session, doc.id) is None


def test_retailor_endpoint_threads_guidance(client, db_session, doc, monkeypatch):
    db_session.add(AtsScan(doc_id=doc.id, kind="jd_match", status="done",
                           report={"match_score": 61, "missing_keywords": ["Python"],
                                   "weak_areas": [], "suggestions": [], "summary": "s"}))
    db_session.commit()
    from app.api import generate as generate_api
    seen = {}
    def fake_tailor(db, resume, job, jd_text, guidance=None):
        seen["guidance"] = guidance
        d = GeneratedDoc(job_id=job.id, base_resume_id=resume.id,
                         doc_type=DocType.RESUME, tex_source="new")
        db.add(d)
        db.flush()
        from app.services.tailor_flow import TailorOutcome
        return TailorOutcome(doc=d, pages=1, warnings=[], applied=[], rejected=[],
                             divergence=0.0)
    monkeypatch.setattr(generate_api.tailor_flow, "tailor_to_doc", fake_tailor)
    r = client.post("/api/generate/retailor", json={"doc_id": doc.id})
    assert r.status_code == 201
    assert "Python" in seen["guidance"]


def test_retailor_409_without_findings(client, doc):
    r = client.post("/api/generate/retailor", json={"doc_id": doc.id})
    assert r.status_code == 409
    assert "scan" in r.json()["detail"].lower()
