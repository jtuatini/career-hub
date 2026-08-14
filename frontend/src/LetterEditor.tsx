import { useState } from "react";
import { api } from "./api";
import { useToast } from "./Toast";

export default function LetterEditor({
  doc,
  onSaved,
  onError,
}: {
  doc: { id: number; vetted: boolean };
  onSaved: () => void | Promise<void>;
  onError?: (msg: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [edited, setEdited] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();

  const startEdit = async () => {
    setError(null);
    try {
      const detail = await api.getDoc(doc.id);
      setEdited(detail.body_text ?? "");
      setEditing(true);
    } catch (e) {
      const msg = (e as Error).message;
      setError(msg);
      onError?.(msg);
      setEditing(true);
    }
  };

  const save = async () => {
    if (!edited.trim()) {
      setError("Cover letter body cannot be empty");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.amendDoc(doc.id, edited);
      toast("Letter updated — re-attach it in the extension if you already attached the old one", "ok");
      setEditing(false);
      await onSaved();
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
          Edit letter
        </button>
        {error && <p className="error" style={{ marginLeft: 8, marginTop: 0 }}>{error}</p>}
      </>
    );

  return (
    <div className="letter-editor">
      {error && <p className="error">{error}</p>}
      <label>
        Cover letter text
        <textarea
          rows={12}
          value={edited}
          onChange={(e) => setEdited(e.target.value)}
        />
      </label>
      <div className="btn-row">
        <button className="primary" onClick={save} disabled={busy}>
          {busy ? "Saving…" : "Save"}
        </button>
        <button className="ghost" onClick={() => setEditing(false)} disabled={busy}>
          Cancel
        </button>
      </div>
    </div>
  );
}
