import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import mcp_server
from app.config import settings
from app.db.base import Base
from app.db.models import Job, Resume

MINIMAL_TEX = "\\documentclass{article}\\begin{document}Original wording\\end{document}"


@pytest.fixture
def mcp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    settings.files_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(mcp_server, "SessionLocal", TestSession)
    db = TestSession()
    yield db
    db.close()


@pytest.fixture
def seeded(mcp_db):
    resume = Resume(name="SWE", job_type="swe", tex_source=MINIMAL_TEX)
    pdf_only = Resume(name="Scan", job_type="swe", tex_source=None)
    job = Job(company="Acme", title="SWE Intern", jd_text="Build things with Python.")
    mcp_db.add_all([resume, pdf_only, job])
    mcp_db.commit()
    return {"resume": resume, "pdf_only": pdf_only, "job": job}


def test_list_resumes_marks_heads_and_pdf_only(seeded):
    listed = mcp_server.list_resumes()
    by_id = {r["id"]: r for r in listed}
    assert by_id[seeded["resume"].id]["is_latest_version"] is True
    assert by_id[seeded["resume"].id]["has_tex"] is True
    assert by_id[seeded["pdf_only"].id]["has_tex"] is False


def test_get_resume_tex_rejects_pdf_only(seeded):
    assert mcp_server.get_resume_tex(seeded["resume"].id) == MINIMAL_TEX
    with pytest.raises(ValueError, match="PDF-only"):
        mcp_server.get_resume_tex(seeded["pdf_only"].id)


def test_update_creates_compiled_version(seeded):
    result = mcp_server.update_resume_tex(
        seeded["resume"].id, MINIMAL_TEX.replace("Original", "Better")
    )
    assert result["status"] == "updated"
    assert result["parent_id"] == seeded["resume"].id
    assert result["page_count"] == 1
    # The new version is now the only editable head.
    heads = [r for r in mcp_server.list_resumes() if r["is_latest_version"] and r["has_tex"]]
    assert [h["id"] for h in heads] == [result["id"]]


def test_update_reports_compile_failure_without_saving(seeded):
    result = mcp_server.update_resume_tex(seeded["resume"].id, "\\documentclass{article}\\broken")
    assert result["status"] == "compile_failed"
    assert result["error"]
    assert len(mcp_server.list_resumes()) == 2  # nothing new persisted


def test_bulk_find_replace_scopes_and_versions(seeded):
    outcomes = mcp_server.bulk_find_replace("Original", "Refined", job_type="swe")
    assert len(outcomes) == 1
    assert outcomes[0]["status"] == "updated"
    assert "Refined" in mcp_server.get_resume_tex(outcomes[0]["new_id"])


def test_job_tools_expose_jd(seeded):
    jobs = mcp_server.list_jobs()
    assert jobs[0]["company"] == "Acme"
    detail = mcp_server.get_job(seeded["job"].id)
    assert detail["jd_text"] == "Build things with Python."
    with pytest.raises(ValueError, match="not found"):
        mcp_server.get_job(9999)


# ——— Brain tools ———


@pytest.fixture
def brain_seed(mcp_db, fake_embeddings):
    e1 = mcp_server.add_entry("story", "Robotics competition win", "Led embedded software")
    e2 = mcp_server.add_entry("skill", "Python backend work", "FastAPI and testing")
    mcp_server.save_qa_answer("Why us?", "Because of the mission.")
    return {"story": e1, "skill": e2}


def test_add_entry_validates_type(mcp_db, fake_embeddings):
    with pytest.raises(ValueError, match="must be one of"):
        mcp_server.add_entry("dream", "x", "y")


def test_search_memory_ranks_and_scores(brain_seed):
    hits = mcp_server.search_memory("robotics embedded software", k=1)
    assert hits[0]["title"] == "Robotics competition win"
    assert 0 < hits[0]["score"] <= 1


def test_link_and_get_entry_shows_neighbors(brain_seed):
    link = mcp_server.link_entries(
        brain_seed["story"]["id"], brain_seed["skill"]["id"], "demonstrates"
    )
    assert link["relation"] == "demonstrates"
    detail = mcp_server.get_entry(brain_seed["story"]["id"])
    assert detail["links"][0]["id"] == brain_seed["skill"]["id"]


def test_qa_roundtrip(brain_seed):
    hits = mcp_server.search_qa("why do you want to work here", k=1)
    assert hits[0]["question"] == "Why us?"
