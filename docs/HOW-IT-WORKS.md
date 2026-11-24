# How TagTeam works

TagTeam converts conversations between three native formats and keeps them in step with hooks. Claude Code is the hub:
each AI syncs with Claude, and whatever just arrived from one side is relayed to the third in the same run.

```
              ┌──────────── hooks ────────────┐
   Gemini  ◄──┤  sincronizar.py (state per PC) ├──►  Grok
 (app + agy)  └───────────── ▲ ───────────────┘   (CLI)
                             │
                        Claude Code
                 ~/.claude/projects/*.jsonl
```

| Module | Role |
|---|---|
| `scripts/claude_jsonl.py` | reads Claude JSONL into a neutral `Conversa` (turns, title, cwd, timestamps) |
| `scripts/para_claude.py` | writes a Claude session from Gemini/Grok (`entrypoint` marker, "[Antigravity]"/"[Grok]" title) |
| `scripts/antigravity.py` + `pb.py` | reads/writes Antigravity's SQLite + protobuf conversations (lossless protobuf editor) |
| `scripts/servidor_local.py` | talks to Antigravity's local language server (load, rename, delete conversations live) |
| `scripts/grok.py` | reads/writes Grok sessions (`updates.jsonl`, `chat_history.jsonl`, `summary.json`) |
| `scripts/sincronizar.py` | the engine: every direction, versions, mirrors, relay, per-PC state |
| `scripts/gancho.py` | Claude Code and Grok hook (throttled; synchronous inside Grok) |
| `scripts/gancho_antigravity.py` | Antigravity `Stop` hook (sync + Gemini quota handoff) |
| `scripts/passagem.py`, `statusline.py` | handoff near the limit (Claude status line usage, Gemini quota API) |
| `scripts/instalar.py` | environment mirror, hooks, CLIs, shortcuts (idempotent) |
| `scripts/agy_resume.py`, `grok-resume/` | resume pickers for the `agy` and Grok CLIs |

## Claude Code
- `~/.claude/projects/<cwd with non-alphanumerics as ->/<session>.jsonl`. On Windows `C--x` and `c--x` are the same
  folder (case-insensitive file system).
- Titles: `ai-title` / `custom-title` lines. Generated sessions chain messages by `parentUuid`, alternate roles
  (consecutive replies are merged) and carry `entrypoint: "antigravity-import"` or `"grok-import"`.
- Automated sessions (`claude -p`, SDK, `entrypoint` starting with `sdk`) are not synced.

## Gemini — Google Antigravity (app and `agy` CLI)
- One SQLite per conversation: `~/.gemini/antigravity/conversations/<cascade>.db` (app) and the same format under
  `~/.gemini/antigravity-cli/` (CLI), plus an index `conversation_summaries.db`.
- `PRAGMA user_version = 1` is required ("trajectory db is not initialized" otherwise).
- `steps.step_payload` is protobuf without a public schema: type **14** = user message, **15** = assistant reply
  (text, reasoning, tool calls), **132** = tool result (skipped).
- TagTeam builds each conversation from a **native conversation used as a template**, swapping only text, ids and
  timestamps, so the result is byte-compatible with what the app writes. A `claude_import` table marks imported ones.
- With the app open, writes go through its local language server (`LoadTrajectory`, `UpdateConversationAnnotations`,
  `DeleteCascadeTrajectory`; port and CSRF token from `daemon/hub_<port>.json`). With the app closed, files and the
  index are written directly.
- Gotchas: `DeleteCascadeTrajectory` answers before it finishes, and the app refuses a deleted id until restart, so every
  update gets a **new versioned id** (written first, old one removed after). The CLI cannot open app conversations
  ("trajectory not found"), so TagTeam writes one copy per store.
- The Antigravity hook runs through `cmd /c` in the `hooks.json` folder: the command is relative and prefixed with
  `.\` because `NoDefaultCurrentDirectoryInExePath=1` stops `cmd` from searching the current folder.

## Grok CLI (xAI)
- `~/.grok/sessions/<url-encoded cwd>/<id>/`: `updates.jsonl` (source of truth; ACP updates such as
  `user_message_chunk`, `agent_message_chunk`, `tool_call`, `turn_completed`), `chat_history.jsonl` (what the model
  receives) and `summary.json` (title, cwd, counters). TagTeam writes into a `.novo` folder and swaps it in atomically.
- Imported sessions carry `claude_import.json` with the source, the number of turns and the size of the written
  `updates.jsonl`; a different size means the user continued there, so the copy is kept and a new version is created.
- **The working folder must exist** or Grok refuses to resume; folders from another PC are remapped to this one, and to
  `TAGTEAM_WORKSPACE` when they don't exist.
- Grok does not run the hooks in `~/.claude/settings.json` nor read `~/.claude.json`, and it ships Claude plugins
  disabled. `instalar.py` writes a marked block in `~/.grok/config.toml` with `[[hooks.Stop]]`, `[[hooks.SessionEnd]]`,
  `[plugins] enabled` and `[mcp_servers.*]`. Under PowerShell, the first quoted word must be an `.exe` followed by
  arguments (`"python.exe" -I "gancho.py"`).
- Grok runs hooks inside a **Windows job object that kills the whole process tree** when it exits and refuses
  `CREATE_BREAKAWAY_FROM_JOB`. A background child dies mid-sync (in `grok -p` the session ends right after the hook),
  so inside Grok the sync runs synchronously with a 60-second budget. One session takes 1–2 s; `Stop` is throttled to
  once every 2 minutes per session.
- `grok sessions list` shows the same list as the resume picker; `grok usage` only works inside the TUI, so there is
  no automatic handoff *from* Grok.

## Versions, mirrors and the relay
1. Changed at the source → rewritten at the destination (same logical conversation, never duplicated).
2. Imported and untouched → never sent back (mirror).
3. Continued at the destination and changed at the source → **new version** next to the continued copy. This is
   exactly what happens when you tag out: every switch becomes a new conversation on the other side.
4. Deleted at the destination → not recreated (unless `--forcar`).
5. What one run imports into Claude from Gemini is relayed to Grok in the same run, and vice versa.

## The handoff
- Claude: `statusline.py` stores the `rate_limits` Claude Code sends to the status line; at ≥ 95% (5-hour or 7-day
  window) the `Stop` hook returns `decision: block` once per conversation and limit window.
- Gemini: the Antigravity `Stop` hook asks the local server for `RetrieveUserQuotaSummary` (the quota group of the
  model in use) and returns `decision: continue` with the request.
- The receiving side is told by the generated `GEMINI.md` / `AGENTS.md` to follow the handoff, or to reconstruct it
  from the last messages when there is none.
