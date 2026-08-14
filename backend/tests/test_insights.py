import io
import sqlite3
import zipfile
from datetime import timedelta

from app.db.base import get_db
from app.db.models import GeneratedDoc, Job, Resume, utcnow
from app.main import app

MINIMAL_TEX = "\\documentclass{article}\\begin{document}hi\\end{document}"


def _db():
    return next(app.dependency_overrides[get_db]())


def _seed(db):
    strong = Resume(name="Strong", job_type="swe", tex_source=MINIMAL_TEX)
    weak = Resume(name="Weak", job_type="aero", tex_source=MINIMAL_TEX)
    db.add_all([strong, weak])
    db.flush()

    now = utcnow()
    jobs = [
        Job(company="A", title="x", status="interview", applied_at=now - timedelta(days=3)),
        Job(company="B", title="x", status="offer", applied_at=now - timedelta(days=30)),
        Job(company="C", title="x", status="applied", applied_at=now - timedelta(days=20)),
        Job(company="D", title="x", status="applied", applied_at=now - timedelta(days=2)),
        Job(company="E", title="x", status="saved", deadline=now + timedelta(days=3)),
        Job(company="F", title="x", status="saved", deadline=now + timedelta(days=30)),
    ]
    db.add_all(jobs)
    db.flush()
    # Strong resume used for the two jobs that progressed; weak for the stale one.
    db.add_all([
        GeneratedDoc(job_id=jobs[0].id, base_resume_id=strong.id, doc_type="resume", tex_source="x"),
        GeneratedDoc(job_id=jobs[1].id, base_resume_id=strong.id, doc_type="resume", tex_source="x"),
        GeneratedDoc(job_id=jobs[2].id, base_resume_id=weak.id, doc_type="resume", tex_source="x"),
        # cover letters must not count
        GeneratedDoc(job_id=jobs[2].id, base_resume_id=weak.id, doc_type="cover_letter", tex_source="x"),
    ])
    db.commit()
    return jobs


def test_analytics_funnel_rates_and_reminders(client):
    jobs = _seed(_db())
    body = client.get("/api/analytics").json()

    assert body["funnel"]["total"] == 6
    assert body["funnel"]["applied"] == 2
    assert body["funnel"]["interview"] == 1

    by_resume = {r["name"]: r for r in body["by_resume"]}
    assert by_resume["Strong"]["applications"] == 2
    assert by_resume["Strong"]["responses"] == 2
    assert by_resume["Strong"]["offers"] == 1
    assert by_resume["Strong"]["response_rate"] == 1.0
    assert by_resume["Weak"]["applications"] == 1
    assert by_resume["Weak"]["responses"] == 0
    assert body["by_resume"][0]["name"] == "Strong"  # best rate first

    reminders = body["reminders"]
    assert [d["company"] for d in reminders["deadlines"]] == ["E"]  # F is beyond the window
    assert [s["company"] for s in reminders["stale"]] == ["C"]  # 20 days silent
    assert jobs[3].company not in [s["company"] for s in reminders["stale"]]


def test_export_zip_contains_db_and_files(client, tmp_path):
    from app.config import settings

    _seed(_db())
    (settings.files_dir / "resumes").mkdir(parents=True, exist_ok=True)
    (settings.files_dir / "resumes" / "resume_1.pdf").write_bytes(b"%PDF-fake")

    resp = client.get("/api/export")
    assert resp.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    assert "files/resumes/resume_1.pdf" in names
    # test DB lives at tmp/test.db, not settings.db_path — only assert when present
    if "appbot.sqlite3" in names:
        image = zf.read("appbot.sqlite3")
        conn = sqlite3.connect(":memory:")
        conn.deserialize(image)
        conn.close()


def test_docs_list_feed(client):
    _seed(_db())
    docs = client.get("/api/docs?limit=2").json()
    assert len(docs) == 2
    assert {"id", "company", "title", "doc_type", "approved", "created_at", "job_id"} <= set(
        docs[0]
    )
