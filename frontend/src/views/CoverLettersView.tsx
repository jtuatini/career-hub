import { useCallback, useEffect, useRef, useState } from "react";
import { api, pdfUrl } from "../api";
import type { DocFeedItem, JobDetail } from "../api";
import ConfirmButton from "../ConfirmButton";
import EmptyState from "../EmptyState";
import LetterEditor from "../LetterEditor";
import { safeHttpUrl } from "../url";

const STATUSES = [
  ["", "All"],
  ["draft", "Draft"],
  ["unvetted", "AI draft — review"],
  ["vetted", "Vetted"],
] as const;

const JOB_STATUS_LABELS: Record<string, string> = {
  saved: "Saved",
  applied: "Applied",
  oa: "Online assessment",
  interview: "Interview",
  offer: "Offer",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
};

/** Full library over cover-letter generated_docs: vetting states, editing,
 * the linked job's JD/status/URL, and a deep link into the Tracker. */
export default function CoverLettersView({ focusDocId }: { focusDocId?: number }) {
  const [docs, setDocs] = useState<DocFeedItem[] | null>(null);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [openId, setOpenId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const debounceRef = useRef<number | undefined>(undefined);
  const mountedQRef = useRef(false);
  const focusedRef = useRef(false);

  const refresh = useCallback(
    () =>
      api
        .listDocs({ doc_type: "cover_letter", status: status || undefined, q: q || undefined, limit: 200 })
        .then(setDocs)
        .catch((e) => setError((e as Error).message)),
    [q, status],
  );

  useEffect(() => {
    window.clearTimeout(debounceRef.current);
    refresh();
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

  useEffect(() => {
    if (!mountedQRef.current) {
      mountedQRef.current = true;
      return;
    }
    debounceRef.current = window.setTimeout(refresh, 300);
    return () => window.clearTimeout(debounceRef.current);
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  // Deep link (#/letters?doc=N): open + scroll once the list is loaded.
  useEffect(() => {
    if (!focusDocId || focusedRef.current || docs === null) return;
    focusedRef.current = true;
    setOpenId(focusDocId);
    window.setTimeout(
      () => document.getElementById(`ldoc-${focusDocId}`)?.scrollIntoView({ block: "center" }),
      50,
    );
  }, [focusDocId, docs]);

  const chip = (d: DocFeedItem) =>
    !d.approved ? (
      <span className="chip">Draft</span>
    ) : !d.vetted ? (
      <span className="chip chip-draft">AI draft — review</span>
    ) : (
      <span className="chip chip-ok">Vetted</span>
    );

  return (
    <section>
      <div className="view-head">
        <h2>Cover letters {docs != null && <span className="count">{docs.length}</span>}</h2>
      </div>
      {error && (
        <p className="error" onClick={() => setError(null)}>
          {error}
        </p>
      )}
      <div className="form-row">
        <input
          placeholder="Search company, role, or letter text…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <div className="btn-row">
          {STATUSES.map(([value, label]) => (
            <button
              key={value}
              className={status === value ? "active-toggle" : ""}
              onClick={() => setStatus(value)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      {docs === null && <div className="skeleton" style={{ height: 120 }} />}
      {docs != null && docs.length === 0 && (
        <EmptyState
          title="No cover letters"
          hint="Generate one from a job in the Tailor tab — letters collect here."
        />
      )}
      <ul className="cl-rows tailored-rows">
        {docs?.map((d) => (
          <li key={d.id} id={`ldoc-${d.id}`}>
            <div className="tailored-row-head">
              <span className="cl-title">
                {d.company} — {d.title}
              </span>
              <span className="meta">{new Date(d.created_at).toLocaleDateString()}</span>
              {chip(d)}
              <span className={`chip status-${d.job_status}`}>
                {JOB_STATUS_LABELS[d.job_status] ?? d.job_status}
              </span>
              {safeHttpUrl(d.job_url) && (
                <a href={d.job_url} target="_blank" rel="noreferrer">
                  posting ↗
                </a>
              )}
              {d.approved && (
                <a href={pdfUrl.doc(d.id)} target="_blank" rel="noreferrer">
                  PDF
                </a>
              )}
              {d.approved && <LetterEditor doc={d} onSaved={refresh} onError={setError} />}
              <button className="ghost" onClick={() => setOpenId(openId === d.id ? null : d.id)}>
                {openId === d.id ? "Hide details" : "Details"}
              </button>
              <ConfirmButton
                ariaLabel={`Delete letter for ${d.company}`}
                confirmText="Delete letter?"
                onConfirm={() => api.deleteDoc(d.id).then(refresh)}
                onError={(e) => setError(e.message)}
              >
                ✕
              </ConfirmButton>
            </div>
            {openId === d.id && <LetterDetailPane doc={d} />}
          </li>
        ))}
      </ul>
    </section>
  );
}

function LetterDetailPane({ doc }: { doc: DocFeedItem }) {
  const [job, setJob] = useState<JobDetail | null>(null);
  const [showJd, setShowJd] = useState(false);

  useEffect(() => {
    api.getJob(doc.job_id).then(setJob, () => {});
  }, [doc.job_id]);

  return (
    <div className="tailored-detail">
      <div className="btn-row">
        <a className="ghost-link" href={`#/tracker?job=${doc.job_id}`}>
          View in Tracker →
        </a>
        {job?.jd_text && (
          <button className="ghost" onClick={() => setShowJd((s) => !s)}>
            {showJd ? "Hide job description" : "Job description"}
          </button>
        )}
      </div>
      {showJd && job?.jd_text && <div className="jd-view">{job.jd_text}</div>}
    </div>
  );
}
