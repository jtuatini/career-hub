import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { ImportSessionStatus } from "../api";
import { useToast } from "../Toast";

const STAGE_LABELS: Record<string, string> = {
  extract: "Reading the PDF",
  convert: "Converting to LaTeX",
  compile: "Compiling",
  verify: "Verifying fidelity, fit, and alignment",
  review: "Ready for review",
};

const CHECKS: [keyof NonNullable<ImportSessionStatus["report"]>, string][] = [
  ["fidelity", "Fidelity — nothing dropped, invented, or altered"],
  ["fit", "Fit — same page count, nothing overflowing"],
  ["alignment", "Alignment — page renders cleanly"],
];

// Vetted PDF→LaTeX import: upload → live stage progress → verification
// checklist → preview → save to bank. Nothing is saved until Accept.
export default function ImportView() {
  const toast = useToast();
  const [name, setName] = useState("");
  const [jobType, setJobType] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [session, setSession] = useState<ImportSessionStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const timer = useRef<number | null>(null);

  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current); }, []);

  const poll = (id: number) => {
    api.getImportSession(id).then((s) => {
      setSession(s);
      if (s.status === "running") timer.current = window.setTimeout(() => poll(id), 1500);
    }, (e) => setError((e as Error).message));
  };

  const start = async () => {
    if (!file || !name.trim() || !jobType.trim()) return;
    setError(null);
    setBusy(true);
    try {
      const { id } = await api.importPdf(name.trim(), jobType.trim(), file);
      poll(id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const accept = async () => {
    if (!session) return;
    try {
      await api.acceptImport(session.id);
      toast("Saved to the resume bank");
      poll(session.id);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const report = session?.report;
  const clean = !!report && CHECKS.every(([k]) => report[k].length === 0);

  return (
    <section>
      <div className="view-head">
        <h2>
          Import PDF
          <span className="meta-inline">
            convert an existing PDF résumé into a tailorable LaTeX résumé
          </span>
        </h2>
      </div>
      {error && <p className="error">{error}</p>}

      {!session && (
        <div className="panel form-panel">
          <div className="form-row">
            <label>
              Name for the bank
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Alex — general" />
            </label>
            <label>
              Job type
              <input value={jobType} onChange={(e) => setJobType(e.target.value)} placeholder="e.g. software" />
            </label>
            <label>
              PDF file
              <input type="file" accept="application/pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            </label>
            <button className="primary" disabled={busy || !file || !name.trim() || !jobType.trim()} onClick={start}>
              {busy ? "Uploading…" : "Convert to LaTeX"}
            </button>
          </div>
          <p className="hint">
            The AI places your content into a battle-tested template, then verifies
            nothing was dropped, invented, or misaligned before you save anything.
          </p>
        </div>
      )}

      {session && session.status === "running" && (
        <div className="panel">
          <div className="category-head">Importing…</div>
          <p>{STAGE_LABELS[session.stage] ?? session.stage} ({Math.round(session.progress * 100)}%)</p>
          {session.rounds > 0 && <p className="hint">Fix round {session.rounds} — re-verifying.</p>}
        </div>
      )}

      {session && session.status === "error" && (
        <div className="panel">
          <div className="category-head">Import failed</div>
          <p className="error">{session.error}</p>
          <button onClick={() => setSession(null)}>Start over</button>
        </div>
      )}

      {session && (session.status === "review" || session.status === "done") && report && (
        <div className="panel">
          <div className="category-head">Verification</div>
          <ul>
            {CHECKS.map(([key, label]) => (
              <li key={key}>
                {report[key].length === 0 ? "✓" : "✗"} {label}
                {report[key].length > 0 && (
                  <ul>{report[key].map((i) => <li key={i} className="error">{i}</li>)}</ul>
                )}
              </li>
            ))}
          </ul>
          <div className="btn-row">
            <a className="button" href={`/api/resumes/import-sessions/${session.id}/pdf`} target="_blank" rel="noreferrer">
              Preview PDF
            </a>
            {session.status === "review" && (
              <button className="primary" onClick={accept}>
                {clean ? "Save to bank" : "Save anyway (with warnings)"}
              </button>
            )}
            {session.status === "done" && <span className="meta">Saved — see the Resume bank.</span>}
            <button onClick={() => setSession(null)}>Import another</button>
          </div>
          {!clean && session.status === "review" && (
            <p className="hint">
              Prefer not to save with warnings? You can keep the original PDF as an
              untailorable bank entry instead: Resume bank → upload PDF.
            </p>
          )}
        </div>
      )}
    </section>
  );
}
