"""Fill-plan builder: profile fills, essays, attach, nav classification, submit guard."""

import pytest

from app.db.models import ApplySession, GeneratedDoc, ProfileField
from app.services import fillplan


@pytest.fixture
def session(db_session, client):
    job = client.post("/api/jobs", json={"company": "Acme", "title": "SWE", "jd_text": "Build."}).json()
    resume_doc = GeneratedDoc(job_id=job["id"], doc_type="resume", tex_source="t", pdf_path="/x/r.pdf")
    cover_doc = GeneratedDoc(job_id=job["id"], doc_type="cover_letter", tex_source="t", pdf_path="/x/c.pdf", approved=True)
    db_session.add_all([resume_doc, cover_doc])
    db_session.commit()
    s = ApplySession(
        url="https://x", job_id=job["id"], resume_doc_id=resume_doc.id, cover_doc_id=cover_doc.id,
        state={"qa_drafts": {}},
    )
    db_session.add(s)
    db_session.commit()
    return s


F = lambda i, **kw: {"index": i, "type": "text", "id": "", "name": "", "placeholder": "", "aria_label": "", "label": "", **kw}


def test_profile_fields_fill_and_essays_draft(db_session, session, monkeypatch):
    fields = [
        F(0, label="First name"),
        F(1, type="textarea", label="Why do you want to work at Acme?"),
    ]
    monkeypatch.setattr(
        fillplan.autofill_service, "map_fields",
        lambda db, flds, use_ai: [{"index": 0, "value": "Alex", "confidence": "high"}],
    )
    monkeypatch.setattr(
        fillplan.answers_service, "draft_answer",
        lambda db, question, job_id=None: {"draft": "Because I build things."},
    )
    actions = fillplan.build_plan(db_session, session, fields, [])
    kinds = {a["kind"]: a for a in actions}
    assert kinds["fill"]["index"] in (0, 1)
    essay = next(a for a in actions if a.get("essay"))
    assert essay["review"] is True and essay["value"] == "Because I build things."
    assert session.state["qa_drafts"]["Why do you want to work at Acme?"] == "Because I build things."


def test_profile_fill_indexes_align_with_file_field_mid_list(db_session, session):
    """Regression (CRITICAL): map_fields returns POSITIONAL indexes over the
    list it was given (`plain`, files excluded) — build_plan must resolve
    those positions against `plain` and re-emit the snapshot's own index,
    not resolve m['index'] against the full fields list. Otherwise a file
    input mid-page shifts every later mapping onto the wrong element (e.g.
    email lands in the file input, nothing lands on the real email field).
    Uses the REAL autofill.map_fields (no monkeypatch) against real
    ProfileField rows to exercise the actual index math end to end."""
    db_session.add_all([
        ProfileField(key="first_name", value="Alex"),
        ProfileField(key="email", value="alex@example.com"),
    ])
    db_session.commit()
    fields = [
        F(0, label="First name"),
        F(1, type="file", label="Resume"),
        F(2, label="Email"),
    ]
    actions = fillplan.build_plan(db_session, session, fields, [])
    fills = {a["index"]: a for a in actions if a["kind"] == "fill"}
    assert fills[0]["value"] == "Alex"
    assert fills[2]["value"] == "alex@example.com"
    assert 1 not in fills


def test_attach_targets_by_doc_kind(db_session, session, monkeypatch):
    monkeypatch.setattr(fillplan.autofill_service, "map_fields", lambda db, f, use_ai: [])
    fields = [
        F(0, type="file", label="Resume/CV"),
        F(1, type="file", label="Cover letter (optional)"),
    ]
    actions = fillplan.build_plan(db_session, session, fields, [])
    attaches = {a["doc_kind"]: a for a in actions if a["kind"] == "attach"}
    assert attaches["resume"]["index"] == 0 and attaches["resume"]["doc_id"] == session.resume_doc_id
    assert attaches["cover_letter"]["index"] == 1 and attaches["cover_letter"]["doc_id"] == session.cover_doc_id


def test_profile_mapping_requests_ai_assist(db_session, session, monkeypatch):
    """The pipeline must opt in to map_fields' AI fallback — heuristics alone
    leave every unmatched short answer empty (the 'nothing registers' bug)."""
    seen = {}

    def spy(db, flds, use_ai):
        seen["use_ai"] = use_ai
        return []

    monkeypatch.setattr(fillplan.autofill_service, "map_fields", spy)
    fillplan.build_plan(db_session, session, [F(0, label="Preferred first name")], [])
    assert seen["use_ai"] is True


def test_radio_group_answered_and_validated(db_session, session, monkeypatch):
    """Yes/No radio groups (work auth, relocation) ride the select engine call:
    the group's question comes from group_label, the answer must match one
    radio's own label verbatim, and the matching radio gets a review-flagged
    'check' action. A value matching no radio degrades to nothing."""
    monkeypatch.setattr(fillplan.autofill_service, "map_fields", lambda db, f, use_ai: [])
    fields = [
        F(3, type="radio", label="Yes", name="cards[auth]",
          group_label="Are you legally authorized to work in the United States?"),
        F(4, type="radio", label="No", name="cards[auth]",
          group_label="Are you legally authorized to work in the United States?"),
    ]

    captured = {}

    def fake_engine(system, content, schema):
        captured["content"] = content
        return {"values": [{"index": 3, "value": "Yes"}]}

    monkeypatch.setattr(fillplan, "generate_json", fake_engine)
    actions = fillplan.build_plan(db_session, session, fields, [])
    check = next(a for a in actions if a["kind"] == "check")
    assert check["index"] == 3 and check["review"] is True
    assert "legally authorized" in captured["content"]
    assert "Yes, No" in captured["content"] or "Yes / No" in captured["content"]

    monkeypatch.setattr(
        fillplan, "generate_json",
        lambda s, c, sch: {"values": [{"index": 3, "value": "Maybe"}]},
    )
    session.state = {**session.state, "last_page_sig": None}  # separate scenario, not a stalled replan
    actions = fillplan.build_plan(db_session, session, fields, [])
    assert not any(a["kind"] == "check" for a in actions)


def test_radio_answer_fuzzy_matches_member(db_session, session, monkeypatch):
    """ATS radio labels are long ('Yes, I am authorized…'); the engine often
    answers with the short form. The nearest member label must receive the
    check instead of the whole group being dropped."""
    monkeypatch.setattr(fillplan.autofill_service, "map_fields", lambda db, f, use_ai: [])
    fields = [
        F(3, type="radio", label="Yes, I am legally authorized", name="auth", group_label="Are you authorized to work?"),
        F(4, type="radio", label="No, I am not", name="auth", group_label="Are you authorized to work?"),
    ]
    monkeypatch.setattr(
        fillplan, "generate_json",
        lambda s, c, sch: {"values": [{"index": 3, "value": "Yes"}]},
    )
    actions = fillplan.build_plan(db_session, session, fields, [])
    check = next(a for a in actions if a["kind"] == "check")
    assert check["index"] == 3


def test_radio_answer_targets_group_member_not_first(db_session, session, monkeypatch):
    monkeypatch.setattr(fillplan.autofill_service, "map_fields", lambda db, f, use_ai: [])
    fields = [
        F(3, type="radio", label="Yes", name="cards[auth]", group_label="Authorized to work?"),
        F(4, type="radio", label="No", name="cards[auth]", group_label="Authorized to work?"),
    ]
    monkeypatch.setattr(
        fillplan, "generate_json",
        lambda s, c, sch: {"values": [{"index": 3, "value": "No"}]},
    )
    actions = fillplan.build_plan(db_session, session, fields, [])
    check = next(a for a in actions if a["kind"] == "check")
    assert check["index"] == 4  # the "No" radio, not the group's anchor index


def test_checkboxes_are_never_auto_checked(db_session, session, monkeypatch):
    """Consent/marketing checkboxes stay with the human — no check actions."""
    monkeypatch.setattr(fillplan.autofill_service, "map_fields", lambda db, f, use_ai: [])
    fields = [
        F(0, type="checkbox", label="I consent to be contacted", name="c1",
          group_label="I consent to be contacted"),
    ]
    monkeypatch.setattr(
        fillplan, "generate_json",
        lambda s, c, sch: {"values": [{"index": 0, "value": "I consent to be contacted"}]},
    )
    actions = fillplan.build_plan(db_session, session, fields, [])
    assert not any(a["kind"] == "check" for a in actions)


def test_attach_filename_is_personalized_from_profile(db_session, session, monkeypatch):
    monkeypatch.setattr(fillplan.autofill_service, "map_fields", lambda db, f, use_ai: [])
    db_session.add_all([
        ProfileField(key="first_name", value="Alex"),
        ProfileField(key="last_name", value="Sample"),
    ])
    db_session.commit()
    fields = [F(0, type="file", label="Resume/CV"), F(1, type="file", label="Cover letter")]
    actions = fillplan.build_plan(db_session, session, fields, [])
    attaches = {a["doc_kind"]: a for a in actions if a["kind"] == "attach"}
    assert attaches["resume"]["filename"] == "Sample_Alex_resume.pdf"
    assert attaches["cover_letter"]["filename"] == "Sample_Alex_cover_letter.pdf"


def test_attach_filename_falls_back_without_profile_names(db_session, session, monkeypatch):
    monkeypatch.setattr(fillplan.autofill_service, "map_fields", lambda db, f, use_ai: [])
    fields = [F(0, type="file", label="Resume/CV")]
    actions = fillplan.build_plan(db_session, session, fields, [])
    attach = next(a for a in actions if a["kind"] == "attach")
    assert attach["filename"] == "resume.pdf"


def test_nav_button_clicked_submit_never(db_session, session, monkeypatch):
    monkeypatch.setattr(fillplan.autofill_service, "map_fields", lambda db, f, use_ai: [])
    fields = [F(0, label="First name")]
    monkeypatch.setattr(
        fillplan.autofill_service, "map_fields",
        lambda db, f, use_ai: [{"index": 0, "value": "Jared", "confidence": "high"}],
    )
    buttons = [
        {"index": 0, "text": "Submit Application", "aria_label": "", "name": "", "value": ""},
        {"index": 1, "text": "Next", "aria_label": "", "name": "", "value": ""},
    ]
    actions = fillplan.build_plan(db_session, session, fields, buttons)
    assert actions[-1]["kind"] == "click_nav" and actions[-1]["button_index"] == 1
    assert actions[-1]["expect_text"] == "Next"
    assert not any(a.get("button_index") == 0 and a["kind"] == "click_nav" for a in actions)


def test_submit_only_page_hands_off(db_session, session, monkeypatch):
    monkeypatch.setattr(fillplan.autofill_service, "map_fields", lambda db, f, use_ai: [])
    buttons = [{"index": 0, "text": "Submit Application", "aria_label": "", "name": "", "value": ""}]
    actions = fillplan.build_plan(db_session, session, [], buttons)
    assert actions[-1]["kind"] == "await_user" and actions[-1]["button_index"] == 0
    # Submit handoff is the terminal one: content.js's __execute reports done=true
    # only when terminal is true, ending the session here.
    assert actions[-1]["terminal"] is True


def test_engine_failure_still_returns_profile_fills(db_session, session, monkeypatch):
    # conftest stub raises on fillplan.generate_json; selects degrade, fills survive
    monkeypatch.setattr(
        fillplan.autofill_service, "map_fields",
        lambda db, f, use_ai: [{"index": 0, "value": "Alex", "confidence": "high"}],
    )
    fields = [F(0, label="First name"), F(1, type="select", label="Country", options=["US", "CA"])]
    actions = fillplan.build_plan(db_session, session, fields, [])
    assert any(a["kind"] == "fill" and a["index"] == 0 for a in actions)
    assert not any(a["kind"] == "select" for a in actions)  # degraded, not crashed


def test_engine_select_value_validated_against_options(db_session, session, monkeypatch):
    """Defense-in-depth: the select-mapping prompt asks the engine for a verbatim
    option match, but nothing enforces that on the model's side — build_plan must
    reject an out-of-options value rather than trust it blind."""
    monkeypatch.setattr(fillplan.autofill_service, "map_fields", lambda db, f, use_ai: [])
    fields = [F(0, type="select", label="Country", options=["US", "CA"])]

    monkeypatch.setattr(
        fillplan, "generate_json",
        lambda system, content, schema: {"values": [{"index": 0, "value": "France"}]},
    )
    actions = fillplan.build_plan(db_session, session, fields, [])
    assert not any(a["kind"] == "select" for a in actions)

    monkeypatch.setattr(
        fillplan, "generate_json",
        lambda system, content, schema: {"values": [{"index": 0, "value": "US"}]},
    )
    session.state = {**session.state, "last_page_sig": None}  # separate scenario, not a stalled replan
    actions = fillplan.build_plan(db_session, session, fields, [])
    select = next(a for a in actions if a["kind"] == "select")
    assert select["index"] == 0 and select["value"] == "US"


def test_engine_select_value_snapped_to_closest_option(db_session, session, monkeypatch):
    """Case drift and short-form answers must snap to the closest real option
    (review) instead of being rejected — the 'Company website' testbed failure
    and the 'skipped instead of next best' complaint."""
    monkeypatch.setattr(fillplan.autofill_service, "map_fields", lambda db, f, use_ai: [])
    fields = [
        F(0, type="select", label="How did you hear about us?", options=["Company Website", "Job Board"]),
        F(1, type="select", label="Authorized?", options=["Yes, I am authorized to work in the US", "No"]),
    ]
    monkeypatch.setattr(
        fillplan, "generate_json",
        lambda system, content, schema: {"values": [
            {"index": 0, "value": "company website"},
            {"index": 1, "value": "Yes"},
        ]},
    )
    actions = fillplan.build_plan(db_session, session, fields, [])
    selects = {a["index"]: a for a in actions if a["kind"] == "select"}
    assert selects[0]["value"] == "Company Website"
    assert selects[1]["value"] == "Yes, I am authorized to work in the US"


def test_nav_button_with_no_field_actions_hands_off(db_session, session, monkeypatch):
    """Additional safety requirement (navguard review carry-forward): many real ATS
    final/review pages label their actual submit button "Continue" (allowlisted as
    nav). A page with zero field actions is likely such a review/confirm step, so
    click_nav must NOT fire there — the nav-classified button becomes await_user
    instead, forcing a human to look before advancing."""
    monkeypatch.setattr(fillplan.autofill_service, "map_fields", lambda db, f, use_ai: [])
    buttons = [{"index": 0, "text": "Continue", "aria_label": "", "name": "", "value": ""}]
    actions = fillplan.build_plan(db_session, session, [], buttons)
    assert actions[-1]["kind"] == "await_user"
    assert actions[-1]["button_index"] == 0
    assert actions[-1]["reason"] == "Nothing to fill here — advance manually after reviewing."
    assert not any(a["kind"] == "click_nav" for a in actions)
    # Fieldless nav handoff is NOT terminal: more fields may appear once the
    # user advances past this review/confirm step, unlike the final submit.
    assert actions[-1]["terminal"] is False


def test_question_shaped_text_input_gets_a_draft(db_session, session, monkeypatch):
    """Testbed audit finding: 'What excites you about robotics, in one
    sentence?' is a text input, not a textarea — it must still get a drafted
    answer, while fact-shaped question inputs (salary, referral) must not."""
    monkeypatch.setattr(fillplan.autofill_service, "map_fields", lambda db, f, use_ai: [])
    monkeypatch.setattr(
        fillplan.answers_service, "draft_answer",
        lambda db, question, job_id=None: {"draft": "Robots fail in the real world."},
    )
    fields = [
        F(0, type="text", label="What excites you about robotics, in one sentence?"),
        F(1, type="text", label="What is your expected salary?"),
        F(2, type="text", label="If referred by an employee, who?"),
        F(3, type="text", label="Middle name"),
    ]
    actions = fillplan.build_plan(db_session, session, fields, [])
    essays = [a for a in actions if a.get("essay")]
    assert [a["index"] for a in essays] == [0]
    assert essays[0]["value"] == "Robots fail in the real world."


def test_select_prompt_includes_brain_notes(db_session, session, monkeypatch):
    """Preference questions (working style, relocation) are only answerable
    from the brain — the select/radio engine call must carry memory context
    when the brain has it, and skip retrieval entirely when it doesn't."""
    from app.db.models import MemoryEntry

    captured = {}

    def fake_engine(system, content, schema):
        captured["content"] = content
        return {"values": []}

    monkeypatch.setattr(fillplan, "generate_json", fake_engine)
    monkeypatch.setattr(fillplan.autofill_service, "map_fields", lambda db, f, use_ai: [])
    fields = [
        F(3, type="radio", label="Deep solo work", name="style", group_label="Which working style fits you best?"),
        F(4, type="radio", label="Frequent pairing", name="style", group_label="Which working style fits you best?"),
    ]

    # Empty brain: no notes block, and retrieval is never attempted.
    def boom(db, q, k_seeds=4):
        raise AssertionError("retrieve_context must not run on an empty brain")

    monkeypatch.setattr(fillplan.memory_service, "retrieve_context", boom)
    fillplan.build_plan(db_session, session, fields, [])
    assert "APPLICANT NOTES" not in captured["content"]

    # Brain with an embedded entry: notes reach the prompt.
    db_session.add(MemoryEntry(type="preference", title="Working style", content="Likes pairing", embedding=b"x"))
    db_session.commit()

    class Ctx:
        markdown = "- Prefers frequent pairing and design discussion"

    monkeypatch.setattr(fillplan.memory_service, "retrieve_context", lambda db, q, k_seeds=4: Ctx())
    session.state = {**session.state, "last_page_sig": None}  # separate scenario, not a stalled replan
    fillplan.build_plan(db_session, session, fields, [])
    assert "APPLICANT NOTES" in captured["content"]
    assert "frequent pairing" in captured["content"]


def test_unrecognized_file_slot_left_for_human(db_session, session, monkeypatch):
    """Testbed audit finding: a transcript/portfolio slot must NOT receive the
    resume — only slots that name resume/cv (or cover letter) get attachments."""
    monkeypatch.setattr(fillplan.autofill_service, "map_fields", lambda db, f, use_ai: [])
    fields = [
        F(0, type="file", label="Unofficial transcript (optional)"),
        F(1, type="file", label="Writing sample"),
        F(2, type="file", label="Curriculum Vitae"),
    ]
    actions = fillplan.build_plan(db_session, session, fields, [])
    attaches = [a for a in actions if a["kind"] == "attach"]
    assert [a["index"] for a in attaches] == [2]
    assert attaches[0]["doc_kind"] == "resume"


def test_date_and_month_values_coerced_to_iso(db_session, session, monkeypatch):
    """Testbed audit finding: native date/month inputs silently reject non-ISO
    writes, so profile phrasings like 'May 1, 2028' must be coerced at plan
    time — and plain text fields keep the human phrasing untouched."""
    monkeypatch.setattr(
        fillplan.autofill_service, "map_fields",
        lambda db, f, use_ai: [
            {"index": 0, "value": "May 1, 2028", "confidence": "high"},
            {"index": 1, "value": "May, 2028", "confidence": "high"},
            {"index": 2, "value": "May 1, 2028", "confidence": "high"},
        ],
    )
    fields = [
        F(0, type="date", label="Earliest start date"),
        F(1, type="month", label="Expected graduation"),
        F(2, type="text", label="When can you start?"),
    ]
    actions = fillplan.build_plan(db_session, session, fields, [])
    values = {a["index"]: a["value"] for a in actions if a["kind"] == "fill"}
    assert values[0] == "2028-05-01"
    assert values[1] == "2028-05"
    assert values[2] == "May 1, 2028"


def test_workday_date_segments_get_sliced_values(db_session, session, monkeypatch):
    """Workday renders dates as separate MM / DD / YYYY spinner inputs
    (dateSectionMonth-input etc.) — each segment must receive just its part of
    the mapped date, never the whole string."""
    monkeypatch.setattr(
        fillplan.autofill_service, "map_fields",
        lambda db, f, use_ai: [
            {"index": 0, "value": "May 2028", "confidence": "high"},
            {"index": 1, "value": "May 2028", "confidence": "high"},
        ],
    )
    fields = [
        F(0, label="Expected graduation", automation_id="dateSectionMonth-input"),
        F(1, label="Expected graduation", automation_id="dateSectionYear-input"),
    ]
    actions = fillplan.build_plan(db_session, session, fields, [])
    values = {a["index"]: a["value"] for a in actions if a["kind"] == "fill"}
    assert values[0] == "05"
    assert values[1] == "2028"


def test_unparseable_temporal_passes_through(db_session, session, monkeypatch):
    monkeypatch.setattr(
        fillplan.autofill_service, "map_fields",
        lambda db, f, use_ai: [{"index": 0, "value": "as soon as possible", "confidence": "high"}],
    )
    fields = [F(0, type="date", label="Start date")]
    actions = fillplan.build_plan(db_session, session, fields, [])
    assert next(a for a in actions if a["kind"] == "fill")["value"] == "as soon as possible"


def test_same_page_replan_after_nav_click_stalls(db_session, session, monkeypatch):
    """Workday validation-failure loop: we filled a page and clicked Next, but
    the wizard stayed put (required field rejected) and the DOM mutation
    triggered a replan of the SAME page. Re-planning would re-run the engine,
    overwrite manual corrections, and click Next forever — the second plan must
    be a single stall handoff and further replans of that page empty."""
    monkeypatch.setattr(
        fillplan.autofill_service, "map_fields",
        lambda db, f, use_ai: [{"index": 0, "value": "Jared", "confidence": "high"}],
    )
    fields = [F(0, label="First name")]
    buttons = [{"index": 1, "text": "Next", "aria_label": "", "name": "", "value": ""}]
    first = fillplan.build_plan(db_session, session, fields, buttons)
    assert first[-1]["kind"] == "click_nav"

    second = fillplan.build_plan(db_session, session, fields, buttons)
    assert [a["kind"] for a in second] == ["await_user"]
    assert second[0]["terminal"] is False
    assert second[0]["button_index"] is None

    third = fillplan.build_plan(db_session, session, fields, buttons)
    assert third == []

    # A genuinely different page plans normally again.
    monkeypatch.setattr(
        fillplan.autofill_service, "map_fields",
        lambda db, f, use_ai: [{"index": 0, "value": "j@x.com", "confidence": "high"}],
    )
    fourth = fillplan.build_plan(db_session, session, [F(0, label="Email")], buttons)
    assert any(a["kind"] == "fill" for a in fourth)


def test_same_page_replan_without_nav_is_silently_empty(db_session, session, monkeypatch):
    """A replan of an unchanged page we did NOT try to advance (mutation noise
    while the user edits) must return no actions — never re-fill over the
    user's corrections — and no stall notice either, since nothing is stuck."""
    monkeypatch.setattr(
        fillplan.autofill_service, "map_fields",
        lambda db, f, use_ai: [{"index": 0, "value": "Alex", "confidence": "high"}],
    )
    fields = [F(0, label="First name")]
    first = fillplan.build_plan(db_session, session, fields, [])
    assert any(a["kind"] == "fill" for a in first)
    assert fillplan.build_plan(db_session, session, fields, []) == []


def test_nav_button_with_empty_expect_text_hands_off(db_session, session, monkeypatch):
    """Minor finding: a button classified 'nav' purely by its `name` attribute
    (no visible text/aria/value) gives click-time text re-verification in
    content.js nothing to compare against — expect_text would be empty and the
    guard would silently no-op. build_plan must hand off instead of clicking
    blind."""
    monkeypatch.setattr(
        fillplan.autofill_service, "map_fields",
        lambda db, f, use_ai: [{"index": 0, "value": "Alex", "confidence": "high"}],
    )
    fields = [F(0, label="First name")]
    buttons = [{"index": 0, "text": "", "aria_label": "", "value": "", "name": "continue"}]
    actions = fillplan.build_plan(db_session, session, fields, buttons)
    assert actions[-1]["kind"] == "await_user"
    assert actions[-1]["button_index"] == 0
    assert actions[-1]["terminal"] is False
    assert not any(a["kind"] == "click_nav" for a in actions)


from app.db.models import ApplySession, DocType, GeneratedDoc, Job


def _tailor_only_session(db, with_doc=True):
    job = Job(company="Acme", title="SWE Intern")
    db.add(job)
    db.flush()
    doc_id = None
    if with_doc:
        doc = GeneratedDoc(job_id=job.id, doc_type=DocType.RESUME, tex_source="x")
        db.add(doc)
        db.flush()
        doc_id = doc.id
    s = ApplySession(
        url="https://jobs.example/apply",
        job_id=job.id,
        resume_doc_id=doc_id,
        state={"mode": "tailor_only", "fill_scope": "resume_slot_only",
               "options": {"tailor_resume": True, "cover_letter": False,
                           "answer_questions": False},
               "qa_drafts": {}},
    )
    db.add(s)
    db.commit()
    return s


TAILOR_FIELDS = [
    {"index": 0, "type": "text", "label": "Full name", "name": "name"},
    {"index": 1, "type": "file", "label": "Resume/CV", "name": "resume"},
    {"index": 2, "type": "file", "label": "Cover letter", "name": "cover"},
    {"index": 3, "type": "textarea", "label": "Why do you want to work here?", "name": "why"},
    {"index": 4, "type": "select", "label": "Country", "name": "country",
     "options": ["United States", "Australia"]},
]


def test_resume_slot_only_attaches_resume_and_touches_nothing_else(db_session):
    s = _tailor_only_session(db_session)
    actions = fillplan.build_plan(db_session, s, TAILOR_FIELDS, buttons=[])
    assert len(actions) == 1
    a = actions[0]
    assert a["kind"] == "attach" and a["index"] == 1 and a["doc_kind"] == "resume"
    assert a["doc_id"] == s.resume_doc_id
    # Replan-guard bookkeeping (regression): the resume_slot_only branch must
    # write last_page_sig same as the general path, or an identical repeat
    # snapshot of the same page (Workday re-rendering the DOM without a real
    # page change) re-emits the attach action on every replan instead of
    # being suppressed by the same-page guard.
    actions2 = fillplan.build_plan(db_session, s, TAILOR_FIELDS, buttons=[])
    assert actions2 == []


def test_resume_slot_only_no_slot_notifies_once(db_session):
    s = _tailor_only_session(db_session)
    no_file_fields = [f for f in TAILOR_FIELDS if f["type"] != "file"]
    actions = fillplan.build_plan(db_session, s, no_file_fields, buttons=[])
    assert len(actions) == 1 and actions[0]["kind"] == "await_user"
    assert "resume bank" in actions[0]["reason"].lower() or "bank" in actions[0]["reason"]
    # Second snapshot of a DIFFERENT page shape: no repeat notification.
    actions2 = fillplan.build_plan(db_session, s, no_file_fields[:1], buttons=[])
    assert actions2 == []


def test_resume_slot_only_without_doc_attaches_nothing(db_session):
    s = _tailor_only_session(db_session, with_doc=False)
    actions = fillplan.build_plan(db_session, s, TAILOR_FIELDS, buttons=[])
    # No tailored doc yet: nothing to attach — even with a résumé slot on the
    # page. The empty-plan path emits the one-time notice instead.
    assert [a for a in actions if a["kind"] == "attach"] == []
    assert len(actions) == 1 and actions[0]["kind"] == "await_user"
