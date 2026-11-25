"""Le uma conversa do Claude Code (.jsonl) e devolve os turnos em texto, para outra ferramenta mostrar.

Mantem o que a pessoa escreveu e o texto das respostas; cada uso de ferramenta vira uma linha curta.
Pula subagente (sidechain), linha de sistema, resultado de ferramenta e linha quebrada.
"""
import json
import re
from dataclasses import dataclass, field
from datetime import datetime

MAX_LINHA = 20 * 1024 * 1024      # linha maior que isso e ignorada (anexo gigante)
MAX_TEXTO_TURNO = 12_000          # caracteres por mensagem mostrada
MAX_DESCRICAO_FERRAMENTA = 140

_TAGS_FORA = re.compile(
    r"<(system-reminder|local-command-caveat|local-command-stdout|local-command-stderr|"
    r"command-message|bash-stdout|bash-stderr|user-memory-input|pasted_content[^>]*)>.*?</\1\s*>",
    re.S)
_TAG_PASTED = re.compile(r"<pasted_content[^>]*>(.*?)</pasted_content[^>]*>", re.S)
_CMD_NOME = re.compile(r"<command-name>\s*(.*?)\s*</command-name>", re.S)
_CMD_ARGS = re.compile(r"<command-args>\s*(.*?)\s*</command-args>", re.S)
_BASH_IN = re.compile(r"<bash-input>\s*(.*?)\s*</bash-input>", re.S)


@dataclass
class Turno:
    papel: str            # "usuario" ou "assistente"
    texto: str
    quando: datetime


@dataclass
class Conversa:
    sessao: str
    titulo: str = ""
    entrypoint: str = ""
    cwd: str = ""
    turnos: list = field(default_factory=list)


def _quando(o):
    t = o.get("timestamp")
    if isinstance(t, str):
        try:
            return datetime.fromisoformat(t.replace("Z", "+00:00"))
        except ValueError:
            return None          # horario ilegivel: a linha e pulada por quem chama
    return None


def _cortar(texto, limite=MAX_TEXTO_TURNO):
    if len(texto) <= limite:
        return texto
    return texto[:limite].rstrip() + f"\n\n[... cortado: {len(texto) - limite} caracteres a mais]"


def limpar_texto_usuario(texto):
    """Tira as marcacoes que o Claude Code poe em volta do que a pessoa digitou."""
    nome = _CMD_NOME.search(texto)
    if nome:
        args = _CMD_ARGS.search(texto)
        return (nome.group(1) + (" " + args.group(1) if args and args.group(1) else "")).strip()
    bash = _BASH_IN.search(texto)
    if bash:
        return "! " + bash.group(1).strip()
    texto = _TAG_PASTED.sub(lambda m: "[texto colado]\n" + m.group(1).strip(), texto)
    texto = _TAGS_FORA.sub("", texto)
    return texto.strip()


def _resumo_ferramenta(bloco):
    nome = str(bloco.get("name") or "ferramenta")[:60]
    entrada = bloco.get("input") if isinstance(bloco.get("input"), dict) else {}
    desc = ""
    for chave in ("description", "file_path", "pattern", "query", "url", "skill", "prompt", "command"):
        v = entrada.get(chave)
        if isinstance(v, str) and v.strip():
            desc = v.strip().splitlines()[0]
            break
    if len(desc) > MAX_DESCRICAO_FERRAMENTA:
        desc = desc[:MAX_DESCRICAO_FERRAMENTA] + "..."
    return f"`[{nome}]` {desc}".rstrip()


SUFIXO_CONTINUA = " (part 1, continued in another)"


def ler_conversa(caminho, sessao):
    """Le o .jsonl linha a linha (arquivo pode estar sendo escrito) e monta os turnos."""
    conv = Conversa(sessao=sessao)
    titulo_ia = titulo_proprio = continua_em = ""
    turnos = []
    with open(caminho, "rb") as f:
        for bruta in f:
            if len(bruta) > MAX_LINHA:
                continue
            try:
                o = json.loads(bruta)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(o, dict):
                continue
            tipo = o.get("type")
            if tipo == "ai-title" and isinstance(o.get("aiTitle"), str):
                titulo_ia = o["aiTitle"]
                continue
            if tipo == "custom-title" and isinstance(o.get("customTitle"), str):
                titulo_proprio = o["customTitle"]
                continue
            if tipo == "continued-in" and isinstance(o.get("continuedInSessionId"), str):
                continua_em = o["continuedInSessionId"]       # o Claude Code seguiu esta conversa em outro arquivo
                continue
            if tipo not in ("user", "assistant") or o.get("isSidechain") or o.get("isMeta"):
                continue
            if not conv.entrypoint and isinstance(o.get("entrypoint"), str):
                conv.entrypoint = o["entrypoint"]
            if not conv.cwd and isinstance(o.get("cwd"), str):
                conv.cwd = o["cwd"]
            quando = _quando(o)
            msg = o.get("message")
            if quando is None or not isinstance(msg, dict):
                continue
            conteudo = msg.get("content")
            if tipo == "user":
                partes = []
                if isinstance(conteudo, str):
                    partes.append(conteudo)
                elif isinstance(conteudo, list):
                    if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in conteudo):
                        continue
                    partes += [b.get("text", "") for b in conteudo if isinstance(b, dict) and b.get("type") == "text"]
                    if any(isinstance(b, dict) and b.get("type") == "image" for b in conteudo):
                        partes.append("[imagem anexada]")
                texto = limpar_texto_usuario("\n".join(p for p in partes if isinstance(p, str)))
                if texto:
                    turnos.append(Turno("usuario", texto, quando))
            else:
                if not isinstance(conteudo, list):
                    continue
                linhas = []
                for b in conteudo:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text" and isinstance(b.get("text"), str) and b["text"].strip():
                        linhas.append(b["text"].strip())
                    elif b.get("type") == "tool_use":
                        linhas.append(_resumo_ferramenta(b))
                if not linhas:
                    continue
                texto = "\n\n".join(linhas)
                # respostas seguidas do assistente (texto, ferramenta, texto) viram um turno so
                if turnos and turnos[-1].papel == "assistente":
                    turnos[-1].texto += "\n\n" + texto
                else:
                    turnos.append(Turno("assistente", texto, quando))
    for t in turnos:
        t.texto = _cortar(t.texto)
    conv.turnos = turnos
    primeiro = next((t.texto for t in turnos if t.papel == "usuario"), "")
    primeira_linha = primeiro.splitlines()[0] if primeiro else ""
    conv.titulo = (titulo_proprio or titulo_ia or primeira_linha or "Conversa do Claude").strip()[:80]
    if continua_em:
        # a continuacao tem o mesmo titulo: sem a marca, as outras IAs mostram duas iguais e a antiga parece cortada
        conv.titulo = conv.titulo[:80 - len(SUFIXO_CONTINUA)].rstrip() + SUFIXO_CONTINUA
    return conv
