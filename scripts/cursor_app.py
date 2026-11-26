"""Grava sessoes do Claude como conversas do app do Cursor (state.vscdb), so com o app fechado.

O importador nativo do Cursor ("Import Claude Code Conversations") nao acha nada no Windows (monta a pasta do projeto
do Claude trocando so "/"), entao o TagTeam escreve no banco do app o que ele mesmo escreveria para uma conversa: uma
linha em composerHeaders (a lista), composerData:<id>, composerConvHeaders:<id>:000000 (a ordem das mensagens) e um
bubbleId:<id>:<mensagem> por mensagem. Tudo copiado de uma conversa nativa (o molde, da versao do app instalada), com
o texto trocado; a conversationState fica vazia e o app monta o contexto pelas mensagens (provado: continuar a
conversa no app traz a historia).

Regras: so com o app fechado (aberto, ele guarda o estado em memoria e sobrescreveria); so formato conhecido
(composerData._v em VERSOES); so pastas que o app ja conhece (as mesmas que o importador nativo olharia); o id da
conversa sai da sessao e da versao (uuid5), para estado perdido nao duplicar.
"""
import copy
import json
import os
import sqlite3
import time
import uuid

import cursor

VERSOES = (18,)                     # composerData._v que o TagTeam sabe escrever
NS = uuid.UUID("3f1a8c52-7d0e-4b8e-9c41-5b2f6a7d9e10")
MAX_TEXTO = 200_000
CHUNK = 256
TIMEOUT_S = 10
CAMPOS_HEADERS = ("composerId", "workspaceId", "createdAt", "lastUpdatedAt", "isArchived", "isSubagent", "recency",
                  "checkpointAt", "subagentTypeName", "value")
INSERIR_HEADER = ("insert into composerHeaders(composerId, workspaceId, createdAt, lastUpdatedAt, isArchived, isSubagent,"
                  " recency, checkpointAt, subagentTypeName, value) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")


class FormatoDesconhecido(RuntimeError):
    """O banco do app nao tem o formato que o TagTeam sabe escrever (app atualizado): nada e gravado."""


def id_para_sessao(sessao, versao=0):
    return str(uuid.uuid5(NS, f"claude-app:{sessao}:{versao}"))


def app_aberto():
    """O app do Cursor esta rodando (Cursor.exe). O CLI (cursor-agent, node) nao conta."""
    if os.name != "nt":
        return False
    import servidor_local
    return any(nome == "cursor.exe" for _pai, nome in servidor_local._processos().values())


def abrir():
    """Conexao de escrita no banco do app (so chamar com o app fechado)."""
    return sqlite3.connect(cursor.banco_app(), timeout=TIMEOUT_S)


def conferir_formato(c):
    """FormatoDesconhecido se as tabelas e colunas que o TagTeam escreve nao estao como esperado."""
    tabelas = {n for (n,) in c.execute("select name from sqlite_master where type='table'")}
    if not {"cursorDiskKV", "composerHeaders"} <= tabelas:
        raise FormatoDesconhecido("tabelas do app mudaram")
    colunas = tuple(r[1] for r in c.execute("pragma table_info(composerHeaders)"))
    if colunas != CAMPOS_HEADERS:
        raise FormatoDesconhecido(f"colunas de composerHeaders mudaram: {colunas[:12]}")


def chave_da_pasta(caminho):
    """Pasta comparavel: barras normais, sem barra no fim, letra do disco e tudo em minusculas (Windows)."""
    return os.path.normcase(os.path.normpath(str(caminho or ""))).rstrip("\\/")


def projetos(c):
    """{pasta comparavel: (workspaceId, workspaceIdentifier)} das pastas que o app conhece (conversas e projetos)."""
    saida = {}

    def anotar(ident, wid=None):
        if not isinstance(ident, dict):
            return
        uri = ident.get("uri") if isinstance(ident.get("uri"), dict) else {}
        fs = uri.get("fsPath")
        wid = wid or ident.get("id")
        if isinstance(fs, str) and fs and isinstance(wid, str) and wid:
            saida.setdefault(chave_da_pasta(fs), (wid, ident))
    for wid, valor in c.execute("select workspaceId, value from composerHeaders"):
        try:
            anotar((json.loads(valor) or {}).get("workspaceIdentifier"), wid)
        except (ValueError, TypeError, AttributeError):
            continue
    linha = c.execute("select value from ItemTable where key = 'cursor/glass.additionalProjects'").fetchone() \
        if c.execute("select 1 from sqlite_master where name='ItemTable'").fetchone() else None
    try:
        lista = json.loads(linha[0]) if linha and linha[0] else []
    except (ValueError, TypeError):
        lista = []                  # lista de projetos ilegivel: ficam as pastas das conversas
    for p in lista if isinstance(lista, list) else []:
        anotar(p.get("workspaceIdentifier") if isinstance(p, dict) else None)
    return saida


def _valor(c, chave):
    return cursor._valor(c, chave)


def mensagens(c, cid):
    """Quantas mensagens a conversa tem no app agora (None se ela nao existe mais: apagada no app)."""
    if not c.execute("select 1 from composerHeaders where composerId = ?", (cid,)).fetchone():
        return None
    return len(cursor._cabecalhos(c, cid))


def achar_molde(c, nossos=()):
    """Partes de uma conversa nativa do app (a mais recente com pergunta e resposta), sem o texto dela:
    {"dados", "head", "usuario", "assistente"}. None se o app ainda nao tem conversa nativa."""
    linhas = c.execute("select composerId, value from composerHeaders order by lastUpdatedAt desc").fetchall()
    for cid, valor in linhas:
        if cid in nossos or not cursor.ID_RE.match(cid or ""):
            continue
        dados = _valor(c, f"composerData:{cid}")
        if not isinstance(dados, dict) or dados.get("isDraft"):
            continue
        bolhas = {}
        for h in cursor._cabecalhos(c, cid):
            b = _valor(c, f"bubbleId:{cid}:{h['bubbleId']}")
            if isinstance(b, dict) and h.get("type") in (1, 2) and (b.get("text") or "").strip():
                bolhas.setdefault(h["type"], b)
        if 1 in bolhas and 2 in bolhas:
            try:
                head = json.loads(valor)
            except (ValueError, TypeError):
                continue
            return _limpar({"dados": dados, "head": head, "usuario": bolhas[1], "assistente": bolhas[2]})
    return None


def _limpar(molde):
    """Tira do molde o que e da conversa de origem (texto, ids, estado, uso), para nada dela vazar para as copias."""
    m = copy.deepcopy(molde)
    for parte in ("usuario", "assistente"):
        b = m[parte]
        for campo in ("text", "richText", "conversationState", "requestId", "checkpointId", "allThinkingBlocks",
                      "codeBlocks", "toolFormerData", "thinking", "usageData", "tokenCount", "serverBubbleId"):
            b.pop(campo, None)
    d = m["dados"]
    for campo in ("conversationState", "text", "richText", "subtitle", "name", "usageData", "todos", "queueItems",
                  "promptTokenBreakdown", "promptContextUsageTree", "conversationCheckpointLastUpdatedAt",
                  "latestChatGenerationUUID", "blobEncryptionKey", "speculativeSummarizationEncryptionKey",
                  "contextTokensUsed", "contextUsagePercent", "headerLog"):
        d.pop(campo, None)
    for campo in ("name", "subtitle", "conversationCheckpointLastUpdatedAt", "contextUsagePercent"):
        m["head"].pop(campo, None)
    return m


def _iso(ms):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ms / 1000)) + f".{int(ms % 1000):03d}Z"


def _rich_text(texto):
    """richText (editor Lexical) de uma mensagem do usuario: um paragrafo por linha."""
    linhas = texto.split("\n")
    filhos = [{"children": [{"detail": 0, "format": 0, "mode": "normal", "style": "", "text": l, "type": "text",
                             "version": 1}] if l else [],
               "direction": None, "format": "", "indent": 0, "type": "paragraph", "version": 1} for l in linhas]
    return json.dumps({"root": {"children": filhos, "direction": None, "format": "", "indent": 0, "type": "root",
                                "version": 1}}, ensure_ascii=False)


def montar(conv, cid, wid, ident, molde, agora_ms):
    """O que gravar no banco para a conversa: (linha de composerHeaders, {chave do cursorDiskKV: valor})."""
    turnos = [t for t in conv.turnos if (t.texto or "").strip()]
    cab, kv = [], {}
    inicio = agora_ms - len(turnos)
    for i, t in enumerate(turnos):
        tipo = 1 if t.papel == "usuario" else 2
        bid = str(uuid.uuid5(NS, f"{cid}:{i}"))
        ms = int(t.criada.timestamp() * 1000) if getattr(t, "criada", None) else inicio + i
        texto = t.texto[:MAX_TEXTO]
        b = copy.deepcopy(molde["usuario" if tipo == 1 else "assistente"])
        b.update(bubbleId=bid, type=tipo, text=texto, createdAt=_iso(ms), conversationState="")
        if tipo == 1:
            b["richText"] = _rich_text(texto)
        kv[f"bubbleId:{cid}:{bid}"] = b
        cab.append({"bubbleId": bid, "type": tipo, "grouping": {"isRenderable": True, "hasText": True,
                    "isShortPlainText": len(texto) < 80 and "\n" not in texto, "toolDisplayComputed": True},
                    "createdAt": _iso(ms), "startedAtMs": ms, "completedAtMs": ms})
    for n in range(0, max(len(cab), 1), CHUNK):
        kv[f"composerConvHeaders:{cid}:{n // CHUNK:06d}"] = cab[n:n + CHUNK]
    criado = cab[0]["startedAtMs"] if cab else agora_ms
    atualizado = max(cab[-1]["completedAtMs"] if cab else agora_ms, criado)
    primeira = next((t.texto for t in turnos if t.papel == "usuario"), "")
    subtitulo = (primeira.splitlines()[0] if primeira else "")[:80]
    dados = copy.deepcopy(molde["dados"])
    dados.update(composerId=cid, name=conv.titulo, subtitle=subtitulo, createdAt=criado, lastUpdatedAt=atualizado,
                 conversationState="", text="", richText="", hasUnreadMessages=False, isDraft=False,
                 fullConversationHeadersOnly=[], conversationMap={}, generatingBubbleIds=[], status="completed",
                 latestChatGenerationUUID=str(uuid.uuid5(NS, f"{cid}:geracao")), workspaceIdentifier=ident,
                 headerLog={"chunkSize": CHUNK, "chunkCount": len(range(0, max(len(cab), 1), CHUNK)),
                            "count": len(cab), "generation": str(uuid.uuid5(NS, f"{cid}:{len(cab)}"))})
    dados["tagteam"] = {"fonte": f"claude:{conv.sessao}", "mensagens": len(cab)}   # marca de copia (reserva do estado)
    kv[f"composerData:{cid}"] = dados
    head = copy.deepcopy(molde["head"])
    head.update(composerId=cid, name=conv.titulo, subtitle=subtitulo, createdAt=criado, lastUpdatedAt=atualizado,
                hasUnreadMessages=False, isDraft=False, isArchived=False, workspaceIdentifier=ident)
    linha = (cid, wid, criado, atualizado, 0, 0, atualizado, None, "", json.dumps(head, ensure_ascii=False))
    return linha, kv


def gravar(c, conv, cid, wid, ident, molde, agora_ms):
    """Grava (ou regrava por inteiro) a conversa no banco do app, numa transacao so. Devolve quantas mensagens tem."""
    linha, kv = montar(conv, cid, wid, ident, molde, agora_ms)
    with c:
        apagar(c, cid, commit=False)
        c.execute(INSERIR_HEADER, linha)
        c.executemany("insert into cursorDiskKV(key, value) values (?, ?)",
                      [(k, json.dumps(v, ensure_ascii=False)) for k, v in kv.items()])
    return sum(1 for k in kv if k.startswith("bubbleId:"))


def apagar(c, cid, commit=True):
    """Tira do banco tudo o que e da conversa (so usado nas copias do TagTeam)."""
    if not cursor.ID_RE.match(cid or ""):
        raise ValueError("id invalido")

    def tirar():
        c.execute("delete from composerHeaders where composerId = ?", (cid,))
        for padrao in (f"composerData:{cid}", f"composerConvHeaders:{cid}:%", f"bubbleId:{cid}:%",
                       f"checkpointId:{cid}:%", f"composerTranscriptHeightTail:{cid}", f"composerRootWriterDiagnostic:{cid}"):
            c.execute("delete from cursorDiskKV where key like ?", (padrao,))
    if commit:
        with c:
            tirar()
    else:
        tirar()
