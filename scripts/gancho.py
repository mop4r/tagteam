"""Hook do Claude Code: leva a conversa atual para o Antigravity, em segundo plano.

Stop (fim de cada resposta): so a sessao atual, no maximo a cada INTERVALO_S por sessao.
SessionEnd: a sessao atual, sem esperar o intervalo.
SessionStart: reaplica o ambiente (instalar.py) e sincroniza tudo nos dois sentidos (pega as que
chegaram de outro PC pelo sync), depois de um atraso.
Nunca imprime nada (saida num Stop pode travar o Claude) e sai em milissegundos.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)   # com python -I a pasta do script nao entra no caminho de import (passagem.py)
SESSAO_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
INTERVALO_S = 120
MAX_ENTRADA = 1024 * 1024
MAX_LOG = 512 * 1024
PASTA = os.path.join(tempfile.gettempdir(), "tagteam")
SEM_JANELA = 0x08000000 | 0x00000008          # CREATE_NO_WINDOW | DETACHED_PROCESS
# O Grok poe o hook num job do Windows que nao deixa processo filho sair (CREATE_BREAKAWAY_FROM_JOB da
# "Acesso negado") e encerra a arvore inteira quando o Grok sai: dentro dele a sincronizacao roda esperando.
PRAZO_ESPERANDO_S = 60


def _python_sem_janela():
    w = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return w if os.path.exists(w) else sys.executable


def lancar(args, script="sincronizar.py", esperar=False):
    """Roda o script com a saida no log. esperar=True: so volta quando ele termina (ou no prazo)."""
    os.makedirs(PASTA, exist_ok=True)
    log = os.path.join(PASTA, "sincronizar.log")
    if os.path.exists(log) and os.path.getsize(log) > MAX_LOG:
        os.replace(log, log + ".1")
    with open(log, "a", encoding="utf-8") as saida:
        saida.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} {script} {' '.join(args)}\n")
        saida.flush()
        comando = [_python_sem_janela(), "-I", os.path.join(AQUI, script), *args]
        if not esperar:
            subprocess.Popen(comando, stdin=subprocess.DEVNULL, stdout=saida, stderr=saida,
                             creationflags=SEM_JANELA, close_fds=True)
            return
        try:
            subprocess.run(comando, stdin=subprocess.DEVNULL, stdout=saida, stderr=saida,
                           creationflags=SEM_JANELA, timeout=PRAZO_ESPERANDO_S, check=False)
        except subprocess.TimeoutExpired:
            saida.write(f"prazo de {PRAZO_ESPERANDO_S}s estourado; o proximo Stop ou SessionEnd tenta de novo\n")


def deve_rodar(sessao, agora):
    """Marca por sessao: Stop seguidos dentro do intervalo nao relancam."""
    os.makedirs(PASTA, exist_ok=True)
    marca = os.path.join(PASTA, f"ultimo-{sessao}")
    if os.path.exists(marca) and agora - os.path.getmtime(marca) < INTERVALO_S:
        return False
    with open(marca, "w", encoding="utf-8") as f:
        f.write(str(agora))
    return True


def liberar(chave):
    """Tira a marca do intervalo: o proximo Stop sincroniza na hora (usado depois de pedir a passagem)."""
    marca = os.path.join(PASTA, f"ultimo-{chave}")
    if os.path.exists(marca):
        os.remove(marca)


def main():
    gemini = os.path.join(os.path.expanduser("~"), ".gemini")
    tem_antigravity = any(os.path.isdir(os.path.join(gemini, n, "conversations")) for n in ("antigravity", "antigravity-cli")) \
        or os.path.isdir(os.path.join(os.path.expanduser("~"), ".grok", "sessions"))     # ou o Grok
    try:
        # bytes + utf-8-sig: o stdin do Windows decodifica no codigo de pagina local, e o pipe do
        # PowerShell ainda poe BOM na frente; o Claude Code manda UTF-8 puro
        entrada = json.loads(sys.stdin.buffer.read(MAX_ENTRADA).decode("utf-8-sig") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 0
    if not isinstance(entrada, dict):
        return 0
    evento = str(entrada.get("hook_event_name", ""))
    sessao = str(entrada.get("session_id", ""))
    # O Grok CLI roda os hooks do ~/.claude/settings.json (compatibilidade) e define GROK_SESSION_ID: o evento e
    # de uma sessao do Grok, nao do Claude. Leva a sessao do Grok para o Claude (que repassa ao Antigravity) e
    # nunca pede a passagem do Claude (o uso do limite do Claude nao vale para o Grok).
    sessao_grok = os.environ.get("GROK_SESSION_ID", "")
    if sessao_grok:
        if SESSAO_RE.match(sessao_grok) and os.path.isdir(os.path.join(os.path.expanduser("~"), ".grok", "sessions")) and \
                (evento == "SessionEnd" or (evento == "Stop" and deve_rodar("grok-" + sessao_grok, time.time()))):
            lancar(["--direcao", "claude-de-grok", "--grok", sessao_grok], esperar=True)
        return 0
    if evento == "SessionStart":
        # sempre: num PC novo o instalar.py instala o agy e os atalhos; depois so grava o que mudou
        lancar([], "instalar.py")
        if tem_antigravity:
            lancar(["--atraso", "90"])
        return 0
    if not tem_antigravity or not SESSAO_RE.match(sessao):
        return 0
    if evento == "Stop":
        import passagem
        texto = passagem.decidir_claude(sessao, bool(entrada.get("stop_hook_active")), time.time())
        if texto:                                  # limite perto do fim: escreve a passagem antes de parar
            liberar(sessao)
            sys.stdout.write(json.dumps({"decision": "block", "reason": texto}, ensure_ascii=False))
            return 0
    if evento == "SessionEnd" or (evento == "Stop" and deve_rodar(sessao, time.time())):
        lancar(["--sessao", sessao])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 - hook nunca pode quebrar o Claude; o erro de verdade fica no log do filho
        sys.exit(0)
