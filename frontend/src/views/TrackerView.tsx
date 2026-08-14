import { useEffect, useRef, useState } from "react";
import { safeHttpUrl } from "../url";
import {
  api,
  pdfUrl,
  JOB_STATUSES,
  type Analytics,
  type DraftResult,
  type Job,
  type JobDetail,
  type Research,
} from "../api";
import ConfirmButton from "../ConfirmButton";
import LetterEditor from "../LetterEditor";
import EmptyState from "../EmptyState";

const STATUS_LABELS: Record<string, string> = {
  saved: "Saved",
  applied: "Applied",
  oa: "Online assessment",
  interview: "Interview",
  offer: "Offer",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
};

export default function TrackerView({ focusJobId }: { focusJobId?: number }) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<JobDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = () =>
    api.listJobs().then(setJobs).catch((e) => setError(e.message)).finally(() => setLoading(false));
  useEffect(() => {
    refresh();
  }, []);

  const focusedRef = useRef(false);
  useEffect(() => {
    if (!focusJobId || focusedRef.current || loading) return;
    const job = jobs.find((j) => j.id === focusJobId);
    if (!job) return;
    focusedRef.current = true;
    api.getJob(job.id).then(setExpanded);
    window.setTimeout(
      () => document.getElementById(`job-row-${job.id}`)?.scrollIntoView({ block: "center" }),
      50,
    );
  }, [focusJobId, loading, jobs]);

  async function setStatus(job: Job, status: string) {
    await api.patchJob(job.id, { status });
    refresh();
    if (expanded?.id === job.id) setExpanded(await api.getJob(job.id));
  }

  async function toggle(job: Job) {
    setExpanded(expanded?.id === job.id ? null : await api.getJob(job.id));
  }

  async function remove(job: Job) {
    try {
      await api.deleteJob(job.id);
      if (expanded?.id === job.id) setExpanded(null);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <section>
      <div className="view-head">
        <h2>Tracker</h2>
      </div>
      {error && <p className="error">{error}</p>}
      <InsightsPanel jobs={jobs} />
      {loading ? (
        <div className="skeleton" style={{ height: 160 }} />
      ) : jobs.length === 0 ? (
        <EmptyState
          title="No applications tracked"
          hint="Jobs land here automatically when you tailor a resume for a posting — head to the Tailor tab to get started."
        />
      ) : (
        <table className="tracker">
          <thead>
            <tr>
              <th>Company</th>
              <th>Role</th>
              <th>Status</th>
              <th>Applied</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <>
                <tr
                  key={job.id}
                  id={`job-row-${job.id}`}
                  onClick={() => toggle(job)}
                  className="job-row"
                >
                  <td>{job.company}</td>
                  <td>
                    {safeHttpUrl(job.url) ? (
                      <a href={job.url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>
                        {job.title}
                      </a>
                    ) : (
                      job.title
                    )}
                  </td>
                  <td onClick={(e) => e.stopPropagation()}>
                    <select
                      className={`status status-${job.status}`}
                      value={job.status}
                      onChange={(e) => setStatus(job, e.target.value)}
                    >
                      {JOB_STATUSES.map((s) => (
                        <option key={s} value={s}>
                          {STATUS_LABELS[s]}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>{job.applied_at ? new Date(job.applied_at).toLocaleDateString() : "—"}</td>
                  <td onClick={(e) => e.stopPropagation()}>
                    <ConfirmButton
                      ariaLabel="Delete job"
                      confirmText="Delete job?"
                      onConfirm={() => remove(job)}
                      onError={(e) => setError(e.message)}
                    >
                      ✕
                    </ConfirmButton>
                  </td>
                </tr>
                {expanded?.id === job.id && (
                  <tr className="job-detail" key={`${job.id}-detail`}>
                    <td colSpan={5}>
                      {expanded.docs.length > 0 ? (
                        <ul className="doc-list">
                          {expanded.docs.map((d) => (
                            <li key={d.id}>
                              {d.doc_type === "cover_letter" && !d.approved ? (
                                <span className="meta">Cover letter #{d.id} — Draft — finish in Tailor</span>
                              ) : (
                                <>
                                  <a href={pdfUrl.doc(d.id)} target="_blank" rel="noreferrer">
                                    {d.doc_type === "resume" ? "Tailored resume" : "Cover letter"} #{d.id}
                                  </a>
                                  {d.doc_type === "resume" && (
                                    <a className="meta" href={`#/tailored?doc=${d.id}`}>
                                      open in Tailored
                                    </a>
                                  )}
                                  {d.doc_type === "cover_letter" && d.approved && (
                                    <a className="meta" href={`#/letters?doc=${d.id}`}>
                                      open in Letters
                                    </a>
                                  )}
                                  {d.doc_type === "cover_letter" && d.approved && (
                                    <LetterEditor
                                      doc={d}
                                      onSaved={async () => {
                                        if (expanded) setExpanded(await api.getJob(expanded.id));
                                      }}
                                    />
                                  )}
                                </>
                              )}
                              {d.approved && <span className="chip chip-ok">approved</span>}
                              {d.doc_type === "cover_letter" && d.approved && !d.vetted && (
                                <span className="chip chip-draft">AI draft — review</span>
                              )}
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <p className="empty">No documents generated for this job yet.</p>
                      )}
                      <UrlEditor job={expanded} onSaved={refresh} />
                      <NotesEditor job={expanded} onSaved={refresh} />
                      <ResearchPanel job={expanded} />
                      <PrepPanel job={expanded} />
                      <QAPanel job={expanded} />
                    </td>
                  </tr>
                )}
              </>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function InsightsPanel({ jobs }: { jobs: Job[] }) {
  const [data, setData] = useState<Analytics | null>(null);
  const [showStats, setShowStats] = useState(false);

  useEffect(() => {
    api.getAnalytics().then(setData, () => {});
  }, [jobs]);

  if (!data) return null;
  const { deadlines, stale } = data.reminders;
  const funnelChips = ["applied", "oa", "interview", "offer", "rejected"]
    .filter((s) => data.funnel[s] > 0)
    .map((s) => `${data.funnel[s]} ${s}`);

  return (
    <div className="insights">
      {deadlines.map((j) => (
        <p className="warning" key={`d${j.id}`}>
          Deadline {new Date(j.deadline!).toLocaleDateString()}: {j.company} — {j.title} is
          still unsubmitted.
        </p>
      ))}
      {stale.map((j) => (
        <p className="warning" key={`s${j.id}`}>
          {j.company} — {j.title}: applied {new Date(j.applied_at!).toLocaleDateString()},
          no response yet. Worth a follow-up.
        </p>
      ))}
      {data.by_resume.length > 0 && (
        <p className="meta">
          {data.funnel.total} tracked{funnelChips.length > 0 && ` · ${funnelChips.join(" · ")}`}{" "}
          <button className="ghost" onClick={() => setShowStats((s) => !s)}>
            {showStats ? "hide resume stats" : "resume stats"}
          </button>
        </p>
      )}
      {showStats && (
        <table className="tracker" style={{ marginBottom: 18 }}>
          <thead>
            <tr>
              <th>Resume</th>
              <th>Applications</th>
              <th>Responses</th>
              <th>Interviews</th>
              <th>Offers</th>
              <th>Response rate</th>
            </tr>
          </thead>
          <tbody>
            {data.by_resume.map((r) => (
              <tr key={r.resume_id}>
                <td>
                  {r.name} <span className="meta-inline">{r.job_type}</span>
                </td>
                <td>{r.applications}</td>
                <td>{r.responses}</td>
                <td>{r.interviews}</td>
                <td>{r.offers}</td>
                <td>{r.response_rate === null ? "—" : `${Math.round(r.response_rate * 100)}%`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function ResearchPanel({ job }: { job: JobDetail }) {
  const [research, setResearch] = useState<Research | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    setResearch(null);
    setOpen(false);
    setError(null);
    api.getResearch(job.id).then(setResearch, () => {});
  }, [job.id]);

  const run = async (force: boolean) => {
    setBusy(true);
    setError(null);
    try {
      setResearch(await api.runResearch(job.id, force));
      setOpen(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="research-panel">
      <div className="btn-row">
        {research ? (
          <>
            <button className="ghost" onClick={() => setOpen((o) => !o)}>
              {open ? "Hide company research" : "Show company research"}
            </button>
            <button className="ghost" onClick={() => run(true)} disabled={busy}>
              {busy ? "Researching…" : "Refresh"}
            </button>
          </>
        ) : (
          <button className="ghost" onClick={() => run(false)} disabled={busy}>
            {busy ? "Researching…" : "Research company…"}
          </button>
        )}
      </div>
      {error && <p className="error">{error}</p>}
      {research && open && (
        <div className="research-body">
          <div className="memory-content">{research.findings}</div>
          {research.sources.length > 0 && (
            <p className="meta">
              Sources:{" "}
              {research.sources.filter(safeHttpUrl).map((s) => (
                <a key={s} href={s} target="_blank" rel="noreferrer" style={{ marginRight: 10 }}>
                  {new URL(s).hostname}
                </a>
              ))}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function PrepPanel({ job }: { job: JobDetail }) {
  const [prep, setPrep] = useState<Awaited<ReturnType<typeof api.interviewPrep>> | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setPrep(null);
    setError(null);
  }, [job.id]);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      setPrep(await api.interviewPrep(job.id));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="research-panel">
      <button className="ghost" onClick={run} disabled={busy}>
        {busy ? "Building prep pack…" : prep ? "Rebuild interview prep" : "Interview prep…"}
      </button>
      {error && <p className="error">{error}</p>}
      {prep && (
        <div className="research-body">
          {prep.questions.map((q, i) => (
            <div key={i} className="prep-q">
              <p className="memory-content">{q.question}</p>
              <p className="meta">{q.why_asked}</p>
              {q.story_titles.map((t) => (
                <span className="chip" key={t} style={{ marginRight: 6 }}>
                  {t}
                </span>
              ))}
              <ul className="meta">
                {q.talking_points.map((tp) => (
                  <li key={tp}>{tp}</li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function QAPanel({ job }: { job: JobDetail }) {
  const [open, setOpen] = useState(false);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<DraftResult | null>(null);
  const [answer, setAnswer] = useState("");
  const [saved, setSaved] = useState(false);

  const runDraft = async () => {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      const d = await api.draftAnswer({ question, job_id: job.id });
      setDraft(d);
      setAnswer(d.draft);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    await api.saveQA({ question, answer, job_id: job.id, draft: draft?.draft });
    setSaved(true);
  };

  if (!open)
    return (
      <p className="qa-toggle">
        <button className="ghost" onClick={() => setOpen(true)}>
          Draft a supplemental answer…
        </button>
      </p>
    );

  return (
    <div className="qa-panel">
      <label>
        Supplemental question
        <textarea
          rows={2}
          value={question}
          placeholder="e.g. Why do you want to intern at Acme?"
          onChange={(e) => {
            setQuestion(e.target.value);
            setDraft(null);
          }}
        />
      </label>
      <div className="btn-row">
        <button onClick={runDraft} disabled={busy || !question.trim()}>
          {busy ? "Drafting from your brain…" : "Draft answer"}
        </button>
        <button className="ghost" onClick={() => setOpen(false)}>
          Close
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {draft && (
        <>
          <label>
            Draft — edit until it sounds like you
            <textarea rows={6} value={answer} onChange={(e) => setAnswer(e.target.value)} />
          </label>
          <p className="meta">
            Built from:{" "}
            {draft.memories_used.map((m) => (
              <span className="chip" key={`m${m.id}`}>
                {m.title}
              </span>
            ))}
            {draft.past_answers_used.map((p) => (
              <span className="chip chip-version" key={`q${p.id}`}>
                past: {p.question.slice(0, 40)}
              </span>
            ))}
            {draft.memories_used.length === 0 && "no brain matches — answer is generic"}
          </p>
          <div className="btn-row">
            <button className="primary" onClick={save} disabled={saved || !answer.trim()}>
              {saved ? "Saved to answer bank" : "Approve & save to answer bank"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function UrlEditor({ job, onSaved }: { job: JobDetail; onSaved: () => void }) {
  const [url, setUrl] = useState(job.url ?? "");
  const [saved, setSaved] = useState(true);

  return (
    <div className="notes">
      <input
        value={url}
        placeholder="Job posting URL — https://…"
        onChange={(e) => {
          setUrl(e.target.value);
          setSaved(false);
        }}
      />
      {!saved && (
        <button
          onClick={async () => {
            await api.patchJob(job.id, { url: url || null });
            setSaved(true);
            onSaved();
          }}
        >
          Save link
        </button>
      )}
    </div>
  );
}

function NotesEditor({ job, onSaved }: { job: JobDetail; onSaved: () => void }) {
  const [notes, setNotes] = useState(job.notes ?? "");
  const [saved, setSaved] = useState(true);

  return (
    <div className="notes">
      <textarea
        rows={2}
        value={notes}
        placeholder="Notes — referrals, recruiter names, deadlines…"
        onChange={(e) => {
          setNotes(e.target.value);
          setSaved(false);
        }}
      />
      {!saved && (
        <button
          onClick={async () => {
            await api.patchJob(job.id, { notes });
            setSaved(true);
            onSaved();
          }}
        >
          Save notes
        </button>
      )}
    </div>
  );
}
