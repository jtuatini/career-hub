import { useCallback, useEffect, useState } from "react";
import { api, MEMORY_TYPES, RELATIONS } from "../api";
import { useToast } from "../Toast";
import ConfirmButton from "../ConfirmButton";
import type { GraphData, MemoryEntry, MemoryEntryDetail } from "../api";
import MemoryGraph from "./MemoryGraph";
import EmptyState from "../EmptyState";

const EMPTY_FORM = { type: "experience", title: "", content: "", tags: "" };

export default function MemoryView() {
  const toast = useToast();
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [entries, setEntries] = useState<MemoryEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<"graph" | "cards">("graph");
  const [query, setQuery] = useState("");
  const [highlightIds, setHighlightIds] = useState<Set<number> | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [showForm, setShowForm] = useState(false);
  const [selected, setSelected] = useState<MemoryEntryDetail | null>(null);
  const [editContent, setEditContent] = useState("");
  const [linkTarget, setLinkTarget] = useState("");
  const [linkRelation, setLinkRelation] = useState("related");
  const [busy, setBusy] = useState<string | null>(null); // "ingest" | "organize" | "github"

  const refresh = useCallback(
    () =>
      Promise.all([api.getGraph(), api.listMemory()])
        .then(([g, list]) => {
          setGraph(g);
          setEntries(list);
        })
        .catch((e) => setError((e as Error).message)),
    [],
  );
  useEffect(() => {
    refresh();
  }, [refresh]);

  const open = async (id: number | null) => {
    if (id === null) {
      setSelected(null);
      return;
    }
    try {
      const detail = await api.getMemory(id);
      setSelected(detail);
      setEditContent(detail.content);
      setLinkTarget("");
      setLinkRelation("related");
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const search = async () => {
    if (!query.trim()) {
      setHighlightIds(null);
      return;
    }
    try {
      const hits = await api.searchMemory(query);
      setHighlightIds(new Set(hits.map((h) => h.entry.id)));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const submit = async () => {
    try {
      const created = await api.createMemory({
        type: form.type,
        title: form.title,
        content: form.content,
        tags: form.tags.split(",").map((t) => t.trim()).filter(Boolean),
      });
      setForm(EMPTY_FORM);
      setShowForm(false);
      toast(
        created.links.length > 0
          ? `Saved — linked to ${created.links.length} ${created.links.length === 1 ? "entity" : "entities"}`
          : "Saved to brain",
      );
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const toggleMute = async () => {
    if (!selected) return;
    const next = !selected.muted;
    try {
      await api.updateMemory(selected.id, { muted: next });
      toast(next ? "Muted — the AI won't use this" : "Unmuted");
      setSelected({ ...selected, muted: next });
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const saveContent = async () => {
    if (!selected) return;
    try {
      await api.updateMemory(selected.id, { content: editContent });
      toast("Updated");
      setSelected({ ...selected, content: editContent });
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const remove = async () => {
    if (!selected) return;
    try {
      await api.deleteMemory(selected.id);
      setSelected(null);
      toast("Memory deleted");
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const addLink = async () => {
    if (!selected || !linkTarget) return;
    try {
      await api.linkMemory({
        from_id: selected.id,
        to_id: Number(linkTarget),
        relation: linkRelation,
      });
      open(selected.id);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const organize = async () => {
    setBusy("organize");
    try {
      const r = await api.organizeBrain();
      toast(`Organized: ${r.hubs_created} hubs, ${r.links_created} links` +
        (r.errors.length ? ` (${r.errors.length} errors)` : ""));
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const syncGithub = async () => {
    setBusy("github");
    try {
      const r = await api.syncGithub();
      toast(`GitHub: ${r.repos_synced} repos synced, ${r.hubs_created} new hubs`);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const ingest = async (file: File) => {
    setBusy("ingest");
    setError(null);
    try {
      const created = await api.ingestDocument(file);
      toast(`${created.length} memories extracted`);
      refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <section>
      <div className="view-head">
        <h2>
          Memory
          <span className="meta-inline">the brain — a living graph the AI writes from</span>
        </h2>
        <div className="btn-row">
          <button onClick={() => setMode(mode === "graph" ? "cards" : "graph")}>
            {mode === "graph" ? "Cards" : "Graph"}
          </button>
          <button disabled={busy !== null} onClick={organize}>
            {busy === "organize" ? "Organizing…" : "Organize brain"}
          </button>
          <button disabled={busy !== null} onClick={syncGithub}>
            {busy === "github" ? "Syncing…" : "Sync GitHub"}
          </button>
          <label className="button" style={{ cursor: busy ? "default" : "pointer" }}>
            {busy === "ingest" ? "Extracting…" : "Ingest a document"}
            <input
              type="file"
              accept=".pdf,.txt,.md"
              style={{ display: "none" }}
              disabled={busy !== null}
              onChange={(e) => {
                const file = e.target.files?.[0];
                e.target.value = "";
                if (file) ingest(file);
              }}
            />
          </label>
          <button className="primary" onClick={() => setShowForm((s) => !s)}>
            {showForm ? "Close" : "Add memory"}
          </button>
        </div>
      </div>
      {error && (
        <p className="error" onClick={() => setError(null)}>
          {error}
        </p>
      )}

      {showForm && (
        <div className="panel form-panel">
          <div className="form-row">
            <label>
              Type
              <select value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })}>
                {MEMORY_TYPES.map((t) => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </label>
            <label>
              Title
              <input
                value={form.title}
                onChange={(e) => setForm({ ...form, title: e.target.value })}
                placeholder="Robotics competition win"
              />
            </label>
            <label>
              Tags <span className="optional">comma-separated</span>
              <input
                value={form.tags}
                onChange={(e) => setForm({ ...form, tags: e.target.value })}
                placeholder="robotics, leadership"
              />
            </label>
          </div>
          <label>
            The memory — what happened, what you did, what it shows
            <textarea
              rows={5}
              value={form.content}
              onChange={(e) => setForm({ ...form, content: e.target.value })}
            />
          </label>
          <button
            className="primary"
            disabled={!form.title.trim() || !form.content.trim()}
            onClick={submit}
          >
            Save to brain
          </button>
        </div>
      )}

      <div className="form-row" style={{ marginBottom: 12 }}>
        <label style={{ flex: 3 }}>
          Semantic search <span className="optional">highlights matches in the graph</span>
          <input
            value={query}
            placeholder="e.g. a time I led a team under pressure"
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && search()}
          />
        </label>
        <div className="btn-row">
          <button onClick={search}>Search</button>
          {highlightIds && (
            <button className="ghost" onClick={() => { setHighlightIds(null); setQuery(""); }}>
              Clear
            </button>
          )}
        </div>
      </div>

      <div className={`graph-layout${selected ? " with-panel" : ""}`}>
        {graph !== null && entries.length === 0 ? (
          <EmptyState
            title="Your brain is empty"
            hint="Add experiences and stories here, or upload an old résumé/essay — the AI splits it into memory entries that power your application answers."
          />
        ) : (
          <>
            {mode === "graph" && graph && graph.nodes.length > 0 && (
              <MemoryGraph
                data={graph}
                selectedId={selected?.id ?? null}
                highlightIds={highlightIds}
                onSelect={open}
              />
            )}
            {mode === "cards" && (
              <div className="card-grid">
                {entries.map((entry) => (
                  <div className="card clickable" key={entry.id} onClick={() => open(entry.id)}>
                    <header>
                      <h3>{entry.title}</h3>
                      <span className="chip">{entry.type}</span>
                    </header>
                    <p className="meta">{entry.content.slice(0, 120)}</p>
                    {entry.muted && <span className="chip">muted</span>}
                  </div>
                ))}
              </div>
            )}
          </>
        )}

        {selected && (
          <aside className="graph-panel">
            <div className="inspector-head">
              <h3>
                {selected.title}
                <span className="chip" style={{ marginLeft: 10 }}>{selected.type}</span>
              </h3>
              <button onClick={() => setSelected(null)}>Close</button>
            </div>
            {selected.muted && (
              <p className="warning">Muted — excluded from everything the AI writes.</p>
            )}
            <textarea
              rows={6}
              value={editContent}
              onChange={(e) => setEditContent(e.target.value)}
            />
            <div className="btn-row">
              <button onClick={saveContent} disabled={editContent === selected.content}>
                Save
              </button>
              <button onClick={toggleMute}>{selected.muted ? "Unmute" : "Mute"}</button>
              <ConfirmButton
                ariaLabel="Delete memory"
                confirmText="Delete?"
                onConfirm={remove}
                onError={(e) => setError(e.message)}
              >
                Delete
              </ConfirmButton>
            </div>
            <div className="memory-links">
              <h3>Connections</h3>
              {selected.links.length === 0 && <p className="empty">Not linked to anything yet.</p>}
              <ul>
                {selected.links.map((l) => (
                  <li key={l.link_id}>
                    <button className="ghost" onClick={() => open(l.entry.id)}>
                      {l.entry.title}
                    </button>
                    {l.relation && <span className="meta-inline">{l.relation}</span>}
                  </li>
                ))}
              </ul>
              <div className="form-row">
                <label>
                  Link to
                  <select value={linkTarget} onChange={(e) => setLinkTarget(e.target.value)}>
                    <option value="">— pick a memory —</option>
                    {entries
                      .filter((e) => e.id !== selected.id)
                      .map((e) => (
                        <option key={e.id} value={e.id}>{e.title}</option>
                      ))}
                  </select>
                </label>
                <label>
                  Relation
                  <select value={linkRelation} onChange={(e) => setLinkRelation(e.target.value)}>
                    {RELATIONS.map((r) => (
                      <option key={r} value={r}>{r}</option>
                    ))}
                  </select>
                </label>
                <button onClick={addLink} disabled={!linkTarget}>Link</button>
              </div>
            </div>
          </aside>
        )}
      </div>
    </section>
  );
}
