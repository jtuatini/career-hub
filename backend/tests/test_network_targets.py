"""Network targets: models + derivation from applied jobs."""

from app.db.models import Job, NetworkTarget, Person
from app.services import network


def _job(db, company, status):
    j = Job(company=company, title="SWE Intern", jd_text="x", status=status)
    db.add(j)
    db.commit()
    return j


def test_sync_targets_derives_from_applied_jobs(db_session):
    _job(db_session, "Acme", "applied")
    _job(db_session, "Globex", "saved")  # not applied -> no target
    network.sync_targets(db_session)
    targets = db_session.query(NetworkTarget).all()
    assert [(t.company, t.source, t.active) for t in targets] == [("Acme", "application", True)]


def test_sync_targets_is_idempotent_and_deactivates_retracted(db_session):
    j = _job(db_session, "Acme", "applied")
    network.sync_targets(db_session)
    network.sync_targets(db_session)
    assert db_session.query(NetworkTarget).count() == 1

    j.status = "saved"
    db_session.commit()
    network.sync_targets(db_session)
    t = db_session.query(NetworkTarget).one()
    assert t.active is False

    j.status = "interview"  # interview/offer still count as applied-family
    db_session.commit()
    network.sync_targets(db_session)
    assert db_session.query(NetworkTarget).one().active is True


def test_sync_preserves_manual_targets(db_session):
    db_session.add(NetworkTarget(company="SpaceY", role_type="GNC", source="manual"))
    db_session.commit()
    network.sync_targets(db_session)
    t = db_session.query(NetworkTarget).filter_by(source="manual").one()
    assert t.active is True and t.role_type == "GNC"


def test_sync_skips_derived_target_for_company_with_active_manual_target(db_session):
    """Finding 5: a company with an active manual target already covers
    discovery for that company — sync must not also create/keep-active a
    derived target for it (two chips, doubled discovery spend)."""
    db_session.add(NetworkTarget(company="SpaceY", role_type="GNC", source="manual", active=True))
    _job(db_session, "SpaceY", "applied")
    network.sync_targets(db_session)

    active_derived = (
        db_session.query(NetworkTarget)
        .filter_by(source="application", company="SpaceY", active=True)
        .all()
    )
    assert active_derived == []

    manual = db_session.query(NetworkTarget).filter_by(source="manual").one()
    manual.active = False
    db_session.commit()
    network.sync_targets(db_session)

    derived = db_session.query(NetworkTarget).filter_by(source="application", company="SpaceY").one()
    assert derived.active is True


def test_person_model_defaults(db_session):
    p = Person(name="Sarah Chen", company="Acme", source="manual")
    db_session.add(p)
    db_session.commit()
    assert p.status == "found" and p.person_type == "other"
    assert p.evidence_urls == [] and p.match_signals == []
