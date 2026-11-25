<p align="center">
  <img src="assets/banner.svg" alt="TagTeam — one conversation, four AIs: Claude Code, Gemini (Antigravity), Grok CLI and OpenAI Codex" width="100%">
</p>

<h1 align="center">TagTeam — Sync Claude Code, Gemini (Antigravity), Grok CLI &amp; OpenAI Codex Conversations</h1>

<p align="center">
  <b>English</b> · <a href="README.pt-BR.md">Português (Brasil)</a> · <a href="https://mop4r.github.io/tagteam/">Project site</a>
</p>

<p align="center">
  <a href="https://github.com/mop4r/tagteam/actions/workflows/tests.yml"><img src="https://img.shields.io/github/actions/workflow/status/mop4r/tagteam/tests.yml?branch=main&style=for-the-badge&label=tests" alt="Tests"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-8B7CF6?style=for-the-badge" alt="MIT license"></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/platform-Windows-0078D4?style=for-the-badge&logo=windows&logoColor=white" alt="Windows">
  <img src="https://img.shields.io/badge/dependencies-zero-3FB950?style=for-the-badge" alt="Zero dependencies">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Claude_Code-D97757?style=flat-square&logo=anthropic&logoColor=white" alt="Claude Code">
  <img src="https://img.shields.io/badge/Gemini_·_Antigravity-8B7CF6?style=flat-square&logo=googlegemini&logoColor=white" alt="Gemini Antigravity">
  <img src="https://img.shields.io/badge/Grok_CLI-111111?style=flat-square&logo=x&logoColor=white" alt="Grok CLI">
  <img src="https://img.shields.io/badge/OpenAI_Codex-10A37F?style=flat-square&logo=openai&logoColor=white" alt="OpenAI Codex">
  <a href="https://github.com/mop4r/tagteam/stargazers"><img src="https://img.shields.io/github/stars/mop4r/tagteam?style=flat-square&color=F4A481" alt="Stars"></a>
  <img src="https://img.shields.io/badge/PRs-welcome-3FB950?style=flat-square" alt="PRs welcome">
</p>

<h3 align="center">Hit a usage limit in the middle of a task? <i>Tag out.</i><br>The next AI picks up the <u>same conversation</u> — history, context and a handoff note included.</h3>

<p align="center">
  <img src="assets/demo.svg" alt="Demo: Claude Code reaches 95% of its limit and writes a handoff, Gemini continues the same conversation, then Grok, and everything is back in claude --resume" width="100%">
</p>

---

## ✨ Why TagTeam?

You are deep into a refactor with **Claude Code** and the 5-hour limit hits. You open **Gemini** (Antigravity), the
**Grok CLI** or **OpenAI Codex**… and start from zero, pasting context, re-explaining the task, losing the thread.

**TagTeam makes your conversations travel.** Every Claude Code session shows up in Gemini, Grok and Codex — and every
Gemini, Grok or Codex session shows up in `claude --resume` (and from there in the others) — automatically, both
ways, with the right title. Close to the
limit, the agent that is about to stop **writes a five-line handoff** so the next one knows exactly where to continue.

> As far as we know, it is the first open-source tool that keeps **one conversation alive across four different
> AI coding agents** — not a chat wrapper, not an API proxy: your real sessions, in each tool's native format.

## 🚀 Features

| | |
|---|---|
| 🔁 **Four-way sync, both directions** | Claude Code ⇄ Gemini (Antigravity app **and** `agy` CLI) ⇄ Grok CLI ⇄ OpenAI Codex (app **and** CLI), through hooks — no command to remember. |
| ▶️ **Native resume everywhere** | Sessions land in each tool's own storage: `claude --resume`, the Antigravity sidebar, `agy`, `grok --resume`, the Codex app and `codex resume`. |
| 🤝 **Automatic handoff** | At 95% of the Claude limit (or the Gemini quota) a hook makes the agent write *Request / Done / In progress / Next step / Watch out* before stopping. |
| 🧠 **Same brain in every tool** | Gemini and Grok read your `~/.claude/CLAUDE.md`, the same skills, the Claude plugins and your MCP servers — by reference, never copied. Codex too: a generated `AGENTS.md` block, one junction per skill, Claude's plugins installed natively from Claude's own marketplaces, and the same MCP servers. |
| 🛡️ **Never clobbers your work** | A conversation continued on the other side is never overwritten: a new version is created and yours stays. Mirrors never bounce back. |
| 💻 **Multi-PC friendly** | Paths from another machine (other user, other drive) are remapped to this one; state is per PC. |
| 🪶 **Zero dependencies** | Pure Python standard library (SQLite, protobuf parsing, TOML) + the official installers of each CLI. |
| 🔒 **Local only** | Nothing leaves your machine. No telemetry, no server, no API keys. |

## ⚡ Quick start

> **Requirements:** Windows 10/11, Python 3.11+, [Claude Code](https://claude.com/claude-code). The Antigravity and Codex
> apps are optional; the `agy` CLI, the Grok CLI and the Codex CLI are installed for you.

```powershell
# 1. Install as a Claude Code skill
git clone https://github.com/mop4r/tagteam "$HOME\.claude\skills\tagteam"

# 2. Wire everything (hooks, Gemini + Grok + Codex environment, CLIs, Start Menu shortcuts). Safe to re-run.
python -I "$HOME\.claude\skills\tagteam\scripts\instalar.py"

# 3. Sign in once to each CLI (opens the browser), then just work.
agy
grok
codex
```

That's it. Start a conversation in any of the four; open another one and it is already there.

<details>
<summary><b>Dry run first?</b></summary>

```powershell
python -I "$HOME\.claude\skills\tagteam\scripts\instalar.py" --seco      # shows what would change
python -I "$HOME\.claude\skills\tagteam\scripts\sincronizar.py" --seco   # shows what would sync
```
</details>

## 🧩 How it works

```mermaid
flowchart LR
    C["🟠 Claude Code<br/><sub>~/.claude/projects/*.jsonl</sub>"]
    G["🟣 Gemini · Antigravity<br/><sub>app + agy CLI (SQLite + protobuf)</sub>"]
    K["⚫ Grok CLI<br/><sub>~/.grok/sessions/*/updates.jsonl</sub>"]
    X["🟢 OpenAI Codex<br/><sub>app + CLI · ~/.codex (official app-server API)</sub>"]
    C <-- "hooks: Stop · SessionEnd" --> G
    C <-- "hooks: Stop · SessionEnd" --> K
    C <-- "hooks: Stop · SessionEnd" --> X
    G -. "relayed through Claude in the same run" .-> K
    K -. "relayed through Claude" .-> X
```

- **Claude Code is the hub.** Each AI syncs with Claude; whatever just arrived from one of them is relayed to the
  others in the same run, so all four converge.
- **Hooks do the work.** Claude Code (`SessionStart`, `Stop`, `SessionEnd`), Antigravity (`Stop`), Grok and Codex
  (`Stop`, `SessionEnd`) each call TagTeam after every reply — throttled to once every 2 minutes per conversation.
- **Native formats.** Claude JSONL, Antigravity's per-conversation SQLite with protobuf steps (written through its
  local language server when the app is open), Grok's ACP `updates.jsonl` + `chat_history.jsonl`, and Codex through
  its own local API (`codex app-server`) and native session import — the app and the CLI share `~/.codex`.
- **No ping-pong.** Every imported copy carries a marker (`entrypoint`, a `claude_import` table, a
  `claude_import.json`); a copy nobody continued is never sent back. Continued copies spawn a new version instead of
  being overwritten.

Deep dive: [`docs/HOW-IT-WORKS.md`](docs/HOW-IT-WORKS.md).

## 🤝 The handoff

When the Claude Code status line reports ≥ 95% of the 5-hour or 7-day limit (or Antigravity's quota API says the
same for Gemini), the `Stop` hook blocks **once** and the agent writes, as its last message:

```markdown
## Handoff to Gemini or Grok
- Request: migrate the auth module to async.
- Done: login, token and middleware migrated; 34 tests green (commit 3f2a91c).
- In progress: session.py half done, branch feat/async-auth.
- Next step: finish SessionStore.refresh() and run tests/auth.
- Watch out: don't touch the legacy /v1 routes; credentials are in .env.local.
```

The receiving AI is told (by the generated `GEMINI.md` / `AGENTS.md`) to look for it and follow the *Next step*. No
handoff because the limit hit hard? It reconstructs those five lines from the last messages before acting.

Test it without waiting for the limit: `set TAGTEAM_HANDOFF_THRESHOLD=10`.

## 📦 What gets mirrored

| From Claude Code | Gemini (Antigravity / agy) | Grok CLI | OpenAI Codex (app / CLI) |
|---|---|---|---|
| Conversations | ✅ both ways (app + CLI) | ✅ both ways | ✅ both ways (app + CLI) |
| `~/.claude/CLAUDE.md` | ✅ via generated `~/.gemini/GEMINI.md` | ✅ loaded natively + `~/.grok/AGENTS.md` | ✅ via a generated block in `~/.codex/AGENTS.md` |
| Skills (`~/.claude/skills`) | ✅ `config/skills.json` | ✅ native | ✅ one junction per skill in `~/.codex/skills` |
| claude.ai account skills (`~/.claude/skills/synced`) | ✅ `config/skills.json` | ✅ junctions in `~/.grok/skills` (so Claude's docx/pdf/pptx/xlsx win over Grok's built-ins) | ✅ junctions |
| Claude plugins | ✅ skills + Antigravity-ready plugins | ✅ enabled in `config.toml` | ✅ installed natively from Claude's local marketplaces (reinstalled when Claude's version changes) |
| MCP servers (`~/.claude.json`) | ✅ `config/mcp_config.json` | ✅ remote servers in `config.toml` | ✅ `config.toml`, through Codex's config API |
| Automatic handoff at the limit | ✅ (quota API) | ✋ manual — Grok doesn't expose usage outside its TUI | ✋ manual |
| Resume picker shortcut | ✅ "Agy CLI --resume" | ✅ "Grok --resume" | ✅ "Codex --resume" |

## 🛠️ Manual commands

```powershell
cd "$HOME\.claude\skills\tagteam\scripts"
python -I sincronizar.py                       # sync everything, every direction
python -I sincronizar.py --direcao grok        # Claude -> Grok only   (antigravity | claude | grok | claude-de-grok | codex | claude-de-codex)
python -I sincronizar.py --sessao <claude-id>  # one Claude session    (--cascade <gemini-id> / --grok <grok-id> / --codex <codex-id>)
python -I sincronizar.py --forcar              # rewrite / recreate deleted copies (never a continued one)
python -m unittest discover -p "test_*.py"     # 128 tests (the Codex ones run the real Codex in a throwaway folder)
```

| Environment variable | Default | What it does |
|---|---|---|
| `TAGTEAM_HANDOFF_THRESHOLD` | `95` | % of the limit that triggers the handoff |
| `TAGTEAM_WORKSPACE` | your home | default working folder (shortcuts, fallback for sessions whose folder doesn't exist here) |
| `TAGTEAM_STATE_DIR` | `~/.tagteam` | per-PC sync state |

Logs: `%TEMP%\tagteam\sincronizar.log`.

## ❓ FAQ

<details><summary><b>Is this affiliated with Anthropic, Google, xAI or OpenAI?</b></summary>

No. TagTeam is an independent community project. It only reads and writes files on your own machine, in the formats
these tools already use.</details>

<details><summary><b>Does it send my conversations anywhere?</b></summary>

No. Everything is local file I/O plus the local servers of Antigravity and Codex. No network calls except the
official CLI installers, when you don't have them yet.</details>

<details><summary><b>Can I use it across several PCs?</b></summary>

Yes. Sync `~/.claude/projects` with whatever you like (git, Syncthing, OneDrive…). On each PC, TagTeam imports the
Claude sessions into that PC's Gemini, Grok and Codex, remapping paths from other users and drives.</details>

<details><summary><b>What happens if I continue the same chat in two tools?</b></summary>

Nothing is lost. A copy that was continued is never overwritten; the next sync creates a new version next to it.</details>

<details><summary><b>Why are some comments in Portuguese?</b></summary>

TagTeam was born as a personal tool in Brazil. User-facing text is English; code comments are being translated —
PRs welcome.</details>

<details><summary><b>macOS / Linux?</b></summary>

Not yet: the shortcuts, the Grok picker and some paths are Windows-specific. The sync core is portable — help wanted!</details>

## 🗺️ Roadmap

- [x] Claude Code ⇄ Antigravity app + `agy` CLI
- [x] Grok CLI, three-way relay
- [x] Automatic handoff (Claude, Gemini)
- [x] OpenAI Codex (app + CLI), four-way relay
- [ ] macOS and Linux
- [ ] More agents: Gemini CLI, Copilot CLI, Cursor
- [ ] Optional cleanup command for test conversations

## 🙌 Contributing

Issues and PRs are very welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). If TagTeam saved your flow, a ⭐ helps
other people find it.

## ⭐ Star history

<a href="https://star-history.com/#mop4r/tagteam&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=mop4r/tagteam&type=Date&theme=dark">
    <img alt="Star history chart" src="https://api.star-history.com/svg?repos=mop4r/tagteam&type=Date">
  </picture>
</a>

## 📄 License

[MIT](LICENSE). Claude, Gemini, Antigravity, Grok, OpenAI and Codex are trademarks of their respective owners; TagTeam is not
endorsed by them.

<p align="center"><sub>Built for people who refuse to lose a train of thought to a rate limit.</sub></p>
