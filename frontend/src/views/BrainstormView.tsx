import { useRef, useState } from "react";
import { api, MEMORY_TYPES, streamBrainstorm } from "../api";
import { useToast } from "../Toast";

interface ChatMessage {
  role: "user" | "assistant";
  text: string;
  tools: string[];
  error?: boolean;
}

const toolLabel = (name: string) =>
  name.replace("mcp__copilot__", "brain: ").replace(/^Web/, "web: Web");

export default function BrainstormView() {
  const toast = useToast();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [saveFor, setSaveFor] = useState<number | null>(null);
  const [saveForm, setSaveForm] = useState({ type: "story", title: "", content: "" });
  const [savedNote, setSavedNote] = useState<number | null>(null);
  const sessionRef = useRef<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const patchLast = (fn: (m: ChatMessage) => ChatMessage) =>
    setMessages((ms) => [...ms.slice(0, -1), fn(ms[ms.length - 1])]);

  const send = async () => {
    const message = input.trim();
    if (!message || busy) return;
    setInput("");
    setBusy(true);
    setMessages((ms) => [
      ...ms,
      { role: "user", text: message, tools: [] },
      { role: "assistant", text: "", tools: [] },
    ]);
    try {
      await streamBrainstorm(message, sessionRef.current, (event) => {
        if (event.type === "session") sessionRef.current = event.session_id;
        if (event.type === "tool")
          patchLast((m) => ({ ...m, tools: [...m.tools, toolLabel(event.name)] }));
        if (event.type === "text")
          patchLast((m) => ({ ...m, text: m.text ? `${m.text}\n\n${event.text}` : event.text }));
        if (event.type === "error")
          patchLast((m) => ({ ...m, text: event.message, error: true }));
        scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
      });
    } catch (e) {
      patchLast((m) => ({ ...m, text: (e as Error).message, error: true }));
    } finally {
      setBusy(false);
    }
  };

  const openSave = (index: number) => {
    const text = messages[index].text;
    setSaveForm({
      type: "story",
      title: text.split(/[.\n]/)[0].slice(0, 80),
      content: text,
    });
    setSaveFor(index);
    setSavedNote(null);
  };

  const saveToBrain = async () => {
    await api.createMemory({ ...saveForm, source: "brainstorm" });
    setSavedNote(saveFor);
    setSaveFor(null);
    toast("Saved to brain");
  };

  const reset = () => {
    sessionRef.current = null;
    setMessages([]);
    setSaveFor(null);
    setSavedNote(null);
  };

  return (
    <section>
      <div className="view-head">
        <h2>
          Brainstorm
          <span className="meta-inline">
            thinks with your brain — subscription-billed, saves only what you approve
          </span>
        </h2>
        {messages.length > 0 && (
          <button onClick={reset} disabled={busy}>
            New session
          </button>
        )}
      </div>

      <div className="chat-panel">
        <div className="chat-scroll" ref={scrollRef}>
          {messages.length === 0 && (
            <p className="empty">
              Try: “Which of my experiences fits an infra internship at a big lab?” or
              “Help me find an angle for a ‘why us’ essay.”
            </p>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`chat-msg chat-${m.role}${m.error ? " chat-error" : ""}`}>
              {m.tools.length > 0 && (
                <div className="chat-tools">{m.tools.join(" · ")}</div>
              )}
              {m.text || (busy && i === messages.length - 1 ? "…" : "")}
              {m.role === "assistant" && m.text && !m.error && (
                <div className="chat-actions">
                  {savedNote === i ? (
                    <span className="chip chip-ok">saved to brain</span>
                  ) : (
                    <button className="ghost" onClick={() => openSave(i)}>
                      Save to brain…
                    </button>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>

        {saveFor !== null && (
          <div className="panel form-panel save-panel">
            <div className="form-row">
              <label>
                Type
                <select
                  value={saveForm.type}
                  onChange={(e) => setSaveForm({ ...saveForm, type: e.target.value })}
                >
                  {MEMORY_TYPES.map((t) => (
                    <option key={t}>{t}</option>
                  ))}
                </select>
              </label>
              <label style={{ flex: 2 }}>
                Title
                <input
                  value={saveForm.title}
                  onChange={(e) => setSaveForm({ ...saveForm, title: e.target.value })}
                />
              </label>
            </div>
            <label>
              What goes in the brain <span className="optional">edit before saving</span>
              <textarea
                rows={4}
                value={saveForm.content}
                onChange={(e) => setSaveForm({ ...saveForm, content: e.target.value })}
              />
            </label>
            <div className="btn-row">
              <button className="primary" disabled={!saveForm.title.trim()} onClick={saveToBrain}>
                Save
              </button>
              <button onClick={() => setSaveFor(null)}>Cancel</button>
            </div>
          </div>
        )}

        <div className="chat-input">
          <textarea
            rows={2}
            value={input}
            placeholder={busy ? "Thinking…" : "Brainstorm with your brain…"}
            disabled={busy}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
          />
          <button className="primary" onClick={send} disabled={busy || !input.trim()}>
            Send
          </button>
        </div>
      </div>
    </section>
  );
}
