import { useEffect, useState } from "react";
import { api } from "../api";
import { useToast } from "../Toast";
import ConfirmButton from "../ConfirmButton";
import type { VoiceProfile, VoiceSample } from "../api";

const EMPTY_FORM = { title: "", kind: "formal", text: "" };

export default function VoicePanel() {
  const toast = useToast();
  const [samples, setSamples] = useState<VoiceSample[]>([]);
  const [profile, setProfile] = useState<VoiceProfile | null>(null);
  const [content, setContent] = useState("");
  const [form, setForm] = useState(EMPTY_FORM);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = () =>
    Promise.all([api.listVoiceSamples(), api.getVoiceProfile()])
      .then(([s, p]) => {
        setSamples(s);
        setProfile(p);
        setContent(p.content ?? "");
      })
      .catch((e) => setError((e as Error).message));
  useEffect(() => {
    refresh();
  }, []);

  const addSample = async () => {
    try {
      await api.addVoiceSample(form);
      setForm(EMPTY_FORM);
      toast("Sample added");
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const upload = async (file: File) => {
    setBusy("upload");
    try {
      await api.uploadVoiceSample(file.name.replace(/\.[^.]+$/, ""), form.kind, file);
      toast("Sample uploaded");
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const rebuild = async () => {
    setBusy("rebuild");
    setError(null);
    try {
      const p = await api.rebuildVoiceProfile();
      setProfile(p);
      setContent(p.content ?? "");
      toast("Voice profile rebuilt");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const saveContent = async () => {
    try {
      setProfile(await api.updateVoiceProfile({ content }));
      toast("Profile saved");
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const dropRule = async (index: number) => {
    if (!profile) return;
    const rules = profile.learned_rules.filter((_, i) => i !== index);
    try {
      setProfile(await api.updateVoiceProfile({ learned_rules: rules }));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div className="panel form-panel voice-panel">
      <div className="category-head">
        Voice
        <span className="meta-inline">how the AI learns to write like you</span>
      </div>
      {error && (
        <p className="error" onClick={() => setError(null)}>
          {error}
        </p>
      )}

      <h3>Writing samples ({samples.length})</h3>
      {samples.length === 0 && (
        <p className="empty">
          Add 3-10 pieces of your real writing — essays for your formal register,
          texts/posts for your natural rhythm — then hit Rebuild.
        </p>
      )}
      <ul className="voice-samples">
        {samples.map((s) => (
          <li key={s.id}>
            <span className="chip">{s.kind}</span> {s.title}
            <ConfirmButton
              ariaLabel={`Delete ${s.title}`}
              confirmText="Delete?"
              onConfirm={() => api.deleteVoiceSample(s.id).then(refresh)}
              onError={(e) => setError(e.message)}
            >
              Delete
            </ConfirmButton>
          </li>
        ))}
      </ul>
      <div className="form-row">
        <label>
          Title
          <input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
        </label>
        <label>
          Kind
          <select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
            <option value="formal">formal</option>
            <option value="informal">informal</option>
          </select>
        </label>
        <label className="button" style={{ alignSelf: "end" }}>
          {busy === "upload" ? "Uploading…" : "Upload file"}
          <input
            type="file"
            accept=".pdf,.txt,.md"
            style={{ display: "none" }}
            disabled={busy !== null}
            onChange={(e) => {
              const f = e.target.files?.[0];
              e.target.value = "";
              if (f) upload(f);
            }}
          />
        </label>
      </div>
      <label>
        Or paste the text
        <textarea rows={4} value={form.text} onChange={(e) => setForm({ ...form, text: e.target.value })} />
      </label>
      <button disabled={!form.title.trim() || form.text.trim().length < 40} onClick={addSample}>
        Add sample
      </button>

      <h3 style={{ marginTop: 20 }}>Style profile</h3>
      <div className="btn-row">
        <button className="primary" disabled={busy !== null || samples.length === 0} onClick={rebuild}>
          {busy === "rebuild" ? "Analyzing your writing…" : profile?.content ? "Rebuild from samples" : "Build from samples"}
        </button>
      </div>
      {profile?.content != null && (
        <>
          <textarea rows={14} value={content} onChange={(e) => setContent(e.target.value)} />
          <button onClick={saveContent} disabled={content === (profile.content ?? "")}>
            Save profile edits
          </button>
          <h3>Learned from your edits</h3>
          {profile.learned_rules.length === 0 && (
            <p className="empty">Nothing yet — edit AI drafts before approving and rules appear here.</p>
          )}
          <div className="voice-rules">
            {profile.learned_rules.map((r, i) => (
              <span className="chip" key={`${r.date}-${i}`} title={r.date}>
                {r.rule}
                <button className="ghost" onClick={() => dropRule(i)}>
                  ×
                </button>
              </span>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
