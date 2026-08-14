import { useState } from "react";
import { api } from "./api";
import { useToast } from "./Toast";

/** Expandable LaTeX editor for a tailored resume stored in the DB.
 * Loads the doc's tex_source on open, saves via PUT /api/docs/{id}/tex
 * (which recompiles the PDF before persisting anything). */
export default function TexEditor({
  doc,
  onSaved,
  onError,
}: {
  doc: { id: number };
  onSaved: (result: { page_count: number; approved: boolean; warnings: string[] }) => void | Promise<void>;
  onError?: (msg: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [tex, setTex] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();

  const startEdit = async () => {
    setError(null);
    try {
      const detail = await api.getDoc(doc.id);
      setTex(detail.tex_source);
      setEditing(true);
    } catch (e) {
      const msg = (e as Error).message;
      setError(msg);
      onError?.(msg);
    }
  };

  const save = async () => {
    if (!tex.trim()) {
      setError("LaTeX source cannot be empty");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await api.updateDocTex(doc.id, tex);
      if (result.warnings.length > 0) {
        result.warnings.forEach((w) => toast(w, "error"));
      } else {
        toast(`Recompiled — ${result.page_count} page${result.page_count === 1 ? "" : "s"}`, "ok");
      }
      setEditing(false);
      await onSaved(result);
    } catch (e) {
      const msg = (e as Error).message;
      setError(msg);
      onError?.(msg);
    } finally {
      setBusy(false);
    }
  };

  if (!editing)
    return (
      <>
        <button className="ghost" onClick={startEdit}>
          Edit LaTeX
        </button>
        {error && <p className="error" style={{ marginLeft: 8, marginTop: 0 }}>{error}</p>}
      </>
    );

  return (
    <div className="letter-editor tex-editor">
      {error && <p className="error">{error}</p>}
      <label>
        LaTeX source <span className="optional">saved to the database; PDF recompiles on save</span>
        <textarea
          rows={20}
          spellCheck={false}
          value={tex}
          onChange={(e) => setTex(e.target.value)}
        />
      </label>
      <div className="btn-row">
        <button className="primary" onClick={save} disabled={busy}>
          {busy ? "Compiling…" : "Recompile & save"}
        </button>
        <button className="ghost" onClick={() => setEditing(false)} disabled={busy}>
          Cancel
        </button>
      </div>
    </div>
  );
}
