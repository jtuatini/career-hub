import { useEffect, useMemo, useState } from "react";
import {
  api,
  pdfUrl,
  type BulkEditResult,
  type Resume,
  type ResumeDetail,
} from "../api";
import ConfirmButton from "../ConfirmButton";
import EmptyState from "../EmptyState";
import AtsPanel from "./AtsPanel";

type FormMode = null | "tex" | "pdf" | "bulk";

// Bank display order: categories for the roles currently being applied to
// come first; unlisted categories follow alphabetically.
const TYPE_PRIORITY = ["software", "aerospace", "quant", "fintech"];
const typeRank = (t: string) => {
  const i = TYPE_PRIORITY.indexOf(t.toLowerCase());
  return i === -1 ? TYPE_PRIORITY.length : i;
};
// Optional: resumes whose name contains this keyword sort first.
// Set with localStorage.setItem("resume-pin", "<keyword>") in the browser console.
const PIN_KEYWORD = (localStorage.getItem("resume-pin") ?? "").toLowerCase();
const nameRank = (n: string) =>
  PIN_KEYWORD && n.toLowerCase().includes(PIN_KEYWORD) ? 0 : 1;

export default function ResumesView() {
  const [resumes, setResumes] = useState<Resume[]>([]);
  const [loading, setLoading] = useState(true);
  const [mode, setMode] = useState<FormMode>(null);
  const [selected, setSelected] = useState<ResumeDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = () =>
    api.listResumes().then(setResumes).catch((e) => setError(e.message)).finally(() => setLoading(false));
  useEffect(() => {
    refresh();
  }, []);

  const groups = useMemo(() => {
    const byType = new Map<string, Resume[]>();
    for (const r of resumes) {
      const list = byType.get(r.job_type) ?? [];
      list.push(r);
      byType.set(r.job_type, list);
    }
    for (const list of byType.values()) {
      list.sort((a, b) => nameRank(a.name) - nameRank(b.name));
    }
    return [...byType.entries()].sort(
      ([a], [b]) => typeRank(a) - typeRank(b) || a.localeCompare(b),
    );
  }, [resumes]);

  async function open(r: Resume) {
    setSelected(await api.getResume(r.id));
  }

  async function remove(r: Resume) {
    try {
      await api.deleteResume(r.id);
      if (selected?.id === r.id) setSelected(null);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <section>
      <div className="view-head">
        <h2>Resume bank</h2>
        <div className="btn-row">
          <button className={mode === "bulk" ? "active-toggle" : ""} onClick={() => setMode(mode === "bulk" ? null : "bulk")}>
            Bulk edit
          </button>
          <button className={mode === "pdf" ? "active-toggle" : ""} onClick={() => setMode(mode === "pdf" ? null : "pdf")}>
            Upload PDF
          </button>
          <button className="primary" onClick={() => setMode(mode === "tex" ? null : "tex")}>
            {mode === "tex" ? "Cancel" : "Add LaTeX resume"}
          </button>
        </div>
      </div>

      {mode === "tex" && <TexUploadForm onDone={() => { setMode(null); refresh(); }} />}
      {mode === "pdf" && <PdfUploadForm onDone={() => { setMode(null); refresh(); }} />}
      {mode === "bulk" && <BulkEditForm onDone={refresh} />}

      {error && <p className="error">{error}</p>}

      {!loading && resumes.length === 0 && mode === null ? (
        <EmptyState
          title="No résumés yet"
          hint="Click “Add LaTeX resume” above to paste one in, or convert an existing PDF: Documents ▾ → Import PDF."
        />
      ) : (
        groups.map(([jobType, list]) => (
          <div key={jobType} className="category">
            <h3 className="category-head">
              {jobType} <span className="count">{list.length}</span>
            </h3>
            <div className="card-grid">
              {list.map((r) => (
                <article
                  className={`card clickable ${r.page_count == null ? "" : ""}`}
                  key={r.id}
                  onClick={() => open(r)}
                >
                  <header>
                    <h3>{r.name}</h3>
                    <ConfirmButton
                      ariaLabel={`Delete ${r.name}`}
                      confirmText={r.version_count > 1 ? `Delete ${r.version_count} versions?` : "Delete?"}
                      onConfirm={() => remove(r)}
                      onError={(e) => setError(e.message)}
                    >
                      ✕
                    </ConfirmButton>
                  </header>
                  <p className="meta">
                    {r.version_count > 1 && <span className="chip chip-version">v{r.version_count}</span>}{" "}
                    {r.page_count === 1 ? "1 page" : `${r.page_count ?? "?"} pages`} ·{" "}
                    {new Date(r.created_at).toLocaleDateString()}
                  </p>
                  <footer onClick={(e) => e.stopPropagation()}>
                    <a href={pdfUrl.resume(r.id)} target="_blank" rel="noreferrer">
                      PDF
                    </a>
                  </footer>
                </article>
              ))}
            </div>
          </div>
        ))
      )}

      {selected && (
        <ResumeInspector
          resume={selected}
          onClose={() => setSelected(null)}
          onSaved={() => { setSelected(null); refresh(); }}
        />
      )}
    </section>
  );
}

function TexUploadForm({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState("");
  const [jobType, setJobType] = useState("");
  const [texSource, setTexSource] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="panel form-panel">
      <div className="form-row">
        <label>
          Name
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="SWE resume" />
        </label>
        <label>
          Category
          <input value={jobType} onChange={(e) => setJobType(e.target.value)} placeholder="software, quant, aerospace…" />
        </label>
        <label className="file-label">
          .tex file
          <input
            type="file"
            accept=".tex"
            onChange={async (e) => {
              const f = e.target.files?.[0];
              if (!f) return;
              setTexSource(await f.text());
              if (!name) setName(f.name.replace(/\.tex$/, ""));
            }}
          />
        </label>
      </div>
      <label>
        LaTeX source
        <textarea
          rows={10}
          value={texSource}
          onChange={(e) => setTexSource(e.target.value)}
          placeholder="Paste your .tex here, or choose a file above."
          spellCheck={false}
        />
      </label>
      {error && <p className="error">{error}</p>}
      <button
        className="primary"
        disabled={busy || !name || !jobType || !texSource}
        onClick={async () => {
          setBusy(true);
          setError(null);
          try {
            await api.createResume({ name, job_type: jobType, tex_source: texSource });
            onDone();
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? "Compiling…" : "Save & compile"}
      </button>
    </div>
  );
}

function PdfUploadForm({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState("");
  const [jobType, setJobType] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="panel form-panel">
      <p className="hint">
        PDF-only entries are stored for reference — they can't be tailored (no LaTeX source).
      </p>
      <div className="form-row">
        <label>
          Name
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label>
          Category
          <input value={jobType} onChange={(e) => setJobType(e.target.value)} />
        </label>
        <label className="file-label">
          PDF file
          <input
            type="file"
            accept="application/pdf"
            onChange={(e) => {
              const f = e.target.files?.[0] ?? null;
              setFile(f);
              if (f && !name) setName(f.name.replace(/\.pdf$/, ""));
            }}
          />
        </label>
      </div>
      {error && <p className="error">{error}</p>}
      <button
        className="primary"
        disabled={busy || !name || !jobType || !file}
        onClick={async () => {
          setBusy(true);
          setError(null);
          try {
            await api.uploadPdfResume(name, jobType, file!);
            onDone();
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? "Uploading…" : "Add to bank"}
      </button>
    </div>
  );
}

function BulkEditForm({ onDone }: { onDone: () => void }) {
  const [find, setFind] = useState("");
  const [replace, setReplace] = useState("");
  const [jobType, setJobType] = useState("");
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<BulkEditResult[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="panel form-panel">
      <p className="hint">
        Find & replace exact text across the latest version of every LaTeX resume — use it for
        universal updates (phone number, a project bullet) or scope it to one category. Each change
        becomes a new version, so nothing is overwritten.
      </p>
      <div className="form-row">
        <label>
          Find (exact text)
          <input value={find} onChange={(e) => setFind(e.target.value)} spellCheck={false} />
        </label>
        <label>
          Replace with
          <input value={replace} onChange={(e) => setReplace(e.target.value)} spellCheck={false} />
        </label>
        <label>
          Category <span className="optional">optional — blank = all</span>
          <input value={jobType} onChange={(e) => setJobType(e.target.value)} placeholder="all categories" />
        </label>
      </div>
      {error && <p className="error">{error}</p>}
      <button
        className="primary"
        disabled={busy || !find}
        onClick={async () => {
          setBusy(true);
          setError(null);
          setResults(null);
          try {
            const resp = await api.bulkEdit({
              find,
              replace,
              job_type: jobType || undefined,
            });
            setResults(resp.results);
            onDone();
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? "Applying…" : "Apply to all matching"}
      </button>
      {results && (
        <ul className="bulk-results">
          {results.length === 0 && <li>No resumes contained that text.</li>}
          {results.map((r) => (
            <li key={r.id} className={r.status === "updated" ? "ok" : "bad"}>
              {r.name}: {r.status === "updated" ? `updated → new version #${r.new_id}` : `compile failed — ${r.error}`}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ResumeInspector({
  resume,
  onClose,
  onSaved,
}: {
  resume: ResumeDetail;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [showAts, setShowAts] = useState(false);
  const [tex, setTex] = useState(resume.tex_source ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dirty = tex !== (resume.tex_source ?? "");

  return (
    <div className="overlay" onClick={onClose}>
      <div className="inspector" onClick={(e) => e.stopPropagation()}>
        <header className="inspector-head">
          <h3>
            {resume.name} <span className="chip">{resume.job_type}</span>
          </h3>
          <div className="btn-row">
            <button onClick={() => setShowAts((s) => !s)}>
              {showAts ? "Hide ATS" : "ATS scan"}
            </button>
            {resume.tex_source !== null && (
              <button
                className="primary"
                disabled={busy || !dirty}
                onClick={async () => {
                  setBusy(true);
                  setError(null);
                  try {
                    await api.updateResume(resume.id, { tex_source: tex });
                    onSaved();
                  } catch (e) {
                    setError((e as Error).message);
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                {busy ? "Compiling…" : "Save as new version"}
              </button>
            )}
            <button onClick={onClose}>Close</button>
          </div>
        </header>
        {error && <p className="error">{error}</p>}
        {showAts && <AtsPanel resumeId={resume.id} />}
        <div className={resume.tex_source === null ? "inspector-body single" : "inspector-body"}>
          {resume.tex_source !== null && (
            <textarea
              className="tex-editor"
              value={tex}
              onChange={(e) => setTex(e.target.value)}
              spellCheck={false}
            />
          )}
          <iframe title="PDF preview" src={pdfUrl.resume(resume.id)} className="pdf-frame" />
        </div>
      </div>
    </div>
  );
}
