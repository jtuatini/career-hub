import { useEffect, useRef, useState } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";

// Matches the ink-on-slate palette in index.css.
const TERM_THEME = {
  background: "#141519",
  foreground: "#e9e7e2",
  cursor: "#8aa3f5",
  selectionBackground: "#222b4a",
};

type Status = "connecting" | "connected" | "ended" | "replaced" | "error";

export default function TerminalView({ visible }: { visible: boolean }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const [status, setStatus] = useState<Status>("connecting");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const connect = () => {
    const term = termRef.current;
    if (!term) return;
    setStatus("connecting");
    setErrorMsg(null);
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/api/terminal/ws`);
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    ws.onopen = () => {
      setStatus("connected");
      sendResize();
    };
    ws.onmessage = (ev) => {
      if (typeof ev.data === "string") {
        const control = JSON.parse(ev.data);
        if (control.type === "exit") setStatus("ended");
        if (control.type === "replaced") setStatus("replaced");
        if (control.type === "error") {
          setStatus("error");
          setErrorMsg(control.message);
        }
        return;
      }
      term.write(new Uint8Array(ev.data));
    };
    ws.onclose = () => {
      setStatus((s) => (s === "connected" || s === "connecting" ? "ended" : s));
    };
  };

  const sendResize = () => {
    const term = termRef.current;
    const ws = wsRef.current;
    if (term && ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
    }
  };

  const restart = async () => {
    wsRef.current?.close();
    await fetch("/api/terminal/restart", { method: "POST" });
    termRef.current?.reset();
    connect();
  };

  useEffect(() => {
    const term = new Terminal({
      theme: TERM_THEME,
      fontFamily: 'ui-monospace, "SF Mono", Menlo, Consolas, monospace',
      fontSize: 13,
      cursorBlink: true,
      scrollback: 5000,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(hostRef.current!);
    fit.fit();
    termRef.current = term;
    fitRef.current = fit;

    const encoder = new TextEncoder();
    term.onData((data) => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(encoder.encode(data));
      }
    });
    term.onResize(sendResize);

    const observer = new ResizeObserver(() => fit.fit());
    observer.observe(hostRef.current!);

    connect();
    return () => {
      observer.disconnect();
      wsRef.current?.close();
      term.dispose();
    };
  }, []);

  // Refit when the tab becomes visible again (size is 0 while hidden).
  useEffect(() => {
    if (visible) {
      fitRef.current?.fit();
      termRef.current?.focus();
    }
  }, [visible]);

  return (
    <section>
      <div className="view-head">
        <h2>
          Terminal
          <span className="meta-inline">
            your selected engine's CLI, sandboxed to data/ai-workspace — subscription-billed
          </span>
        </h2>
        <div className="btn-row">
          <span className={`term-status term-status-${status}`}>{status}</span>
          {(status === "ended" || status === "replaced" || status === "error") && (
            <button onClick={status === "replaced" ? connect : restart}>
              {status === "replaced" ? "Reattach here" : "Restart session"}
            </button>
          )}
        </div>
      </div>
      {errorMsg && <p className="error">{errorMsg}</p>}
      <div className="terminal-panel">
        <div ref={hostRef} className="terminal-host" />
      </div>
      <p className="hint">
        It can read and bulk-edit the resume bank (every edit becomes a new compiled
        version), read tracked jobs, and search the web. You approve each change in the
        terminal before it happens.
      </p>
    </section>
  );
}
