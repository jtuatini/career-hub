# Setup — Application Copilot

Everything runs on your machine. Your résumés, answers, and profile live in a
local `data/` folder and are sent nowhere — except the job-posting text,
résumé text (for tailoring and the JD-match/Deep ATS scans), and the snippets
being written, which go to the ONE AI engine you choose below.

## Step 1 — Pick an AI engine

Install one before setup. Your subscription, your choice — the app never
bills an API key from a CLI run:

- **Claude (recommended):** install [Claude Code](https://claude.com/claude-code),
  run `claude` once to log in. Your prompts go to Anthropic.
- **OpenAI:** install the Codex CLI (`npm i -g @openai/codex`), run
  `codex login` (ChatGPT plan). Your prompts go to OpenAI.
- **Gemini:** install the Gemini CLI (`npm i -g @google/gemini-cli`), run
  `gemini` once to sign in. Your prompts go to Google.
- **No subscription?** Put `ANTHROPIC_API_KEY=sk-...` in `backend/.env`
  (metered billing) and restart the backend.

## Step 2 — One-time setup

```bash
./setup.sh
```

Checks for `uv` and Node and offers to install whichever is missing, installs
the backend and frontend dependencies, and creates the database. Safe to
re-run any time.

One more prerequisite `setup.sh` doesn't install for you:

- LaTeX, for PDF compilation: [TinyTeX](https://yihui.org/tinytex/) —
  `curl -sL "https://yihui.org/tinytex/install-bin-unix.sh" | sh`

## Step 3 — Start

```bash
./start.sh
```

Or double-click **Start Hub.command** in Finder. Either way, the browser
opens to the app once the backend is ready. Ctrl-C in the terminal (or
closing the `start.sh`/`Start Hub.command` window) stops both servers.
To stop it later: double-click `Stop Hub.command` (or run `./stop.sh`).

## Optional

### Chrome extension

1. chrome://extensions → enable Developer mode → "Load unpacked" → pick the
   `extension/` folder.
2. In the app: Tools ▾ → Profile → Extension access → Reveal token → copy.
3. Click the extension icon → click **Settings** at the bottom of the popup →
   paste the token in the API token field → click Save.

On any job posting: **Tailor résumé only** tailors and attaches your résumé and
touches nothing else; **Run pipeline** can also draft answers and cover
letters; **Fill application only** just fills your saved profile. Nothing is
ever submitted — you always click Submit yourself.

### Choosing a model

Switch engines any time via the chip in the app header. Click it to reveal a
free-text model field — type any model string and press Enter to save it for
that engine; leaving it empty reverts to that engine's default.

### ATS scans

The Keyword scan runs locally — no AI, nothing leaves your machine. JD-match
and Deep scans send your résumé text through your chosen engine above — no
extra install. The optional Hiring-agent scan wraps a local clone of the
hiring-agent repo and runs it through Ollama (also local); to enable it, set
`ATS_REPO_PATH=/path/to/hiring-agent` in `backend/.env`.

### Interview prep

The Prep tab runs mock interviews (your job description, tailored résumé, and
brain snippets go to your chosen engine — the same data tailoring already
sends) and OA research (the company name and role title go to your engine for web search,
plus public GitHub repo reads via api.github.com). Nothing is sent anywhere
else, and nothing runs without you clicking it.

### Metered API key fallback

If your chosen engine fails mid-run, the app falls back to your Claude login
(then the Anthropic API if you configured a key) — it never silently bills a
metered key for another provider.

### No LaTeX résumé yet?

Documents ▾ → Import PDF converts your existing PDF résumé into LaTeX, and
shows you a fidelity/fit/alignment verification before anything is saved.
