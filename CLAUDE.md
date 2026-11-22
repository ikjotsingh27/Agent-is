# Pilot — Notes for Claude Code

Read these files first at the start of every session (in order):

1. `docs/ANTIGRAVITY_CHANGES.md` — exact file-by-file change log of work done by Antigravity after the Session 3b pause (output-limit fix + built-in `make_pdf` tool + test isolation fix).
2. `docs/BLUEPRINT.md` — the manual and Progress Log (newest entry at top).
3. `docs/STATE.md` — current state, test results, open issues, and next steps.

Quick verification command (PowerShell, inside `pilot/`):
```powershell
python -m unittest discover -s tests -t .
```

Notes:
- `git` is not installed / not in `PATH` on this Windows machine, so changes are in the working tree without git commits.
- Never print or commit `data/settings.json`.
