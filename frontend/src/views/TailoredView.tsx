import { useCallback, useEffect, useRef, useState } from "react";
import { api, pdfUrl } from "../api";
import type { DocFeedItem, JobDetail } from "../api";
import ConfirmButton from "../ConfirmButton";
import EmptyState from "../EmptyState";
import TexEditor from "../TexEditor";
import { safeHttpUrl } from "../url";
import AtsPanel from "./AtsPanel";

const STATUSES = [
  ["", "All"],
  ["draft", "Draft"],
  ["approved", "Approved"],
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

/** Every tailored resume lives in generated_docs; this view is the full
 * library over those rows: LaTeX editing, the linked job's JD/status/URL,
 * ATS scans, and a deep link into the Tracker. */
export default function TailoredView({ focusDocId }: { focusDocId?: number }) {
  const [docs, setDocs] = useState<DocFeedItem[] | null>(null);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [openId, setOpenId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revs, setRevs] = useState<Record<number, number>>({});
  const debounceRef = useRef<number | undefined>(undefined);
  const mountedQRef = useRef(false);
  const focusedRef = useRef(false);

  const refresh = useCallback(
    () =>
      api
        .listDocs({ doc_type: "resume", q: q || undefined, limit: 200 })
        .then(setDocs)
        .catch((e) => setError((e as Error).message)),
    [q],
  );

  useEffect(() => {
    window.clearTimeout(debounceRef.current);
    refresh();
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!mountedQRef.current) {
      mountedQRef.current = true;
      return;
    }
    debounceRef.current = window.setTimeout(refresh, 300);
    return () => window.clearTimeout(debounceRef.current);
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  // Deep link (#/tailored?doc=N): open + scroll once the list is loaded.
  useEffect(() => {
    if (!focusDocId || focusedRef.current || docs === null) return;
    focusedRef.current = true;
    setOpenId(focusDocId);
    window.setTimeout(
      () => document.getElementById(`tdoc-${focusDocId}`)?.scrollIntoView({ block: "center" }),
      50,
    );
  }, [focusDocId, docs]);

  const shown = docs?.filter((d) =>
    status === "draft" ? !d.approved : status === "approved" ? d.approved : true,
  );

  return (
    <section>
      <div className="view-head">
        <h2>Tailored résumés {shown != null && <span className="count">{shown.length}</span>}</h2>
      </div>
      {error && (
        <p className="error" onClick={() => setError(null)}>
          {error}
        </p>
      )}
      <div className="form-row">
        <input
          placeholder="Search company or role…"
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
      {shown != null && shown.length === 0 && (
        <EmptyState
          title="No tailored résumés"
          hint="Tailor a resume for a job posting in the Tailor tab — results collect here."
        />
      )}
      <ul className="cl-rows tailored-rows">
        {shown?.map((d) => (
          <li key={d.id} id={`tdoc-${d.id}`}>
            <div className="tailored-row-head">
              <span className="cl-title">
                {d.company} — {d.title}
              </span>
              <span className="meta">{new Date(d.created_at).toLocaleDateString()}</span>
              {d.approved ? (
                <span className="chip chip-ok">Approved</span>
              ) : (
                <span className="chip">Draft</span>
              )}
              <span className={`chip status-${d.job_status}`}>
                {JOB_STATUS_LABELS[d.job_status] ?? d.job_status}
              </span>
              {safeHttpUrl(d.job_url) && (
                <a href={d.job_url} target="_blank" rel="noreferrer">
                  posting ↗
                </a>
              )}
              <a
                href={`${pdfUrl.doc(d.id)}${revs[d.id] ? `?rev=${revs[d.id]}` : ""}`}
                target="_blank"
                rel="noreferrer"
              >
                PDF
              </a>
              <TexEditor
                doc={d}
                onSaved={() => {
                  setRevs((r) => ({ ...r, [d.id]: (r[d.id] ?? 0) + 1 }));
                  return refresh();
                }}
                onError={setError}
              />
              <button className="ghost" onClick={() => setOpenId(openId === d.id ? null : d.id)}>
                {openId === d.id ? "Hide details" : "Details"}
              </button>
              <ConfirmButton
                ariaLabel={`Delete tailored résumé for ${d.company}`}
                confirmText="Delete résumé?"
                onConfirm={() => api.deleteDoc(d.id).then(refresh)}
                onError={(e) => setError(e.message)}
              >
                ✕
              </ConfirmButton>
            </div>
            {openId === d.id && <DocDetailPane doc={d} onRetailored={refresh} />}
          </li>
        ))}
      </ul>
    </section>
  );
}

function DocDetailPane({ doc, onRetailored }: { doc: DocFeedItem; onRetailored?: () => void }) {
  const [job, setJob] = useState<JobDetail | null>(null);
  const [showJd, setShowJd] = useState(false);
  const [fit, setFit] = useState<Awaited<ReturnType<typeof api.getDocFit>> | null>(null);
  const [retailoring, setRetailoring] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    api.getJob(doc.job_id).then(setJob, () => {});
    api.getDocFit(doc.id).then(setFit, () => {});
  }, [doc.job_id, doc.id]);

  const retailor = async () => {
    setRetailoring(true);
    setNote(null);
    try {
      const result = await api.retailorDoc(doc.id);
      setNote(`New tailored version #${result.id} created — it's now in this list.`);
      onRetailored?.();
    } catch (e) {
      setNote((e as Error).message);
    } finally {
      setRetailoring(false);
    }
  };

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
        <button className="ghost" onClick={() => void retailor()} disabled={retailoring}>
          {retailoring ? "Re-tailoring…" : "Re-tailor with scan findings"}
        </button>
        {fit && (
          <span className={`chip ${fit.fits ? "chip-ok" : "chip-error"}`}>
            fit ~{fit.lines}/{fit.effective_budget} lines
          </span>
        )}
      </div>
      {note && <p className="meta">{note}</p>}
      {showJd && job?.jd_text && <div className="jd-view">{job.jd_text}</div>}
      <AtsPanel docId={doc.id} />
    </div>
  );
}
