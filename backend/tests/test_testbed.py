"""The /testbed dummy application: served tokenless (it's not under /api),
static, and self-contained — the training ground for the fill pipeline."""

from fastapi.testclient import TestClient

from app.main import app
from app.services import fillplan
from tests.test_fillplan import _tailor_only_session


def test_testbed_serves_without_token(db_sessionmaker):
    with TestClient(app) as c:  # deliberately no X-Copilot-Token
        r = c.get("/testbed")
        assert r.status_code == 200
        assert "Meridian Autonomy Labs" in r.text
        # Both wizard pages ship in the one document.
        assert 'id="page1"' in r.text and 'id="page2"' in r.text
        # It must never talk to anything: no form action, no fetch/XHR.
        assert "action=" not in r.text
        assert "fetch(" not in r.text and "XMLHttpRequest" not in r.text


def test_greenhouse_trial_serves_and_stays_local(db_sessionmaker):
    with TestClient(app) as c:
        r = c.get("/testbed/greenhouse")
        assert r.status_code == 200
        assert "Cascade Robotics" in r.text
        # The defining widget shapes must be present: react-select comboboxes,
        # hidden file inputs behind Attach buttons, EEOC wordings.
        assert 'class="select__input"' in r.text
        assert 'data-attach="resume"' in r.text
        assert "Decline To Self Identify" in r.text
        assert "action=" not in r.text
        assert "fetch(" not in r.text and "XMLHttpRequest" not in r.text


def test_workday_trial_serves_and_stays_local(db_sessionmaker):
    with TestClient(app) as c:
        r = c.get("/testbed/workday")
        assert r.status_code == 200
        assert "Northwind Dynamics" in r.text
        # Workday's defining shapes: promptOption search results, date-segment
        # spinners, listbox buttons, the blocking error banner.
        assert "promptOption" in r.text
        assert "dateSectionMonth-input" in r.text
        assert 'aria-haspopup="listbox"' in r.text
        assert "errorBanner" in r.text
        assert "action=" not in r.text
        assert "fetch(" not in r.text and "XMLHttpRequest" not in r.text


def test_api_still_gated(db_sessionmaker):
    with TestClient(app) as c:
        assert c.get("/api/jobs").status_code == 401


# Hand-built snapshot of backend/app/static/testbed_greenhouse.html's #app-form,
# in DOM order, as the extension's content.js __snapshot() would actually
# capture it: honeypot and the react-select shim inputs are invisible (not
# type=file) so they never make the list; both file slots are captured
# despite being visually hidden behind their "Attach" buttons (file inputs
# are exempt from the visibility check); every react-select-style control
# (role="combobox", or — for question_7005 — the "select__input" class hint
# alone) becomes type "combobox", never "select". No <select>, radio, or
# checkbox element exists anywhere on this page.
_GREENHOUSE_FIELDS = [
    {"index": 0, "type": "text", "id": "first_name", "name": "first_name", "label": "First Name *"},
    {"index": 1, "type": "text", "id": "last_name", "name": "last_name", "label": "Last Name *"},
    {"index": 2, "type": "email", "id": "email", "name": "email", "label": "Email *"},
    {"index": 3, "type": "combobox", "id": "phone_country", "name": "", "label": "Country"},
    {"index": 4, "type": "tel", "id": "phone", "name": "phone", "label": "Phone *"},
    {"index": 5, "type": "combobox", "id": "candidate-location", "name": "", "label": "Location (City) *"},
    {"index": 6, "type": "file", "id": "resume", "name": "resume", "label": "Resume/CV *"},
    {"index": 7, "type": "file", "id": "cover_letter", "name": "cover_letter", "label": "Cover Letter"},
    {"index": 8, "type": "combobox", "id": "school--0", "name": "", "label": "School *"},
    {"index": 9, "type": "combobox", "id": "degree--0", "name": "", "label": "Degree *"},
    {"index": 10, "type": "combobox", "id": "discipline--0", "name": "", "label": "Discipline *"},
    {"index": 11, "type": "text", "id": "question_7001", "name": "question_7001", "label": "LinkedIn Profile"},
    {"index": 12, "type": "text", "id": "question_7002", "name": "question_7002", "label": "Website / Portfolio / GitHub"},
    {"index": 13, "type": "combobox", "id": "question_7003", "name": "",
     "label": "Are you legally authorized to work in the United States? *"},
    {"index": 14, "type": "combobox", "id": "question_7004", "name": "",
     "label": "Will you now or in the future require sponsorship for employment visa status "
              "(e.g. H-1B visa status)? *"},
    {"index": 15, "type": "combobox", "id": "question_7005", "name": "", "label": "How did you hear about this job? *"},
    {"index": 16, "type": "textarea", "id": "question_7006", "name": "question_7006",
     "label": "What draws you to flight software specifically? *"},
    {"index": 17, "type": "text", "id": "question_7007", "name": "question_7007",
     "label": "What are your salary expectations?"},
    {"index": 18, "type": "combobox", "id": "gender", "name": "", "label": "Gender"},
    {"index": 19, "type": "combobox", "id": "hispanic_ethnicity", "name": "", "label": "Are you Hispanic/Latino?"},
    {"index": 20, "type": "combobox", "id": "race", "name": "", "label": "Race"},
    {"index": 21, "type": "combobox", "id": "veteran_status", "name": "", "label": "Veteran Status"},
    {"index": 22, "type": "combobox", "id": "disability_status", "name": "", "label": "Disability Status"},
]


def test_testbed_tailor_only_plan_is_attach_only(db_session):
    """A tailor_only session against the FULL Greenhouse-clone field snapshot
    must attach the résumé into the résumé-hinted slot (index 6) and touch
    nothing else — not the cover-letter slot (index 7, no résumé hint in its
    id/name/label), not the 15 profile/essay/combobox fields, no nav click."""
    s = _tailor_only_session(db_session)
    actions = fillplan.build_plan(db_session, s, _GREENHOUSE_FIELDS, buttons=[])
    kinds = {a["kind"] for a in actions}
    assert kinds == {"attach"}
    assert all(a["doc_kind"] == "resume" for a in actions)
    indexes = {a["index"] for a in actions}
    assert indexes == {6}
    assert 7 not in indexes  # cover-letter slot must never receive the résumé
