"""Cursor (CLI cursor-agent e app): le e grava conversas no armazenamento do agente, ~/.cursor/chats.

Formato (medido no CLI 2026.11.22, 25/11/2026):
- ~/.cursor/chats/<md5 da pasta de trabalho>/<agentId>/meta.json ({schemaVersion, createdAtMs, updatedAtMs, cwd,
  hasConversation}) e store.db (SQLite: blobs(id, data) enderecados por SHA-256 do conteudo; meta(key '0') = JSON em
  hex com agentId, latestRootBlobId, name, mode, createdAt...).
- A raiz (protobuf) tem f1 = historico que o modelo recebe (blobs JSON {role, content}) e f8 repetido, um por turno
  (cada f8 aponta para um blob {f1: turno}). Turno: f1 -> blob da pergunta (f1 texto, f2 id, f25 horario em ms); f2 -> blobs dos passos (f1 texto
  da resposta {f1 texto}, f2 chamada de ferramenta, f3 raciocinio).
- Conversa gravada aqui leva a marca meta('tagteam') = {"fonte": ..., "turnos": n}: espelho que ninguem continuou nao
  volta; turno a mais = continuada no Cursor.
"""
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone

import pb
from claude_jsonl import Conversa, Turno, MAX_DESCRICAO_FERRAMENTA, _cortar

ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
MARCA = "tagteam"
NOME_PADRAO = "New Agent"
MAX_BLOB = 64 * 1024 * 1024
_USER_QUERY = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.S)


def base():
    return os.environ.get("TAGTEAM_CURSOR_HOME") or os.path.join(os.path.expanduser("~"), ".cursor")


def pasta_chats():
    return os.path.join(base(), "chats")


def cli():
    caminho = os.path.join(os.environ.get("LOCALAPPDATA", ""), "cursor-agent", "cursor-agent.cmd")
    return caminho if os.path.isfile(caminho) else None


def instalado():
    return os.path.isdir(pasta_chats()) or bool(cli())


def pasta_do_cwd(cwd):
    """O CLI agrupa as conversas pelo md5 do texto exato da pasta de trabalho."""
    return os.path.join(pasta_chats(), hashlib.md5(cwd.encode("utf-8"), usedforsecurity=False).hexdigest())  # nome de pasta do Cursor, nao seguranca


def conversas():
    """[(pasta, agentId)] de todas as conversas (de todas as pastas de trabalho)."""
    raiz = pasta_chats()
    saida = []
    for grupo in (os.listdir(raiz) if os.path.isdir(raiz) else []):
        g = os.path.join(raiz, grupo)
        for aid in (os.listdir(g) if os.path.isdir(g) else []):
            p = os.path.join(g, aid)
            if ID_RE.match(aid) and os.path.isfile(os.path.join(p, "store.db")):
                saida.append((p, aid))
    return saida


# ---------------------------------------------------------------- leitura
def _abrir(pasta):
    return sqlite3.connect(f"file:{os.path.join(pasta, 'store.db')}?mode=ro", uri=True, timeout=10)


def _ler(pasta):
    """(meta, marca, blobs) da conversa; blobs: {id hex: bytes}."""
    c = _abrir(pasta)
    try:
        linhas = dict(c.execute("select key, value from meta").fetchall())
        blobs = {i: d for i, d in c.execute("select id, data from blobs") if isinstance(d, bytes) and len(d) <= MAX_BLOB}
    finally:
        c.close()
    meta = json.loads(bytes.fromhex(linhas.get("0", "")).decode("utf-8") or "{}")
    marca = json.loads(linhas[MARCA]) if MARCA in linhas else None
    return meta if isinstance(meta, dict) else {}, marca if isinstance(marca, dict) else None, blobs


def _raiz(meta, blobs):
    rid = meta.get("latestRootBlobId")
    return pb.ler(blobs[rid]) if isinstance(rid, str) and rid in blobs else []


def _turnos_brutos(meta, blobs):
    saida = []
    for campo, tipo, v in _raiz(meta, blobs):
        if campo == 8 and tipo == 2 and v.hex() in blobs:
            saida += [t for c, ti, t in pb.ler(blobs[v.hex()]) if c == 1 and ti == 2]
    return saida


def marca(pasta):
    try:
        return _ler(pasta)[1]
    except (sqlite3.Error, ValueError, OSError):
        return None


def assinatura(pasta):
    """Muda a cada resposta: o id da raiz e trocado sempre que a conversa anda."""
    meta, _m, _b = _ler(pasta)
    return str(meta.get("latestRootBlobId", ""))


def _texto(v):
    try:
        return v.decode("utf-8")
    except UnicodeDecodeError:
        return ""


def _strings(dados, prof=0):
    """Textos curtos e imprimiveis de uma mensagem protobuf (para resumir uma chamada de ferramenta)."""
    if prof > 6:
        return []
    saida = []
    try:
        campos = pb.ler(dados)
    except ValueError:
        return []
    for _c, tipo, v in campos:
        if tipo != 2:
            continue
        s = _texto(v)
        if s and s.isprintable() and 0 < len(s) <= 300:
            saida.append(s)
        else:
            saida += _strings(v, prof + 1)
    return saida


def _passo(dados):
    """Um passo da resposta: texto, uma linha por ferramenta, ou None (raciocinio)."""
    for campo, tipo, v in pb.ler(dados):
        if campo == 1 and tipo == 2:
            return (pb.obter(v, [1]) or b"").decode("utf-8", "replace").strip() or None
        if campo == 2 and tipo == 2:
            textos = _strings(v)
            desc = textos[-1] if textos else ""
            if len(desc) > MAX_DESCRICAO_FERRAMENTA:
                desc = desc[:MAX_DESCRICAO_FERRAMENTA] + "..."
            return f"`[ferramenta]` {desc}".rstrip()
    return None


def _quando(ms, padrao):
    if isinstance(ms, int) and 946684800000 <= ms <= 4102444800000:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return padrao


def ler_conversa(pasta):
    """Conversa do Cursor -> Conversa (usuario / assistente), como o claude_jsonl monta a do Claude."""
    meta, _m, blobs = _ler(pasta)
    with open(os.path.join(pasta, "meta.json"), encoding="utf-8") as f:
        mj = json.load(f)
    criada = _quando(meta.get("createdAt"), datetime.now(timezone.utc))
    conv = Conversa(sessao=str(meta.get("agentId", os.path.basename(pasta))), cwd=str(mj.get("cwd") or ""))
    turnos = []
    for turno in _turnos_brutos(meta, blobs):
        partes = pb.ler(turno)
        pergunta = next((blobs.get(v.hex()) for c, t, v in partes if c == 1 and t == 2), None)
        if pergunta:
            texto = (pb.obter(pergunta, [1]) or b"").decode("utf-8", "replace").strip()
            quando = _quando(pb.obter(pergunta, [25]), criada)
            if texto:
                turnos.append(Turno("usuario", texto, quando))
        resposta = [x for x in (_passo(blobs[v.hex()]) for c, t, v in partes
                                if c == 2 and t == 2 and v.hex() in blobs) if x]
        if resposta:
            turnos.append(Turno("assistente", "\n\n".join(resposta), turnos[-1].quando if turnos else criada))
    for t in turnos:
        t.texto = _cortar(t.texto)
    conv.turnos = turnos
    nome = meta.get("name") if isinstance(meta.get("name"), str) else ""
    primeiro = next((t.texto for t in turnos if t.papel == "usuario"), "")
    titulo = nome if nome and nome != NOME_PADRAO else (primeiro.splitlines()[0] if primeiro else "")
    conv.titulo = (titulo or "Conversa do Cursor").strip()[:80]
    return conv


# ---------------------------------------------------------------- molde e gravacao
def achar_molde(guardado=None):
    """A conversa nativa (sem a marca) mais recente com ao menos um turno: dela vem o que o Cursor espera na raiz
    (instrucoes do sistema, contexto, ferramentas). Achado, fica guardado no arquivo <guardado> (deste PC), que vale
    quando nao houver nenhuma nativa (apagadas). None se nao houver nenhum dos dois (abrir o CLI e mandar um "oi")."""
    molde = _molde_das_nativas()
    if molde is not None and guardado:
        _guardar_molde(molde, guardado)
    return molde if molde is not None else (_ler_molde(guardado) if guardado else None)


def _guardar_molde(molde, caminho):
    b64 = lambda b: base64.b64encode(b).decode("ascii")   # noqa: E731
    dados = {"cwd": molde["cwd"], "fixos": [b64(b) for b in molde["fixos"]],
             "contexto": b64(molde["contexto"]) if molde.get("contexto") else None,
             "raiz": [[c, t, v if t == 0 else b64(v)] for c, t, v in molde["raiz"]],
             "turno_extra": [[c, t, v if t == 0 else b64(v)] for c, t, v in molde["turno_extra"]]}
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    with open(caminho + ".novo", "w", encoding="utf-8") as f:
        json.dump(dados, f)
    os.replace(caminho + ".novo", caminho)


def _ler_molde(caminho):
    try:
        with open(caminho, encoding="utf-8") as f:
            d = json.load(f)
        de = base64.b64decode
        campos = lambda xs: [[int(c), int(t), int(v) if int(t) == 0 else de(v)] for c, t, v in xs]   # noqa: E731
        return {"cwd": str(d["cwd"]), "fixos": [de(b) for b in d["fixos"]],
                "contexto": de(d["contexto"]) if d.get("contexto") else None,
                "raiz": campos(d["raiz"]), "turno_extra": campos(d["turno_extra"])}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _molde_das_nativas():
    candidatas = []
    for pasta, _aid in conversas():
        try:
            meta, m, blobs = _ler(pasta)
        except (sqlite3.Error, ValueError, OSError):
            continue
        if m is None and _turnos_brutos(meta, blobs):
            candidatas.append((meta.get("createdAt") or 0, pasta, meta, blobs))
    if not candidatas:
        return None
    _q, pasta, meta, blobs = max(candidatas, key=lambda x: x[0])
    with open(os.path.join(pasta, "meta.json"), encoding="utf-8") as f:
        cwd = json.load(f).get("cwd") or ""
    raiz = _raiz(meta, blobs)
    historico = [blobs[v.hex()] for c, t, v in raiz if c == 1 and t == 2 and v.hex() in blobs]
    fixos = []                                     # system e o primeiro contexto do usuario (antes da 1a pergunta)
    for b in historico:
        if json.loads(b).get("role") == "system" or (len(fixos) == 1 and "<user_query>" not in b.decode("utf-8", "replace")):
            fixos.append(b)
        else:
            break
    turno = pb.ler(_turnos_brutos(meta, blobs)[0])
    pergunta = pb.ler(next(blobs[v.hex()] for c, t, v in turno if c == 1))
    contexto = next((blobs.get(v.hex()) for c, t, v in pergunta if c == 10), None)
    return {"cwd": cwd, "raiz": [x for x in raiz if x[0] not in (1, 8, 26, 38)], "fixos": fixos,
            "turno_extra": [x for x in turno if x[0] in (5, 9)], "contexto": contexto}


def _ms(dt):
    return int(dt.timestamp() * 1000)


def _pares(turnos):
    """[(pergunta, resposta ou None)] na ordem; resposta sem pergunta antes entra junto da anterior."""
    pares = []
    for t in turnos:
        if t.papel == "usuario":
            pares.append([t, None])
        elif pares:
            ant = pares[-1][1]
            pares[-1][1] = t if ant is None else Turno("assistente", ant.texto + "\n\n" + t.texto, ant.quando)
    return pares


def gravar_conversa(conv, cwd, molde, aid, titulo, fonte):
    """Grava a conversa como do CLI do Cursor em ~/.cursor/chats/<md5(cwd)>/<aid>/. Devolve a pasta."""
    blobs = {}

    def guardar(dados):
        h = hashlib.sha256(dados).digest()
        blobs[h.hex()] = dados
        return h

    fixos = [b.replace(json.dumps(molde["cwd"])[1:-1].encode(), json.dumps(cwd)[1:-1].encode()) for b in molde["fixos"]]
    historico = [guardar(b) for b in fixos]
    contexto = guardar(molde["contexto"]) if molde.get("contexto") else None
    turnos = []
    agora = _ms(datetime.now(timezone.utc))
    for pergunta, resposta in _pares(conv.turnos):
        quando = _ms(pergunta.quando) if pergunta.quando else agora
        campos = [[1, 2, pergunta.texto.encode("utf-8")], [2, 2, str(uuid.uuid4()).encode()], [3, 2, b""], [4, 0, 1]]
        if contexto:
            campos.append([10, 2, contexto])
        campos += [[25, 0, quando], [26, 0, quando]]
        turno = [[1, 2, guardar(pb.gravar(campos))]]
        historico.append(guardar(json.dumps({"role": "user", "content": [
            {"type": "text", "text": f"<user_query>\n{pergunta.texto}\n</user_query>"}]}, ensure_ascii=False).encode("utf-8")))
        if resposta:
            texto = pb.gravar([[1, 2, resposta.texto.encode("utf-8")], [2, 0, quando]])
            turno.append([2, 2, guardar(pb.gravar([[1, 2, texto]]))])
            historico.append(guardar(json.dumps({"role": "assistant", "content": [
                {"type": "text", "text": resposta.texto}]}, ensure_ascii=False).encode("utf-8")))
        turno += [[3, 2, str(uuid.uuid4()).encode()]] + molde["turno_extra"] + [[10, 2, str(uuid.uuid4()).encode()]]
        turnos.append(pb.gravar(turno))
    raiz_campos = [[1, 2, h] for h in historico] + [[8, 2, guardar(pb.gravar([[1, 2, t]]))] for t in turnos]
    for campo, tipo, v in molde["raiz"]:
        if campo == 9:
            v = ("file:///app/backend/server/" + _codificar(cwd)).encode()
        raiz_campos.append([campo, tipo, v])
    raiz_campos += [[26, 0, agora], [38, 2, str(uuid.uuid4()).encode()]]
    raiz = guardar(pb.gravar(raiz_campos))
    criada = _ms(conv.turnos[0].quando) if conv.turnos and conv.turnos[0].quando else agora
    meta = {"agentId": aid, "latestRootBlobId": raiz.hex(), "name": titulo, "mode": "default",
            "isRunEverything": False, "createdAt": criada, "blobEncryptionKey": secrets.token_hex(32)}
    pasta = os.path.join(pasta_do_cwd(cwd), aid)
    tmp = pasta + ".novo"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    db = sqlite3.connect(os.path.join(tmp, "store.db"))
    try:
        db.execute("create table blobs (id text primary key, data blob)")
        db.execute("create table meta (key text primary key, value text)")
        db.executemany("insert into blobs values (?, ?)", list(blobs.items()))
        db.execute("insert into meta values ('0', ?)", (json.dumps(meta, separators=(",", ":")).encode().hex(),))
        db.execute("insert into meta values (?, ?)", (MARCA, json.dumps({**fonte, "turnos": len(turnos)})))
        db.commit()
    finally:
        db.close()
    with open(os.path.join(tmp, "meta.json"), "w", encoding="utf-8") as f:
        # a lista do CLI (cursor-agent --resume) le o nome daqui, nao do store.db: sem "title" aparece "New Agent"
        json.dump({"schemaVersion": 1, "createdAtMs": criada, "hasConversation": True, "updatedAtMs": agora,
                   "cwd": cwd, **({"title": titulo} if titulo else {})}, f, ensure_ascii=False)
    if os.path.isdir(pasta):
        shutil.rmtree(pasta)
    os.replace(tmp, pasta)
    return pasta


def _codificar(cwd):
    import urllib.parse
    return urllib.parse.quote(cwd, safe=":")         # como o CLI grava: C:%5CUsers%5C...


def turnos(pasta):
    meta, _m, blobs = _ler(pasta)
    return len(_turnos_brutos(meta, blobs))


def id_para_sessao(sessao, versao=0):
    return str(uuid.uuid5(uuid.UUID("6a1f3c2e-9b7d-4e5a-8c1f-2d3e4f5a6b7c"), f"cursor:{sessao}" + (f":{versao}" if versao else "")))


def apagar(pasta):
    """Tira uma copia nossa (nunca uma conversa nativa: so as que tem a marca)."""
    if marca(pasta) is not None and os.path.isdir(pasta):
        shutil.rmtree(pasta)


# ---------------------------------------------------------------- app do Cursor (so leitura)
# O app guarda as conversas no proprio banco (globalStorage/state.vscdb, tabela cursorDiskKV), nao em ~/.cursor/chats:
# composerData:<id> (nome, pasta, datas), composerConvHeaders:<id>:<pedaco> (a ordem das mensagens: type 1 = usuario,
# 2 = assistente) e bubbleId:<id>:<mensagem> (o texto). O app fica aberto e grava ali o tempo todo: aqui so se le, em
# modo somente leitura. Claude -> app e a opcao nativa dele ("Import Claude Code Conversations").
MAX_VALOR_APP = 16 * 1024 * 1024


def banco_app():
    padrao = os.path.join(os.environ.get("APPDATA", ""), "Cursor", "User", "globalStorage", "state.vscdb")
    return os.environ.get("TAGTEAM_CURSOR_APP_DB") or padrao


def _abrir_app():
    return sqlite3.connect(f"file:{banco_app()}?mode=ro", uri=True, timeout=10)


def _valor(c, chave):
    linha = c.execute("select value from cursorDiskKV where key = ?", (chave,)).fetchone()
    if not linha or linha[0] is None or len(linha[0]) > MAX_VALOR_APP:
        return None
    try:
        return json.loads(linha[0])
    except (ValueError, TypeError):
        return None


def _cabecalhos(c, cid):
    saida = []
    for _chave, valor in c.execute("select key, value from cursorDiskKV where key like ? order by key",
                                  (f"composerConvHeaders:{cid}:%",)):
        try:
            pedaco = json.loads(valor) if valor and len(valor) <= MAX_VALOR_APP else []
        except (ValueError, TypeError):
            continue
        saida += [h for h in pedaco if isinstance(h, dict) and isinstance(h.get("bubbleId"), str)]
    return saida


def conversas_app():
    """[(id, composerData)] das conversas do app com ao menos uma mensagem. [] se o app nao estiver instalado."""
    if not os.path.isfile(banco_app()):
        return []
    c = _abrir_app()
    try:
        ids = [k.split(":", 1)[1] for (k,) in c.execute(
            "select key from cursorDiskKV where key like 'composerData:%'")]
        saida = []
        for cid in ids:
            if not ID_RE.match(cid):
                continue
            dados = _valor(c, f"composerData:{cid}")
            if isinstance(dados, dict) and not dados.get("isDraft") and _cabecalhos(c, cid):
                saida.append((cid, dados))
        return saida
    finally:
        c.close()


def assinatura_app(dados, cabecalhos):
    """Muda quando a conversa anda: a ultima atualizacao e a lista de mensagens."""
    base = json.dumps([dados.get("lastUpdatedAt"), dados.get("conversationCheckpointLastUpdatedAt"),
                       [h.get("bubbleId") for h in cabecalhos]], separators=(",", ":"))
    return hashlib.sha256(base.encode()).hexdigest()


def _texto_da_mensagem(b):
    """Texto de uma mensagem do app; raciocinio fica de fora; ferramenta vira uma linha."""
    texto = b.get("text") if isinstance(b.get("text"), str) else ""
    ferramenta = b.get("toolFormerData")
    if isinstance(ferramenta, dict) and not texto.strip():
        nome = str(ferramenta.get("name") or ferramenta.get("tool") or "ferramenta")
        return f"`[{nome[:60]}]`"
    return texto.strip()


def ler_conversa_app(cid):
    """(Conversa, assinatura) de uma conversa do app."""
    c = _abrir_app()
    try:
        dados = _valor(c, f"composerData:{cid}") or {}
        cabecalhos = _cabecalhos(c, cid)
        turnos = []
        for h in cabecalhos:
            b = _valor(c, f"bubbleId:{cid}:{h['bubbleId']}")
            if not isinstance(b, dict):
                continue
            texto = _texto_da_mensagem(b)
            if not texto:
                continue
            criada = _quando(b.get("startedAtMs") or h.get("startedAtMs"), datetime.now(timezone.utc))
            papel = "usuario" if h.get("type") == 1 else "assistente"
            if papel == "assistente" and turnos and turnos[-1].papel == "assistente":
                turnos[-1].texto += "\n\n" + texto
            else:
                turnos.append(Turno(papel, texto, criada))
    finally:
        c.close()
    for t in turnos:
        t.texto = _cortar(t.texto)
    uri = (dados.get("workspaceIdentifier") or {}).get("uri") or {}
    conv = Conversa(sessao=cid, cwd=str(uri.get("fsPath") or "") if isinstance(uri, dict) else "")
    conv.turnos = turnos
    nome = dados.get("name") if isinstance(dados.get("name"), str) else ""
    primeiro = next((t.texto for t in turnos if t.papel == "usuario"), "")
    conv.titulo = (nome or (primeiro.splitlines()[0] if primeiro else "") or "Conversa do Cursor").strip()[:80]
    return conv, assinatura_app(dados, cabecalhos)
