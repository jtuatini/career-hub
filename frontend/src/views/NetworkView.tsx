import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { NetworkPerson, NetworkTarget } from "../api";
import ConfirmButton from "../ConfirmButton";
import EmptyState from "../EmptyState";
import { safeHttpUrl } from "../url";

const STATUS_FILTERS: [string, string][] = [
  ["", "All"],
  ["found", "Found"],
  ["shortlisted", "Shortlisted"],
  ["contacted", "Contacted"],
  ["replied", "Replied"],
  ["met", "Met"],
  ["archived", "Archived"],
];

const PERSON_TYPE_FILTERS: [string, string][] = [
  ["", "All"],
  ["alumni", "Alumni"],
  ["engineer", "Engineer"],
  ["recruiter", "Recruiter"],
  ["manager", "Manager"],
  ["other", "Other"],
];

const STATUS_LABELS: Record<string, string> = {
  found: "Found",
  shortlisted: "Shortlisted",
  contacted: "Contacted",
  replied: "Replied",
  met: "Met",
  archived: "Archived",
};

const PERSON_TYPE_LABELS: Record<string, string> = {
  alumni: "Alumni",
  engineer: "Engineer",
  recruiter: "Recruiter",
  manager: "Manager",
  other: "Other",
};

const SOURCE_GLYPH: Record<string, string> = {
  web_search: "🌐",
  linkedin_capture: "💼",
  manual: "✎",
};

// The status action buttons on an expanded card. "found" is the implicit
// starting state (set by discovery/creation, never re-applied by hand), so
// it's a filter chip above but not a button here.
const STATUS_ACTIONS: [string, string][] = [
  ["shortlisted", "Shortlist"],
  ["contacted", "Contacted"],
  ["replied", "Replied"],
  ["met", "Met"],
  ["archived", "Archive"],
];

function safeHost(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}

interface DiscoverState {
  running: boolean;
  done: number;
  total: number;
}

export default function NetworkView() {
  const [targets, setTargets] = useState<NetworkTarget[] | null>(null);
  const [people, setPeople] = useState<NetworkPerson[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [status, setStatus] = useState("");
  const [personType, setPersonType] = useState("");
  const [q, setQ] = useState("");

  const [newCompany, setNewCompany] = useState("");
  const [newRoleType, setNewRoleType] = useState("");

  const [discover, setDiscover] = useState<DiscoverState | null>(null);
  const [discoverElapsed, setDiscoverElapsed] = useState(0);

  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [redraftingIds, setRedraftingIds] = useState<Set<number>>(new Set());

  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // --- targets ---------------------------------------------------------

  const refreshTargets = useCallback(
    () => api.network.targets().then(setTargets).catch((e) => setError((e as Error).message)),
    [],
  );

  // Mount-only fetch, but still routed through a cleared timer so React
  // StrictMode's mount→cleanup→mount dance can only leave one in flight.
  useEffect(() => {
    const t = window.setTimeout(refreshTargets, 0);
    return () => window.clearTimeout(t);
  }, [refreshTargets]);

  // --- people ------------------------------------------------------------

  const refreshPeople = useCallback(
    () =>
      api.network
        .people({ status: status || undefined, person_type: personType || undefined, q: q || undefined })
        .then(setPeople)
        .catch((e) => setError((e as Error).message)),
    [status, personType, q],
  );

  // Keep a ref to the latest refreshPeople so the discover-poll effect below
  // (which intentionally doesn't depend on filters) never calls a stale
  // closure once discovery finishes mid-session.
  const refreshPeopleRef = useRef(refreshPeople);
  useEffect(() => {
    refreshPeopleRef.current = refreshPeople;
  }, [refreshPeople]);

  // Single effect, keyed on the refresh callback's identity. Only a change to
  // `q` alone gets the 300ms debounce (matching CoverLettersView's chip vs.
  // typing split) — chip/select filters and the initial mount fetch fire with
  // a 0ms timer instead. Either way the timer+cleanup pairing is what
  // guarantees a single fetch under StrictMode's mount→cleanup→mount replay:
  // the first pass's timer is always cancelled before the second pass's runs.
  const prevFiltersRef = useRef({ status, personType, q });
  useEffect(() => {
    const prev = prevFiltersRef.current;
    const onlyQChanged = prev.status === status && prev.personType === personType && prev.q !== q;
    prevFiltersRef.current = { status, personType, q };
    const t = window.setTimeout(refreshPeople, onlyQChanged ? 300 : 0);
    return () => window.clearTimeout(t);
  }, [refreshPeople, status, personType, q]);

  function applyPersonUpdate(updated: NetworkPerson) {
    setPeople((prev) => prev?.map((p) => (p.id === updated.id ? updated : p)) ?? prev);
  }

  async function handleDeletePerson(person: NetworkPerson) {
    await api.network.deletePerson(person.id);
    setPeople((prev) => prev?.filter((p) => p.id !== person.id) ?? prev);
    setExpandedId((id) => (id === person.id ? null : id));
  }

  async function redraft(person: NetworkPerson) {
    setRedraftingIds((s) => new Set(s).add(person.id));
    try {
      await api.network.enrich(person.id);
    } catch (e) {
      setError((e as Error).message);
      setRedraftingIds((s) => {
        const next = new Set(s);
        next.delete(person.id);
        return next;
      });
      return;
    }

    const snapshot = (p: NetworkPerson) =>
      JSON.stringify({
        summary: p.summary,
        connection_note: p.connection_note,
        followup: p.followup,
        match_signals: p.match_signals,
      });
    const before = snapshot(person);

    const refetch = async (): Promise<NetworkPerson | null> => {
      try {
        const list = await api.network.people({ company: person.company });
        const updated = list.find((p) => p.id === person.id) ?? null;
        if (mountedRef.current && updated) applyPersonUpdate(updated);
        return updated;
      } catch {
        return null;
      }
    };

    const finishRedrafting = () => {
      if (!mountedRef.current) return;
      setRedraftingIds((s) => {
        const next = new Set(s);
        next.delete(person.id);
        return next;
      });
    };

    // Two-shot refresh: check at 3s, and once more at 10s only if the first
    // check found nothing changed yet. No infinite poll.
    window.setTimeout(async () => {
      if (!mountedRef.current) return;
      const updated = await refetch();
      if (updated && snapshot(updated) !== before) {
        finishRedrafting();
        return;
      }
      window.setTimeout(async () => {
        if (!mountedRef.current) return;
        await refetch();
        finishRedrafting();
      }, 7000);
    }, 3000);
  }

  // --- discover ------------------------------------------------------------

  // Pick up an already-running discovery (e.g. the page was reloaded mid-run)
  // so the progress card and polling resume instead of the button looking idle.
  // Routed through the same set/clear timer pattern as targets/people above so
  // StrictMode's mount→cleanup→mount replay can only leave one fetch in flight.
  useEffect(() => {
    const t = window.setTimeout(() => {
      api.network
        .discoverStatus()
        .then((s) => {
          if (mountedRef.current && s.running) {
            setDiscoverElapsed(0);
            setDiscover({ running: true, done: s.done, total: s.total });
          }
        })
        .catch(() => {});
    }, 0);
    return () => window.clearTimeout(t);
  }, []);

  useEffect(() => {
    if (!discover?.running) return;
    let cancelled = false;
    const tick = () => {
      api.network
        .discoverStatus()
        .then((s) => {
          if (cancelled || !mountedRef.current) return;
          setDiscover({ running: s.running, done: s.done, total: s.total });
          if (!s.running) {
            refreshPeopleRef.current();
            if (s.last_error) setError(s.last_error);
          }
        })
        .catch((e) => {
          if (cancelled || !mountedRef.current) return;
          setError((e as Error).message);
          setDiscover((d) => (d ? { ...d, running: false } : d));
        });
    };
    tick();
    const poll = window.setInterval(tick, 2000);
    const secTimer = window.setInterval(() => setDiscoverElapsed((s) => s + 1), 1000);
    return () => {
      cancelled = true;
      window.clearInterval(poll);
      window.clearInterval(secTimer);
    };
  }, [discover?.running]);

  async function runDiscover() {
    setError(null);
    try {
      await api.network.discover();
      setDiscoverElapsed(0);
      setDiscover({ running: true, done: 0, total: 0 });
    } catch (e) {
      setError((e as Error).message);
    }
  }

  // --- target actions --------------------------------------------------

  async function toggleTarget(t: NetworkTarget) {
    try {
      const updated = await api.network.patchTarget(t.id, !t.active);
      setTargets((prev) => prev?.map((x) => (x.id === t.id ? updated : x)) ?? prev);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function removeTarget(t: NetworkTarget) {
    await api.network.deleteTarget(t.id);
    setTargets((prev) => prev?.filter((x) => x.id !== t.id) ?? prev);
  }

  async function addTarget() {
    if (!newCompany.trim()) return;
    try {
      await api.network.addTarget({ company: newCompany.trim(), role_type: newRoleType.trim() || undefined });
      setNewCompany("");
      setNewRoleType("");
      refreshTargets();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  // --- grouping ----------------------------------------------------------

  const grouped: [string, NetworkPerson[]][] = [];
  if (people) {
    const byCompany = new Map<string, NetworkPerson[]>();
    for (const p of people) {
      if (!byCompany.has(p.company)) byCompany.set(p.company, []);
      byCompany.get(p.company)!.push(p);
    }
    grouped.push(...[...byCompany.entries()].sort((a, b) => a[0].localeCompare(b[0])));
  }

  const phaseText =
    discover?.total ? `Searching ${Math.min(discover.done + 1, discover.total)} of ${discover.total} companies…` : "Starting discovery…";

  // `people` is fetched pre-filtered from the server (status/personType/q are
  // query params, not a client-side filter over a held full list) — so an
  // empty result can't tell "truly no people saved" apart from "filters
  // matched nothing" on its own. Only claim "no people saved" when no filter
  // is active; otherwise say the filters found nothing, which stays true
  // regardless of how many people are actually saved.
  const noFilters = !status && !personType && !q.trim();

  return (
    <section>
      <div className="view-head">
        <h2>Network</h2>
      </div>

      {error && (
        <p className="error" onClick={() => setError(null)}>
          {error}
        </p>
      )}

      <div className="panel">
        <div className="category-head">Targets {targets && <span className="count">{targets.length}</span>}</div>
        {targets === null ? (
          <div className="skeleton" style={{ height: 40 }} />
        ) : (
          <div className="network-targets">
            {targets.map((t) => (
              <span
                key={t.id}
                className={`target-chip${t.source === "application" ? " derived" : ""}${!t.active ? " inactive" : ""}`}
                title={t.source === "application" ? "from your applications" : undefined}
                onClick={() => toggleTarget(t)}
              >
                {t.company}
                {t.role_type ? ` · ${t.role_type}` : ""}
                {t.source === "manual" && (
                  <ConfirmButton
                    ariaLabel={`Delete target ${t.company}`}
                    confirmText="Delete?"
                    onConfirm={() => removeTarget(t)}
                    onError={(e) => setError(e.message)}
                  >
                    ✕
                  </ConfirmButton>
                )}
              </span>
            ))}
            <span className="target-form">
              <input
                placeholder="Company"
                value={newCompany}
                onChange={(e) => setNewCompany(e.target.value)}
                style={{ width: 140 }}
              />
              <input
                placeholder="Role type (optional)"
                value={newRoleType}
                onChange={(e) => setNewRoleType(e.target.value)}
                style={{ width: 140 }}
              />
              <button onClick={addTarget} disabled={!newCompany.trim()}>
                Add
              </button>
            </span>
          </div>
        )}
        {targets !== null && targets.length === 0 && (
          <p className="empty">
            Add a company above or apply to jobs — applied companies appear here automatically.
          </p>
        )}
      </div>

      <div className="panel">
        <div className="view-head">
          <h3>Discover</h3>
          <button className="primary" onClick={runDiscover} disabled={!!discover?.running}>
            {discover?.running ? "Discovering…" : "Discover"}
          </button>
        </div>
        {discover?.running && (
          <div className="gen-card">
            <div className="gen-head">
              <span className="gen-dots" aria-hidden="true">
                <span />
                <span />
                <span />
              </span>
              <span className="gen-phase">{phaseText}</span>
              <span className="meta">{discoverElapsed}s</span>
            </div>
            <div className="skeleton gen-line" style={{ width: "92%" }} />
            <div className="skeleton gen-line" style={{ width: "100%" }} />
            <div className="skeleton gen-line" style={{ width: "70%" }} />
            <p className="gen-note">
              Searching the web for people at your target companies and drafting outreach. This
              can take a while per company.
            </p>
          </div>
        )}
      </div>

      <div className="panel">
        <div className="form-row">
          <input
            placeholder="Search name, headline, or company…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <label>
            Type
            <select value={personType} onChange={(e) => setPersonType(e.target.value)}>
              {PERSON_TYPE_FILTERS.map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="btn-row" style={{ marginTop: 12 }}>
          {STATUS_FILTERS.map(([v, l]) => (
            <button key={v} className={status === v ? "active-toggle" : ""} onClick={() => setStatus(v)}>
              {l}
            </button>
          ))}
        </div>
      </div>

      {people === null && <div className="skeleton" style={{ height: 160 }} />}

      {people !== null && people.length === 0 && (
        <EmptyState
          title={noFilters ? "No people saved" : "No matches"}
          hint={
            noFilters
              ? "Open a LinkedIn profile and use the extension popup's “Save this profile”, or add target companies above to discover people."
              : "No saved people match these filters — try clearing the search, status, or type."
          }
        />
      )}

      {grouped.map(([company, list]) => (
        <div className="category" key={company}>
          <div className="category-head">
            {company} <span className="count">{list.length}</span>
          </div>
          <div className="person-grid">
            {list.map((p) => (
              <PersonCard
                key={p.id}
                person={p}
                expanded={expandedId === p.id}
                redrafting={redraftingIds.has(p.id)}
                onToggle={() => setExpandedId((id) => (id === p.id ? null : p.id))}
                onUpdate={applyPersonUpdate}
                onDelete={() => handleDeletePerson(p)}
                onRedraft={() => redraft(p)}
                onError={setError}
              />
            ))}
          </div>
        </div>
      ))}
    </section>
  );
}

function PersonCard({
  person,
  expanded,
  redrafting,
  onToggle,
  onUpdate,
  onDelete,
  onRedraft,
  onError,
}: {
  person: NetworkPerson;
  expanded: boolean;
  redrafting: boolean;
  onToggle: () => void;
  onUpdate: (p: NetworkPerson) => void;
  onDelete: () => void | Promise<void>;
  onRedraft: () => void;
  onError: (msg: string) => void;
}) {
  const [connectionNote, setConnectionNote] = useState(person.connection_note ?? "");
  const [followup, setFollowup] = useState(person.followup ?? "");
  const [notes, setNotes] = useState(person.notes ?? "");
  const [saving, setSaving] = useState(false);

  // Belt-and-suspenders against a prompt-injected profile_url: the backend
  // now gates on "starts with http" before storing (network.py's
  // _upsert_person), but never trust a stored href without re-checking here
  // too — an old row written before that gate, or any future write path
  // that skips it, must still render as a plain span, not a clickable
  // javascript: link.
  const safeUrl = safeHttpUrl(person.profile_url);

  useEffect(() => {
    setConnectionNote(person.connection_note ?? "");
    setFollowup(person.followup ?? "");
    setNotes(person.notes ?? "");
  }, [person.id, person.connection_note, person.followup, person.notes]);

  async function saveDrafts() {
    setSaving(true);
    try {
      onUpdate(await api.network.patchPerson(person.id, { connection_note: connectionNote, followup }));
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function saveNotesOnBlur() {
    if (notes === (person.notes ?? "")) return;
    try {
      onUpdate(await api.network.patchPerson(person.id, { notes }));
    } catch (e) {
      onError((e as Error).message);
    }
  }

  async function setPersonStatus(value: string) {
    try {
      onUpdate(await api.network.patchPerson(person.id, { status: value }));
    } catch (e) {
      onError((e as Error).message);
    }
  }

  return (
    <div className="person-card">
      <div className="person-card-head" onClick={onToggle}>
        <div className="person-card-name">
          {safeUrl ? (
            <a href={safeUrl} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>
              {person.name}
            </a>
          ) : (
            person.name
          )}
          <span className="source-glyph" title={person.source}>
            {SOURCE_GLYPH[person.source] ?? ""}
          </span>
        </div>
        {person.headline && <p className="meta">{person.headline}</p>}
        <div className="btn-row">
          <span className="chip">{PERSON_TYPE_LABELS[person.person_type] ?? person.person_type}</span>
          {person.match_signals.slice(0, 3).map((s, i) => (
            <span className="chip signal-chip" key={i}>
              {s.signal}
            </span>
          ))}
          <span className="chip">{STATUS_LABELS[person.status] ?? person.status}</span>
        </div>
      </div>

      {expanded && (
        <div className="person-card-body" onClick={(e) => e.stopPropagation()}>
          {person.summary && <p className="meta">{person.summary}</p>}
          {person.evidence_urls.length > 0 && (
            <p className="meta">
              Evidence:{" "}
              {person.evidence_urls.filter(safeHttpUrl).map((u) => (
                <a key={u} href={u} target="_blank" rel="noreferrer" style={{ marginRight: 10 }}>
                  {safeHost(u)}
                </a>
              ))}
            </p>
          )}

          <label>
            Connection note draft{" "}
            <span className={`char-count${connectionNote.length > 300 ? " over" : ""}`}>
              {connectionNote.length}/300
            </span>
            <textarea rows={4} value={connectionNote} onChange={(e) => setConnectionNote(e.target.value)} />
          </label>
          <div className="btn-row">
            <button className="ghost" onClick={() => navigator.clipboard.writeText(connectionNote)}>
              Copy
            </button>
          </div>

          <label>
            Follow-up draft
            <textarea rows={4} value={followup} onChange={(e) => setFollowup(e.target.value)} />
          </label>
          <div className="btn-row">
            <button className="ghost" onClick={() => navigator.clipboard.writeText(followup)}>
              Copy
            </button>
          </div>

          <div className="btn-row">
            <button className="primary" onClick={saveDrafts} disabled={saving}>
              {saving ? "Saving…" : "Save drafts"}
            </button>
            <button onClick={onRedraft} disabled={redrafting}>
              {redrafting ? "Re-drafting…" : "Re-draft"}
            </button>
          </div>

          <div className="btn-row">
            {STATUS_ACTIONS.map(([value, label]) => (
              <button
                key={value}
                className={person.status === value ? "active-toggle" : ""}
                onClick={() => setPersonStatus(value)}
              >
                {label}
              </button>
            ))}
          </div>

          <label>
            Notes
            <textarea
              rows={2}
              value={notes}
              placeholder="Where you met, referral chain, reminders…"
              onChange={(e) => setNotes(e.target.value)}
              onBlur={saveNotesOnBlur}
            />
          </label>

          <div className="btn-row">
            <ConfirmButton
              ariaLabel={`Delete ${person.name}`}
              confirmText="Delete contact?"
              onConfirm={onDelete}
              onError={(e) => onError(e.message)}
            >
              Delete
            </ConfirmButton>
          </div>
        </div>
      )}
    </div>
  );
}
