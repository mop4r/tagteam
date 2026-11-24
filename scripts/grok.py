"""Le e grava sessoes do Grok CLI (~/.grok/sessions/<pasta codificada>/<id>/).

Formato (Grok 1.0.x): summary.json (indice: titulo, datas, contagens), updates.jsonl (registro oficial que o
/resume usa) e chat_history.jsonl (mensagens mandadas ao modelo). Tudo JSON, sem formato binario.
Sessao gravada a partir do Claude leva claude_import.json (o Grok ignora arquivo a mais): e assim que a volta
reconhece um espelho e nao o devolve.
"""
import json
import os
import shutil
import urllib.parse
import uuid
from datetime import datetime, timezone

from claude_jsonl import Conversa, Turno, MAX_DESCRICAO_FERRAMENTA, _cortar

MARCA = "claude_import.json"
MODELO_PADRAO = "grok-4.7"
NAMESPACE = uuid.UUID("9b7e2f14-61c3-4d0a-8f25-3e6a1c9d0b47")
MAX_LINHA = 20 * 1024 * 1024


def base():
    return os.path.join(os.path.expanduser("~"), ".grok")


def instalado():
    return os.path.isdir(os.path.join(base(), "sessions"))


def pasta_do_cwd(cwd):
    """O Grok agrupa as sessoes pela pasta de trabalho codificada como URL (C%3A%5CUsers%5C...)."""
    return os.path.join(base(), "sessions", urllib.parse.quote(cwd, safe=""))


def id_para_sessao(sessao, versao=0):
    """Id da sessao no Grok (formato UUID: o Grok sempre o trata como id, nunca como titulo)."""
    return str(uuid.uuid5(NAMESPACE, "claude:" + sessao + (f":{versao}" if versao else "")))


def sessoes():
    """[(pasta da sessao, id)] de todas as sessoes do Grok neste PC."""
    raiz = os.path.join(base(), "sessions")
    if not os.path.isdir(raiz):
        return []
    saida = []
    for grupo in os.listdir(raiz):
        g = os.path.join(raiz, grupo)
        if not os.path.isdir(g):
            continue
        for sid in os.listdir(g):
            d = os.path.join(g, sid)
            if os.path.isfile(os.path.join(d, "updates.jsonl")) and os.path.isfile(os.path.join(d, "summary.json")):
                saida.append((d, sid))
    return saida


def _ler_json(caminho, padrao=None):
    try:
        with open(caminho, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return padrao


def marca(pasta):
    """Conteudo do claude_import.json (sessao que veio do Claude) ou None (sessao nativa do Grok)."""
    m = _ler_json(os.path.join(pasta, MARCA))
    return m if isinstance(m, dict) and isinstance(m.get("turnos"), int) else None


def assinatura(pasta):
    """Muda quando a sessao muda (o Grok so acrescenta no updates.jsonl)."""
    st = os.stat(os.path.join(pasta, "updates.jsonl"))
    return f"{st.st_size}:{int(st.st_mtime)}"


def _ferramenta(u):
    nome = str(u.get("title") or "ferramenta")[:60]
    ri = u.get("rawInput") if isinstance(u.get("rawInput"), dict) else {}
    desc = next((str(ri[k]) for k in ("description", "path", "file_path", "pattern", "query", "url", "command")
                 if isinstance(ri.get(k), str) and ri[k].strip()), "")
    desc = desc.strip().splitlines()[0] if desc.strip() else ""
    if len(desc) > MAX_DESCRICAO_FERRAMENTA:
        desc = desc[:MAX_DESCRICAO_FERRAMENTA] + "..."
    return f"`[{nome}]` {desc}".rstrip()


def ler_sessao(pasta):
    """updates.jsonl -> turnos. Pedacos seguidos da mesma mensagem viram uma; ferramenta vira uma linha curta."""
    resumo = _ler_json(os.path.join(pasta, "summary.json"), {}) or {}
    info = resumo.get("info") if isinstance(resumo.get("info"), dict) else {}
    turnos, buffer, papel_buf, quando_buf = [], [], None, None

    def fechar():
        nonlocal buffer, papel_buf
        if papel_buf and "".join(buffer).strip():
            texto = "".join(buffer).strip()
            if turnos and turnos[-1].papel == papel_buf:
                turnos[-1].texto += "\n\n" + texto
            else:
                turnos.append(Turno(papel_buf, texto, quando_buf))
        buffer, papel_buf = [], None

    with open(os.path.join(pasta, "updates.jsonl"), "rb") as f:
        for bruta in f:
            if len(bruta) > MAX_LINHA:
                continue
            try:
                o = json.loads(bruta)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue                       # linha sendo escrita agora
            u = ((o.get("params") or {}).get("update") or {}) if isinstance(o, dict) else {}
            tipo = u.get("sessionUpdate")
            ts = o.get("timestamp_us") or (o.get("timestamp") or 0) * 1_000_000
            quando = datetime.fromtimestamp(ts / 1e6, tz=timezone.utc) if ts else None
            if tipo in ("user_message_chunk", "agent_message_chunk"):
                papel = "usuario" if tipo == "user_message_chunk" else "assistente"
                texto = ((u.get("content") or {}).get("text")) if isinstance(u.get("content"), dict) else None
                if not isinstance(texto, str):
                    continue
                if papel != papel_buf:
                    fechar()
                    papel_buf, quando_buf = papel, quando
                buffer.append(texto)
            elif tipo == "tool_call":
                fechar()
                linha = _ferramenta(u)
                if turnos and turnos[-1].papel == "assistente":
                    turnos[-1].texto += "\n\n" + linha
                elif quando:
                    turnos.append(Turno("assistente", linha, quando))
    fechar()
    turnos = [t for t in turnos if t.quando]
    for t in turnos:
        t.texto = _cortar(t.texto)
    titulo = resumo.get("generated_title") or resumo.get("session_summary") or ""
    primeiro = next((t.texto.splitlines()[0] for t in turnos if t.papel == "usuario"), "")
    return Conversa(sessao=str(info.get("id") or os.path.basename(pasta)), titulo=(titulo or primeiro or "Conversa do Grok")[:80],
                    entrypoint="grok", cwd=str(info.get("cwd") or ""), turnos=turnos)


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def gravar_sessao(conv, sid, cwd, marca_fonte, prefixo_titulo=""):
    """Grava a sessao inteira numa pasta temporaria e troca de uma vez. Devolve a pasta."""
    destino = os.path.join(pasta_do_cwd(cwd), sid)
    tmp = destino + ".novo"
    if os.path.isdir(tmp):
        shutil.rmtree(tmp)
    os.makedirs(tmp)
    updates, chat, n = [], [], 0
    prompt = -1
    for t in conv.turnos:
        us = int(t.quando.timestamp() * 1_000_000)
        n += 1
        if t.papel == "usuario":
            prompt += 1
            upd = {"sessionUpdate": "user_message_chunk", "content": {"type": "text", "text": t.texto},
                   "_meta": {"modelId": MODELO_PADRAO, "promptIndex": prompt}}
            metodo = "session/update"
            chat.append({"type": "user", "content": [{"type": "text", "text": t.texto}]})
        else:
            upd = {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": t.texto}}
            metodo = "session/update"
            chat.append({"type": "assistant", "content": t.texto, "model_id": MODELO_PADRAO})
        updates.append({"timestamp": us // 1_000_000, "timestamp_us": us, "method": metodo,
                        "params": {"sessionId": sid, "update": upd, "_meta": {"eventId": f"{sid}-{n}"}}})
        if t.papel == "assistente":
            n += 1
            updates.append({"timestamp": us // 1_000_000, "timestamp_us": us + 1, "method": "_x.ai/session/update",
                            "params": {"sessionId": sid, "update": {"sessionUpdate": "turn_completed",
                                                                     "prompt_id": str(uuid.uuid5(NAMESPACE, f"{sid}:{prompt}")),
                                                                     "stop_reason": "end_turn"},
                                       "_meta": {"eventId": f"{sid}-{n}"}}})
    titulo = (prefixo_titulo + conv.titulo)[:120]
    ultimo_assistente = next((t.texto for t in reversed(conv.turnos) if t.papel == "assistente"), "")
    resumo = {"info": {"id": sid, "cwd": cwd}, "session_summary": titulo, "generated_title": titulo,
              "title_is_manual": True, "created_at": _iso(conv.turnos[0].quando), "updated_at": _iso(conv.turnos[-1].quando),
              "last_active_at": _iso(conv.turnos[-1].quando), "num_messages": len(updates), "num_chat_messages": len(chat),
              "current_model_id": MODELO_PADRAO, "chat_format_version": 1, "grok_home": base(),
              "agent_name": "grok-build", "sandbox_profile": "off",
              "last_turn_summary": ultimo_assistente.splitlines()[0][:120] if ultimo_assistente else ""}
    for nome, linhas in (("updates.jsonl", updates), ("chat_history.jsonl", chat)):
        with open(os.path.join(tmp, nome), "w", encoding="utf-8", newline="\n") as f:
            for item in linhas:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
    with open(os.path.join(tmp, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(resumo, f, ensure_ascii=False, indent=2)
    # bytes_updates: o Grok so acrescenta no updates.jsonl; tamanho diferente = alguem continuou a sessao la
    with open(os.path.join(tmp, MARCA), "w", encoding="utf-8") as f:
        json.dump(dict(marca_fonte, turnos=len(conv.turnos),
                       bytes_updates=os.path.getsize(os.path.join(tmp, "updates.jsonl"))), f)
    if os.path.isdir(destino):
        shutil.rmtree(destino)
    os.replace(tmp, destino)
    return destino


def apagar(pasta):
    """Tira a versao antiga (nunca uma sessao nativa: so as que levam a marca do import)."""
    if marca(pasta) is not None and os.path.isdir(pasta):
        shutil.rmtree(pasta)
