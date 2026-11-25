# How TagTeam works

TagTeam converts conversations between five native formats and keeps them in step with hooks. Claude Code is the hub:
each AI syncs with Claude, and whatever just arrived from one side is relayed to the others in the same run.

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
| `scripts/antigravity_db.py` + `pb.py` | reads/writes Antigravity's SQLite + protobuf conversations (lossless protobuf editor) |
| `scripts/servidor_local.py` | talks to Antigravity's local language server (load, rename, delete conversations live) |
| `scripts/grok.py` | reads/writes Grok sessions (`updates.jsonl`, `chat_history.jsonl`, `summary.json`) |
| `scripts/codex.py` | talks to Codex through its official local API (`codex app-server`): list, read, native import |
| `scripts/codex_ambiente.py` | Codex environment = Claude's: skill junctions, native plugins, MCP servers |
| `scripts/sincronizar.py` | the engine: every direction, versions, mirrors, relay, per-PC state |
| `scripts/gancho.py` | Claude Code and Grok hook (throttled; synchronous inside Grok) |
| `scripts/gancho_antigravity.py` | Antigravity `Stop` hook (sync + Gemini quota handoff) |
| `scripts/gancho_codex.py` | Codex `Stop`/`SessionEnd` hook (`~/.codex/hooks.json`) |
| `scripts/cursor.py` | reads and writes the Cursor CLI chat store (`~/.cursor/chats`) |
| `scripts/gancho_cursor.py` | Cursor `stop`/`sessionEnd` hook (`~/.cursor/hooks.json`) |
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

## OpenAI Codex (app and CLI)
- The app and the CLI share `~/.codex`; one write shows up in both. TagTeam never touches Codex's databases: it starts
  `codex app-server` (JSON per line over stdio) for each run and kills its process tree at the end.
- **Claude → Codex** uses Codex's own session importer (`externalAgentConfig/import`, type `SESSIONS`): it creates the
  conversation with the session title, appends to the **same** conversation when the source changes, does nothing
  when it didn't, and never brings back a conversation deleted in Codex. Its map (source path → conversation) lives in
  `~/.codex/external_agent_session_imports.json`.
- The importer only accepts sessions under `<home>/.claude/projects` whose **working folder exists on this PC**, and it
  skips the others silently. TagTeam imports a trimmed copy (user/assistant/title lines only) with this PC's folder,
  from a staging folder that is the `USERPROFILE` of the app-server child; the copy is deleted after the import.
- **Turns made in Codex have UUID ids**; imported turns have `external-import-turn-N`, and `rollout-N` when an update
  appends only replies. A conversation with a UUID turn was continued in Codex: when the Claude session also moves on,
  TagTeam forgets the importer's entry so the new Claude version becomes a separate conversation, and the continued one
  stays (and goes to Claude as "[Codex] …"). Codex drops the name of an imported conversation once it is continued;
  TagTeam restores the original title.
- **Codex → Claude**: `thread/list` (only `cli`, `vscode` = app and `appServer`; never `exec`, sub-agents or ephemeral
  threads) and `thread/read` with turns. Only conversations whose `updatedAt` changed are read again.
- Hooks: `~/.codex/hooks.json` (Claude hook format; Codex doesn't read `~/.claude/settings.json`). Codex runs the
  command through **PowerShell**, so it is `& "python.exe" -I "gancho_codex.py"`; without `&` a quoted path is just a
  string and nothing runs. Codex only runs **trusted** hooks: `instalar.py` marks TagTeam's own hooks through the
  official API (`hooks/list` → `config/value/write` on `hooks.state`), never anyone else's.
- **Same environment as Claude** (`scripts/codex_ambiente.py`): one junction per skill in `~/.codex/skills` (Codex
  follows junctions; `skills/extraRoots/set` is not persisted, so junctions it is), Claude's enabled plugins installed
  natively from a *local* marketplace that points at `~/.claude/plugins/marketplaces/<name>` (reinstalled when Claude's
  version is not in Codex's cache yet), and Claude's global MCP servers written with `config/value/write` (Codex's own
  importer only adds servers that are missing, so a changed token would never arrive). Skills Codex copied from an
  earlier import are moved to `~/.codex/skills-desativadas` (nothing is deleted).
- Shortcuts: "Codex" (`--dangerously-bypass-approvals-and-sandbox`) and "Codex --resume" (`codex resume --all`), in
  Windows Terminal as administrator. A new PC gets the CLI from the official installer
  (`https://chatgpt.com/codex/install.ps1`, non-interactive); the app stays with you.

## Versions, mirrors and the relay
1. Changed at the source → rewritten at the destination (same logical conversation, never duplicated).
2. Imported and untouched → never sent back (mirror).
3. Continued at the destination and changed at the source → **new version** next to the continued copy. This is
   exactly what happens when you tag out: every switch becomes a new conversation on the other side.
4. Deleted at the destination → not recreated (unless `--forcar`).
5. What one run imports into Claude from Gemini, Grok or Codex is relayed to the other two in the same run.

## The handoff
- Claude: `statusline.py` stores the `rate_limits` Claude Code sends to the status line; at ≥ 95% (5-hour or 7-day
  window) the `Stop` hook returns `decision: block` once per conversation and limit window.
- Gemini: the Antigravity `Stop` hook asks the local server for `RetrieveUserQuotaSummary` (the quota group of the
  model in use) and returns `decision: continue` with the request.
- The receiving side is told by the generated `GEMINI.md` / `AGENTS.md` to follow the handoff, or to reconstruct it
  from the last messages when there is none.

## Cursor (CLI `cursor-agent`)
- Chats live in `~/.cursor/chats/<md5 of the exact working-folder text>/<agentId>/`: `meta.json` plus `store.db`
  (SQLite: `blobs(id = sha256 hex, data)` and `meta(key '0')` = hex JSON with `agentId`, `latestRootBlobId`, `name`).
  The root blob (protobuf) holds the model history (`f1`, JSON role/content blobs) and **one `f8` per turn**.
- **The `--resume` list reads the title from `meta.json` (`title`)**, not from `store.db`; without it every chat shows
  as "New Agent".
- Writing copies the system prompt and context from the newest native chat (the template). A PC with no native chat
  gets no copies until you open the CLI once.
- Copies carry `meta('tagteam') = {source, turns}`: same turn count = mirror (never goes back); more turns = continued
  in Cursor (goes to Claude as "[Cursor] …", and a newer Claude version becomes a new Cursor chat).
- `~/.cursor/hooks.json` runs `gancho_cursor.py` on `stop` and `sessionEnd` (with `-p` only `sessionEnd` fires).
- Environment: Cursor reads `~/.claude/skills`, `~/.codex/skills`, `~/.grok/skills`, `CLAUDE.md` and Claude's enabled
  plugins natively; `instalar.py` writes `~/.cursor/mcp.json` and the claude.ai skill junctions in `~/.cursor/skills`.
- The Cursor **app** stores its own chats in `%APPDATA%\Cursor\User\globalStorage\state.vscdb`; its setting *Import
  Claude Code Conversations* shows Claude's sessions there.
