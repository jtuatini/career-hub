// Render-time gate for every externally-sourced href — AI engine output,
// stored rows, extension snapshots. Only http(s) URLs may become links; a
// prompt-injected `javascript:` URL must render as inert text, never an <a>.
export function safeHttpUrl(u: string | null | undefined): string | undefined {
  if (!u) return undefined;
  return /^https?:\/\//i.test(u) ? u : undefined;
}
