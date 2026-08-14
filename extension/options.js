const $ = (id) => document.getElementById(id);

chrome.storage.local.get("apiToken").then(({ apiToken }) => {
  if (apiToken) $("token").value = apiToken;
});

$("save").addEventListener("click", async () => {
  await chrome.storage.local.set({ apiToken: $("token").value.trim() });
  try {
    const resp = await fetch("http://127.0.0.1:8321/api/engine/status", {
      headers: { "X-Copilot-Token": $("token").value.trim() },
    });
    $("status").textContent = resp.ok ? "Saved — backend reachable." : `Saved, but backend said: ${resp.status}`;
  } catch {
    $("status").textContent = "Saved, but the backend is not running.";
  }
});

$("app").addEventListener("click", () => {
  chrome.tabs.create({ url: "http://localhost:5173" });
});
