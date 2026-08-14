import app.services.claude as claude
from tests.test_latex import SAMPLE_TEX

GOOD_EDIT = {
    "original": "Developed a web scraper that collected data from 100 sites.",
    "replacement": "Built a resilient scraper aggregating data from 100+ sites.",
}


def _make_resume(client):
    return client.post(
        "/api/resumes",
        json={"name": "SWE", "job_type": "software", "tex_source": SAMPLE_TEX},
    ).json()["id"]


def _tailor(client, resume_id, **kwargs):
    payload = {
        "resume_id": resume_id,
        "company": "Acme",
        "title": "SWE Intern",
        "jd_text": "Looking for interns with scraping and data experience.",
    }
    payload.update(kwargs)
    return client.post("/api/generate/tailor", json=payload)


def _bullet_text(i, n=12, extra_words=0):
    words = [f"wordnum{i}x{j}" for j in range(n)] + [f"bloatword{k}" for k in range(extra_words)]
    return " ".join(words) + "."


def _bulleted_resume_tex(n_bullets=20, words_per_bullet=12):
    """A resume sized to sit exactly on the one-page/two-page boundary: N filler
    bullets that compile to one page, so a modest (in-budget) growth of a single
    bullet tips it to two."""
    items = "\n".join(r"\item " + _bullet_text(i, words_per_bullet) for i in range(n_bullets))
    return (
        "\\documentclass{article}\n"
        "\\usepackage[margin=1in]{geometry}\n"
        "\\begin{document}\n"
        "\\section{Experience}\n"
        "\\begin{itemize}\n"
        f"{items}\n"
        "\\end{itemize}\n"
        "\\end{document}\n"
    )


def test_tailor_creates_job_and_doc_with_pdf(client, monkeypatch):
    monkeypatch.setattr(claude, "generate_json", lambda *a, **k: {"edits": [GOOD_EDIT]})
    rid = _make_resume(client)

    resp = _tailor(client, rid)
    assert resp.status_code == 201
    body = resp.json()
    assert body["doc_type"] == "resume"
    assert body["page_count"] == 1
    assert body["applied_edits"] == [GOOD_EDIT]
    assert body["approved"] is False

    job = client.get(f"/api/jobs/{body['job_id']}").json()
    assert job["company"] == "Acme"
    assert len(job["docs"]) == 1

    pdf = client.get(f"/api/docs/{body['id']}/pdf")
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")


def test_tailor_with_existing_job_reuses_it(client, monkeypatch):
    monkeypatch.setattr(claude, "generate_json", lambda *a, **k: {"edits": [GOOD_EDIT]})
    rid = _make_resume(client)
    jid = client.post(
        "/api/jobs", json={"company": "Acme", "title": "SWE Intern", "jd_text": "scraping JD"}
    ).json()["id"]

    resp = _tailor(client, rid, job_id=jid)
    assert resp.status_code == 201
    assert resp.json()["job_id"] == jid
    assert len(client.get("/api/jobs").json()) == 1


def test_tailor_page_guard_runs_tighten_round(client, monkeypatch):
    """Regression test for the tighten round's divergence-budget exemption.

    Round 1 grows one bullet just enough (~10% divergence, well within the 25%
    default budget) to push the resume from one page to two. Round 2 (the
    page-guard tighten call) then has to shrink several *other* bullets to fit
    back on one page — a change that is >25% divergence relative to round 1's
    own output. Under the shipped default budget, without the tighten call's
    exemption (`budget=1.0` in generate.py), those shrink edits would be
    rejected partway through with "divergence budget reached" and the resume
    would stay at two pages. No divergence override here: this proves the
    tighten round works under the real, shipped default.
    """
    base_tex = _bulleted_resume_tex()
    calls = []

    def fake_generate(system, user, schema, max_tokens=16000):
        calls.append(user)
        if len(calls) == 1:
            # Round 1: grow bullet 0 just enough to overflow to a second page.
            return {
                "edits": [
                    {
                        "original": _bullet_text(0),
                        "replacement": _bullet_text(0, extra_words=24),
                    }
                ]
            }
        # Round 2 (tighten): shrink 8 *other* bullets to fit back on one page.
        # Relative to round 1's output this is >25% divergence -- the point of the test.
        return {
            "edits": [
                {"original": _bullet_text(i), "replacement": _bullet_text(i, n=2)}
                for i in range(1, 9)
            ]
        }

    monkeypatch.setattr(claude, "generate_json", fake_generate)
    rid = client.post(
        "/api/resumes",
        json={"name": "Filler", "job_type": "software", "tex_source": base_tex},
    ).json()["id"]

    resp = _tailor(client, rid)
    assert resp.status_code == 201
    body = resp.json()
    assert len(calls) == 2
    assert body["page_count"] == 1
    # No spurious rejections from the tighten round's shrink edits.
    assert body["rejected_edits"] == []
    # The base-vs-final drift warning still fires -- only the intermediate
    # tighten check is exempt, not the endpoint's overall divergence report.
    assert any("review before approving" in w for w in body["warnings"])


def test_approve_doc(client, monkeypatch):
    monkeypatch.setattr(claude, "generate_json", lambda *a, **k: {"edits": [GOOD_EDIT]})
    rid = _make_resume(client)
    doc_id = _tailor(client, rid).json()["id"]

    resp = client.post(f"/api/docs/{doc_id}/approve")
    assert resp.status_code == 200
    assert resp.json()["approved"] is True


def test_cover_letter_generation_returns_draft(client, monkeypatch):
    from app.services import coverletter

    monkeypatch.setattr(
        coverletter,
        "generate_text",
        lambda *a, **k: "I grew revenue by 50% at Smith & Sons.\n\nSincerely,\nJared",
    )
    rid = _make_resume(client)
    jid = client.post(
        "/api/jobs", json={"company": "Acme", "title": "SWE Intern", "jd_text": "JD text"}
    ).json()["id"]

    resp = client.post("/api/generate/cover-letter", json={"job_id": jid, "resume_id": rid})
    assert resp.status_code == 201
    body = resp.json()
    assert body["doc_type"] == "cover_letter"
    assert body["page_count"] == 0
    assert body["body_text"] == "I grew revenue by 50% at Smith & Sons.\n\nSincerely,\nJared"


def test_get_doc_returns_tex_for_diff_view(client, monkeypatch):
    monkeypatch.setattr(claude, "generate_json", lambda *a, **k: {"edits": [GOOD_EDIT]})
    rid = _make_resume(client)
    doc_id = _tailor(client, rid).json()["id"]

    resp = client.get(f"/api/docs/{doc_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert GOOD_EDIT["replacement"] in body["tex_source"]
    assert body["base_tex_source"] == SAMPLE_TEX
