"""Widened page-fit guarantee: overfull vbox counts, staged trim, cut warning."""

import pytest

from app.db.models import Job, Resume
from app.services import tailor_flow
from app.services.latex import CompileInfo
from app.services.tailor import TailorResult


@pytest.fixture
def resume(db_session):
    r = Resume(name="SWE", job_type="software", tex_source="\\documentclass{article}", page_count=1)
    db_session.add(r)
    db_session.commit()
    return r


@pytest.fixture
def job(db_session):
    j = Job(company="Acme", title="SWE", jd_text="Build backend services.")
    db_session.add(j)
    db_session.commit()
    return j


def _wire(monkeypatch, infos, captured, pdf_path):
    """Stub tailor_resume (recording extra_instruction + deletion flag) and
    compile: `infos` is the scripted sequence of CompileInfo per compile.
    `pdf_path` must be test-owned (tmp_path) — the hard-fail path really
    unlinks whatever doc.pdf_path names."""
    seq = iter(infos)

    def fake_tailor(tex, jd, memory_context="", extra_instruction=None, budget=None, allow_item_deletion=False):
        captured.append({"instruction": extra_instruction or "", "delete": allow_item_deletion})
        return TailorResult(tex=tex, applied=[], rejected=[])

    def fake_compile(doc):
        info = next(seq)
        doc.pdf_path = str(pdf_path)
        return info

    monkeypatch.setattr(tailor_flow, "tailor_resume", fake_tailor)
    monkeypatch.setattr(tailor_flow, "_compile_doc_info", fake_compile)
    monkeypatch.setattr(tailor_flow, "_short_tail_bullets", lambda p: ["Implemented pipeline with retries"])


def test_overfull_alone_triggers_tighten_and_passes_when_fixed(db_session, monkeypatch, tmp_path, resume, job):
    captured = []
    _wire(monkeypatch, [CompileInfo(1, 14.0), CompileInfo(1, 0.0)], captured, tmp_path / "doc.pdf")
    outcome = tailor_flow.tailor_to_doc(db_session, resume, job, job.jd_text)
    assert outcome.pages == 1
    # initial tailor + one compress round; compress round names the target bullet
    assert len(captured) == 2
    assert captured[1]["delete"] is False
    assert "Implemented pipeline with retries" in captured[1]["instruction"]
    assert "delete" not in captured[1]["instruction"].lower() or "may not" in captured[1]["instruction"].lower()


def test_below_threshold_overfull_is_ignored(db_session, monkeypatch, tmp_path, resume, job):
    captured = []
    _wire(monkeypatch, [CompileInfo(1, 1.9)], captured, tmp_path / "doc.pdf")
    outcome = tailor_flow.tailor_to_doc(db_session, resume, job, job.jd_text)
    assert len(captured) == 1  # no tighten rounds


def test_final_round_licenses_deletion_and_warns(db_session, monkeypatch, tmp_path, resume, job):
    captured = []
    # initial + rounds 1..3 all overflowing until the last fixes it
    _wire(
        monkeypatch,
        [CompileInfo(1, 14.0), CompileInfo(1, 12.0), CompileInfo(1, 9.0), CompileInfo(1, 0.0)],
        captured,
        tmp_path / "doc.pdf",
    )
    outcome = tailor_flow.tailor_to_doc(db_session, resume, job, job.jd_text)
    assert [c["delete"] for c in captured] == [False, False, False, True]
    final = captured[-1]["instruction"].lower()
    assert "delete" in final and "least" in final
    # Relevance is still judged against the JD — but by REFERENCE: tailor_resume
    # already receives the full JD as its own argument and puts it above the
    # instruction, so re-embedding it here would just duplicate it in the prompt.
    assert "job description" in final
    assert job.jd_text not in captured[-1]["instruction"]
    # Deletion edits must override TAILOR_SYSTEM's prose-only rule for "original".
    assert "overrides" in final and "\\item" in captured[-1]["instruction"]
    assert any("cut" in w.lower() for w in outcome.warnings) is False  # no edits actually deleted (stub returned none)


def test_deletion_warning_when_bullets_cut(db_session, monkeypatch, tmp_path, resume, job):
    seq = iter([CompileInfo(1, 14.0), CompileInfo(1, 12.0), CompileInfo(1, 9.0), CompileInfo(1, 0.0)])

    def fake_tailor(tex, jd, memory_context="", extra_instruction=None, budget=None, allow_item_deletion=False):
        applied = [{"original": "\\item Old bullet", "replacement": ""}] if allow_item_deletion else []
        return TailorResult(tex=tex, applied=applied, rejected=[])

    def fake_compile(doc):
        info = next(seq)
        doc.pdf_path = str(tmp_path / "doc.pdf")
        return info

    monkeypatch.setattr(tailor_flow, "tailor_resume", fake_tailor)
    monkeypatch.setattr(tailor_flow, "_compile_doc_info", fake_compile)
    monkeypatch.setattr(tailor_flow, "_short_tail_bullets", lambda p: [])
    outcome = tailor_flow.tailor_to_doc(db_session, resume, job, job.jd_text)
    assert any("1 bullet" in w and "review" in w.lower() for w in outcome.warnings)


def test_hard_fail_names_the_overfull_condition(db_session, monkeypatch, tmp_path, resume, job):
    captured = []
    # The hard-fail path unlinks doc.pdf_path — point it at a test-owned file.
    fake_pdf = tmp_path / "doc.pdf"
    fake_pdf.write_bytes(b"%PDF")
    _wire(monkeypatch, [CompileInfo(1, 14.0)] * 5, captured, fake_pdf)
    with pytest.raises(tailor_flow.PageOverflowError, match="bottom"):
        tailor_flow.tailor_to_doc(db_session, resume, job, job.jd_text)
    assert not fake_pdf.exists()  # stale PDF cleaned up
