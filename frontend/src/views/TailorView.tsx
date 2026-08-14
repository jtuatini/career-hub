import { useEffect, useState } from "react";
import { api, pdfUrl, type AtsReport, type GenerateResult, type Resume } from "../api";
import TexEditor from "../TexEditor";

const COVER_PHASES = [
  "Reading your résumé…",
  "Studying the job description…",
  "Drafting in your voice…",
  "Checking for AI clichés…",
  "Polishing the wording…",
];

export default function TailorView() {
  const [resumes, setResumes] = useState<Resume[]>([]);
  const [resumeId, setResumeId] = useState<number | "">("");
  const [company, setCompany] = useState("");
  const [title, setTitle] = useState("");
  const [url, setUrl] = useState("");
  const [jdText, setJdText] = useState("");
  const [busy, setBusy] = useState(false);
  const [coverBusy, setCoverBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<GenerateResult | null>(null);
  const [cover, setCover] = useState<GenerateResult | null>(null);
  const [coverBody, setCoverBody] = useState("");
  const [coverDone, setCoverDone] = useState(false);
  const [ats, setAts] = useState<AtsReport | null>(null);
  const [atsBusy, setAtsBusy] = useState(false);
  const [rawPaste, setRawPaste] = useState("");
  const [parsing, setParsing] = useState(false);
  const [parsedNote, setParsedNote] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [coverElapsed, setCoverElapsed] = useState(0);
  const [pdfRev, setPdfRev] = useState(0);

  useEffect(() => {
    api.listResumes().then((rs) => {
      setResumes(rs);
      if (rs.length > 0) setResumeId(rs[0].id);
    });
  }, []);

  async function runParse() {
    setParsing(true);
    setError(null);
    setParsedNote(null);
    try {
      const parsed = await api.parsePosting({ text: rawPaste });
      setCompany(parsed.company);
      setTitle(parsed.title);
      setJdText(parsed.jd_text);
      setParsedNote(
        `${Math.round(parsed.confidence * 100)}% confident` +
          (parsed.location ? ` · ${parsed.location}` : ""),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setParsing(false);
    }
  }

  async function runTailor() {
    if (resumeId === "") return;
    setBusy(true);
    setError(null);
    setResult(null);
    setCover(null);
    setAts(null);
    setElapsed(0);
    setPdfRev(0);
    const timer = setInterval(() => setElapsed((s) => s + 1), 1000);
    try {
      const r = await api.tailor({
        resume_id: resumeId,
        company,
        title,
        url: url || undefined,
        jd_text: jdText,
      });
      setResult(r);
      setAtsBusy(true);
      api
        .atsCheck(r.id)
        .then(setAts)
        .catch(() => {})
        .finally(() => setAtsBusy(false));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      clearInterval(timer);
      setBusy(false);
    }
  }

  async function runCoverLetter() {
    if (!result || resumeId === "") return;
    setCoverBusy(true);
    setError(null);
    setCoverElapsed(0);
    const timer = setInterval(() => setCoverElapsed((s) => s + 1), 1000);
    try {
      const r = await api.coverLetter({ job_id: result.job_id, resume_id: resumeId });
      setCover(r);
      setCoverBody(r.body_text ?? "");
      setCoverDone(false);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      clearInterval(timer);
      setCoverBusy(false);
    }
  }

  async function approve() {
    if (!result) return;
    try {
      await api.approveDoc(result.id);
      setResult({ ...result, approved: true });
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <section>
      <div className="view-head">
        <h2>Tailor</h2>
      </div>

      <div className="panel form-panel paste-panel">
        <label>
          Paste the posting — page text, emails, anything{" "}
          <span className="optional">AI extracts the fields below</span>
          <textarea
            rows={3}
            value={rawPaste}
            placeholder="⌘A ⌘C the job page, paste here, hit Parse."
            onChange={(e) => setRawPaste(e.target.value)}
          />
        </label>
        <div className="btn-row">
          <button onClick={runParse} disabled={parsing || rawPaste.trim().length < 80}>
            {parsing ? "Parsing…" : "Parse with AI"}
          </button>
          {parsedNote && <span className="chip chip-ok">{parsedNote}</span>}
        </div>
      </div>

      <div className="panel form-panel">
        <div className="form-row">
          <label>
            Base resume
            <select
              value={resumeId}
              onChange={(e) => setResumeId(Number(e.target.value))}
              disabled={resumes.length === 0}
            >
              {resumes.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name} ({r.job_type})
                </option>
              ))}
            </select>
          </label>
          <label>
            Company
            <input value={company} onChange={(e) => setCompany(e.target.value)} placeholder="Acme" />
          </label>
          <label>
            Role
            <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="SWE Intern" />
          </label>
        </div>
        <label>
          Posting URL <span className="optional">optional</span>
          <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…" />
        </label>
        <label>
          Job description
          <textarea
            rows={10}
            value={jdText}
            onChange={(e) => setJdText(e.target.value)}
            placeholder="Paste the full job description here."
          />
        </label>
        <button
          className="primary"
          disabled={busy || resumeId === "" || !company || !title || !jdText}
          onClick={runTailor}
        >
          {busy ? `Tailoring… ${elapsed}s (wording-only edits, then compile)` : "Tailor resume"}
        </button>
        {resumes.length === 0 && (
          <p className="empty">Add a resume in the Resume bank first.</p>
        )}
      </div>

      {error && <p className="error">{error}</p>}

      {result && (
        <div className="panel result-panel">
          <div className="view-head">
            <h3>
              Wording changes <span className="meta-inline">{result.page_count} page{result.page_count === 1 ? "" : "s"}
                {result.divergence != null &&
                  ` · ${Math.round(result.divergence * 100)}% changed from base`}</span>
            </h3>
            <div className="btn-row">
              <a
                className="button"
                href={`${pdfUrl.doc(result.id)}${pdfRev ? `?rev=${pdfRev}` : ""}`}
                target="_blank"
                rel="noreferrer"
              >
                Open tailored PDF
              </a>
              <TexEditor
                doc={result}
                onSaved={(r) => {
                  setResult({ ...result, page_count: r.page_count, approved: r.approved });
                  setPdfRev((n) => n + 1);
                }}
              />
              <button className="primary" onClick={approve} disabled={result.approved}>
                {result.approved ? "Approved" : "Approve"}
              </button>
            </div>
          </div>

          {result.warnings.map((w) => (
            <p className="warning" key={w}>
              {w}
            </p>
          ))}

          <div className="result-split">
            <div>
              {result.applied_edits.length === 0 && (
                <p className="empty">No wording changes were needed for this posting.</p>
              )}
              <ul className="redlines">
                {result.applied_edits.map((e, i) => (
                  <li key={i}>
                    <del>{e.original}</del>
                    <ins>{e.replacement}</ins>
                  </li>
                ))}
              </ul>
            </div>
            <iframe
              title="Tailored PDF"
              src={`${pdfUrl.doc(result.id)}${pdfRev ? `?rev=${pdfRev}` : ""}`}
              className="pdf-frame"
            />
          </div>

          {result.rejected_edits.length > 0 && (
            <details className="rejected">
              <summary>{result.rejected_edits.length} suggested edit(s) rejected by the template guard</summary>
              <ul>
                {result.rejected_edits.map((e, i) => (
                  <li key={i}>
                    <code>{e.original.slice(0, 100)}</code>
                    <p className="meta">{e.reason}</p>
                  </li>
                ))}
              </ul>
            </details>
          )}

          <div className="cover-row ats-row">
            {ats ? (
              <div className="ats-report">
                <p>
                  <span className={ats.ats_readable ? "chip chip-ok" : "chip chip-bad"}>
                    {ats.ats_readable ? "ATS-readable" : "ATS parse thin"}
                  </span>
                  <span className="meta-inline">{ats.parsed_words} words parsed from the PDF</span>
                  {ats.keyword_score !== null && (
                    <span className="meta-inline">
                      · keyword match {Math.round(ats.keyword_score * 100)}% (
                      {ats.present_keywords.length}/{ats.keywords_checked})
                    </span>
                  )}
                </p>
                {ats.missing_keywords.length > 0 && (
                  <p className="meta">
                    Missing JD terms:{" "}
                    {ats.missing_keywords.map((k) => (
                      <span className="chip chip-miss" key={k}>
                        {k}
                      </span>
                    ))}
                  </p>
                )}
              </div>
            ) : (
              <button
                onClick={async () => {
                  if (!result) return;
                  setAtsBusy(true);
                  try {
                    setAts(await api.atsCheck(result.id));
                  } catch (e) {
                    setError((e as Error).message);
                  } finally {
                    setAtsBusy(false);
                  }
                }}
                disabled={atsBusy}
              >
                {atsBusy ? "Checking…" : "ATS + keyword check"}
              </button>
            )}
          </div>

          <div className="cover-row">
            {!cover && !coverBusy && (
              <button onClick={runCoverLetter}>Generate cover letter</button>
            )}
            {!cover && coverBusy && (
              <div className="gen-card">
                <div className="gen-head">
                  <span className="gen-dots" aria-hidden="true">
                    <span />
                    <span />
                    <span />
                  </span>
                  <span className="gen-phase">
                    {COVER_PHASES[Math.min(Math.floor(coverElapsed / 8), COVER_PHASES.length - 1)]}
                  </span>
                  <span className="meta">{coverElapsed}s</span>
                </div>
                <div className="skeleton gen-line" style={{ width: "92%" }} />
                <div className="skeleton gen-line" style={{ width: "100%" }} />
                <div className="skeleton gen-line" style={{ width: "96%" }} />
                <div className="skeleton gen-line" style={{ width: "58%" }} />
                <p className="gen-note">
                  Writing a letter grounded in your résumé, scrubbed of AI tells. Usually
                  under a minute.
                </p>
              </div>
            )}
            {cover && !coverDone && (
              <div className="cover-edit fade-in">
                <label>
                  Cover letter draft{" "}
                  <span className="optional">
                    {coverBody.trim().split(/\s+/).filter(Boolean).length} words — edit freely,
                    your changes teach the AI your voice
                  </span>
                  <textarea rows={12} value={coverBody} onChange={(e) => setCoverBody(e.target.value)} />
                </label>
                <button
                  className="primary"
                  disabled={coverBusy || !coverBody.trim()}
                  onClick={async () => {
                    setCoverBusy(true);
                    try {
                      await api.updateDocBody(cover.id, coverBody);
                      await api.finalizeDoc(cover.id);
                      setCoverDone(true);
                    } catch (e) {
                      setError((e as Error).message);
                    } finally {
                      setCoverBusy(false);
                    }
                  }}
                >
                  {coverBusy ? "Compiling…" : "Approve & compile PDF"}
                </button>
              </div>
            )}
            {cover && coverDone && (
              <a className="button" href={pdfUrl.doc(cover.id)} target="_blank" rel="noreferrer">
                Open cover letter PDF
              </a>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
