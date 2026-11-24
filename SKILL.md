---
name: tagteam
description: Use when the user mentions TagTeam, switching between Claude Code, Gemini (Google Antigravity app or agy CLI) and the Grok CLI when a usage limit runs out, continuing a Claude conversation in Gemini or Grok (or one of theirs in Claude), conversations missing on one side, a "## Handoff to ..." message, ~/.gemini (GEMINI.md, config/skills.json, hooks.json), ~/.grok (AGENTS.md, config.toml, sessions), "trajectory not found" or "trajectory db is not initialized".
---

# TagTeam: one conversation across Claude Code, Gemini and Grok

## Overview
The user alternates between Claude Code, Google Antigravity (Gemini; app and `agy` CLI) and the Grok CLI when one of
them hits its usage limit. TagTeam keeps the three in step:
- **Environment:** Gemini and Grok read the same rules (`~/.claude/CLAUDE.md`), skills, Claude plugins and MCP
  servers, by reference (`scripts/instalar.py`).
- **Conversations, every direction:** Claude sessions appear in Antigravity (app and CLI) and in Grok; theirs appear in
  `claude --resume` as "[Antigravity] …" / "[Grok] …" (`scripts/sincronizar.py`, driven by hooks on all sides).
- **Handoff:** near the limit, the agent writes "## Handoff to …" (Request, Done, In progress, Next step, Watch out).

## How to operate
- Re-apply the environment (new plugin, new skill, new PC): `python -I scripts/instalar.py [--seco]`. The Claude
  `SessionStart` hook already does it.
- Sync by hand: `python -I scripts/sincronizar.py [--direcao antigravity|claude|grok|claude-de-grok] [--sessao|--cascade|--grok <id>] [--seco] [--forcar]`.
- Tests: `python -m unittest discover -s scripts -p "test_*.py"`.
- Log: `%TEMP%\tagteam\sincronizar.log`. State per PC: `~/.tagteam/estado-<PC>.json`.

## Rules of the sync
- A conversation that changed at the source is rewritten at the destination, never duplicated.
- A pure mirror never goes back (markers: `entrypoint` `antigravity-import`/`grok-import`, `claude_import` table,
  `claude_import.json`).
- A copy continued at the destination is never overwritten: a new version (new id) is created and the continued copy
  stays.
- A copy deleted at the destination does not come back (only with `--forcar`). Automated Claude sessions (`claude -p`,
  SDK) are skipped.

## When continuing a conversation from another AI
Look for the final "## Handoff to …" message and follow its *Next step*. If there is none, first write those five lines
yourself from the last messages, then continue.

## Never
- Swap a conversation's `.db` under the Antigravity app while it is open (use the local server: `LoadTrajectory`,
  `DeleteCascadeTrajectory`).
- Recreate with the **same id** a conversation the app just deleted (it remembers the id until restart).
- Edit the generated `~/.gemini/GEMINI.md`, `~/.grok/AGENTS.md` or the marked block of `~/.grok/config.toml`: edit the
  templates in `references/` or `instalar.py`.

## Common errors
| Symptom | Cause | Fix |
|---|---|---|
| `trajectory db is not initialized` | `.db` without `PRAGMA user_version=1` | written from a native template; send one "hi" in Antigravity so a template exists |
| CLI: `trajectory not found` | the conversation belongs to the app | TagTeam writes a copy into the CLI folder too |
| Grok: "This session's folder no longer exists" | the session's working folder doesn't exist on this PC | remapped to `TAGTEAM_WORKSPACE` (default: home) |
| Grok session never reaches Claude, log has only the header | Grok runs hooks in a Windows job that kills child processes and refuses breakaway | inside Grok TagTeam runs synchronously (60 s budget, hook `timeout = 75`) |
| Antigravity hook doesn't run | `NoDefaultCurrentDirectoryInExePath=1` | command is `.\gancho-antigravity.cmd` (already written that way) |
| Nothing happens on a PC | Antigravity has no native conversation yet | open it and send "hi" once |

Deep dive: `docs/HOW-IT-WORKS.md`.
