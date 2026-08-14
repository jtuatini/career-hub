// Content script: snapshots the page, renders the overlay, executes fill
// plans. SUBMIT GUARD (client layer): before clicking a plan's click_nav
// button, this file re-checks the live element against SUBMIT_DENYLIST
// (injected first) and the plan's expect_text (see __execute's click_nav
// branch). That guard covers plan-driven navigation clicks only — it does
// not cover __selectComboOption's internal option click; see the comment
// at that call site in __execute for the residual, inherited risk. This
// file dispatches no synthetic keyboard events except the single
// documented Escape inside __selectComboOption, and never calls
// form.submit()/requestSubmit().
// var/function declarations only — must tolerate re-injection.

var __copilotFields = __copilotFields || [];
var __copilotButtons = __copilotButtons || [];
var __copilotFieldMeta = __copilotFieldMeta || [];
var __copilotButtonMeta = __copilotButtonMeta || [];
var __copilotOverlay = __copilotOverlay || null;
var __copilotEssays = __copilotEssays || {}; // index -> {label, draft}
var __copilotWatch = __copilotWatch || null;

/* ---------- extraction (from extract.js, unchanged logic) ---------- */
// Injected into the active tab to extract the job posting.
// Returns {company, title, jd} using site-specific selectors first,
// then a generic fallback on the page's main content.
function __extractJobPosting() {
  const text = (el) => (el ? el.innerText.trim() : "");
  const meta = (name) =>
    document.querySelector(`meta[property="${name}"], meta[name="${name}"]`)?.content ?? "";

  const host = location.hostname;
  let jd = "";
  let company = "";
  let title = "";

  const SITE_SELECTORS = [
    // Greenhouse
    { match: /greenhouse\.io|greenhouse\.dev/, jd: "#content, .job__description, [class*='job-post']" },
    // Lever
    { match: /lever\.co/, jd: ".posting-page, .content .section-wrapper" },
    // Workday
    { match: /myworkdayjobs\.com|workday/, jd: "[data-automation-id='jobPostingDescription']" },
    // Ashby
    { match: /ashbyhq\.com/, jd: "[class*='_description'], [class*='JobPosting']" },
    // SmartRecruiters
    { match: /smartrecruiters\.com/, jd: "[itemprop='description'], .job-sections" },
  ];

  for (const site of SITE_SELECTORS) {
    if (site.match.test(host)) {
      jd = text(document.querySelector(site.jd));
      break;
    }
  }
  if (!jd) {
    // Generic: prefer semantic containers, fall back to whole body.
    jd = text(document.querySelector("main")) || text(document.querySelector("article")) || text(document.body);
  }
  // Keep the payload sane on giant pages.
  jd = jd.slice(0, 30000);

  title =
    text(document.querySelector("h1")) ||
    meta("og:title") ||
    document.title;

  company =
    meta("og:site_name") ||
    (/greenhouse|lever|ashby|smartrecruiters/.test(host)
      ? (location.pathname.split("/").filter(Boolean)[0] ?? "")
      : host.replace(/^www\.|\.(com|io|co|org|net)$/g, "").split(".")[0]);

  // Workday puts the company in the subdomain: acme.wd5.myworkdayjobs.com
  if (/myworkdayjobs\.com/.test(host)) company = host.split(".")[0];

  return { company, title: title.slice(0, 200), jd };
}

/* ---------- field primitives (from autofill.js, unchanged logic) ---------- */
function __resolveLabelledBy(el) {
  const labelledBy = el.getAttribute("aria-labelledby");
  if (!labelledBy) return "";
  return labelledBy
    .split(/\s+/)
    .map((id) => document.getElementById(id)?.textContent.trim() ?? "")
    .join(" ")
    .trim();
}

function __labelFor(el) {
  // ARIA reference (Workday's pattern for custom widgets).
  const own = __resolveLabelledBy(el);
  if (own) return own.slice(0, 200);
  if (el.id) {
    const lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
    if (lab) return lab.textContent.trim();
  }
  const wrapping = el.closest("label");
  if (wrapping) return wrapping.textContent.trim();
  // Workday labels GROUPS by putting aria-labelledby on a wrapper div (date
  // segments, search containers) — the control itself carries nothing.
  let node = el.parentElement;
  for (let depth = 0; node && depth < 4; depth++, node = node.parentElement) {
    const viaWrapper = __resolveLabelledBy(node);
    if (viaWrapper) return viaWrapper.slice(0, 200);
  }
  return __containerLabel(el);
}

function __containerLabel(el) {
  // Label rendered as a sibling inside the field container. Real ATSes
  // (Lever, Greenhouse custom questions) keep the question text 2-3 wrappers
  // above the control, outside any <label> — walk up, but stop as soon as a
  // container holds unrelated controls, or we'd grab another field's label.
  let node = el.parentElement;
  const ownName = el.getAttribute("name") || "";
  for (let depth = 0; node && node !== document.body && depth < 6; depth++, node = node.parentElement) {
    const controls = node.querySelectorAll("input, select, textarea");
    // Foreign = a control from a different field. Same non-empty name means
    // same group (radio clusters); NAMELESS siblings are distinct fields
    // (Workday's inputs carry no name at all), so they stop the walk too —
    // otherwise the whole step's first label would win.
    const foreign = [...controls].some(
      (c) => c !== el && ((c.getAttribute("name") || "") !== ownName || ownName === ""),
    );
    if (foreign) break;
    for (const lab of node.querySelectorAll("legend, [class*='question'], [class*='label'], label")) {
      const t = lab.textContent.trim();
      // A question block never contains the controls themselves.
      if (t.length >= 2 && !lab.contains(el) && !lab.querySelector("input, select, textarea")) {
        return t.slice(0, 200);
      }
    }
  }
  return "";
}

function __questionText(el) {
  // Group question for a radio/checkbox cluster: fieldset legend first, then
  // the container's question text — never the wrapping "Yes"/"No" label.
  const legend = el.closest("fieldset")?.querySelector("legend");
  if (legend) {
    const t = legend.textContent.trim();
    if (t) return t.slice(0, 200);
  }
  return __containerLabel(el);
}

function __visible(el) {
  const style = getComputedStyle(el);
  return (
    style.display !== "none" &&
    style.visibility !== "hidden" &&
    el.getBoundingClientRect().height > 0
  );
}

function __sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function __dispatchMouse(target) {
  // react-select-style widgets commit options on mousedown, native listboxes
  // on click — send the full press sequence so both take it. Mouse events on
  // [role="option"] only; see the residual-risk note at __execute's combobox
  // branch.
  for (const type of ["pointerdown", "mousedown", "mouseup", "click"]) {
    target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
  }
}

// Punctuation-insensitive: "University of Michigan - Ann Arbor" and
// "University of Michigan-Ann Arbor" must compare equal.
var __COMBO_NORM = (s) => (s || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();

function __comboScore(wanted, text) {
  // 0..1 similarity, mirroring the backend's option scorer: word-boundary
  // containment (never "no" inside "now"), token overlap, and a bigram-dice
  // character similarity; the max of the three.
  const v = __COMBO_NORM(wanted);
  const o = __COMBO_NORM(text);
  if (!v || !o) return 0;
  if (v === o) return 1;
  const [shorter, longer] = v.length <= o.length ? [v, o] : [o, v];
  let score = 0;
  const escaped = shorter.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  if (new RegExp(`\\b${escaped}\\b`).test(longer)) {
    score = 0.75 + 0.2 * (shorter.length / longer.length);
  }
  const tv = new Set(v.split(" "));
  const to = new Set(o.split(" "));
  let inter = 0;
  for (const t of tv) if (to.has(t)) inter += 1;
  score = Math.max(score, inter / (tv.size + to.size - inter));
  if (tv.size >= 2 && inter > 0) {
    // Prefix credit: "co" completes to "colorado" — but only anchored by at
    // least one exact shared token, so "no" can never ride on "not".
    let cov = 0;
    for (const t of tv) {
      if (to.has(t)) { cov += 1; continue; }
      for (const u of to) {
        if ((t.length >= 2 && u.startsWith(t)) || (u.length >= 2 && t.startsWith(u))) { cov += 1; break; }
      }
    }
    score = Math.max(score, 0.75 * (cov / tv.size));
  }
  const grams = (s) => {
    const g = new Map();
    for (let i = 0; i < s.length - 1; i++) {
      const k = s.slice(i, i + 2);
      g.set(k, (g.get(k) || 0) + 1);
    }
    return g;
  };
  const ga = grams(v);
  const gb = grams(o);
  let common = 0;
  let total = 0;
  for (const [, c] of ga) total += c;
  for (const [, c] of gb) total += c;
  for (const [k, c] of ga) if (gb.has(k)) common += Math.min(c, gb.get(k));
  if (total > 0) score = Math.max(score, (2 * common) / total);
  // Values whose numbers differ ("May 2028" vs "May 2027") must never snap —
  // a silently wrong year is worse than an empty field.
  const dv = v.match(/\d+/g) || [];
  const dO = o.match(/\d+/g) || [];
  if (dv.length && dO.length && dv.join(",") !== dO.join(",")) return Math.min(score, 0.3);
  return score;
}

async function __selectComboOption(el, value) {
  // Two widget shapes share this driver: button-style comboboxes (Workday)
  // open on click; input-style autocompletes (Greenhouse school/location,
  // react-select) only load options in response to typed input — the
  // native-setter write fires the `input` event their filter listens to.
  // Autocomplete backends often find nothing for the FULL profile value
  // ("University of Michigan - Ann Arbor" vs their "Univ of Michigan"), so
  // typed queries fall back from the full value to shorter prefixes, and the
  // surfaced options are picked by similarity score, not exact text.
  const isInput = el.tagName === "INPUT";
  el.focus?.();
  el.click();
  const full = (value || "").trim();
  const words = full.split(/\s+/);
  const queries = isInput
    ? [...new Set([full, words.slice(0, 2).join(" "), words[0], full.slice(0, 4).trim()])].filter(Boolean)
    : [null];
  for (const query of queries) {
    if (isInput && query !== null) __setNativeValue(el, query);
    let roundsWithOptions = 0;
    for (let attempt = 0; attempt < 12; attempt++) {
      await __sleep(200);
      // Workday's searchable prompts render options as [data-automation-id=
      // "promptOption"], not [role="option"] — scan both shapes.
      const options = [
        ...document.querySelectorAll('[role="option"], [data-automation-id="promptOption"]'),
      ].filter(__visible);
      if (options.length === 0) continue;
      let best = null;
      let bestScore = 0;
      for (const o of options) {
        const s = __comboScore(full, o.textContent);
        if (s > bestScore) { best = o; bestScore = s; }
      }
      if (best && bestScore >= 0.6) {
        __dispatchMouse(best);
        await __sleep(120);
        return true;
      }
      // Options rendered but none plausible yet — async lists (school search)
      // may still be streaming in; give them a few more polls, then move to
      // the next (shorter) query.
      roundsWithOptions += 1;
      if (roundsWithOptions >= 4) break;
    }
    if (!isInput) break; // click-open widgets have one fixed option list
  }
  // Close whatever opened; leave the field for the human.
  // SUBMIT-GUARD EXCEPTION: this is the only synthetic KeyboardEvent dispatched
  // anywhere in the extension. It sends Escape to close a listbox this same
  // function opened a moment ago — Escape cannot submit a form, so it does not
  // violate the "no synthetic keyboard events" rule enforced by the grep guard.
  el.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  if (isInput) __setNativeValue(el, "");
  document.body.click();
  return false;
}

function __setNativeValue(el, value) {
  // React-controlled inputs ignore plain .value writes; go through the native
  // setter, then fire the events frameworks listen for.
  const proto =
    el.tagName === "TEXTAREA"
      ? window.HTMLTextAreaElement.prototype
      : el.tagName === "SELECT"
        ? window.HTMLSelectElement.prototype
        : window.HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
  if (setter) setter.call(el, value);
  else el.value = value;
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
}

function __isComboInput(el) {
  // Input-based comboboxes wear many disguises across ATS generations:
  // ARIA roles/attrs (react-select v5, Workday search boxes), aria-controls
  // pointing at a listbox, or only a library class name (older Greenhouse
  // select2 search fields). Missing one means we TYPE into a widget whose
  // state never commits — the classic "typed but nothing selected" failure.
  if (el.getAttribute("role") === "combobox") return true;
  if (el.getAttribute("aria-haspopup") === "listbox") return true;
  const auto = el.getAttribute("aria-autocomplete");
  if (auto === "list" || auto === "both") return true;
  if (el.hasAttribute("aria-expanded")) return true;
  const controls = el.getAttribute("aria-controls") || el.getAttribute("aria-owns") || "";
  if (/listbox|results|options|menu/i.test(controls)) return true;
  if (/select__input|select2-search|autocomplete|typeahead|combobox/i.test(el.className || "")) return true;
  if (/searchbox|searchinput/i.test(el.getAttribute("data-automation-id") || "")) return true; // Workday prompts
  return el.closest(
    '[role="combobox"], [class*="select__control"], [class*="select2-container"],'
    + ' [data-automation-id="multiSelectContainer"], [data-automation-id="multiselectInputContainer"]'
  ) !== null;
}

function __snapshot() {
  const SKIP_TYPES = new Set(["hidden", "submit", "button", "image", "reset", "password"]);
  __copilotFields = [];
  const fields = [];
  // One widget, one field: react-select renders several inputs inside one
  // [role="combobox"] wrapper — capture only the first, and keep the widget
  // scan below from re-adding the wrapper itself.
  const comboWrappers = new Set();
  for (const el of document.querySelectorAll("input, select, textarea")) {
    let type = (el.getAttribute("type") || el.tagName.toLowerCase()).toLowerCase();
    // File inputs are exempt from the visibility check: real ATS upload
    // widgets (Greenhouse "Attach", Workday "Select files") visually hide the
    // input behind a styled button, and DataTransfer assignment works on
    // hidden inputs. Attach actions only fire on resume/cover-hinted slots,
    // so stray hidden file inputs stay untouched.
    if (SKIP_TYPES.has(type) || (!__visible(el) && type !== "file")) continue;
    // Input-based ARIA comboboxes (react-select, Greenhouse's dropdowns) look
    // like plain text inputs but must be DRIVEN (open → pick an option), not
    // typed into — raw value writes never commit to the widget's state.
    if (el.tagName === "INPUT" && __isComboInput(el)) {
      type = "combobox";
      const wrapper = el.closest('[role="combobox"], [class*="select__control"], [class*="select2-container"]');
      if (wrapper && wrapper !== el) {
        if (comboWrappers.has(wrapper)) continue;
        comboWrappers.add(wrapper);
      }
    }
    const index = __copilotFields.length;
    __copilotFields.push(el);
    const d = {
      index, type,
      id: el.id || "", name: el.name || "",
      placeholder: el.placeholder || "",
      aria_label: el.getAttribute("aria-label") || "",
      automation_id: el.getAttribute("data-automation-id") || "",
      label: __labelFor(el),
    };
    if (el.tagName === "SELECT") {
      d.options = [...el.options].map((o) => o.textContent.trim()).filter(Boolean).slice(0, 50);
    }
    if (type === "radio" || type === "checkbox") {
      // The per-control label is just "Yes"/"No" — the backend groups radios
      // by name and needs the cluster's shared question to answer them.
      d.group_label = __questionText(el);
    }
    fields.push(d);
  }
  // Legacy styled-select libraries (select2, chosen, selectize, nice-select)
  // hide the real <select> behind a widget — invisible, so the scan above
  // skipped it, and the styled widget itself carries no form semantics. The
  // libraries listen for (jQuery-bridged) native change events, so setting
  // the hidden select's value still commits: capture it as a normal select.
  for (const el of document.querySelectorAll("select")) {
    if (__visible(el) || __copilotFields.includes(el)) continue;
    const companion = el.nextElementSibling;
    if (!companion || !/select2|chosen|selectize|nice-select/i.test(companion.className || "")) continue;
    const index = __copilotFields.length;
    __copilotFields.push(el);
    fields.push({
      index, type: "select", id: el.id || "", name: el.name || "",
      placeholder: "", aria_label: el.getAttribute("aria-label") || "",
      automation_id: el.getAttribute("data-automation-id") || "",
      label: __labelFor(el),
      options: [...el.options].map((o) => o.textContent.trim()).filter(Boolean).slice(0, 50),
    });
  }
  for (const el of document.querySelectorAll('[role="combobox"], [aria-haspopup="listbox"]')) {
    if (["INPUT", "SELECT", "TEXTAREA"].includes(el.tagName) || !__visible(el)) continue;
    if (comboWrappers.has(el)) continue; // inner input already captured above
    const index = __copilotFields.length;
    __copilotFields.push(el);
    fields.push({
      index, type: "combobox", id: el.id || "", name: el.getAttribute("name") || "",
      placeholder: "", aria_label: el.getAttribute("aria-label") || "",
      automation_id: el.getAttribute("data-automation-id") || "",
      label: __labelFor(el),
    });
  }
  __copilotButtons = [];
  const buttons = [];
  for (const el of document.querySelectorAll('button, input[type="submit"], input[type="button"], [role="button"]')) {
    if (!__visible(el)) continue;
    const index = __copilotButtons.length;
    __copilotButtons.push(el);
    buttons.push({
      index,
      text: (el.innerText || el.value || "").trim().slice(0, 80),
      aria_label: el.getAttribute("aria-label") || "",
      name: el.getAttribute("name") || "",
      value: el.value || "",
    });
  }
  // Posting pages often render "Apply now" as a plain link. Only apply-shaped
  // anchors are captured (nav menus would flood the snapshot otherwise).
  for (const el of document.querySelectorAll("a[href]")) {
    if (!__visible(el)) continue;
    const t = (el.innerText || "").trim();
    if (t.length > 40 || !/^apply\b/i.test(t)) continue;
    const index = __copilotButtons.length;
    __copilotButtons.push(el);
    buttons.push({ index, text: t.slice(0, 80), aria_label: el.getAttribute("aria-label") || "", name: "", value: "" });
  }
  const posting = __extractJobPosting();
  // Descriptors kept for re-finding: SPA frameworks (Workday) re-render whole
  // step sections mid-plan (e.g. after a multiselect pill commit), detaching
  // every element captured above.
  __copilotFieldMeta = fields;
  __copilotButtonMeta = buttons;
  return { url: location.href, page_text: posting.jd, fields, buttons };
}

function __refindField(index) {
  const meta = __copilotFieldMeta[index];
  if (!meta) return null;
  if (meta.id) {
    const byId = document.getElementById(meta.id);
    if (byId) return byId;
  }
  let candidates = [];
  if (meta.automation_id) {
    candidates = [...document.querySelectorAll(`[data-automation-id="${CSS.escape(meta.automation_id)}"]`)];
  }
  if (candidates.length === 0 && meta.name) {
    candidates = [...document.querySelectorAll(`[name="${CSS.escape(meta.name)}"]`)];
  }
  candidates = candidates.filter(__visible);
  if (candidates.length > 1 && meta.label) {
    const byLabel = candidates.find((c) => __labelFor(c) === meta.label);
    if (byLabel) return byLabel;
  }
  return candidates[0] || null;
}

function __refindButton(index) {
  const meta = __copilotButtonMeta[index];
  const want = ((meta && meta.text) || "").trim();
  if (!want) return null;
  for (const el of document.querySelectorAll('button, input[type="submit"], input[type="button"], [role="button"]')) {
    if (!__visible(el)) continue;
    if (((el.innerText || el.value || "").trim()) === want) return el;
  }
  return null;
}

/* ---------- overlay (shadow DOM) ---------- */
var __STAGES = [
  ["parsing", "Reading the job posting"],
  ["classifying", "Choosing your base resume"],
  ["tailoring", "Tailoring the resume"],
  ["cover_letter", "Writing the cover letter"],
  ["ready", "Filling the application"],
];

// Trusted Types: pages that enforce `require-trusted-types-for 'script'`
// apply their policy to our shadow-root innerHTML sink too. Where the CSP
// allows named policies, this passthrough policy lets the overlay render;
// where it doesn't (or a default policy sanitizes us away), __overlay()
// degrades to a non-visual stub instead of crashing — filling and the
// toolbar badge still work, only the floating panel is lost.
var __copilotTT = __copilotTT || null;
function __trustedHTML(s) {
  if (window.trustedTypes?.createPolicy) {
    try {
      __copilotTT = __copilotTT || trustedTypes.createPolicy("copilot-overlay", { createHTML: (h) => h });
      return __copilotTT.createHTML(s);
    } catch {
      // Policy name not allowlisted — fall through; assignment may still throw.
    }
  }
  return s;
}

function __overlay() {
  if (__copilotOverlay && document.contains(__copilotOverlay.host)) return __copilotOverlay;
  const host = document.createElement("div");
  host.id = "copilot-overlay-host";
  host.style.cssText = "position:fixed;top:16px;right:16px;z-index:2147483647;";
  const root = host.attachShadow({ mode: "open" });
  const markup = `
    <style>
      .panel { width: 300px; background: #1c1c1e; color: #f2f2f2; border-radius: 10px;
        font: 13px/1.45 -apple-system, system-ui, sans-serif; padding: 14px;
        box-shadow: 0 8px 30px rgba(0,0,0,.45); }
      .title { font-weight: 700; display: flex; justify-content: space-between; align-items: center; }
      .stages { margin: 10px 0 6px; padding: 0; list-style: none; }
      .stages li { padding: 2px 0; opacity: .45; }
      .stages li.on { opacity: 1; }
      .stages li.done { opacity: .8; }
      .stages li.done::before { content: "✓ "; color: #4cd964; }
      .stages li.on::before { content: "▸ "; color: #ffd60a; }
      .bar { height: 6px; background: #3a3a3c; border-radius: 3px; overflow: hidden; margin: 8px 0; }
      .bar > div { height: 100%; width: 0; background: #4cd964; transition: width .3s; }
      .action { min-height: 16px; color: #d0d0d0; }
      .err { color: #ff6b6b; white-space: pre-wrap; }
      button { background: #3a3a3c; color: #fff; border: 0; border-radius: 6px;
        padding: 5px 10px; margin: 6px 6px 0 0; cursor: pointer; font: inherit; }
      button.primary { background: #0a84ff; }
      .never { margin-top: 8px; color: #8e8e93; font-size: 11px; }
    </style>
    <div class="panel">
      <div class="title"><span>Application Copilot</span><button id="stop">Stop</button></div>
      <ul class="stages">${__STAGES.map(([k, t]) => `<li data-stage="${k}">${t}</li>`).join("")}</ul>
      <div class="bar"><div id="bar"></div></div>
      <div class="action" id="action"></div>
      <div class="err" id="err"></div>
      <div id="buttons"></div>
      <div id="review" style="display:none">
        <button id="review-letter">Review letter</button>
        <button id="reattach">Re-attach PDFs</button>
      </div>
      <div class="never">Fills and highlights only — you always click Submit.</div>
    </div>`;
  try {
    root.innerHTML = __trustedHTML(markup);
  } catch {
    // Enforcing page with no policy allowed: leave the shadow root empty.
  }
  document.documentElement.appendChild(host);
  // Null-safe element access: on Trusted-Types-sanitized pages the panel may
  // not exist — hand back a detached dummy so callers' wiring is a no-op.
  const byId = (id) => root.getElementById(id) ?? document.createElement("span");
  byId("stop").addEventListener("click", () => chrome.runtime.sendMessage({ type: "stop" }));
  byId("review-letter").addEventListener("click", () =>
    chrome.runtime.sendMessage({ type: "open_app", url: "http://localhost:5173" }));
  // After amending the letter in the app, a fresh plan re-attaches the updated PDFs.
  byId("reattach").addEventListener("click", () =>
    chrome.runtime.sendMessage({ type: "page_changed" }));
  __copilotOverlay = { host, root, byId };
  return __copilotOverlay;
}

function __setStage(stage, progress) {
  const { root, byId } = __overlay();
  const order = __STAGES.map(([k]) => k);
  const at = order.indexOf(stage);
  root.querySelectorAll(".stages li").forEach((li, i) => {
    li.className = i < at ? "done" : i === at ? "on" : "";
  });
  byId("bar").style.width = `${Math.round((progress ?? 0) * 100)}%`;
  byId("review").style.display = stage === "ready" ? "block" : "none";
}

function __setAction(text) { __overlay().byId("action").textContent = text; }
function __setError(text, session) {
  const { byId } = __overlay();
  byId("err").textContent = text || "";
  const box = byId("buttons");
  box.replaceChildren();
  if (text) {
    const retry = document.createElement("button");
    retry.className = "primary";
    retry.textContent = "Retry";
    retry.addEventListener("click", () => { __setError(""); chrome.runtime.sendMessage({ type: "retry" }); });
    box.appendChild(retry);
    // Token/auth failures are fixed in the options page, but nothing else
    // points there (the old popup's inline token box is gone) — offer it.
    if (/token|401|unauthorized/i.test(text)) {
      const opts = document.createElement("button");
      opts.textContent = "Open settings";
      opts.addEventListener("click", () => chrome.runtime.sendMessage({ type: "open_options" }));
      box.appendChild(opts);
    }
  }
}

/* ---------- executor ---------- */
var __HL = {
  ok: "2px solid #2c7a3f", review: "2px solid #d9b44a", fail: "2px solid #c0392b",
};

function __isSubmitLike(el) {
  const hay = [el.innerText, el.value, el.getAttribute("aria-label"), el.getAttribute("name")]
    .map((s) => (s || "").trim().toLowerCase().replace(/\s+/g, " "))
    .filter(Boolean);
  return hay.some((t) => SUBMIT_DENYLIST.some((term) => new RegExp(`\\b${term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`).test(t)));
}

async function __execute(actions) {
  if (actions.length === 0) {
    // Backend's same-page replan guard returned nothing to do (mutation noise
    // or a page we already filled) — stay quiet, keep watching for a real
    // page change.
    __armPageWatch();
    return;
  }
  // Our own fills mutate the DOM — a live watcher would fire mid-execution
  // and trigger a re-snapshot that invalidates this plan's indexes. Every
  // exit path below re-arms.
  __disarmPageWatch();
  let done = 0;
  const results = [];
  const fillables = actions.filter((a) => ["fill", "select", "combobox", "check", "attach"].includes(a.kind));
  for (const a of actions) {
    if (["fill", "select", "combobox", "check", "attach"].includes(a.kind)) {
      let el = __copilotFields[a.index];
      if (!el || !document.contains(el)) {
        // Step section re-rendered mid-plan — re-locate by snapshot identity.
        el = __refindField(a.index);
        if (el) __copilotFields[a.index] = el;
      }
      if (!el || !document.contains(el)) {
        results.push({ index: a.index, status: "failed" });
        continue;
      }
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      await __sleep(220);
      __setAction(`Filling: ${a.label || a.kind}`);
      let ok = true;
      try {
        if (a.kind === "fill") {
          __setNativeValue(el, a.value);
          if (el.tagName === "INPUT" && (a.value || "").trim()) {
            await __sleep(80);
            if ((el.value || "").trim() === "") {
              // The write didn't stick: native date/month inputs reject
              // non-conforming values (report honestly — retyping won't help),
              // and controlled widgets the combobox detection missed reset the
              // value on re-render — drive those like a combobox instead.
              ok = ["date", "month", "time", "week"].includes(el.type)
                ? false
                : await __selectComboOption(el, a.value);
            }
          }
          if (ok && a.essay) {
            __copilotEssays[a.index] = { label: a.label, draft: a.value };
          }
        } else if (a.kind === "select") {
          const wanted = __COMBO_NORM(a.value);
          let opt = [...el.options].find((o) => __COMBO_NORM(o.textContent) === wanted);
          if (!opt) {
            // The plan was validated against snapshot options, but live DOM
            // text can drift (required asterisks, nbsp) — take the closest
            // plausible option rather than failing the field.
            let bestScore = 0;
            for (const o of el.options) {
              const s = __comboScore(a.value, o.textContent);
              if (s > bestScore) { bestScore = s; opt = o; }
            }
            if (bestScore < 0.6) opt = null;
          }
          if (opt) __setNativeValue(el, opt.value);
          else ok = false;
        } else if (a.kind === "combobox") {
          // NOTE (submit guard, residual risk): __selectComboOption's internal
          // __dispatchMouse on a matched [role="option"] is NOT re-checked
          // against SUBMIT_DENYLIST — it only ever targets listbox options
          // opened by this same combobox, never a submit/nav control. The
          // click_nav guard below does not apply here.
          ok = await __selectComboOption(el, a.value);
        } else if (a.kind === "check") {
          // SUBMIT GUARD: only ever a real radio/checkbox input, never
          // anything submit-shaped (the denylist check the reserved-kind note
          // demanded). el.click() sets checked AND fires the input/change/
          // click events React-family frameworks listen for.
          const checkable =
            el instanceof HTMLInputElement && (el.type === "radio" || el.type === "checkbox");
          if (!checkable || __isSubmitLike(el)) {
            ok = false;
          } else {
            el.click();
            ok = el.checked;
          }
        } else if (a.kind === "attach") {
          ok = await __attach(el, a);
        }
      } catch {
        ok = false;
      }
      el.style.outline = ok ? (a.review ? __HL.review : __HL.ok) : __HL.fail;
      results.push({ index: a.index, status: ok ? (a.review ? "review" : "filled") : "failed" });
      done += 1;
      __overlay().byId("bar").style.width = `${Math.round((done / Math.max(fillables.length, 1)) * 100)}%`;
    } else if (a.kind === "click_nav") {
      await __reportEdits(results, false);
      let el = __copilotButtons[a.button_index];
      if (!el || !document.contains(el)) {
        el = __refindButton(a.button_index);
        if (el) __copilotButtons[a.button_index] = el;
      }
      const live = ((el?.innerText || el?.value || "").trim());
      if (!el || !document.contains(el) || __isSubmitLike(el) || (a.expect_text && live !== a.expect_text)) {
        __setAction("Navigation button changed — handing control to you.");
        if (el) { el.scrollIntoView({ behavior: "smooth", block: "center" }); el.style.outline = __HL.review; }
        __armPageWatch(); // their manual advance must still re-plan
        return;
      }
      __setAction(`Continuing: ${live}`);
      await __sleep(500);
      __armPageWatch();
      el.click();
      return; // next plan arrives after page_changed
    } else if (a.kind === "click_start") {
      // Posting-page "Apply" click: OPENS the application form. Deliberately
      // exempt from SUBMIT_DENYLIST ("apply" sits on it to protect review
      // pages) because this plan contains zero fill actions — there is no
      // user data on this page to submit. The backend only emits click_start
      // on a fieldless page before anything has been filled this session;
      // here we re-pin the live label: still apply-shaped, still exactly
      // what the plan snapshotted.
      let el = __copilotButtons[a.button_index];
      if (!el || !document.contains(el)) {
        el = __refindButton(a.button_index);
        if (el) __copilotButtons[a.button_index] = el;
      }
      const live = ((el?.innerText || el?.value || "").trim());
      if (!el || !document.contains(el) || !/^apply\b/i.test(live) || (a.expect_text && live !== a.expect_text)) {
        __setAction("Apply button changed — click it yourself to open the application.");
        if (el) { el.scrollIntoView({ behavior: "smooth", block: "center" }); el.style.outline = __HL.review; }
        __armPageWatch(); // their manual click must still re-plan
        return;
      }
      __setAction(`Opening the application: ${live}`);
      await __sleep(500);
      __armPageWatch();
      el.click();
      return; // next plan arrives after page_changed
    } else if (a.kind === "await_user") {
      // Only the submit-handoff await_user is terminal (session ends here).
      // The fieldless-page and unverifiable-nav-button handoffs are not —
      // more fields may show up once the user advances manually, so
      // reporting done:true here would permanently kill a still-live session.
      await __reportEdits(results, a.terminal === true);
      const el = a.button_index != null ? __copilotButtons[a.button_index] : null;
      if (el) {
        el.scrollIntoView({ behavior: "smooth", block: "center" });
        el.style.outline = __HL.review;
      }
      __setAction(a.reason || "Review and submit when ready — I never click Submit.");
      // Non-terminal handoffs (review steps, stalled pages, unlabeled nav):
      // the session continues after the USER advances — on SPA wizards
      // (Workday) that advance changes no URL, so without a live watcher the
      // session would stall forever. Terminal (submit) handoffs end here.
      if (a.terminal !== true) __armPageWatch();
      return;
    }
  }
  await __reportEdits(results, false);
  __setAction("Page filled — review the highlights.");
  // No nav button we could take — the user advances themselves; watch for it.
  __armPageWatch();
}

async function __attach(el, a) {
  if (!(el instanceof HTMLInputElement) || el.type !== "file") return false;
  const resp = await chrome.runtime.sendMessage({ type: "fetch_pdf", docId: a.doc_id });
  if (!resp || resp.error) return false;
  const bytes = Uint8Array.from(atob(resp.b64), (c) => c.charCodeAt(0));
  const file = new File([bytes], a.filename, { type: "application/pdf" });
  const dt = new DataTransfer();
  dt.items.add(file);
  el.files = dt.files;
  el.dispatchEvent(new Event("change", { bubbles: true }));
  return true;
}

async function __reportEdits(results, done) {
  const edits = [];
  for (const [index, meta] of Object.entries(__copilotEssays)) {
    const el = __copilotFields[index];
    if (!el) continue;
    const final = (el.value || "").trim();
    if (final && final !== meta.draft) edits.push({ label: meta.label, draft: meta.draft, final });
  }
  __copilotEssays = {};
  chrome.runtime.sendMessage({ type: "report", payload: { results, edits, done } });
}

/* ---------- wizard page-change detection ---------- */
async function __settleLoading(maxMs) {
  // Wizard steps (Workday especially) render a spinner/skeleton before the
  // next page's fields exist — snapshotting too early yields a bogus
  // "fieldless" page and a wrong plan. Wait for loading markers to clear.
  const deadline = Date.now() + maxMs;
  while (Date.now() < deadline) {
    const busy = document.querySelector(
      '[aria-busy="true"], [data-automation-id*="loading" i], [class*="spinner" i], [class*="loading" i]'
    );
    if (!busy || !__visible(busy)) return;
    await __sleep(300);
  }
}

function __disarmPageWatch() {
  if (!__copilotWatch) return;
  __copilotWatch.obs.disconnect();
  clearInterval(__copilotWatch.urlPoll);
  __copilotWatch = null;
}

function __armPageWatch() {
  if (__copilotWatch) return;
  const startUrl = location.href;
  let settleTimer = null;
  const fire = async () => {
    if (!__copilotWatch) return; // already fired via the other trigger
    __disarmPageWatch();
    await __settleLoading(6000);
    chrome.runtime.sendMessage({ type: "page_changed" });
  };
  const obs = new MutationObserver(() => {
    clearTimeout(settleTimer);
    settleTimer = setTimeout(fire, 900); // DOM settled after the change
  });
  // Attributes matter too: wizards that keep every step in the DOM and flip
  // visibility (hidden/style/class toggles) advance without a single childList
  // mutation — childList-only watching stalls the whole session on such pages.
  // The watcher is armed after our own fills finish (and before nav clicks),
  // so it also catches MANUAL advances on SPA wizards; noise from the user
  // editing fields resolves to an empty plan via the backend's same-page
  // replan guard, so spurious fires are cheap and harmless.
  obs.observe(document.body, {
    childList: true, subtree: true,
    attributes: true, attributeFilter: ["hidden", "style", "class"],
  });
  const urlPoll = setInterval(() => {
    if (location.href !== startUrl) fire();
  }, 300);
  __copilotWatch = { obs, urlPoll };
}

/* ---------- messages ---------- */
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "ping") {
    // Reachability probe for background's reconnect-after-cross-origin-hop
    // logic (chrome.action.onClicked) — presence of a listener IS the answer.
    sendResponse(true);
  } else if (msg.type === "snapshot") {
    sendResponse(__snapshot());
  } else if (msg.type === "status") {
    __overlay();
    __setStage(msg.session.stage, msg.session.progress);
    if (msg.session.status === "error") __setError(msg.session.error);
  } else if (msg.type === "plan") {
    __snapshotIfStale();
    __execute(msg.actions);
  } else if (msg.type === "error") {
    __overlay();
    __setError(msg.error);
  } else if (msg.type === "stopped") {
    __copilotOverlay?.host?.remove();
    __copilotOverlay = null;
    __disarmPageWatch();
  }
  return false;
});

function __snapshotIfStale() {
  // Plans address the CURRENT snapshot's indexes; if the DOM was re-snapshotted
  // by the worker just before the plan was built, indexes line up. Re-snapshot
  // here only when we have none (fresh injection after navigation).
  if (__copilotFields.length === 0) __snapshot();
}
