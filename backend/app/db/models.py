from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import JSON, DateTime, ForeignKey, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobStatus(StrEnum):
    SAVED = "saved"
    APPLIED = "applied"
    OA = "oa"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class MemoryType(StrEnum):
    EXPERIENCE = "experience"
    SKILL = "skill"
    STORY = "story"
    PERSONAL = "personal"
    PREFERENCE = "preference"
    PROJECT = "project"
    COMPANY = "company"
    TRAIT = "trait"


class DocType(StrEnum):
    RESUME = "resume"
    COVER_LETTER = "cover_letter"


class Resume(Base):
    """A base resume template in the bank. Versions form a lineage via parent_id."""

    __tablename__ = "resumes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    job_type: Mapped[str] = mapped_column(String(100), index=True)
    tex_source: Mapped[str | None] = mapped_column(Text)  # None => PDF-only bank entry
    pdf_path: Mapped[str | None] = mapped_column(String(500))
    page_count: Mapped[int | None]
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("resumes.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    versions: Mapped[list["Resume"]] = relationship(remote_side=[id], uselist=True)


class MemoryEntry(Base):
    __tablename__ = "memory_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(20), index=True)
    title: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    source: Mapped[str | None] = mapped_column(String(300))
    muted: Mapped[bool] = mapped_column(default=False)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MemoryLink(Base):
    """Edges of the memory web."""

    __tablename__ = "memory_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    from_id: Mapped[int] = mapped_column(ForeignKey("memory_entries.id"), index=True)
    to_id: Mapped[int] = mapped_column(ForeignKey("memory_entries.id"), index=True)
    relation: Mapped[str | None] = mapped_column(String(100))


class VoiceSample(Base):
    """A piece of the user's real writing — ground truth for the style profile."""

    __tablename__ = "voice_samples"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(10))  # formal | informal
    source: Mapped[str | None] = mapped_column(String(300))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StyleProfile(Base):
    """The engine-built voice profile (single row). learned_rules is separate
    from content so edit-learning never clobbers manual edits."""

    __tablename__ = "style_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    content: Mapped[str] = mapped_column(Text)
    learned_rules: Mapped[list[dict]] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ApplySession(Base):
    """One one-click-apply run against a job page. The pipeline (services/apply.py)
    advances stage/progress; the extension polls this row."""

    __tablename__ = "apply_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|awaiting_user|error|done|stopped
    stage: Mapped[str] = mapped_column(String(20), default="parsing")
    progress: Mapped[float] = mapped_column(default=0.0)
    error: Mapped[str | None] = mapped_column(Text)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"))
    resume_doc_id: Mapped[int | None] = mapped_column(ForeignKey("generated_docs.id"))
    cover_doc_id: Mapped[int | None] = mapped_column(ForeignKey("generated_docs.id"))
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ImportSession(Base):
    """One PDF→LaTeX import run. services/resume_import.py advances
    stage/progress; the Import view polls this row. state carries
    extracted_text, tex, report, rounds, source_page_count, pdf_path,
    candidate_pdf_path."""

    __tablename__ = "import_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(300))
    name: Mapped[str] = mapped_column(String(200))
    job_type: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|review|done|error
    stage: Mapped[str] = mapped_column(String(20), default="extract")
    progress: Mapped[float] = mapped_column(default=0.0)
    error: Mapped[str | None] = mapped_column(Text)
    resume_id: Mapped[int | None] = mapped_column(ForeignKey("resumes.id"))
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    company: Mapped[str] = mapped_column(String(200), index=True)
    title: Mapped[str] = mapped_column(String(300))
    url: Mapped[str | None] = mapped_column(String(1000))
    jd_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.SAVED, index=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    docs: Mapped[list["GeneratedDoc"]] = relationship(back_populates="job")


class GeneratedDoc(Base):
    """A tailored resume or cover letter produced for a specific job."""

    __tablename__ = "generated_docs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    base_resume_id: Mapped[int | None] = mapped_column(ForeignKey("resumes.id"))
    doc_type: Mapped[str] = mapped_column(String(20))
    tex_source: Mapped[str] = mapped_column(Text)
    pdf_path: Mapped[str | None] = mapped_column(String(500))
    edits: Mapped[list[dict] | None] = mapped_column(JSON)
    divergence: Mapped[float | None] = mapped_column()
    draft_text: Mapped[str | None] = mapped_column(Text)  # AI's original CL body (diff baseline)
    body_text: Mapped[str | None] = mapped_column(Text)   # current, possibly user-edited body
    approved: Mapped[bool] = mapped_column(default=False)
    vetted: Mapped[bool] = mapped_column(default=False)  # user-reviewed (exemplar-eligible)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped[Job] = relationship(back_populates="docs")


class QABankEntry(Base):
    __tablename__ = "qa_bank"

    id: Mapped[int] = mapped_column(primary_key=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"))
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary)
    times_used: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProfileField(Base):
    """Key-value applicant profile powering extension autofill (name, email,
    school, work-auth answers, …). Values are the user's own words."""

    __tablename__ = "profile_fields"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class CompanyResearch(Base):
    __tablename__ = "company_research"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    findings: Mapped[str] = mapped_column(Text)
    sources: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NetworkTarget(Base):
    __tablename__ = "network_targets"

    id: Mapped[int] = mapped_column(primary_key=True)
    company: Mapped[str] = mapped_column(String(200), index=True)
    role_type: Mapped[str | None] = mapped_column(String(120))
    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual | application
    active: Mapped[bool] = mapped_column(default=True)
    discovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Person(Base):
    __tablename__ = "people"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    headline: Mapped[str | None] = mapped_column(String(300))
    company: Mapped[str] = mapped_column(String(200), index=True)
    location: Mapped[str | None] = mapped_column(String(200))
    person_type: Mapped[str] = mapped_column(String(20), default="other")
    profile_url: Mapped[str | None] = mapped_column(String(1000))
    evidence_urls: Mapped[list[str]] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(30), default="manual")  # web_search | linkedin_capture | manual
    match_signals: Mapped[list[dict]] = mapped_column(JSON, default=list)
    summary: Mapped[str | None] = mapped_column(Text)
    connection_note: Mapped[str | None] = mapped_column(Text)
    followup: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="found", index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AtsScan(Base):
    """One ATS scan run against a tailored doc or a bank resume. Exactly one
    of doc_id/resume_id is set (enforced at the API layer). kind: keyword |
    jd_match | deep | hiring_agent."""

    __tablename__ = "ats_scans"

    id: Mapped[int] = mapped_column(primary_key=True)
    doc_id: Mapped[int | None] = mapped_column(ForeignKey("generated_docs.id"), index=True)
    resume_id: Mapped[int | None] = mapped_column(ForeignKey("resumes.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(10), default="running")  # running|done|error|cancelled
    report: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PrepSession(Base):
    """One interview-prep artifact for a job. kind: interview (chat transcript
    + debrief in report) | oa (background research report). status: active
    (chat in progress) | running (oa thread) | done | error."""

    __tablename__ = "prep_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(10), default="active")
    transcript: Mapped[list[dict]] = mapped_column(JSON, default=list)
    report: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
