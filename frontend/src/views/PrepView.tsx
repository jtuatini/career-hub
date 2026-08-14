import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Job, PrepSession } from "../api";
import ConfirmButton from "../ConfirmButton";
import EmptyState from "../EmptyState";

const PREP_FIRST = ["oa", "interview"];

/** Interview prep for one job: a live mock-interview chat (engine-turn based)
 * and an OA researcher (background thread, polled like ATS scans). */
export default function PrepView({ focusJobId }: { focusJobId?: number }) {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [jobId, setJobId] = useState<number | null>(focusJobId ?? null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listJobs()
      .then((js) => {
        const sorted = [...js].sort(
          (a, b) => Number(PREP_FIRST.includes(b.status)) - Number(PREP_FIRST.includes(a.status)),
        );
        setJobs(sorted);
        setJobId((cur) => cur ?? sorted[0]?.id ?? null);
      })
      .catch((e) => setError((e as Error).message));
  }, []);

  const job = jobs?.find((j) => j.id === jobId) ?? null;

  return (
    <section>
      <div className="view-head">
        <h2>Interview prep</h2>
      </div>
      {error && (
        <p className="error" onClick={() => setError(null)}>
          {error}
        </p>
      )}
      {jobs != null && jobs.length === 0 && (
        <EmptyState title="No jobs yet" hint="Track a job first — prep is always per-role." />
      )}
      {jobs != null && jobs.length > 0 && (
        <div className="form-row">
          <select value={jobId ?? ""} onChange={(e) => setJobId(Number(e.target.value))}>
            {jobs.map((j) => (
              <option key={j.id} value={j.id}>
                {j.company} — {j.title} ({j.status})
              </option>
            ))}
          </select>
        </div>
      )}
      {job && <JobPrep key={job.id} job={job} onError={setError} />}
    </section>
  );
}

function JobPrep({ job, onError }: { job: Job; onError: (m: string) => void }) {
  const [sessions, setSessions] = useState<PrepSession[] | null>(null);
  const [open, setOpen] = useState<PrepSession | null>(null);

  const refresh = useCallback(
    () => api.prep.list(job.id).then(setSessions).catch((e) => onError((e as Error).message)),
    [job.id, onError],
  );

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Poll while any OA research is running.
  const running = sessions?.some((s) => s.kind === "oa" && s.status === "running") ?? false;
  useEffect(() => {
    if (!running) return;
    const t = window.setInterval(refresh, 2500);
    return () => window.clearInterval(t);
  }, [running, refresh]);

  const start = (kind: "interview" | "oa") =>
    api.prep
      .start(job.id, kind)
      .then((s) => {
        if (kind === "interview") setOpen(s);
        return refresh();
      })
      .catch((e) => onError((e as Error).message));

  return (
    <div className="home-grid">
      <div className="panel">
        <div className="category-head">Mock interview</div>
        <div className="btn-row">
          <button onClick={() => start("interview")} disabled={open?.status === "active"}>
            Start mock interview
          </button>
        </div>
        {open ? (
          <InterviewChat
            session={open}
            onUpdate={(s) => {
              setOpen(s);
              if (s.status === "done") refresh();
            }}
            onClose={() => setOpen(null)}
            onError={onError}
          />
        ) : (
          <SessionList
            sessions={(sessions ?? []).filter((s) => s.kind === "interview")}
            onOpen={setOpen}
            onDeleted={refresh}
            onError={onError}
          />
        )}
      </div>

      <div className="panel">
        <div className="category-head">Online assessment</div>
        <div className="btn-row">
          <button onClick={() => start("oa")} disabled={running}>
            {running ? "Researching…" : "Research OA questions"}
          </button>
        </div>
        {(sessions ?? [])
          .filter((s) => s.kind === "oa")
          .map((s) => (
            <OaReport key={s.id} session={s} onDeleted={refresh} onError={onError} />
          ))}
      </div>
    </div>
  );
}

function SessionList({
  sessions,
  onOpen,
  onDeleted,
  onError,
}: {
  sessions: PrepSession[];
  onOpen: (s: PrepSession) => void;
  onDeleted: () => void;
  onError: (m: string) => void;
}) {
  if (sessions.length === 0)
    return <p className="empty">No sessions yet — start one and answer out loud.</p>;
  return (
    <ul className="action-queue">
      {sessions.map((s) => (
        <li key={s.id}>
          <span className="meta">
            {new Date(s.created_at).toLocaleString()} · {s.transcript.length} turns ·{" "}
            {s.status === "done" ? "debriefed" : s.status}
          </span>
          <span className="btn-row">
            <button className="ghost" onClick={() => onOpen(s)}>
              {s.status === "active" ? "Resume" : "Review"}
            </button>
            <ConfirmButton
              ariaLabel="Delete prep session"
              confirmText="Delete session?"
              onConfirm={() => api.prep.remove(s.id).then(onDeleted)}
              onError={(e) => onError(e.message)}
            >
              ✕
            </ConfirmButton>
          </span>
        </li>
      ))}
    </ul>
  );
}

function InterviewChat({
  session,
  onUpdate,
  onClose,
  onError,
}: {
  session: PrepSession;
  onUpdate: (s: PrepSession) => void;
  onClose: () => void;
  onError: (m: string) => void;
}) {
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "nearest" });
  }, [session.transcript.length, busy]);

  const send = async () => {
    if (!answer.trim() || busy) return;
    setBusy(true);
    try {
      const s = await api.prep.turn(session.id, answer.trim());
      setAnswer("");
      onUpdate(s);
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const finish = async () => {
    setBusy(true);
    try {
      onUpdate(await api.prep.finish(session.id));
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="prep-chat">
        {session.transcript.map((t, i) => (
          <div key={i} className={`chat-bubble chat-${t.role}`}>
            {t.text}
          </div>
        ))}
        {busy && <div className="chat-bubble chat-interviewer meta">thinking…</div>}
        <div ref={endRef} />
      </div>
      {session.status === "done" && session.report != null && <Debrief report={session.report} />}
      {session.status !== "active" && (
        <div className="btn-row">
          <button className="ghost" onClick={onClose}>
            Close
          </button>
        </div>
      )}
      {session.status === "active" && (
        <>
          <textarea
            rows={3}
            placeholder="Answer as you would out loud… (Enter to send, Shift+Enter for a new line)"
            value={answer}
            disabled={busy}
            onChange={(e) => setAnswer(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send();
              }
            }}
          />
          <div className="btn-row">
            <button onClick={() => void send()} disabled={busy || !answer.trim()}>
              Send answer
            </button>
            <button className="ghost" onClick={() => void finish()} disabled={busy}>
              End interview → debrief
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function Debrief({ report }: { report: Record<string, unknown> }) {
  const r = report as {
    strengths?: string[];
    gaps?: string[];
    suggested_answers?: { question: string; points: string[] }[];
  };
  return (
    <div className="tailored-detail">
      <div className="category-head">Debrief</div>
      {(r.strengths ?? []).length > 0 && (
        <p className="meta">Strengths: {r.strengths!.join("; ")}</p>
      )}
      {(r.gaps ?? []).length > 0 && <p className="meta">Gaps: {r.gaps!.join("; ")}</p>}
      {(r.suggested_answers ?? []).map((s) => (
        <div key={s.question}>
          <p>
            <strong>{s.question}</strong>
          </p>
          <ul className="meta">
            {s.points.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

function OaReport({
  session,
  onDeleted,
  onError,
}: {
  session: PrepSession;
  onDeleted: () => void;
  onError: (m: string) => void;
}) {
  const r = (session.report ?? {}) as {
    summary?: string;
    topics?: string[];
    sample_questions?: { question: string; source: string }[];
    links?: string[];
  };
  return (
    <div className="ats-report">
      <p className="meta">
        {new Date(session.created_at).toLocaleString()}
        {session.status === "running" && <span className="chip">running…</span>}
        {session.status === "error" && <span className="chip chip-error">failed</span>}
        <ConfirmButton
          ariaLabel="Delete OA report"
          confirmText="Delete report?"
          onConfirm={() => api.prep.remove(session.id).then(onDeleted)}
          onError={(e) => onError(e.message)}
        >
          ✕
        </ConfirmButton>
      </p>
      {session.status === "error" && <p className="error">{session.error}</p>}
      {session.status === "done" && (
        <div className="ats-body">
          {r.summary && <p>{r.summary}</p>}
          {(r.topics ?? []).length > 0 && (
            <p className="meta">
              Drill:{" "}
              {r.topics!.map((t) => (
                <span className="chip" key={t}>
                  {t}
                </span>
              ))}
            </p>
          )}
          {(r.sample_questions ?? []).map((q) => (
            <p className="meta" key={q.question}>
              {q.question}{" "}
              <a href={q.source} target="_blank" rel="noreferrer">
                source ↗
              </a>
            </p>
          ))}
          {(r.links ?? []).length > 0 && (
            <ul className="meta">
              {r.links!.map((l) => (
                <li key={l}>
                  <a href={l} target="_blank" rel="noreferrer">
                    {l}
                  </a>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
