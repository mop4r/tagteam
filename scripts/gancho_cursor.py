"""Hook do Cursor (app e CLI cursor-agent): leva a conversa atual para o Claude Code, que repassa as outras IAs.

Registrado em ~/.cursor/hooks.json (stop e sessionEnd) pelo instalar.py. O Cursor manda JSON no stdin com
conversation_id (ou session_id) e hook_event_name. Responde "{}" (o Cursor espera JSON) e sai em milissegundos: a
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
    evento = str(entrada.get("hook_event_name", "")).lower()
    conversa = str(entrada.get("conversation_id") or entrada.get("session_id") or "")
    if not ID_RE.match(conversa):
        return 0
    if evento == "sessionend" or (evento == "stop" and gancho.deve_rodar("cursor-" + conversa, time.time())):
        gancho.lancar(["--direcao", "claude-de-cursor", "--cursor", conversa])
    return 0


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 - hook nunca pode quebrar o Cursor; o erro de verdade fica no log do filho
        pass
    print("{}")
    sys.exit(0)
