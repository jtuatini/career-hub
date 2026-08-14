// Orchestration relay: owns the ApplySession lifecycle, polls status, fetches
// PDFs (host permission lives here), and survives popup-less operation.
// All intelligence is in the local backend; this file only relays.

const API = "http://127.0.0.1:8321";
const POLL_MS = 1500;

// In-flight guard for requestPlan: at most one snapshot->plan round trip may
// run per tab at a time (poll()'s ready-transition, the content script's
// page_changed message, and webNavigation.onCommitted can all fire close
// together). Deliberately in-memory, NOT chrome.storage.session: the check
// and the set must be synchronous with no await between them to close the
// race, and a killed/restarted service worker should forget in-flight state
// rather than get permanently stuck thinking a tab is still "planning".
const planningTabs = new Set();

// In-memory guard mirroring planningTabs: at most one self-perpetuating poll()
// chain may run per tab. The 1-minute resurrection alarm calls poll(tabId) for
// every session in storage regardless of whether a live chain is already
// ticking away via its own setTimeout recursion — without this guard, each
// alarm firing on a tab that's still being polled normally would spawn a
// second (third, ...) concurrent chain that never stops (only terminal states
// end a chain; every other status just reschedules itself forever). Cleared
// whenever a chain actually terminates (done/stopped/error/missing session),
// so the alarm only resurrects chains that have genuinely died.
const pollingTabs = new Set();

async function token() {
  return (await chrome.storage.local.get("apiToken")).apiToken ?? "";
}

async function api(path, options = {}) {
  const resp = await fetch(`${API}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", "X-Copilot-Token": await token() },
  });
  if (!resp.ok) {
    let detail = resp.statusText;
    try { detail = (await resp.json()).detail ?? detail; } catch {}
    throw new Error(detail);
  }
  return resp.status === 204 ? null : resp.json();
}

async function sessions() {
  return (await chrome.storage.session.get("apply")).apply ?? {};
}

async function setSession(tabId, data) {
  const all = await sessions();
  if (data === null) delete all[tabId];
  else all[tabId] = { ...(all[tabId] ?? {}), ...data };
  await chrome.storage.session.set({ apply: all });
}

function send(tabId, msg) {
  return chrome.tabs.sendMessage(tabId, msg).catch(() => {});
}

async function inject(tabId) {
  await chrome.scripting.executeScript({
    target: { tabId },
    files: ["submit_denylist.js", "content.js"],
  });
}

// The toolbar icon opens popup.html — starting is always an explicit button
// press there (full pipeline or fill-only), never a side effect of the click.

// Reconnect a session whose content script became unreachable (cross-origin
// wizard hop revoked activeTab): re-inject and resume rather than stop — the
// backend session is still alive and progress would otherwise be lost.
async function reconnect(tabId) {
  try {
    await inject(tabId);
    chrome.action.setBadgeText({ tabId, text: "" });
    chrome.action.setTitle({ tabId, title: "" });
    requestPlan(tabId);
    poll(tabId);
  } catch (e) {
    // Still unreachable (e.g. still on a disallowed page) — nothing more
    // we can do from here; report it rather than silently stopping.
    send(tabId, { type: "error", error: e.message });
  }
}

// Create a fresh backend session for this tab. Used by the popup's start
// buttons and by the overlay's Retry when session creation itself failed
// (401/backend down) — in that case there is no session row to /retry, so we
// start over. `mode` is "full" (pipeline) or "fill_only" (profile fill, no
// generated documents).
// The user's persisted per-feature toggles (popup). Every session-creating
// path must honor them — the popup passes them explicitly, but the overlay's
// Retry-from-scratch (and any future caller) falls back to storage here so
// an unchecked box can never be bypassed by a side entrance.
async function savedOptions() {
  return (await chrome.storage.local.get("applyOptions")).applyOptions ?? null;
}

async function startSession(tab, mode = "full", options = null) {
  try {
    await inject(tab.id);
    const snap = await chrome.tabs.sendMessage(tab.id, { type: "snapshot" });
    const body = {
      url: tab.url, page_text: snap.page_text, fields: snap.fields, buttons: snap.buttons,
      mode,
    };
    const opts = options ?? (await savedOptions());
    if (opts) body.options = opts;
    const created = await api("/api/apply/sessions", {
      method: "POST",
      body: JSON.stringify(body),
    });
    await setSession(tab.id, { id: created.id, filling: false, mode });
    poll(tab.id);
  } catch (e) {
    send(tab.id, { type: "error", error: e.message });
  }
}

async function stopSession(tabId) {
  const s = (await sessions())[tabId];
  if (s) await api(`/api/apply/sessions/${s.id}/stop`, { method: "POST" }).catch(() => {});
  await setSession(tabId, null);
  planningTabs.delete(tabId);
  pollingTabs.delete(tabId);
  chrome.action.setBadgeText({ tabId, text: "" });
  send(tabId, { type: "stopped" });
}

// Public entry point: guarded so calling poll() on a tab that already has a
// live chain (started by a previous poll() call, still self-rescheduling via
// setTimeout below) is a no-op. Only the first caller — the initial
// chrome.action.onClicked/retry kickoff, or the alarm resurrecting a dead
// chain — actually starts ticking.
async function poll(tabId) {
  if (pollingTabs.has(tabId)) return;
  pollingTabs.add(tabId);
  await pollTick(tabId);
}

// The chain's actual recursive step. Reschedules itself directly (NOT via
// poll()) so the guard above doesn't block its own continuation; clears
// pollingTabs at every point the chain terminates.
async function pollTick(tabId) {
  const s = (await sessions())[tabId];
  if (!s) { pollingTabs.delete(tabId); return; }
  let st;
  try {
    st = await api(`/api/apply/sessions/${s.id}`);
  } catch (e) {
    send(tabId, { type: "error", error: e.message });
    pollingTabs.delete(tabId);
    return;
  }
  send(tabId, { type: "status", session: st });
  chrome.action.setBadgeText({
    tabId,
    text: st.status === "error" ? "!" : st.stage === "ready" || st.status === "done" ? "✓" : `${Math.round(st.progress * 100)}`,
  });
  if (st.status === "done" || st.status === "stopped") {
    // Terminal, non-recoverable state: forget this tab's session so the
    // 1-minute resurrection alarm doesn't keep polling a dead session forever.
    await setSession(tabId, null);
    planningTabs.delete(tabId);
    pollingTabs.delete(tabId);
    chrome.action.setBadgeText({ tabId, text: "" });
    return;
  }
  if (st.status === "error") {
    // Keep the session entry — the overlay's Retry needs it — but end this
    // chain; the alarm (or an explicit retry) starts a fresh one.
    pollingTabs.delete(tabId);
    return;
  }
  if (st.stage === "ready" && !s.filling) {
    await setSession(tabId, { filling: true });
    requestPlan(tabId);
  }
  setTimeout(() => pollTick(tabId), POLL_MS);
}

async function requestPlan(tabId) {
  const s = (await sessions())[tabId];
  if (!s || planningTabs.has(tabId)) return; // see planningTabs guard above
  planningTabs.add(tabId);
  try {
    const snap = await chrome.tabs.sendMessage(tabId, { type: "snapshot" }).catch(() => null);
    if (!snap) return;
    const cur = (await sessions())[tabId];
    if (!cur) return; // session ended (stopped/tab closed) while we awaited the snapshot
    const { actions } = await api(`/api/apply/sessions/${cur.id}/page`, {
      method: "POST",
      body: JSON.stringify({ url: snap.url, fields: snap.fields, buttons: snap.buttons }),
    });
    // Re-check: the user may have clicked Stop while the /page request (which
    // can involve an engine call) was in flight. Don't execute a plan against
    // a session that no longer exists.
    if (!(await sessions())[tabId]) return;
    send(tabId, { type: "plan", actions });
  } catch (e) {
    send(tabId, { type: "error", error: e.message });
  } finally {
    planningTabs.delete(tabId);
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  // Only our own extension's pages/scripts — never another extension.
  if (sender.id !== chrome.runtime.id) return false;
  // Popup messages carry their own tabId (the popup has no sender.tab).
  // sender.tab must be absent: a content script — which runs on third-party
  // job sites — must not be able to invoke popup powers like executeScript
  // against arbitrary tabs.
  if (typeof msg.type === "string" && msg.type.startsWith("popup_") && !sender.tab) {
    (async () => {
      const tabId = msg.tabId;
      if (msg.type === "popup_state") {
        const s = (await sessions())[tabId];
        if (!s) {
          sendResponse({ active: false });
          return;
        }
        const alive = !!(await chrome.tabs.sendMessage(tabId, { type: "ping" }).catch(() => null));
        let stage = "running";
        let resumeDocId = null;
        try {
          const st = await api(`/api/apply/sessions/${s.id}`);
          stage = st.status === "error" ? "error" : st.stage;
          resumeDocId = st.resume_doc_id ?? null;
        } catch {}
        sendResponse({ active: true, alive, stage, mode: s.mode ?? null, resumeDocId });
      } else if (msg.type === "popup_start") {
        if ((await sessions())[tabId]) {
          await reconnect(tabId);
        } else {
          const tab = await chrome.tabs.get(tabId).catch(() => null);
          if (tab && /^https?:/.test(tab.url ?? "")) {
            await startSession(tab, msg.mode ?? "full", msg.options ?? null);
          }
        }
        sendResponse({ ok: true });
      } else if (msg.type === "popup_stop") {
        await stopSession(tabId);
        sendResponse({ ok: true });
      } else if (msg.type === "popup_capture") {
        try {
          const [{ result }] = await chrome.scripting.executeScript({
            target: { tabId: msg.tabId },
            func: () => {
              // Header block of the OPEN profile only — no walking, no lists.
              const name = document.querySelector("h1")?.innerText.trim() ?? "";
              const headline = document.querySelector(".text-body-medium.break-words")?.innerText.trim()
                ?? document.querySelector("[data-generated-suggestion-target]")?.innerText.trim() ?? "";
              // Named `loc`, not `location` — a local `location` would shadow
              // `window.location` for the rest of this function, breaking the
              // URL capture below.
              const loc = document.querySelector(".text-body-small.inline.t-black--light.break-words")?.innerText.trim() ?? "";
              const company = document.querySelector('[aria-label*="Current company"]')?.innerText.trim()
                ?? document.querySelector("ul.pv-text-details__right-panel li")?.innerText.trim() ?? "";
              return { name, headline, location: loc, company, url: window.location.href.split("?")[0] };
            },
          });
          if (!result?.name) throw new Error("No name found on this page");
          const created = await api("/api/network/people", {
            method: "POST",
            body: JSON.stringify({
              name: result.name, headline: result.headline || undefined,
              company: result.company || "Unknown", location: result.location || undefined,
              profile_url: result.url, source: "linkedin_capture",
            }),
          });
          sendResponse({ ok: true, name: result.name, id: created.id });
        } catch (e) {
          sendResponse({ error: e.message });
        }
      } else if (msg.type === "popup_download") {
        try {
          const t = await api(`/api/docs/${msg.docId}/pdf-ticket`);
          await chrome.tabs.create({ url: `${API}${t.url}` });
          sendResponse({ ok: true });
        } catch (e) {
          sendResponse({ error: e.message });
        }
      }
    })();
    return true; // async sendResponse
  }
  const tabId = sender.tab?.id;
  if (!tabId) return false;
  (async () => {
    if (msg.type === "page_changed") {
      requestPlan(tabId);
    } else if (msg.type === "report") {
      const s = (await sessions())[tabId];
      if (s) {
        await api(`/api/apply/sessions/${s.id}/report`, {
          method: "POST", body: JSON.stringify(msg.payload),
        }).catch(() => {});
      }
    } else if (msg.type === "fetch_pdf") {
      try {
        const t = await api(`/api/docs/${msg.docId}/pdf-ticket`);
        const resp = await fetch(`${API}${t.url}`);
        const bytes = new Uint8Array(await resp.arrayBuffer());
        let bin = "";
        for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
        sendResponse({ b64: btoa(bin) });
      } catch (e) {
        sendResponse({ error: e.message });
      }
    } else if (msg.type === "stop") {
      stopSession(tabId);
    } else if (msg.type === "retry") {
      const s = (await sessions())[tabId];
      if (s) {
        try {
          // Refresh the session's per-feature switches from the user's saved
          // toggles — a retry re-runs missing stages, and stale options from
          // an older session must not resurrect e.g. cover-letter generation.
          // tailor_only sessions carry a hard preset; refreshing options from
          // the popup's saved toggles would re-enable cover letters/answers.
          const opts = s.mode === "tailor_only" ? null : await savedOptions();
          await api(`/api/apply/sessions/${s.id}/retry`, {
            method: "POST",
            body: JSON.stringify({ ...(msg.payload ?? {}), ...(opts ? { options: opts } : {}) }),
          });
          await setSession(tabId, { filling: false });
          poll(tabId);
        } catch (e) {
          // e.g. 409 "still running" — tell the user instead of doing nothing.
          send(tabId, { type: "error", error: e.message });
        }
      } else {
        // Session creation itself failed (bad token, backend down): there is
        // nothing to /retry — start the flow from scratch.
        const tab = await chrome.tabs.get(tabId).catch(() => null);
        if (tab) await startSession(tab);
      }
    } else if (msg.type === "open_app") {
      // Content scripts run on third-party sites: only ever open the local app.
      if (/^http:\/\/(localhost|127\.0\.0\.1):5173(\/|$)/.test(msg.url ?? "")) {
        chrome.tabs.create({ url: msg.url });
      }
    } else if (msg.type === "open_options") {
      chrome.runtime.openOptionsPage();
    }
  })();
  return msg.type === "fetch_pdf"; // keep the channel open for async sendResponse
});

// Tab closed mid-session: best-effort stop the backend session and forget it,
// so the resurrection alarm never polls a session whose tab no longer exists.
chrome.tabs.onRemoved.addListener(async (tabId) => {
  const s = (await sessions())[tabId];
  if (!s) return;
  await api(`/api/apply/sessions/${s.id}/stop`, { method: "POST" }).catch(() => {});
  await setSession(tabId, null);
  planningTabs.delete(tabId);
  pollingTabs.delete(tabId);
});

// Wizard page navigations: re-inject and continue while a session is active.
chrome.webNavigation.onCommitted.addListener(async ({ tabId, frameId }) => {
  if (frameId !== 0) return;
  const s = (await sessions())[tabId];
  if (!s || !s.filling) return;
  try {
    await inject(tabId);
    // Recovered cleanly — clear any stale "lost access" state a previous
    // cross-origin hop on this tab may have left behind.
    chrome.action.setBadgeText({ tabId, text: "" });
    chrome.action.setTitle({ tabId, title: "" });
    requestPlan(tabId);
  } catch {
    // activeTab is revoked by a cross-origin navigation: injection throws,
    // the overlay is gone, and without this the badge just freezes silently.
    // Surface it so the user knows to open the popup and hit Reconnect.
    chrome.action.setBadgeText({ tabId, text: "!" });
    chrome.action.setTitle({
      tabId,
      title: "Lost page access after navigation — open the popup to reconnect",
    });
  }
});

// Resurrection: if Chrome killed the worker, alarms restart polling.
chrome.alarms.create("apply-poll", { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener(async () => {
  for (const tabId of Object.keys(await sessions())) poll(Number(tabId));
});
