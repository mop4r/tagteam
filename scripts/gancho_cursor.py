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


def _anotar(evento, conversa):
    """Ultimos eventos recebidos (so o nome e o id), para saber se o Cursor chamou o hook. Arquivo pequeno, rotativo."""
    try:
        os.makedirs(gancho.PASTA, exist_ok=True)
        caminho = os.path.join(gancho.PASTA, "gancho-cursor.log")
        linhas = []
        if os.path.exists(caminho) and os.path.getsize(caminho) < 64 * 1024:
            with open(caminho, encoding="utf-8") as f:
                linhas = f.read().splitlines()[-200:]
        linhas.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {evento[:30]} {conversa[:40] if ID_RE.match(conversa) else '?'}")
        with open(caminho, "w", encoding="utf-8") as f:
            f.write("\n".join(linhas) + "\n")
    except OSError:
        return          # o registro e so diagnostico: falhar ao gravar nunca quebra o hook


def main():
    try:
        entrada = json.loads(sys.stdin.buffer.read(gancho.MAX_ENTRADA).decode("utf-8-sig") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 0
    if not isinstance(entrada, dict):
        return 0
    evento = str(entrada.get("hook_event_name", "")).lower()
    conversa = str(entrada.get("conversation_id") or entrada.get("session_id") or "")
    _anotar(evento, conversa)
    gancho.anotar("cursor", evento)
    if not ID_RE.match(conversa) or not gancho.ia_marcada("cursor"):
        return 0
    args = ["--direcao", "claude-de-cursor", "--cursor", conversa]
    if evento == "sessionend":
        gancho.lancar(args)
    elif evento == "stop":
        gancho.disparar("cursor-" + conversa, args, time.time())
    return 0


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 - hook nunca pode quebrar o Cursor; o erro de verdade fica no log do filho
        pass
    print("{}")
    sys.exit(0)
