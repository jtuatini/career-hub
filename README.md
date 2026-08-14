# Career Hub

**A local-first internship application copilot.** Your resumes, essays, and
personal data stay in a folder on your machine — the AI you already subscribe
to does the heavy lifting.

![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-backend-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-frontend-61DAFB?logo=react&logoColor=black)
![TypeScript](https://img.shields.io/badge/TypeScript-strict-3178C6?logo=typescript&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-local%20data-003B57?logo=sqlite&logoColor=white)
![Platform](https://img.shields.io/badge/platform-macOS-black?logo=apple)
![Local first](https://img.shields.io/badge/privacy-local--first-2ea44f)
![License](https://img.shields.io/badge/license-MIT-blue)

## What it does

- **Resume bank** — organize LaTeX resumes by job family, compile to PDF locally
  (TinyTeX), track lineages of tailored versions.
- **AI tailoring that can't wreck your template** — wording-only LaTeX edits,
  enforced by a validator; structure, commands, and preamble are untouchable.
- **Memory web** — your real experiences, stories, and approved past answers
  power supplemental-question drafts in *your* voice, and get better with every
  application you approve.
- **ATS scans** — JD-match and deep scans of the resume the parser actually sees.
- **Job tracking + networking** — pipeline board, deadlines, cold-outreach
  research and drafts.
- **Chrome autofill extension** — fills Workday/Greenhouse-style applications
  and highlights every field it touched. **It never clicks submit — you do.**

## Quick start

```bash
./setup.sh    # once — checks prerequisites, installs deps
./start.sh    # starts backend + frontend, opens the app
./stop.sh     # stops everything
```

Prefer not to touch a terminal? Double-click **Start Hub.command** /
**Stop Hub.command** in Finder.

Full setup details (choosing your AI engine, optional extras): **[SETUP.md](SETUP.md)**.

## Privacy model

- All personal data lives in `data/` (gitignored, never leaves your machine).
- The backend binds to `127.0.0.1` only.
- Outbound traffic is limited to: job-posting text, resume text, and the
  snippets being written — sent to the **one** AI engine you choose
  (Claude / Codex / Antigravity CLI on your subscription, or the Anthropic API);
  plus user-triggered public reads from `api.github.com`.
- CLI engines bill your subscription; the app strips `ANTHROPIC_API_KEY` from
  embedded CLI sessions so you're never metered by accident.

## Layout

| Path | What |
|---|---|
| `backend/` | FastAPI + SQLAlchemy + SQLite (Python 3.13, uv) |
| `frontend/` | React + Vite + TypeScript |
| `extension/` | Chrome MV3 autofill extension |
| `data/` | Your database, resumes, and PDFs — gitignored |

## License

[MIT](LICENSE)
