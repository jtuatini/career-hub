// Popup: explicit start controls (full pipeline vs fill-only) plus stop and
// reconnect for the active tab's session. All real work stays in background.js.

const $ = (id) => document.getElementById(id);

// Multi-page ATS wizards hop origins mid-flow (company site → myworkdayjobs.com),
// which revokes activeTab and strands the session until a manual reconnect.
// Starting a session asks once for persistent access to the big ATS hosts plus
// the current site; declining is fine — activeTab keeps working as before.
const ATS_ORIGINS = [
  "https://*.myworkdayjobs.com/*",
  "https://*.greenhouse.io/*",
  "https://jobs.lever.co/*",
  "https://*.ashbyhq.com/*",
  "https://*.smartrecruiters.com/*",
  "https://*.icims.com/*",
];

async function grantSiteAccess(tab) {
  const origins = [...ATS_ORIGINS];
  try { origins.push(new URL(tab.url).origin + "/*"); } catch {}
  try { await chrome.permissions.request({ origins }); } catch {}
}

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

function show(id) {
  for (const section of ["idle", "active", "nopage"]) {
    $(section).classList.toggle("hidden", section !== id);
  }
}

async function init() {
  const tab = await activeTab();
  if (!tab?.id || !/^https?:/.test(tab.url ?? "")) {
    show("nopage");
    return;
  }

  // Independent of the idle/active/nopage sections below: shows whenever the
  // open tab is a LinkedIn profile, regardless of whether a session is active.
  if (/^https:\/\/(www\.)?linkedin\.com\/in\//.test(tab.url)) {
    $("capture").classList.remove("hidden");
    $("save-profile").onclick = async () => {
      $("capture-result").textContent = "Saving…";
      const resp = await chrome.runtime.sendMessage({ type: "popup_capture", tabId: tab.id });
      $("capture-result").textContent = resp?.ok
        ? `Saved ${resp.name} — see the Network tab.`
        : `Couldn't capture: ${resp?.error ?? "unknown error"}`;
    };
  }

  const state = await chrome.runtime.sendMessage({ type: "popup_state", tabId: tab.id });
  if (state?.active) {
    show("active");
    $("stage").textContent = state.stage || "running";
    $("reconnect").classList.toggle("hidden", state.alive);
    $("reconnect").onclick = async () => {
      await grantSiteAccess(tab);
      await chrome.runtime.sendMessage({ type: "popup_start", tabId: tab.id });
      window.close();
    };
    $("stop").onclick = async () => {
      await chrome.runtime.sendMessage({ type: "popup_stop", tabId: tab.id });
      window.close();
    };
    // Tailor-only runs: once the résumé doc exists, offer the PDF here — the
    // fill step may have found no upload slot on the page.
    if (state.mode === "tailor_only" && state.resumeDocId) {
      $("download-resume").classList.remove("hidden");
      $("download-resume").onclick = async () => {
        await chrome.runtime.sendMessage({ type: "popup_download", docId: state.resumeDocId });
        window.close();
      };
    }
  } else {
    show("idle");
    // Per-feature switches for full-pipeline runs; persisted across sessions.
    // Cover letters ship default-off — most postings don't want one.
    const DEFAULTS = { tailor_resume: true, cover_letter: false, answer_questions: true };
    const stored = (await chrome.storage.local.get("applyOptions")).applyOptions ?? {};
    const opts = { ...DEFAULTS, ...stored };
    const boxes = {
      tailor_resume: $("opt-tailor"),
      cover_letter: $("opt-cover"),
      answer_questions: $("opt-answers"),
    };
    for (const [key, box] of Object.entries(boxes)) {
      box.checked = opts[key];
      box.onchange = async () => {
        opts[key] = box.checked;
        await chrome.storage.local.set({ applyOptions: opts });
      };
    }
    $("run-pipeline").onclick = async () => {
      await grantSiteAccess(tab);
      await chrome.runtime.sendMessage({ type: "popup_start", tabId: tab.id, mode: "full", options: opts });
      window.close();
    };
    $("fill-only").onclick = async () => {
      await grantSiteAccess(tab);
      await chrome.runtime.sendMessage({ type: "popup_start", tabId: tab.id, mode: "fill_only" });
      window.close();
    };
    $("tailor-only").onclick = async () => {
      await grantSiteAccess(tab);
      await chrome.runtime.sendMessage({ type: "popup_start", tabId: tab.id, mode: "tailor_only" });
      window.close();
    };
  }
}

$("settings").onclick = (e) => {
  e.preventDefault();
  chrome.runtime.openOptionsPage();
};

init();
