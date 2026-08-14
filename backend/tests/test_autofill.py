import pytest

from app.services import autofill as autofill_service


@pytest.fixture
def profile(client):
    client.put(
        "/api/profile",
        json={
            "values": {
                "full_name": "Alex Sample",
                "email": "alex@example.com",
                "phone": "555-0100",
                "school": "State University",
                "work_auth": "Yes",
                "sponsorship": "No",
            }
        },
    )
    return client


def test_profile_upsert_and_delete(client):
    put = client.put("/api/profile", json={"values": {"email": "a@b.c", "gpa": "3.8"}})
    assert put.json() == {"email": "a@b.c", "gpa": "3.8"}
    updated = client.put("/api/profile", json={"values": {"gpa": "", "email": "new@b.c"}})
    assert updated.json() == {"email": "new@b.c"}


def test_heuristic_mapping_with_name_split(profile):
    fields = [
        {"label": "First Name", "name": "fname"},
        {"label": "Last Name", "name": "lname"},
        {"label": "Email Address", "name": "candidate_email"},
        {"label": "Mobile phone"},
        {"label": "Favorite color"},  # unmatched
    ]
    resp = profile.post("/api/profile/map", json={"fields": fields})
    mappings = {m["index"]: m for m in resp.json()["mappings"]}
    assert mappings[0]["value"] == "Alex"
    assert mappings[1]["value"] == "Sample"
    assert mappings[2]["value"] == "alex@example.com"
    assert mappings[3]["value"] == "555-0100"
    assert all(m["confidence"] == "high" for m in mappings.values())
    assert 4 not in mappings


def test_specific_key_beats_generic(profile):
    # "First name" must map to first_name, not full_name's "name" synonym.
    resp = profile.post(
        "/api/profile/map",
        json={"fields": [{"name": "first name"}, {"label": "Full Name"}]},
    )
    mappings = {m["index"]: m for m in resp.json()["mappings"]}
    assert mappings[0]["profile_key"] == "first_name"
    assert mappings[1]["profile_key"] == "full_name"
    assert mappings[1]["value"] == "Alex Sample"


def test_select_option_matching(profile):
    fields = [
        {
            "label": "Are you authorized to work in the US?",
            "options": ["Yes", "No", "Decline to answer"],
        },
        {
            "label": "Will you require sponsorship?",
            "options": ["No, I will not", "Yes, I will"],
        },
        {"label": "Work authorization", "options": ["Maybe", "Unsure"]},  # no option fits
    ]
    resp = profile.post("/api/profile/map", json={"fields": fields})
    mappings = {m["index"]: m for m in resp.json()["mappings"]}
    assert mappings[0] == {
        "index": 0, "profile_key": "work_auth", "value": "Yes", "confidence": "high",
    }
    # Partial option match fills but demands review.
    assert mappings[1]["value"] == "No, I will not"
    assert mappings[1]["confidence"] == "review"
    assert 2 not in mappings


def test_select_fuzzy_picks_next_best_not_first_substring(profile):
    """Two live bugs in one: (1) skip-on-no-exact-match — the closest option must
    be chosen (flagged review) instead of skipping; (2) the old substring rule
    matched 'no' inside 'NOw or in the future' and picked the YES option because
    it came first. Scored word-boundary matching must land on the real No."""
    fields = [{
        "label": "Will you now or in the future require sponsorship?",
        "options": [
            "Yes, I will require sponsorship now or in the future",
            "No, I will not require sponsorship",
        ],
    }]
    resp = profile.post("/api/profile/map", json={"fields": fields})
    [m] = resp.json()["mappings"]
    assert m["value"] == "No, I will not require sponsorship"
    assert m["confidence"] == "review"


def test_select_fuzzy_word_overlap_chosen_as_review(profile):
    """No exact or substring relation at all ('State University' vs 'University
    of State') — token overlap must still surface the next-best option instead
    of skipping the field."""
    fields = [{
        "label": "School",
        "options": ["University of State", "Tech Institute"],
    }]
    resp = profile.post("/api/profile/map", json={"fields": fields})
    [m] = resp.json()["mappings"]
    assert m["value"] == "University of State"
    assert m["confidence"] == "review"


def test_select_case_mismatch_is_exact_high(profile):
    """Testbed audit regression: 'Company Website' vs option 'Company website'
    must be treated as an exact (high-confidence) match, not fuzzy or skipped."""
    profile.put("/api/profile", json={"values": {"hear_about": "Company Website"}})
    fields = [{
        "label": "How did you hear about us?",
        "options": ["Company website", "Job board", "Referral"],
    }]
    resp = profile.post("/api/profile/map", json={"fields": fields})
    [m] = resp.json()["mappings"]
    assert m["value"] == "Company website"
    assert m["confidence"] == "high"


def test_select_digit_mismatch_never_snaps(profile):
    """'May 2028' scores dangerously close to 'May 2027' on character
    similarity — values whose numbers differ must never fuzzy-snap (a silently
    wrong year is worse than an empty field)."""
    profile.put("/api/profile", json={"values": {"grad_date": "May 2028"}})
    fields = [{
        "label": "Expected graduation date",
        "options": ["May 2027", "May 2029"],
    }]
    resp = profile.post("/api/profile/map", json={"fields": fields})
    assert resp.json()["mappings"] == []


def test_state_abbreviation_expands_for_selects(profile):
    """US applications overwhelmingly store 'CO' in the profile while the form's
    select lists full state names — expand the abbreviation when the field has
    options so the exact match lands."""
    profile.put("/api/profile", json={"values": {"state": "CO"}})
    fields = [
        {"label": "State", "options": ["California", "Colorado", "Connecticut"]},
        {"label": "State / Province"},  # plain text keeps the user's own form
    ]
    resp = profile.post("/api/profile/map", json={"fields": fields})
    mappings = {m["index"]: m for m in resp.json()["mappings"]}
    assert mappings[0]["value"] == "Colorado"
    assert mappings[0]["confidence"] == "high"
    assert mappings[1]["value"] == "CO"


def test_country_alias_expands_for_selects(profile):
    profile.put("/api/profile", json={"values": {"country": "USA"}})
    fields = [{
        "label": "Country",
        "options": ["Canada", "United States of America", "Mexico"],
    }]
    resp = profile.post("/api/profile/map", json={"fields": fields})
    [m] = resp.json()["mappings"]
    assert m["value"] == "United States of America"


def test_ai_select_values_snapped_to_options(profile, monkeypatch):
    """The AI fallback is asked for verbatim option values but nothing forces
    that — near-matches must be snapped to the closest real option and
    implausible values dropped, mirroring the heuristic path."""
    def fake_generate_json(system, user_content, schema, **kw):
        return {"mappings": [
            {"index": 0, "profile_key": "school", "value": "State University"},
            {"index": 1, "profile_key": "degree", "value": "Doctorate"},
        ]}

    monkeypatch.setattr(autofill_service, "generate_json", fake_generate_json)
    fields = [
        {"label": "Which campus?", "options": ["State University - Main Campus", "Other"]},
        {"label": "Level attained", "options": ["High school", "Some college"]},
    ]
    resp = profile.post("/api/profile/map", json={"fields": fields, "use_ai": True})
    mappings = resp.json()["mappings"]
    assert [m["index"] for m in mappings] == [0]
    assert mappings[0]["value"] == "State University - Main Campus"
    assert mappings[0]["confidence"] == "review"


def test_automation_id_and_camel_case_names_match(profile):
    """Workday fields often carry their meaning only in data-automation-id or
    camelCase names (legalNameSection_firstName) — those must reach the synonym
    matcher as separate words."""
    fields = [
        {"label": "", "name": "", "automation_id": "legalNameSection_firstName"},
        {"label": "", "name": "candidateEmailAddress"},
    ]
    resp = profile.post("/api/profile/map", json={"fields": fields})
    mappings = {m["index"]: m for m in resp.json()["mappings"]}
    assert mappings[0]["value"] == "Alex"
    assert mappings[1]["value"] == "alex@example.com"


def test_ai_fallback_only_when_opted_in(profile, monkeypatch):
    calls = {"n": 0}

    def fake_generate_json(system, user_content, schema, **kw):
        calls["n"] += 1
        assert "Favorite framework" in user_content
        return {"mappings": [
            {"index": 0, "profile_key": "school", "value": "State University"},
            {"index": 99, "profile_key": "email", "value": "x"},  # invalid index dropped
        ]}

    monkeypatch.setattr(autofill_service, "generate_json", fake_generate_json)
    fields = [{"label": "Favorite framework"}]

    without = profile.post("/api/profile/map", json={"fields": fields}).json()
    assert without["mappings"] == [] and calls["n"] == 0

    with_ai = profile.post("/api/profile/map", json={"fields": fields, "use_ai": True}).json()
    assert calls["n"] == 1
    assert with_ai["mappings"] == [
        {"index": 0, "profile_key": "school", "value": "State University", "confidence": "review"}
    ]


def test_ai_mapping_indexes_stay_positional(profile, monkeypatch, db_session):
    """map_fields' contract is POSITIONAL indexes over the list it was given.
    Snapshot field dicts carry their own 'index' key (the page-wide snapshot
    index); the unmatched payload must not let that overwrite the positional
    index, or AI mappings land on the wrong element whenever the caller
    filtered fields (fillplan drops file inputs before mapping)."""
    sent = {}

    def fake_generate_json(system, user_content, schema, **kw):
        sent["payload"] = user_content
        # Echo back the positional index 0 the payload should be using.
        return {"mappings": [{"index": 0, "profile_key": "school", "value": "State University"}]}

    monkeypatch.setattr(autofill_service, "generate_json", fake_generate_json)
    # Position 0 carries snapshot index 7 (files sat before it on the page).
    fields = [{"index": 7, "label": "Favorite framework"}]
    mappings = autofill_service.map_fields(db_session, fields, use_ai=True)
    assert mappings == [
        {"index": 0, "profile_key": "school", "value": "State University", "confidence": "review"}
    ]
    assert '"index": 0' in sent["payload"]


def test_label_tier_beats_misleading_id(profile):
    """Live-fire finding (Greenhouse trial): the dial-country combobox has
    id="phone_country" — 'phone' matched the id and outranked 'country', so
    the PHONE NUMBER was typed into a country selector. The visible label is
    what the human answers: label-tier matches must outrank name/id matches."""
    profile.put("/api/profile", json={"values": {"country": "United States"}})
    fields = [{"label": "Country", "id": "phone_country", "type": "combobox"}]
    resp = profile.post("/api/profile/map", json={"fields": fields})
    [m] = resp.json()["mappings"]
    assert m["profile_key"] == "country"
    assert m["value"] == "United States"


def test_degree_shorthand_expands_for_selects(profile):
    """Profiles store resume phrasing ('B.S.E'); platform degree lists are
    standardized ('Bachelor's Degree' / 'Bachelor's (BA/BS)'). Expand the
    shorthand so the fuzzy matcher can land on the right entry."""
    profile.put("/api/profile", json={"values": {"degree": "B.S.E"}})
    fields = [{
        "label": "Degree",
        "options": ["High School", "Associate's Degree", "Bachelor's Degree", "Master's Degree", "Other"],
    }]
    resp = profile.post("/api/profile/map", json={"fields": fields})
    [m] = resp.json()["mappings"]
    assert m["value"] == "Bachelor's Degree"
    assert m["confidence"] == "review"


def test_eeo_self_id_fills_only_from_profile_and_flags_review(profile):
    """Stored self-identification answers fill (that's the point of storing
    them) but are never shown as confident green — and with nothing stored,
    EEO fields are left alone entirely."""
    fields = [{
        "label": "Veteran Status",
        "options": [
            "I am not a protected veteran",
            "I identify as one or more of the classifications of a protected veteran",
            "I don't wish to answer",
        ],
    }]
    empty = profile.post("/api/profile/map", json={"fields": fields})
    assert empty.json()["mappings"] == []

    profile.put("/api/profile", json={"values": {"veteran_status": "I am not a protected veteran"}})
    resp = profile.post("/api/profile/map", json={"fields": fields})
    [m] = resp.json()["mappings"]
    assert m["value"] == "I am not a protected veteran"
    assert m["confidence"] == "review"


def test_map_requires_fields(client):
    assert client.post("/api/profile/map", json={"fields": []}).status_code == 422


def test_word_boundary_prevents_united_states_shadowing(profile):
    # "United States" must not match the 'state' synonym; and a matched-but-empty
    # profile key must not shadow a later key that has a value.
    fields = [{
        "label": "Are you legally authorized to work in the United States?",
        "options": ["Yes", "No"],
    }]
    resp = profile.post("/api/profile/map", json={"fields": fields})
    [m] = resp.json()["mappings"]
    assert m["profile_key"] == "work_auth"
    assert m["value"] == "Yes"
