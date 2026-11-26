"""Hook do Antigravity (app e CLI agy): leva a conversa atual para o Claude Code, em segundo plano.

Registrado em ~/.gemini/config/hooks.json (evento Stop) pelo instalar.py. O Antigravity manda JSON no stdin
(camelCase: conversationId, continuationActive, modelName) e espera JSON no stdout:
- "{}" deixa o agente parar normalmente;
- {"decision": "continue", "reason": ...} faz ele escrever a passagem antes (limite perto do fim, uma vez
  por conversa e janela; ver passagem.py).
Sai rapido; a sincronizacao vai para um processo a parte.
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gancho  # noqa: E402

ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
BASE_APP = os.path.join(os.path.expanduser("~"), ".gemini", "antigravity")
TIMEOUT_COTA_S = 4


def consultar_cota():
    """Cota da conta pelo servidor local do app ou do CLI (os dois se registram em antigravity/daemon)."""
    import servidor_local
    for dono in servidor_local.DONOS:
        srv = servidor_local.achar(BASE_APP, dono)
        if srv:
            try:
                return servidor_local.chamar(srv, "RetrieveUserQuotaSummary", {}, tentativas=0, timeout=TIMEOUT_COTA_S)
            except servidor_local.ErroServidor:
                continue
    return None


def main():
    try:
        entrada = json.loads(sys.stdin.buffer.read(gancho.MAX_ENTRADA).decode("utf-8-sig") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return "{}"
    if not isinstance(entrada, dict):
        return "{}"
    cascade = str(entrada.get("conversationId", ""))
    gancho.anotar("antigravity", "Stop")
    if not ID_RE.match(cascade) or not gancho.ia_marcada("antigravity"):
        return "{}"
    import passagem
    texto = passagem.decidir_gemini(cascade, bool(entrada.get("continuationActive")),
                                    entrada.get("modelName", ""), consultar_cota)
    if texto:
        gancho.liberar("agy-" + cascade)           # o Stop seguinte (com a passagem escrita) sincroniza na hora
        return json.dumps({"decision": "continue", "reason": texto}, ensure_ascii=False)
    gancho.disparar("agy-" + cascade, ["--direcao", "claude", "--cascade", cascade], time.time())
    return "{}"


if __name__ == "__main__":
    try:
        saida = main()
    except Exception:  # noqa: BLE001 - hook nunca pode travar o agente; o erro do trabalho fica no log do filho
        saida = "{}"
    sys.stdout.write(saida)
