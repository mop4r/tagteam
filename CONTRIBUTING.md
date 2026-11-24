# Contributing to TagTeam

Thanks for helping! A few ground rules keep the sync safe for everyone's conversations.

## Setup
```powershell
git clone https://github.com/mop4r/tagteam
cd tagteam
python -m unittest discover -s scripts -p "test_*.py"
```
No dependencies: the standard library only (Python 3.11+).

## Principles
- **Never lose a conversation.** A copy that the user continued is never overwritten; when in doubt, create a new
  version. Add a test for every new path.
- **Native formats only.** Write what the tool itself would write; don't invent fields it might reject.
- **Local only.** No telemetry, no network calls beyond the official CLI installers.
- **Idempotent setup.** `instalar.py` must change nothing on a second run (there is a test pattern for it).
- **Prove it in the real tool.** A hook that works in a unit test can still be killed by the host (see the Grok job
  object note in `docs/HOW-IT-WORKS.md`): check the effect at the destination.

## Good first issues
- macOS / Linux paths and shortcuts.
- Translating code comments from Portuguese to English.
- New agents (Codex CLI, Gemini CLI, Copilot CLI, Cursor) following the `grok.py` pattern.

## Pull requests
Small, focused PRs with tests. Describe how you verified the change end to end (which tools, which direction).
