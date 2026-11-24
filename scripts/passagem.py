"""Passagem automatica perto do limite: o agente que vai parar escreve onde o trabalho esta, para o outro seguir.

Claude Code: o status line (statusline.py) grava o uso dos limites de 5 h e 7 dias; o hook Stop le e, a partir
de LIMIAR %, bloqueia uma vez ("decision": "block") pedindo a passagem.
Antigravity: o hook Stop consulta a cota no servidor local (RetrieveUserQuotaSummary) e, a partir de LIMIAR %,
pede a passagem uma vez ("decision": "continue").
Uma vez por conversa e por janela de limite (marca com a hora de renovacao). A passagem fica na propria conversa
e a sincronizacao a leva para o outro lado.
"""
import json
import os
import re
import time

def _limiar():
    """% do limite usado a partir da qual pede a passagem. TAGTEAM_HANDOFF_THRESHOLD (1 a 100) troca, para testar."""
    try:
        v = float(os.environ.get("TAGTEAM_HANDOFF_THRESHOLD", "95"))
    except ValueError:
        return 95.0
    return v if 1 <= v <= 100 else 95.0


LIMIAR = _limiar()
VALIDADE_USO_S = 15 * 60       # leitura do status line mais velha que isso nao vale
ID_RE = re.compile(r"^[0-9A-Za-z_-]{1,80}$")


def _pasta():
    import gancho
    return gancho.PASTA


def arquivo_uso_claude():
    return os.path.join(_pasta(), "uso-claude.json")


def pedido(destino, pct, janela):
    """Texto que o hook devolve ao agente (receita: o que a mensagem E, em ordem)."""
    return (
        f"Usage limit at {pct:.0f}% ({janela} window). Before you stop, write now, as the last message of this "
        f"reply and without using tools, a handoff so {destino} can continue this work without you. Start with "
        f"the line '## Handoff to {destino}' followed by exactly these five lines:\n"
        "- Request: what the user asked for, in one sentence.\n"
        "- Done: what is finished and verified (files, commands, commits).\n"
        "- In progress: what was left halfway (file, branch, running command, state).\n"
        "- Next step: the first concrete action for whoever continues.\n"
        "- Watch out: what not to do, where credentials live, open questions for the user.\n"
        "Then stop."
    )


def _marcar(chave):
    """True so na primeira vez para esta chave (conversa + janela)."""
    os.makedirs(_pasta(), exist_ok=True)
    caminho = os.path.join(_pasta(), "passagem-" + re.sub(r"[^0-9A-Za-z_.-]", "_", chave)[:150])
    try:
        fd = os.open(caminho, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    os.write(fd, str(time.time()).encode())
    os.close(fd)
    return True


def _maior(janelas):
    """janelas: [(nome, pct, renova)] -> a mais usada, ou None."""
    validas = [j for j in janelas if isinstance(j[1], (int, float))]
    return max(validas, key=lambda j: j[1]) if validas else None


# ---------------------------------------------------------------- Claude
def gravar_uso_claude(dados, agora):
    """Chamado pelo statusline.py com o JSON que o Claude Code manda para ele."""
    rl = dados.get("rate_limits") if isinstance(dados, dict) else None
    if not isinstance(rl, dict):
        return
    saida = {"atualizado": agora}
    for nome in ("five_hour", "seven_day"):
        j = rl.get(nome)
        if isinstance(j, dict) and isinstance(j.get("used_percentage"), (int, float)):
            saida[nome] = {"pct": float(j["used_percentage"]), "renova": j.get("resets_at")}
    os.makedirs(_pasta(), exist_ok=True)
    tmp = arquivo_uso_claude() + ".novo"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(saida, f)
    os.replace(tmp, arquivo_uso_claude())


def uso_claude(agora):
    try:
        with open(arquivo_uso_claude(), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(d, dict) or agora - float(d.get("atualizado", 0)) > VALIDADE_USO_S:
        return None
    nomes = {"five_hour": "5 h", "seven_day": "7 day"}
    return _maior([(nomes[n], d[n].get("pct"), d[n].get("renova")) for n in nomes if isinstance(d.get(n), dict)])


def decidir_claude(sessao, ja_continuando, agora):
    """Texto do bloqueio (pedir a passagem) ou None."""
    if ja_continuando or not ID_RE.match(str(sessao)):
        return None
    uso = uso_claude(agora)
    if not uso or uso[1] < LIMIAR:
        return None
    if not _marcar(f"claude-{sessao}-{uso[2]}"):
        return None
    return pedido("Gemini or Grok", uso[1], uso[0])


# ---------------------------------------------------------------- Antigravity
def uso_gemini(resumo, modelo=""):
    """Resposta do RetrieveUserQuotaSummary -> (janela, pct usado, renova) do grupo do modelo em uso."""
    grupos = ((resumo or {}).get("response") or {}).get("groups") or []
    terceiros = any(x in str(modelo).lower() for x in ("claude", "gpt"))
    janelas = []
    for g in grupos:
        nome = str(g.get("displayName", "")).lower()
        if ("gemini" in nome) == terceiros:
            continue
        for b in g.get("buckets") or []:
            resto = b.get("remainingFraction")
            if isinstance(resto, (int, float)):
                janelas.append((str(b.get("window", "?")), (1 - float(resto)) * 100, b.get("resetTime")))
    return _maior(janelas)


def decidir_gemini(cascade, ja_continuando, modelo, consultar):
    """consultar() devolve a resposta do RetrieveUserQuotaSummary (ou None). Texto do pedido ou None."""
    if ja_continuando or not ID_RE.match(str(cascade)):
        return None
    resumo = consultar()
    uso = uso_gemini(resumo, modelo) if resumo else None
    if not uso or uso[1] < LIMIAR:
        return None
    if not _marcar(f"agy-{cascade}-{uso[2]}"):
        return None
    return pedido("Claude", uso[1], uso[0])
