"""Antigravity -> Claude Code: le uma conversa do app e grava como sessao do Claude (.jsonl).

A sessao gerada tem entrypoint "antigravity-import" em todas as mensagens: e assim que o caminho
contrario (Claude -> Antigravity) reconhece um espelho e nao o devolve.
"""
import glob
import json
import os
import re
import sqlite3
import urllib.parse
import uuid
from datetime import datetime, timezone

import antigravity_db as antigravity
import pb
from claude_jsonl import Conversa, Turno, MAX_DESCRICAO_FERRAMENTA, _cortar

ENTRYPOINT = "antigravity-import"
PREFIXO_TITULO = "[Antigravity] "
# origem -> (entrypoint das mensagens, prefixo do titulo, modelo mostrado)
ORIGENS = {"antigravity": (ENTRYPOINT, PREFIXO_TITULO, "gemini-antigravity"),
           "grok": ("grok-import", "[Grok] ", "grok-cli"),
           "codex": ("codex-import", "[Codex] ", "codex-cli"),
           "cursor": ("cursor-import", "[Cursor] ", "cursor")}
NOMES_ORIGEM = {"antigravity": "Antigravity", "grok": "Grok", "codex": "Codex", "cursor": "Cursor"}
SUFIXO_RAMO = " ({nome} branch)"
VERSAO_PADRAO = "2.1.0"
NAMESPACE = uuid.UUID("3f1c9a7e-5b2d-4e8f-a6c0-2d9e7b4f1a85")


def titulo_ramo(titulo, origem):
    """Marca explicita de fork quando a mesma conversa divergiu em duas IAs ao mesmo tempo.

    Reserva espaco para o prefixo '[IA] ' que linhas_sessao acrescenta, para o leitor (limite 80) nao cortar o sufixo."""
    sufixo = SUFIXO_RAMO.format(nome=NOMES_ORIGEM.get(origem, origem))
    prefixo = ORIGENS.get(origem, ("", "", ""))[1]
    base = (titulo or "").strip()
    teto = 80 if (prefixo and base.startswith(prefixo)) else 80 - len(prefixo)
    if base.endswith(sufixo) and len(base) <= teto:
        return base
    corpo = base[:-len(sufixo)] if base.endswith(sufixo) else base
    return corpo[:max(0, teto - len(sufixo))].rstrip() + sufixo


def projetos_claude():
    return os.path.join(os.path.expanduser("~"), ".claude", "projects")


def id_sessao(cascade, versao=0, origem="antigravity"):
    prefixo = "agy" if origem == "antigravity" else origem
    return str(uuid.uuid5(NAMESPACE, f"{prefixo}:{cascade}" + (f":{versao}" if versao else "")))


def _quando(meta):
    ts = pb.obter(meta, [1]) if meta else None
    if not ts:
        return None
    seg = pb.obter(ts, [1]) or 0
    nanos = pb.obter(ts, [2]) or 0
    if not antigravity.EPOCA_MIN <= seg <= antigravity.EPOCA_MAX:
        return None
    return datetime.fromtimestamp(seg + nanos / 1e9, tz=timezone.utc)


def _texto(v):
    return v.decode("utf-8", "replace").strip() if isinstance(v, bytes) else ""


def _chamadas(resposta):
    linhas = []
    for campo, tipo, v in pb.ler(resposta):
        if campo != 7 or tipo != 2:
            continue
        nome = _texto(pb.obter(v, [2])) or "ferramenta"
        args = _texto(pb.obter(v, [3]))
        try:
            d = json.loads(args) if args else {}
            resumo = next((str(x) for x in d.values() if isinstance(x, str) and x.strip()), "") if isinstance(d, dict) else ""
        except json.JSONDecodeError:
            resumo = args
        resumo = resumo.strip().splitlines()[0] if resumo.strip() else ""
        if len(resumo) > MAX_DESCRICAO_FERRAMENTA:
            resumo = resumo[:MAX_DESCRICAO_FERRAMENTA] + "..."
        linhas.append(f"`[{nome[:60]}]` {resumo}".rstrip())
    return linhas


def ler_conversa_antigravity(cascade, base=None, desde_idx=-1):
    """Passos feitos da conversa -> turnos (usuario / assistente), na ordem (desde_idx filtra so os passos novos)."""
    base = base or antigravity.base_padrao()
    caminho = os.path.join(base, "conversations", cascade + ".db")
    c = sqlite3.connect(f"file:{caminho}?mode=ro", uri=True, timeout=10)
    try:
        passos = c.execute("select step_type, metadata, step_payload from steps where status=? and idx>? order by idx",
                           (antigravity.STATUS_FEITO, desde_idx)).fetchall()
    finally:
        c.close()
    turnos, ultimo = [], None
    for tipo, meta, payload in passos:
        if not isinstance(payload, bytes):
            continue
        quando = _quando(meta) or ultimo
        if quando is None:
            continue
        ultimo = quando
        if tipo == antigravity.TIPO_USUARIO:
            papel, texto = "usuario", _texto(pb.obter(payload, [19, 2]))
        elif tipo == antigravity.TIPO_RESPOSTA:
            resp = pb.obter(payload, [20]) or b""
            partes = [_texto(pb.obter(resp, [1])) or _texto(pb.obter(resp, [8]))] + _chamadas(resp)
            papel, texto = "assistente", "\n\n".join(p for p in partes if p)
        else:
            continue
        if not texto:
            continue
        if turnos and turnos[-1].papel == papel:          # o Claude exige papeis alternados
            turnos[-1].texto += "\n\n" + texto
        else:
            turnos.append(Turno(papel, texto, quando))
    for t in turnos:
        t.texto = _cortar(t.texto)
    titulo, cwd = _titulo_e_pasta(cascade, base)
    primeiro = next((t.texto.splitlines()[0] for t in turnos if t.papel == "usuario"), "")
    return Conversa(sessao=cascade, titulo=(titulo or primeiro or "Conversa do Antigravity")[:80],
                    entrypoint="antigravity", cwd=cwd, turnos=turnos)


def _titulo_e_pasta(cascade, base):
    indice = os.path.join(base, "conversation_summaries.db")
    if not os.path.exists(indice):
        return "", ""
    c = sqlite3.connect(f"file:{indice}?mode=ro", uri=True, timeout=10)
    try:
        linha = c.execute("select title, raw_summary, workspace_uris from conversation_summaries where conversation_id=?",
                          (cascade,)).fetchone()
    except sqlite3.OperationalError:          # indice sem a tabela ainda (instalacao nova)
        linha = None
    finally:
        c.close()
    if not linha:
        return "", ""
    titulo = linha[0] or (_texto(pb.obter(linha[1], [1])) if isinstance(linha[1], bytes) else "")
    cwd = ""
    try:
        uris = json.loads(linha[2] or "[]")
        if uris and isinstance(uris[0], str) and uris[0].startswith("file:///"):
            cwd = urllib.parse.unquote(uris[0][len("file:///"):]).replace("/", "\\")
            if len(cwd) > 1 and cwd[1] == ":":
                cwd = cwd[0].upper() + cwd[1:]
    except (json.JSONDecodeError, TypeError):
        cwd = ""
    return titulo, cwd


def pasta_do_projeto(cwd, raiz=None):
    """Pasta de ~/.claude/projects do cwd (o Claude troca tudo que nao e letra/numero por '-').

    Se ja existe com outra caixa (c-- x C--), usa a existente: no Windows e a mesma pasta."""
    raiz = raiz or projetos_claude()
    chave = re.sub(r"[^A-Za-z0-9]", "-", cwd)
    existente = next((p for p in glob.glob(os.path.join(raiz, "*")) if os.path.basename(p).lower() == chave.lower()), None)
    return existente or os.path.join(raiz, chave)


def versao_claude(raiz=None):
    """Versao do Claude Code gravada na sessao mais recente (a sessao gerada fica igual as dele)."""
    raiz = raiz or projetos_claude()
    arquivos = sorted(glob.glob(os.path.join(raiz, "*", "*.jsonl")), key=os.path.getmtime, reverse=True)[:5]
    for f in arquivos:
        with open(f, encoding="utf-8", errors="replace") as fh:
            for i, linha in enumerate(fh):
                if i > 50:
                    break
                m = re.search(r'"version":"(\d+\.\d+\.\d+)"', linha)
                if m:
                    return m.group(1)
    return VERSAO_PADRAO


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _montar_mensagens(turnos, sessao, cwd, versao, origem, pai=None, base_idx=0):
    """Monta as linhas user/assistant encadeadas por parentUuid a partir de <pai>."""
    entrypoint, _prefixo, modelo = ORIGENS[origem]
    comum = {"isSidechain": False, "userType": "external", "entrypoint": entrypoint, "cwd": cwd,
             "sessionId": sessao, "version": versao}
    out = []
    for i, t in enumerate(turnos):
        chave = f"{sessao}:{base_idx + i}" if base_idx == 0 else f"{sessao}:ff:{base_idx + i}:{origem}"
        meu = str(uuid.uuid5(NAMESPACE, chave))
        if t.papel == "usuario":
            msg = {"role": "user", "content": t.texto}
            linha = {"parentUuid": pai, **comum, "type": "user", "message": msg, "uuid": meu, "timestamp": _iso(t.quando)}
        else:
            msg = {"id": "msg_agy" + uuid.uuid5(NAMESPACE, meu).hex[:20], "type": "message", "role": "assistant",
                   "model": modelo, "content": [{"type": "text", "text": t.texto}],
                   "stop_reason": "end_turn", "stop_sequence": None, "usage": {"input_tokens": 0, "output_tokens": 0}}
            linha = {"parentUuid": pai, **comum, "type": "assistant", "message": msg, "uuid": meu,
                     "timestamp": _iso(t.quando)}
        out.append(linha)
        pai = meu
    return out, pai


def linhas_sessao(conv, sessao, cwd, versao, origem="antigravity"):
    """Linhas do .jsonl no formato do Claude Code: mensagens encadeadas por parentUuid + titulo."""
    _entrypoint, prefixo, _modelo = ORIGENS[origem]
    out, pai = _montar_mensagens(conv.turnos, sessao, cwd, versao, origem)
    titulo = conv.titulo if conv.titulo.startswith(prefixo) else prefixo + conv.titulo
    out.append({"type": "ai-title", "aiTitle": titulo, "sessionId": sessao})
    ultimo_usuario = next((t.texto for t in reversed(conv.turnos) if t.papel == "usuario"), "")
    out.append({"type": "last-prompt", "lastPrompt": ultimo_usuario[:200], "leafUuid": pai, "sessionId": sessao})
    return out


def _analisar_linhas_cauda(linhas):
    """Extrai (ultimo_uuid, cwd, n_msgs) das linhas validas do .jsonl."""
    pai = folha = cwd = None
    n_msgs = 0
    for ln in linhas:
        try:
            o = json.loads(ln)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(o, dict):
            continue
        cwd = cwd or (o.get("cwd") if isinstance(o.get("cwd"), str) else "")
        tipo = o.get("type")
        if tipo in ("user", "assistant") and not o.get("isSidechain") and not o.get("isMeta"):
            n_msgs += 1
            pai = o["uuid"] if isinstance(o.get("uuid"), str) and o["uuid"] else pai
        elif tipo == "last-prompt" and isinstance(o.get("leafUuid"), str) and o["leafUuid"]:
            folha = o["leafUuid"]
    return (pai or folha), (cwd or ""), n_msgs


def _ler_cauda(caminho):
    """Le bytes originais do .jsonl e extrai (bytes, ultimo_uuid, cwd, n_msgs); None se ultima linha estiver quebrada."""
    with open(caminho, "rb") as f:
        bruto = f.read()
    linhas = [ln for ln in bruto.splitlines() if ln.strip()]
    if not linhas:
        return None
    try:
        json.loads(linhas[-1])
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    pai, cwd, n_msgs = _analisar_linhas_cauda(linhas)
    return bruto, pai, cwd, n_msgs


def acrescentar_turnos(caminho, sessao, novos_turnos, versao, origem="antigravity", marca_esperada=None):
    """Acrescenta turnos novos ao fim de um .jsonl existente (fast-forward), preservando intacto tudo o que ja havia.
    Confere a marca do arquivo antes da troca (evita corrida se o Claude gravou no meio). Devolve (tamanho, mtime) ou None."""
    if not novos_turnos:
        return None
    st_antes = os.stat(caminho)
    marca_antes = (st_antes.st_size, round(st_antes.st_mtime, 3))
    if marca_esperada is not None and marca_antes != marca_esperada:
        return None
    cauda = _ler_cauda(caminho)
    if cauda is None:
        return None
    bruto, pai, cwd, n_msgs = cauda
    novas, folha = _montar_mensagens(novos_turnos, sessao, cwd, versao, origem, pai=pai, base_idx=n_msgs + 1)
    ultimo_u = next((t.texto for t in reversed(novos_turnos) if t.papel == "usuario"), None)
    if ultimo_u is not None:
        novas.append({"type": "last-prompt", "lastPrompt": ultimo_u[:200], "leafUuid": folha, "sessionId": sessao})
    prefixo = bruto if bruto.endswith(b"\n") else bruto + b"\n"
    extra = "".join(json.dumps(linha, ensure_ascii=False) + "\n" for linha in novas).encode("utf-8")
    tmp = caminho + ".novo"
    with open(tmp, "wb") as f:
        f.write(prefixo)
        f.write(extra)
    st_agora = os.stat(caminho)
    if (st_agora.st_size, round(st_agora.st_mtime, 3)) != marca_antes:
        os.remove(tmp)
        return None
    os.replace(tmp, caminho)
    datas = [t.quando.timestamp() for t in novos_turnos if getattr(t, "quando", None)]
    if datas:
        ts = max(max(datas), st_antes.st_mtime)
        if 946684800 <= ts <= 4102444800:
            os.utime(caminho, (ts, ts))
    st_fim = os.stat(caminho)
    return (st_fim.st_size, round(st_fim.st_mtime, 3))


def gravar_sessao(conv, sessao, pasta, cwd, versao, origem="antigravity"):
    """Grava <pasta>/<sessao>.jsonl de uma vez (temporario + troca)."""
    os.makedirs(pasta, exist_ok=True)
    destino = os.path.join(pasta, sessao + ".jsonl")
    tmp = destino + ".novo"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        for linha in linhas_sessao(conv, sessao, cwd, versao, origem):
            f.write(json.dumps(linha, ensure_ascii=False) + "\n")
    os.replace(tmp, destino)
    datas = [t.quando.timestamp() for t in conv.turnos if getattr(t, "quando", None)]
    if datas and 946684800 <= max(datas) <= 4102444800:
        os.utime(destino, (max(datas), max(datas)))
    return destino


def achar_sessao(sessao, raiz=None):
    raiz = raiz or projetos_claude()
    achados = glob.glob(os.path.join(raiz, "*", sessao + ".jsonl"))
    return achados[0] if achados else None
