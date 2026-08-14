import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { EngineStatus } from "./api";
import HomeView from "./views/HomeView";
import ResumesView from "./views/ResumesView";
import TailorView from "./views/TailorView";
import TailoredView from "./views/TailoredView";
import CoverLettersView from "./views/CoverLettersView";
import TrackerView from "./views/TrackerView";
import NetworkView from "./views/NetworkView";
import TerminalView from "./views/TerminalView";
import MemoryView from "./views/MemoryView";
import BrainstormView from "./views/BrainstormView";
import ProfileView from "./views/ProfileView";
import VoiceView from "./views/VoiceView";
import ImportView from "./views/ImportView";
import PrepView from "./views/PrepView";
import NavMenu, { type NavItem } from "./NavMenu";
import { ToastProvider } from "./Toast";

type TabId =
  | "home" | "tailor" | "tailored" | "letters" | "resumes" | "memory" | "brainstorm"
  | "tracker" | "prep" | "network" | "profile" | "terminal" | "voice" | "import";

const TAB_IDS = [
  "home", "tailor", "tailored", "letters", "resumes", "memory", "brainstorm",
  "tracker", "prep", "network", "profile", "terminal", "voice", "import",
] as const;

interface Route {
  tab: TabId;
  job?: number;
  doc?: number;
}

function parseHash(): Route {
  const raw = window.location.hash.replace(/^#\/?/, "");
  const [path, query = ""] = raw.split("?");
  const params = new URLSearchParams(query);
  const tab = (TAB_IDS as readonly string[]).includes(path) ? (path as TabId) : "home";
  const num = (k: string) => {
    const v = Number(params.get(k));
    return Number.isFinite(v) && v > 0 ? v : undefined;
  };
  return { tab, job: num("job"), doc: num("doc") };
}

const CATEGORIES: { label: string; items: NavItem[] }[] = [
  { label: "Apply", items: [
    { id: "tailor", label: "Tailor" },
    { id: "tracker", label: "Tracker" },
    { id: "prep", label: "Prep" },
  ]},
  { label: "Documents", items: [
    { id: "tailored", label: "Tailored" },
    { id: "letters", label: "Cover letters" },
    { id: "resumes", label: "Resume bank" },
    { id: "import", label: "Import PDF" },
  ]},
  { label: "Brain", items: [
    { id: "memory", label: "Memory" },
    { id: "brainstorm", label: "Brainstorm" },
    { id: "voice", label: "Voice" },
  ]},
  { label: "Tools", items: [
    { id: "network", label: "Network" },
    { id: "terminal", label: "Terminal" },
    { id: "profile", label: "Profile" },
  ]},
];

// Suggestions only — free text is the contract, any model string works.
const MODEL_SUGGESTIONS: Record<string, string[]> = {
  claude: ["opus", "sonnet", "haiku"],
  // Current Codex-with-ChatGPT set (Aug 2026): gpt-5.6 sol/terra/luna tiers;
  // gpt-5.5 previous-gen; gpt-5.4[-mini] retire 2026-08-31; gpt-5.3-codex and
  // older are already unavailable on ChatGPT sign-in.
  codex: ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5", "gpt-5.3-codex-spark"],
  // From `agy models` — Antigravity also serves Claude and GPT-OSS models.
  antigravity: [
    "gemini-3.7-flash-high",
    "gemini-3.7-flash-medium",
    "gemini-3.7-flash-low",
    "gemini-3.1-pro-high",
    "gemini-3.1-pro-low",
    "claude-sonnet-4-6",
    "claude-opus-4-6-thinking",
    "gpt-oss-120b-medium",
  ],
  custom: [],
};

export default function App() {
  const [route, setRoute] = useState<Route>(parseHash);
  const tab = route.tab;
  // The terminal mounts on first visit and stays mounted (hidden, not unmounted)
  // so the xterm buffer and websocket survive tab switches.
  const [terminalMounted, setTerminalMounted] = useState(false);
  const [engine, setEngine] = useState<EngineStatus | null>(null);
  const [enginePickerOpen, setEnginePickerOpen] = useState(false);
  const [modelDraft, setModelDraft] = useState<string | null>(null); // null = not editing
  const [commandDraft, setCommandDraft] = useState<string | null>(null); // custom engine command; null = not editing
  const enginePickerRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    api.getEngineStatus().then(setEngine, () => setEngine(null));
  }, []);

  useEffect(() => {
    if (!enginePickerOpen) return;
    const onDown = (e: MouseEvent) => {
      if (enginePickerRef.current && !enginePickerRef.current.contains(e.target as Node)) {
        setEnginePickerOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setEnginePickerOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [enginePickerOpen]);

  // "subscription" requires the SELECTED provider to actually be available —
  // showing it whenever ANY provider is installed (the prior bug) claimed a
  // provider·subscription pair that doesn't exist, e.g. only antigravity installed,
  // provider set to claude -> falsely read "claude · subscription".
  const providerAvailable = engine ? !!engine.providers[engine.ai_provider] : false;
  const anyProviderAvailable = engine ? Object.values(engine.providers).some(Boolean) : false;

  const activeModel = engine ? engine.models?.[engine.ai_provider] || "" : "";
  const engineLabel = engine
    ? providerAvailable
      ? `${engine.ai_provider}${activeModel ? ` · ${activeModel}` : ""} · subscription`
      : anyProviderAvailable
        ? `${engine.ai_provider} · unavailable`
        : engine.api_key_configured
          ? "api"
          : "no engine"
    : null;
  // "unavailable" (selected provider missing but another is installed) still
  // needs the user's attention, so it reuses the no-engine chip styling.
  const engineChipClass = providerAvailable || engineLabel === "api" ? "subscription" : "no-engine";

  // The hash is the source of truth: tab clicks write it, the hashchange
  // listener (also covering back/forward and hand-edited URLs) drives state.
  const selectTab = (id: TabId) => {
    if (window.location.hash === `#/${id}`) return;
    window.location.hash = `/${id}`;
  };

  useEffect(() => {
    const onHash = () => setRoute(parseHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    if (tab === "terminal") setTerminalMounted(true);
  }, [tab]);

  return (
    <ToastProvider>
      <div className="shell">
        <header className="masthead">
          <span className="wordmark">
            Career Hub
            {engineLabel && (
              <span className="navmenu engine-picker" ref={enginePickerRef}>
                <button
                  className={`engine-chip engine-${engineChipClass}`}
                  onClick={() => setEnginePickerOpen((o) => !o)}
                >
                  {engineLabel} ▾
                </button>
                {enginePickerOpen && engine && (
                  <div className="navmenu-panel" role="menu">
                    {Object.values(engine.providers).some(Boolean) || engine.api_key_configured ? (
                      <>
                        {Object.entries(engine.providers).map(([name, ok]) => (
                          <button
                            key={name}
                            className={name === engine.ai_provider ? "navmenu-item active" : "navmenu-item"}
                            disabled={!ok}
                            title={ok ? "" : `${name} CLI not installed / not logged in`}
                            onClick={() => {
                              setEnginePickerOpen(false);
                              setModelDraft(null);
                              api.setEngineProvider(name).then(setEngine);
                            }}
                          >
                            {name} {ok ? "" : "· unavailable"}
                          </button>
                        ))}
                        <div className="engine-model-row">
                          <label>Model — {engine.ai_provider}</label>
                          <div className="model-options">
                            <button
                              className={!activeModel ? "navmenu-item active" : "navmenu-item"}
                              onClick={() => {
                                setModelDraft(null);
                                api.setEngineModel(engine.ai_provider, "").then(setEngine);
                              }}
                            >
                              default ({engine.model_defaults?.[engine.ai_provider] || "provider default"})
                            </button>
                            {(MODEL_SUGGESTIONS[engine.ai_provider] ?? []).map((m) => (
                              <button
                                key={m}
                                className={activeModel === m ? "navmenu-item active" : "navmenu-item"}
                                onClick={() => {
                                  setModelDraft(null);
                                  api.setEngineModel(engine.ai_provider, m).then(setEngine);
                                }}
                              >
                                {m}
                              </button>
                            ))}
                          </div>
                          <div className="model-custom">
                            <input
                              placeholder="custom model id…"
                              value={modelDraft ?? ""}
                              onChange={(e) => setModelDraft(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === "Enter" && modelDraft?.trim()) {
                                  api.setEngineModel(engine.ai_provider, modelDraft.trim()).then(setEngine);
                                  setModelDraft(null);
                                }
                              }}
                            />
                            <button
                              disabled={!modelDraft?.trim()}
                              onClick={() => {
                                api.setEngineModel(engine.ai_provider, modelDraft!.trim()).then(setEngine);
                                setModelDraft(null);
                              }}
                            >
                              Save
                            </button>
                          </div>
                          {activeModel && !(MODEL_SUGGESTIONS[engine.ai_provider] ?? []).includes(activeModel) && (
                            <p className="hint">current: {activeModel}</p>
                          )}
                        </div>
                        <div className="engine-model-row">
                          <label>Custom engine command</label>
                          <div className="model-custom">
                            <input
                              placeholder="e.g. ollama  ·  or: llm -m {model}"
                              value={commandDraft ?? engine.custom_command ?? ""}
                              onChange={(e) => setCommandDraft(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === "Enter" && commandDraft !== null) {
                                  api.setEngineCustomCommand(commandDraft.trim()).then(setEngine);
                                  setCommandDraft(null);
                                }
                              }}
                            />
                            <button
                              disabled={commandDraft === null}
                              onClick={() => {
                                api.setEngineCustomCommand(commandDraft!.trim()).then(setEngine);
                                setCommandDraft(null);
                              }}
                            >
                              Save
                            </button>
                          </div>
                          <p className="hint">
                            Any local CLI as the engine. <code>{"{model}"}</code> ← the model box,{" "}
                            <code>{"{prompt}"}</code> ← the prompt (otherwise sent on stdin). Bare{" "}
                            <code>ollama</code> means <code>ollama run {"{model}"}</code>.
                          </p>
                        </div>
                      </>
                    ) : (
                      <div className="engine-setup-help">
                        <p><strong>No AI engine connected.</strong> Pick one:</p>
                        <p className="hint">
                          <strong>Claude</strong> (recommended): install Claude Code, run <code>claude</code> once to log in.
                        </p>
                        <p className="hint">
                          <strong>OpenAI</strong>: install the Codex CLI, run <code>codex login</code>.
                        </p>
                        <p className="hint">
                          <strong>Google</strong>: install Antigravity, run <code>agy</code> once to sign in.
                        </p>
                        <p className="hint">
                          Or add <code>ANTHROPIC_API_KEY=…</code> to <code>backend/.env</code> and restart. See SETUP.md.
                        </p>
                      </div>
                    )}
                  </div>
                )}
              </span>
            )}
          </span>
          <nav>
            <button
              className={tab === "home" ? "tab active" : "tab"}
              onClick={() => selectTab("home")}
            >
              Home
            </button>
            {CATEGORIES.map((c) => (
              <NavMenu
                key={c.label}
                label={c.label}
                items={c.items}
                activeId={tab}
                onSelect={(id) => selectTab(id as TabId)}
              />
            ))}
          </nav>
        </header>
        <main>
          {tab === "home" && <HomeView onNavigate={(id) => selectTab(id as TabId)} />}
          {tab === "resumes" && <ResumesView />}
          {tab === "tailor" && <TailorView />}
          {tab === "tailored" && <TailoredView focusDocId={route.doc} />}
          {tab === "letters" && <CoverLettersView focusDocId={route.doc} />}
          {tab === "memory" && <MemoryView />}
          {tab === "brainstorm" && <BrainstormView />}
          {tab === "tracker" && <TrackerView focusJobId={route.job} />}
          {tab === "prep" && <PrepView focusJobId={route.job} />}
          {tab === "network" && <NetworkView />}
          {tab === "profile" && <ProfileView />}
          {tab === "voice" && <VoiceView />}
          {tab === "import" && <ImportView />}
          {terminalMounted && (
            <div style={{ display: tab === "terminal" ? "block" : "none" }}>
              <TerminalView visible={tab === "terminal"} />
            </div>
          )}
        </main>
      </div>
    </ToastProvider>
  );
}
