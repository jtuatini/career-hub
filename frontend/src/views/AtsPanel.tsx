import { useCallback, useEffect, useState } from "react";
import { api, type AtsScanRow } from "../api";

const KIND_LABELS: Record<string, string> = {
  keyword: "Keyword check",
  jd_match: "JD match",
  deep: "Deep scan",
  hiring_agent: "Hiring-agent (local)",
};
const KIND_ORDER = ["keyword", "jd_match", "deep", "hiring_agent"];

/** Scan launcher + latest-report display for one doc or bank resume.
 * Buttons come from the backend's capabilities map, so kinds that can't run
 * (no JD, no PDF, no hiring-agent repo) never render. */
export default function AtsPanel({ docId, resumeId }: { docId?: number; resumeId?: number }) {
  const [scans, setScans] = useState<AtsScanRow[]>([]);
  const [caps, setCaps] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const [openKinds, setOpenKinds] = useState<Set<string>>(new Set());

  const refresh = useCallback(
    () =>
      api
        .listAtsScans({ doc_id: docId, resume_id: resumeId })
        .then((r) => {
          setScans(r.scans);
          setCaps(r.capabilities);
        })
        .catch((e) => setError((e as Error).message)),
    [docId, resumeId],
  );

  useEffect(() => {
    refresh();
  }, [refresh]);

  const running = scans.some((s) => s.status === "running");
  useEffect(() => {
    if (!running) return;
    const t = window.setInterval(refresh, 2500);
    return () => window.clearInterval(t);
  }, [running, refresh]);

  const run = (kind: string) => {
    setOpenKinds((s) => new Set(s).add(kind));
    return api
      .runAtsScan({ doc_id: docId, resume_id: resumeId, kind })
      .then(refresh)
      .catch((e) => setError((e as Error).message));
  };

  // Latest scan per kind is the report we show.
  const latest: Record<string, AtsScanRow> = {};
  for (const s of scans) if (!latest[s.kind]) latest[s.kind] = s;

  return (
    <div className="ats-panel">
      <div className="btn-row">
        {KIND_ORDER.filter((k) => caps[k]).map((k) => (
          <button key={k} className="ghost" disabled={running} onClick={() => run(k)}>
            {latest[k] ? `Re-run ${KIND_LABELS[k]}` : KIND_LABELS[k]}
          </button>
        ))}
      </div>
      {caps.hiring_agent && (
        <p className="meta">Hiring-agent scan runs fully local via Ollama — needs `ollama serve` running.</p>
      )}
      {error && (
        <p className="error" onClick={() => setError(null)}>
          {error}
        </p>
      )}
      {KIND_ORDER.filter((k) => latest[k]).map((k) => (
        <div className="ats-report" key={k}>
          <div className="ats-report-header">
            <button
              className="ghost ats-report-toggle"
              onClick={() =>
                setOpenKinds((s) => {
                  const next = new Set(s);
                  if (next.has(k)) next.delete(k);
                  else next.add(k);
                  return next;
                })
              }
            >
              {openKinds.has(k) ? "▾" : "▸"} {KIND_LABELS[k]} ·{" "}
              {new Date(latest[k].created_at).toLocaleString()}
              {latest[k].status === "running" && <span className="chip">running…</span>}
              {latest[k].status === "error" && <span className="chip chip-error">failed</span>}
              {latest[k].status === "cancelled" && <span className="chip">cancelled</span>}
            </button>
            {latest[k].status === "running" && (
              <button
                className="ghost"
                onClick={() => {
                  api
                    .cancelAtsScan(latest[k].id)
                    .then(refresh)
                    .catch((err) => setError((err as Error).message));
                }}
              >
                Stop
              </button>
            )}
          </div>
          {openKinds.has(k) && <ScanReport scan={latest[k]} />}
        </div>
      ))}
    </div>
  );
}

function ScanReport({ scan }: { scan: AtsScanRow }) {
  return (
    <>
      {scan.status === "error" && <p className="error">{scan.error}</p>}
      {scan.status === "done" && scan.report && <ReportBody scan={scan} />}
    </>
  );
}

function ReportBody({ scan }: { scan: AtsScanRow }) {
  const r = scan.report as Record<string, any>;
  if (scan.kind === "keyword")
    return (
      <div className="ats-body">
        <p>
          <strong>{r.ats_readable ? "Parses cleanly" : "PARSE PROBLEM"}</strong> ·{" "}
          {r.parsed_words} words extracted
          {r.keyword_score != null && <> · JD coverage {Math.round(r.keyword_score * 100)}%</>}
        </p>
        {(r.missing_keywords ?? []).length > 0 && (
          <p className="meta">
            Missing:{" "}
            <span className="chip-row">
              {r.missing_keywords.map((k: string) => (
                <span className="chip chip-miss" key={k}>
                  {k}
                </span>
              ))}
            </span>
          </p>
        )}
      </div>
    );
  if (scan.kind === "jd_match")
    return (
      <div className="ats-body">
        <p className="ats-score">{r.match_score}/100 match</p>
        {(r.missing_keywords ?? []).length > 0 && (
          <p className="meta">
            Missing:{" "}
            <span className="chip-row">
              {r.missing_keywords.map((k: string) => (
                <span className="chip chip-miss" key={k}>
                  {k}
                </span>
              ))}
            </span>
          </p>
        )}
        {(r.weak_areas ?? []).length > 0 && (
          <ul className="meta">
            {r.weak_areas.map((w: string) => (
              <li key={w}>Weak: {w}</li>
            ))}
          </ul>
        )}
        {(r.suggestions ?? []).length > 0 && (
          <ul className="meta">
            {r.suggestions.map((s: string) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
        )}
        <p>{r.summary}</p>
      </div>
    );
  if (scan.kind === "deep") {
    const cats: [string, string][] = [
      ["open_source", "Open source"],
      ["self_projects", "Projects"],
      ["production", "Production"],
      ["technical_skills", "Tech skills"],
    ];
    return (
      <div className="ats-body">
        <p className="ats-score">{r.overall_score}/40 overall</p>
        {cats.map(([key, label]) => (
          <p key={key} className="meta">
            <strong>
              {label}: {r[key]?.score}/10
            </strong>{" "}
            — {r[key]?.evidence}
          </p>
        ))}
        {(r.bonus ?? []).length > 0 && <p className="meta">Bonus: {r.bonus.join("; ")}</p>}
        {(r.deductions ?? []).length > 0 && (
          <p className="meta">Deductions: {r.deductions.join("; ")}</p>
        )}
        <p>{r.summary}</p>
      </div>
    );
  }
  return <pre className="ats-raw">{String(r.raw_output ?? JSON.stringify(r, null, 2))}</pre>;
}
