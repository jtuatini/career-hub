import { useEffect, useState } from "react";
import { api, pdfUrl } from "../api";
import type { Analytics, DocFeedItem, EngineStatus } from "../api";

const FUNNEL_ORDER = ["saved", "applied", "oa", "interview", "offer", "rejected"];

// Storage can be disabled (strict privacy modes); degrade to an undismissed
// checklist item instead of crashing the whole view.
function readExtDone(): boolean {
  try {
    return localStorage.getItem("setup_ext_done") === "1";
  } catch {
    return false;
  }
}

function writeExtDone(): void {
  try {
    localStorage.setItem("setup_ext_done", "1");
  } catch {
    /* non-persistent dismissal is fine */
  }
}

function SetupChecklist({ onNavigate }: { onNavigate: (tab: string) => void }) {
  const [engine, setEngine] = useState<EngineStatus | null>(null);
  const [profileOk, setProfileOk] = useState<boolean | null>(null);
  const [resumesOk, setResumesOk] = useState<boolean | null>(null);
  const [extDone, setExtDone] = useState(readExtDone);

  useEffect(() => {
    api.getEngineStatus().then(setEngine, () => setEngine(null));
    api.getProfile().then(
      (p) => setProfileOk(Boolean(p.full_name && p.email)),
      () => setProfileOk(false),
    );
    api.listResumes().then((r) => setResumesOk(r.length > 0), () => setResumesOk(false));
  }, []);

  const engineOk =
    !!engine &&
    (Object.values(engine.providers).some(Boolean) || engine.api_key_configured);

  const items: { ok: boolean; label: string; tab?: string; onDone?: () => void }[] = [
    { ok: engineOk, label: "Connect an AI engine (log in a CLI, or add an API key)" },
    { ok: profileOk === true, label: "Fill in your profile (name + email at minimum)", tab: "profile" },
    { ok: resumesOk === true, label: "Add a résumé — paste LaTeX or import a PDF", tab: "import" },
    {
      ok: extDone,
      label: "Load the Chrome extension and paste its token (Profile → Extension access)",
      tab: "profile",
      onDone: () => { writeExtDone(); setExtDone(true); },
    },
  ];
  if (profileOk === null || resumesOk === null) return null; // still loading
  if (items.every((i) => i.ok)) return null;

  return (
    <div className="panel">
      <div className="category-head">Get set up</div>
      <ul className="setup-list">
        {items.map((i) => (
          <li key={i.label}>
            <span>{i.ok ? "✓" : "○"} {i.label}</span>
            {!i.ok && i.tab && (
              <button onClick={() => onNavigate(i.tab!)}>Open</button>
            )}
            {!i.ok && i.onDone && <button onClick={i.onDone}>Done</button>}
          </li>
        ))}
      </ul>
    </div>
  );
}

const NAV_CARDS: { tab: string; label: string; blurb: string; count?: string }[] = [
  { tab: "tailored", label: "Tailored résumés", blurb: "Per-job résumés, ATS scans, LaTeX edits", count: "tailored" },
  { tab: "letters", label: "Cover letters", blurb: "Drafts, vetting, and finished letters", count: "letters" },
  { tab: "resumes", label: "Resume bank", blurb: "Base templates and lineages", count: "bank_resumes" },
  { tab: "tracker", label: "Tracker", blurb: "Every application and its status", count: "jobs" },
  { tab: "prep", label: "Interview prep", blurb: "Mock interviews and OA research" },
  { tab: "network", label: "Network", blurb: "People worth reaching at target companies", count: "network_people" },
  { tab: "memory", label: "Brain", blurb: "The memory web behind every answer" },
  { tab: "terminal", label: "Terminal", blurb: "Claude CLI over the resume bank" },
];

function ActionQueue({ queue }: { queue: Analytics["action_queue"] }) {
  const empty =
    queue.needs_resume.length === 0 && queue.prep_ready.length === 0 && queue.drafts.length === 0;
  if (empty) return <p className="empty">Nothing queued — the pipeline is moving.</p>;

  const rows = [
    ...queue.needs_resume.map((j) => (
      <li key={`r${j.job_id}`}>
        <span>
          {j.company} — {j.title} has no tailored résumé
        </span>
        <a className="ghost-link" href="#/tailor">
          Tailor →
        </a>
      </li>
    )),
    ...queue.prep_ready.map((j) => (
      <li key={`p${j.job_id}`}>
        <span>
          {j.company} is at the {j.status === "oa" ? "online assessment" : "interview"} stage
        </span>
        <a className="ghost-link" href={`#/prep?job=${j.job_id}`}>
          Prep →
        </a>
      </li>
    )),
    ...queue.drafts.map((d) => (
      <li key={`d${d.doc_id}`}>
        <span>
          Draft {d.doc_type === "resume" ? "résumé" : "letter"} for {d.company} awaits approval
        </span>
        <a
          className="ghost-link"
          href={`#/${d.doc_type === "resume" ? "tailored" : "letters"}?doc=${d.doc_id}`}
        >
          Review →
        </a>
      </li>
    )),
  ];

  return (
    <ul className="action-queue">
      {rows.slice(0, 6)}
      {rows.length > 6 && (
        <li className="meta">+{rows.length - 6} more queued</li>
      )}
    </ul>
  );
}

export default function HomeView({ onNavigate }: { onNavigate: (tab: string) => void }) {
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [docs, setDocs] = useState<DocFeedItem[] | null>(null);

  useEffect(() => {
    api.getAnalytics().then(setAnalytics, () => setAnalytics(null));
    api.listDocs({ limit: 6 }).then(setDocs, () => setDocs([]));
  }, []);

  if (!analytics) {
    return (
      <section>
        <div className="skeleton" style={{ height: 90 }} />
        <div className="skeleton" style={{ height: 180 }} />
      </section>
    );
  }

  const { deadlines, stale } = analytics.reminders;
  const total = analytics.funnel.total;

  return (
    <section>
      <SetupChecklist onNavigate={onNavigate} />
      <div className="view-head">
        <h2>
          Today
          <span className="meta-inline">
            {total === 0
              ? "no applications tracked yet — tailor one to get started"
              : `${total} application${total === 1 ? "" : "s"} tracked`}
          </span>
        </h2>
      </div>

      {deadlines.map((j) => (
        <p className="warning" key={`d${j.id}`}>
          <strong>Deadline {new Date(j.deadline!).toLocaleDateString()}</strong> — {j.company},{" "}
          {j.title} is still unsubmitted.
        </p>
      ))}
      {stale.map((j) => (
        <p className="warning" key={`s${j.id}`}>
          {j.company} — {j.title}: applied {new Date(j.applied_at!).toLocaleDateString()}, silence
          since. Worth a follow-up.
        </p>
      ))}
      {deadlines.length === 0 && stale.length === 0 && total > 0 && (
        <p className="meta">Nothing urgent — no upcoming deadlines, nothing gone stale.</p>
      )}

      {total > 0 && (
        <div className="funnel-row">
          {FUNNEL_ORDER.filter((s) => analytics.funnel[s] > 0).map((s) => (
            <a className="funnel-stat stat-link" href="#/tracker" key={s}>
              <div className="funnel-count">{analytics.funnel[s]}</div>
              <div className="funnel-label">{s}</div>
            </a>
          ))}
        </div>
      )}

      <div className="home-grid">
        <div className="panel">
          <div className="category-head">Action queue</div>
          <ActionQueue queue={analytics.action_queue} />
        </div>

        <div className="panel">
          <div className="category-head">Recent documents</div>
          {docs === null && <div className="skeleton" style={{ height: 80 }} />}
          {docs?.length === 0 && (
            <p className="empty">Tailored resumes and cover letters will appear here.</p>
          )}
          <ul className="doc-feed">
            {docs?.map((d) => (
              <li key={d.id}>
                <a href={pdfUrl.doc(d.id)} target="_blank" rel="noreferrer">
                  {d.doc_type === "resume" ? "Resume" : "Cover letter"} — {d.company}, {d.title}
                </a>
                {d.approved && <span className="chip chip-ok">approved</span>}
                <span className="meta-inline">
                  {new Date(d.created_at).toLocaleDateString()}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div className="nav-cards">
        {NAV_CARDS.map((c) => (
          <a className="nav-card" href={`#/${c.tab}`} key={c.tab}>
            <div className="nav-card-head">
              {c.label}
              {analytics.counts[c.count ?? ""] != null && (
                <span className="count">{analytics.counts[c.count!]}</span>
              )}
            </div>
            <p className="meta">{c.blurb}</p>
          </a>
        ))}
      </div>
    </section>
  );
}
