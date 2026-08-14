from tests.test_latex import SAMPLE_TEX


def _create(client, name="SWE Resume", job_type="software"):
    return client.post(
        "/api/resumes",
        json={"name": name, "job_type": job_type, "tex_source": SAMPLE_TEX},
    )


def test_create_resume_compiles_and_returns_metadata(client):
    resp = _create(client)
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "SWE Resume"
    assert body["job_type"] == "software"
    assert body["page_count"] == 1
    assert body["id"] > 0


def test_create_resume_with_broken_latex_returns_422(client):
    resp = client.post(
        "/api/resumes",
        json={
            "name": "Bad",
            "job_type": "software",
            "tex_source": r"\documentclass{article}\begin{document}\undefinedcmd",
        },
    )
    assert resp.status_code == 422
    assert "compile" in resp.json()["detail"].lower()


def test_list_resumes_filters_by_job_type(client):
    _create(client, name="SWE", job_type="software")
    _create(client, name="Data", job_type="data")
    resp = client.get("/api/resumes", params={"job_type": "data"})
    assert resp.status_code == 200
    names = [r["name"] for r in resp.json()]
    assert names == ["Data"]


def test_list_resumes_orders_by_created_at_descending(client):
    first = _create(client, name="First", job_type="software").json()
    second = _create(client, name="Second", job_type="software").json()
    resp = client.get("/api/resumes")
    assert resp.status_code == 200
    results = resp.json()
    # Newest first: second should come before first
    names = [r["name"] for r in results]
    assert names.index("Second") < names.index("First")


def test_get_resume_includes_tex_source(client):
    rid = _create(client).json()["id"]
    resp = client.get(f"/api/resumes/{rid}")
    assert resp.status_code == 200
    assert resp.json()["tex_source"] == SAMPLE_TEX


def test_download_resume_pdf(client):
    rid = _create(client).json()["id"]
    resp = client.get(f"/api/resumes/{rid}/pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")


def test_update_resume_creates_new_version(client):
    rid = _create(client).json()["id"]
    new_tex = SAMPLE_TEX.replace("Python, Java, and SQL.", "Python, Go, and SQL.")
    resp = client.put(f"/api/resumes/{rid}", json={"tex_source": new_tex})
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] != rid
    assert body["parent_id"] == rid
    assert body["name"] == "SWE Resume"


def test_get_missing_resume_404s(client):
    assert client.get("/api/resumes/9999").status_code == 404


def test_delete_resume(client):
    rid = _create(client).json()["id"]
    assert client.delete(f"/api/resumes/{rid}").status_code == 204
    assert client.get(f"/api/resumes/{rid}").status_code == 404


def test_upload_pdf_only_resume(client, tmp_path):
    from app.services.latex import compile_tex

    pdf = compile_tex(SAMPLE_TEX, tmp_path, "standalone")
    with open(pdf, "rb") as f:
        resp = client.post(
            "/api/resumes/pdf",
            data={"name": "External PDF", "job_type": "software"},
            files={"file": ("external.pdf", f, "application/pdf")},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["page_count"] == 1

    detail = client.get(f"/api/resumes/{body['id']}").json()
    assert detail["tex_source"] is None

    download = client.get(f"/api/resumes/{body['id']}/pdf")
    assert download.content.startswith(b"%PDF")


def test_tailoring_pdf_only_resume_is_rejected(client, tmp_path):
    from app.services.latex import compile_tex

    pdf = compile_tex(SAMPLE_TEX, tmp_path, "standalone2")
    with open(pdf, "rb") as f:
        rid = client.post(
            "/api/resumes/pdf",
            data={"name": "External PDF", "job_type": "software"},
            files={"file": ("external.pdf", f, "application/pdf")},
        ).json()["id"]
    resp = client.post(
        "/api/generate/tailor",
        json={"resume_id": rid, "company": "A", "title": "B", "jd_text": "C"},
    )
    assert resp.status_code == 422
    assert "LaTeX source" in resp.json()["detail"]


def test_bulk_edit_updates_matching_resumes_as_new_versions(client):
    a = _create(client, name="SWE", job_type="software").json()["id"]
    b = _create(client, name="Data", job_type="data").json()["id"]

    resp = client.post(
        "/api/resumes/bulk-edit",
        json={"find": "Led a team of 4 students", "replace": "Led a team of 5 students"},
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert {r["status"] for r in results} == {"updated"}
    assert len(results) == 2

    for base_id in (a, b):
        new_id = next(r["new_id"] for r in results if r["id"] == base_id)
        detail = client.get(f"/api/resumes/{new_id}").json()
        assert "Led a team of 5 students" in detail["tex_source"]
        assert detail["parent_id"] == base_id


def test_bulk_edit_scoped_to_job_type_and_skips_nonmatching(client):
    _create(client, name="SWE", job_type="software")
    _create(client, name="Data", job_type="data")
    resp = client.post(
        "/api/resumes/bulk-edit",
        json={"find": "web scraper", "replace": "data scraper", "job_type": "data"},
    )
    results = resp.json()["results"]
    assert len(results) == 1
    assert results[0]["status"] == "updated"


def test_import_pdf_flow(client, monkeypatch):
    from app.services import resume_import

    monkeypatch.setattr(resume_import, "extract_text", lambda fn, data: "Jane Doe " * 60)
    monkeypatch.setattr(resume_import, "pdf_page_count", lambda p: 1)
    # start_run is stubbed by conftest — drive the pipeline by hand below.

    resp = client.post(
        "/api/resumes/import-pdf",
        files={"file": ("jane.pdf", b"%PDF-fake", "application/pdf")},
        data={"name": "Jane base", "job_type": "software"},
    )
    assert resp.status_code == 201
    sid = resp.json()["id"]

    st = client.get(f"/api/resumes/import-sessions/{sid}").json()
    assert st["status"] == "running" and st["stage"] == "extract"

    # accept before review → 409
    assert client.post(f"/api/resumes/import-sessions/{sid}/accept").status_code == 409


def test_import_pdf_rejects_scanned(client, monkeypatch):
    from app.services import resume_import

    monkeypatch.setattr(resume_import, "extract_text", lambda fn, data: "")
    resp = client.post(
        "/api/resumes/import-pdf",
        files={"file": ("scan.pdf", b"%PDF-fake", "application/pdf")},
        data={"name": "Scan", "job_type": "software"},
    )
    assert resp.status_code == 422
    assert "extractable text" in resp.json()["detail"]


def test_import_accept_with_non_compiling_tex_returns_409(client, db_session):
    from app.db.models import ImportSession

    s = ImportSession(
        filename="jane.pdf",
        name="Jane base",
        job_type="software",
        status="review",
        stage="review",
        progress=1.0,
        state={
            "tex": "\\documentclass{article}\\begin{document}broken\\end{document}",
            "report": {"fidelity": [], "fit": ["does not compile: x"], "alignment": []},
        },
    )
    db_session.add(s)
    db_session.commit()
    sid = s.id

    resp = client.post(f"/api/resumes/import-sessions/{sid}/accept")
    assert resp.status_code == 409
    assert "does not compile" in resp.json()["detail"]
