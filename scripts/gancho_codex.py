"""Hook do Codex (app e CLI): leva a conversa atual para o Claude Code, que repassa ao Antigravity e ao Grok.

Registrado em ~/.codex/hooks.json (Stop e SessionEnd) pelo instalar.py. O Codex manda JSON no stdin no formato dos
hooks do Claude (session_id = id da conversa, hook_event_name). Nunca imprime nada e sai em milissegundos: a
sincronizacao vai para um processo a parte.
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gancho  # noqa: E402

ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def main():
    try:
        entrada = json.loads(sys.stdin.buffer.read(gancho.MAX_ENTRADA).decode("utf-8-sig") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 0
    if not isinstance(entrada, dict):
        return 0
    evento = str(entrada.get("hook_event_name", ""))
    conversa = str(entrada.get("session_id") or entrada.get("thread_id") or os.environ.get("CODEX_THREAD_ID", ""))
    if not ID_RE.match(conversa):
        return 0
    if evento == "SessionEnd" or (evento == "Stop" and gancho.deve_rodar("codex-" + conversa, time.time())):
        gancho.lancar(["--direcao", "claude-de-codex", "--codex", conversa])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 - hook nunca pode quebrar o Codex; o erro de verdade fica no log do filho
        sys.exit(0)
