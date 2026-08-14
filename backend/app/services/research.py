"""Company research via web-grounded generation (subscription CLI first, the
metered API's server-side web_search as fallback — see engine.generate_search),
cached per job in company_research. Only the company name + role title leave
the machine — never personal data."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import CompanyResearch, Job
from app.services import engine
from app.services.claude import ClaudeError

SYSTEM = """\
You research companies for an internship applicant. Produce a tight brief:
1. What the company/team actually does (products, scale, stack if findable).
2. Recent news worth mentioning in an interview (funding, launches, incidents).
3. Internship program specifics: timeline, conversion reputation, comp if public.
4. Two or three sharp "why us" angles grounded in what you found.
Use web search. Markdown headings, under 400 words, no fluff.\
"""


def research_company(db: Session, job: Job, force: bool = False) -> CompanyResearch:
    cached = db.scalar(
        select(CompanyResearch)
        .where(CompanyResearch.job_id == job.id)
        .order_by(CompanyResearch.created_at.desc())
    )
    if cached is not None and not force:
        return cached

    findings, sources = engine.generate_search(
        SYSTEM, f"Research {job.company} for the role: {job.title}.", max_uses=6
    )
    if not findings.strip():
        raise ClaudeError("Research returned no text — try again.")
    research = CompanyResearch(job_id=job.id, findings=findings, sources=sources)
    db.add(research)
    db.commit()
    return research
