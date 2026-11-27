"""Codex (app e CLI, os dois no mesmo ~/.codex) pela API local oficial: `codex app-server` (JSON por linha no stdio).

- Claude -> Codex: importacao nativa do Codex (`externalAgentConfig/import`, tipo SESSIONS). Ela le o .jsonl do Claude,
  cria a conversa e, quando a fonte muda, acrescenta o que entrou na MESMA conversa; fonte igual e conversa apagada no
  Codex ficam como estao. O registro dela (caminho da fonte -> conversa) fica em ~/.codex/external_agent_session_imports.json.
  Ela so aceita sessao que esteja em <home>/.claude/projects e cuja pasta de trabalho exista neste PC (a de outro PC,
  C:\\Users\\<outro>\\..., e pulada CALADA): por isso a fonte e uma copia enxuta, com a pasta deste PC, numa area de
  preparo que e a "home" so do app-server filho (preparar()).
- Turno importado tem id "external-import-turn-N"; turno feito no Codex tem outro id. E assim que se sabe, sem contar,
  que uma conversa importada foi continuada no Codex (turnos_nativos()).
- Codex -> Claude: `thread/read` com os turnos, convertido em claude_jsonl.Conversa.
Nada aqui grava nos bancos do Codex: tudo passa pela API, que o app e o CLI usam. A unica escrita fora dela e no registro
de importacao (esquecer/mover uma entrada), para a versao nova de uma conversa continuada nascer como conversa nova.
"""
import contextlib
import glob
import json
import os
import queue
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timezone

from claude_jsonl import Conversa, Turno, MAX_DESCRICAO_FERRAMENTA, _cortar

CLIENTE = {"name": "tagteam-sync", "version": "1"}
PRAZO_S = 60                      # resposta comum
PRAZO_IMPORTACAO_S = 600          # lote de importacao (conversa grande demora)
MAX_LINHA = 512 * 1024 * 1024     # thread/read de conversa longa vem numa linha so
LOTE_IMPORTACAO = 20
MARCA_IMPORTADA = "<EXTERNAL SESSION IMPORTED>"   # o Codex fecha o ultimo turno importado com isto
# so conversas de gente: CLI (codex-tui), app (vscode) e clientes da API; fora exec (automatica) e subagentes
FONTES_DE_GENTE = ("cli", "vscode", "appServer")
SEM_JANELA = 0x08000000           # CREATE_NO_WINDOW
PREFIXO_LONGO = "\\\\?\\"          # \\?\C:\... (caminho longo do Windows, como o Codex grava)


class ErroCodex(RuntimeError):
    pass


def base():
    return os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")


def executavel():
    padrao = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "OpenAI", "Codex", "bin", "codex.exe")
    if os.path.isfile(padrao):
        return padrao
    return shutil.which("codex")


def instalado():
    return bool(executavel()) and os.path.isdir(base())


# ---------------------------------------------------------------- cliente da API
class Servidor:
    """`codex app-server` filho, encerrado (com a arvore) ao sair do `with`."""

    def __init__(self, home=None, env_extra=None, comando=None):
        self.home = home or base()
        exe = executavel()
        if comando is None and not exe:
            raise ErroCodex("codex nao instalado")
        self.comando = comando or [exe, "app-server"]
        self.env = dict(os.environ, CODEX_HOME=self.home, **(env_extra or {}))
        self.proc = None
        self.respostas = {}
        self.avisos = queue.Queue()
        self.cond = threading.Condition()
        self.proximo = 0
        self.morto = False

    def __enter__(self):
        self.proc = subprocess.Popen(self.comando, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, env=self.env, creationflags=SEM_JANELA)
        threading.Thread(target=self._ler, daemon=True).start()
        try:
            self.chamar("initialize", {"clientInfo": CLIENTE})
            self._enviar({"method": "initialized"})
        except BaseException:
            self.fechar()
            raise
        return self

    def __exit__(self, *exc):
        self.fechar()

    def fechar(self):
        if self.proc and self.proc.poll() is None:
            # o app-server sobe filhos (exec-server, code-mode-host): encerra a arvore inteira
            subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, creationflags=SEM_JANELA, timeout=30, check=False)
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.proc:
            for canal in (self.proc.stdin, self.proc.stdout):
                # canal de processo ja encerrado: o close pode reclamar do pipe quebrado, e nao ha o que fazer
                with contextlib.suppress(OSError):
                    canal.close()

    def _ler(self):
        try:
            for bruta in self.proc.stdout:
                if len(bruta) > MAX_LINHA:
                    continue
                try:
                    msg = json.loads(bruta)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if not isinstance(msg, dict):
                    continue
                if "id" in msg and ("result" in msg or "error" in msg):
                    with self.cond:
                        self.respostas[msg["id"]] = msg
                        self.cond.notify_all()
                elif "method" in msg and "id" not in msg:
                    self.avisos.put(msg)
                # pedido do servidor para o cliente (aprovacao): este cliente nunca inicia turno, ignora
        finally:
            with self.cond:
                self.morto = True
                self.cond.notify_all()

    def _enviar(self, msg):
        try:
            self.proc.stdin.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
            self.proc.stdin.flush()
        except OSError as e:
            raise ErroCodex(f"app-server fechou: {e}") from e

    def chamar(self, metodo, params, prazo=PRAZO_S):
        with self.cond:
            self.proximo += 1
            mid = self.proximo
        self._enviar({"id": mid, "method": metodo, "params": params})
        limite = time.monotonic() + prazo
        with self.cond:
            while mid not in self.respostas:
                resta = limite - time.monotonic()
                if self.morto:
                    raise ErroCodex(f"app-server saiu durante {metodo}")
                if resta <= 0:
                    raise ErroCodex(f"{metodo}: sem resposta em {prazo}s")
                self.cond.wait(resta)
            msg = self.respostas.pop(mid)
        if "error" in msg:
            erro = msg["error"] if isinstance(msg["error"], dict) else {}
            raise ErroCodex(f"{metodo}: {str(erro.get('message', erro))[:300]}")
        return msg.get("result")

    def esperar_aviso(self, metodo, condicao, prazo):
        limite = time.monotonic() + prazo
        while True:
            resta = limite - time.monotonic()
            if resta <= 0:
                raise ErroCodex(f"sem o aviso {metodo} em {prazo}s")
            try:
                msg = self.avisos.get(timeout=min(resta, 1.0))
            except queue.Empty:
                if self.morto:
                    raise ErroCodex(f"app-server saiu esperando {metodo}") from None
                continue
            if msg.get("method") == metodo and condicao(msg.get("params") or {}):
                return msg["params"]


# ---------------------------------------------------------------- leitura
def listar(srv):
    """{id: resumo} das conversas de gente (ativas e arquivadas), sem turnos."""
    saida = {}
    for arquivadas in (False, True):
        cursor = None
        for _ in range(1000):                       # teto de paginas (100 mil conversas)
            r = srv.chamar("thread/list", {"limit": 100, "cursor": cursor, "archived": arquivadas,
                                           "sourceKinds": list(FONTES_DE_GENTE)}) or {}
            for t in r.get("data") or []:
                if isinstance(t, dict) and isinstance(t.get("id"), str) and de_gente(t):
                    saida[t["id"]] = t
            cursor = r.get("nextCursor")
            if not cursor:
                break
    return saida


def de_gente(t):
    """Fora subagente (source em JSON, threadSource 'subagent', parentThreadId) e conversa efemera."""
    fonte = t.get("source")
    return isinstance(fonte, str) and fonte in FONTES_DE_GENTE and not t.get("parentThreadId") \
        and t.get("threadSource") != "subagent" and not t.get("ephemeral")


def ler(srv, tid):
    r = srv.chamar("thread/read", {"threadId": tid, "includeTurns": True}, prazo=PRAZO_IMPORTACAO_S) or {}
    th = r.get("thread")
    if not isinstance(th, dict) or not isinstance(th.get("turns"), list):
        raise ErroCodex(f"thread/read {tid}: resposta sem turnos")
    return th


TURNO_IMPORTADO = "external-import-turn-"
# turno feito no Codex tem id UUID; a importacao usa external-import-turn-N e, ao acrescentar so respostas numa conversa
# ja importada, rollout-N (medido em 268 conversas, 25/11/2026)
TURNO_NATIVO = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def turnos_nativos(th):
    """Turnos feitos no proprio Codex (id UUID); os da importacao nao contam."""
    return sum(1 for t in th.get("turns") or [] if isinstance(t, dict) and TURNO_NATIVO.match(str(t.get("id", ""))))


def importada(th):
    return any(isinstance(t, dict) and str(t.get("id", "")).startswith(TURNO_IMPORTADO) for t in th.get("turns") or [])


def assinatura(th):
    """Muda quando entra turno, item ou o ultimo turno termina."""
    turnos = th.get("turns") or []
    ult = turnos[-1] if turnos else {}
    return f"{len(turnos)}:{ult.get('id', '')}:{ult.get('status', '')}:{len(ult.get('items') or [])}"


def cwd_de(th):
    cwd = th.get("cwd") if isinstance(th.get("cwd"), str) else ""
    if cwd.startswith(PREFIXO_LONGO):
        cwd = cwd[len(PREFIXO_LONGO):]
    return cwd[0].upper() + cwd[1:] if len(cwd) > 1 and cwd[1] == ":" else cwd


def _quando(seg, padrao):
    if isinstance(seg, (int, float)) and 946684800 <= seg <= 4102444800:
        return datetime.fromtimestamp(seg, tz=timezone.utc)
    return padrao


def _linha(nome, desc):
    desc = (desc or "").strip()
    desc = desc.splitlines()[0] if desc else ""
    if len(desc) > MAX_DESCRICAO_FERRAMENTA:
        desc = desc[:MAX_DESCRICAO_FERRAMENTA] + "..."
    return f"`[{nome[:60]}]` {desc}".rstrip()


def _texto_usuario(item):
    partes = []
    for c in item.get("content") or []:
        if not isinstance(c, dict):
            continue
        if c.get("type") == "text" and isinstance(c.get("text"), str):
            partes.append(c["text"])
        elif c.get("type") in ("image", "localImage"):
            partes.append("[imagem anexada]")
        elif c.get("type") in ("mention", "skill") and isinstance(c.get("name"), str):
            partes.append("@" + c["name"])
    return "\n".join(p for p in partes if p).strip()


def _resposta(item):
    t = item.get("text") if isinstance(item.get("text"), str) else ""
    return None if t.strip() in ("", MARCA_IMPORTADA) else t.strip()


def _comando(item):
    acoes = item.get("commandActions") or []
    cmd = acoes[0].get("command") if acoes and isinstance(acoes[0], dict) else None
    return _linha("comando", cmd if isinstance(cmd, str) else str(item.get("command") or ""))


def _arquivos(item):
    caminhos = [c.get("path") for c in item.get("changes") or [] if isinstance(c, dict) and isinstance(c.get("path"), str)]
    return _linha("arquivo", ", ".join(os.path.basename(p) for p in caminhos[:5]))


def _mcp(item):
    args = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
    desc = next((v for v in args.values() if isinstance(v, str) and v.strip()), "")
    return _linha(f"{item.get('server', 'mcp')}.{item.get('tool', '')}", desc)


def _subagente(item):
    return _linha("subagente", str(item.get("prompt") or item.get("agentPath") or item.get("tool") or ""))


# tipo de item -> texto mostrado; o que nao esta aqui (reasoning, contextCompaction, tipos novos) nao aparece
_FORMATOS = {
    "agentMessage": _resposta,
    "commandExecution": _comando,
    "fileChange": _arquivos,
    "webSearch": lambda item: _linha("busca", str(item.get("query") or "")),
    "mcpToolCall": _mcp,
    "imageView": lambda item: _linha("imagem", os.path.basename(str(item.get("path") or ""))),
    "plan": lambda item: _linha("plano", str(item.get("text") or "").lstrip("# ")),
    "collabAgentToolCall": _subagente,
    "subAgentActivity": _subagente,
}


def _texto_assistente(item):
    """Texto da resposta, ou uma linha curta por ferramenta; None para o que nao se mostra."""
    formato = _FORMATOS.get(item.get("type"))
    return formato(item) if formato else None


def _acrescentar(turnos, item, quando):
    """Um item do Codex vira turno novo (pergunta) ou se junta a resposta em andamento (texto e ferramentas)."""
    if item.get("type") == "userMessage":
        texto = _texto_usuario(item)
        if texto:
            turnos.append(Turno("usuario", texto, quando))
        return
    texto = _texto_assistente(item)
    if not texto:
        return
    if turnos and turnos[-1].papel == "assistente":
        turnos[-1].texto += "\n\n" + texto
    else:
        turnos.append(Turno("assistente", texto, quando))


def conversa_de_thread(th):
    """Turnos do Codex -> Conversa (usuario / assistente), como o claude_jsonl monta a do Claude."""
    criada = _quando(th.get("createdAt"), datetime.now(timezone.utc))
    conv = Conversa(sessao=str(th.get("id", "")), cwd=cwd_de(th))
    turnos = []
    for turno in (t for t in th.get("turns") or [] if isinstance(t, dict)):
        quando = _quando(turno.get("startedAt"), _quando(turno.get("completedAt"), criada))
        for item in (i for i in turno.get("items") or [] if isinstance(i, dict)):
            _acrescentar(turnos, item, quando)
    for t in turnos:
        t.texto = _cortar(t.texto)
    conv.turnos = turnos
    nome = th.get("name") if isinstance(th.get("name"), str) else ""
    primeiro = next((t.texto for t in turnos if t.papel == "usuario"), "")
    conv.titulo = (nome or (primeiro.splitlines()[0] if primeiro else "") or "Conversa do Codex").strip()[:80]
    return conv


def turnos_novos_desde(th, ja_sincronizados=0):
    """(novos Turnos nativos prontos desde ja_sincronizados, total de turnos nativos prontos)."""
    criada = _quando(th.get("createdAt"), datetime.now(timezone.utc))
    prontos = [t for t in th.get("turns") or []
               if isinstance(t, dict) and TURNO_NATIVO.match(str(t.get("id", ""))) and t.get("status") != "inProgress"]
    turnos = []
    for turno in prontos[ja_sincronizados:]:
        quando = _quando(turno.get("startedAt"), _quando(turno.get("completedAt"), criada))
        for item in (i for i in turno.get("items") or [] if isinstance(i, dict)):
            _acrescentar(turnos, item, quando)
    for t in turnos:
        t.texto = _cortar(t.texto)
    return turnos, len(prontos)


def apagar(srv, tid):
    """Apaga uma thread substituida no Codex pela API oficial."""
    if not isinstance(tid, str) or not TURNO_NATIVO.match(tid):
        raise ValueError("id de thread invalido")
    srv.chamar("thread/delete", {"threadId": tid})


# ---------------------------------------------------------------- importacao (Claude -> Codex)
ARQ_REGISTROS = "external_agent_session_imports.json"
TIPOS_DA_FONTE = ("user", "assistant", "ai-title", "custom-title", "summary")   # o que a importacao le
MAX_LOTE_BYTES = 150 * 1024 * 1024


def normalizar(caminho):
    """Mesma chave para o mesmo arquivo: sem o prefixo \\\\?\\, nome curto (ANIMAT~2) expandido como o Codex devolve,
    sem diferenca de caixa. realpath expande o trecho que existe mesmo quando o arquivo ja foi apagado."""
    if caminho.startswith(PREFIXO_LONGO):
        caminho = caminho[len(PREFIXO_LONGO):]
    return os.path.normcase(os.path.realpath(caminho))


def _ler_registros(home):
    caminho = os.path.join(home or base(), ARQ_REGISTROS)
    try:
        with open(caminho, encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        return caminho, None
    if not isinstance(d, dict) or not isinstance(d.get("records"), list):
        raise ErroCodex(f"{ARQ_REGISTROS} com formato desconhecido; nada foi mexido")
    return caminho, d


def registros(home=None):
    """{caminho da fonte (normalizado): id da conversa no Codex} da importacao nativa."""
    try:
        _, d = _ler_registros(home)
    except (ErroCodex, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    saida = {}
    for r in (d or {}).get("records") or []:
        if isinstance(r, dict) and isinstance(r.get("source_path"), str) and isinstance(r.get("imported_thread_id"), str):
            saida[normalizar(r["source_path"])] = r["imported_thread_id"]
    return saida


def _regravar_registros(home, mudar):
    """Le, aplica mudar(records) e grava por cima de uma vez (temporario + troca). Devolve quantos mudaram."""
    caminho, d = _ler_registros(home)
    if d is None:
        return 0
    novos, mudou = mudar(d["records"])
    if mudou:
        d["records"] = novos
        tmp = caminho + ".tagteam"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2, ensure_ascii=False)
        os.replace(tmp, caminho)
    return mudou


def esquecer_registro(fonte, home=None):
    """Tira a entrada da fonte: a proxima importacao dela cria conversa NOVA (versao nova; a antiga fica)."""
    alvo = normalizar(fonte)

    def mudar(recs):
        novos = [r for r in recs if not (isinstance(r, dict) and isinstance(r.get("source_path"), str)
                                          and normalizar(r["source_path"]) == alvo)]
        return novos, len(recs) - len(novos)
    return _regravar_registros(home, mudar)


def adotar_registro(fonte, sessao, home=None):
    """Conversa importada antes (outra pasta, como a importacao do proprio Codex) da mesma sessao: aponta a entrada para a
    fonte nova, para a importacao atualizar a conversa que ja existe em vez de duplicar. Devolve o id dela ou None."""
    alvo = normalizar(fonte)
    nome = (sessao + ".jsonl").lower()
    achado = {}

    def mudar(recs):
        if any(isinstance(r, dict) and normalizar(str(r.get("source_path", ""))) == alvo for r in recs):
            return recs, 0
        for r in recs:
            if isinstance(r, dict) and isinstance(r.get("source_path"), str) and \
                    os.path.basename(normalizar(r["source_path"])).lower() == nome:
                r["source_path"] = PREFIXO_LONGO + os.path.realpath(fonte)
                achado["id"] = r.get("imported_thread_id")
                return recs, 1
        return recs, 0
    _regravar_registros(home, mudar)
    return achado.get("id")


def caminho_preparado(sessao, cwd, raiz):
    return os.path.join(raiz, ".claude", "projects", re.sub(r"[^A-Za-z0-9]", "-", cwd), sessao + ".jsonl")


def preparar(caminho, sessao, cwd, raiz):
    """Copia enxuta da sessao do Claude para <raiz>/.claude/projects/<pasta>/<sessao>.jsonl, com a pasta de trabalho
    deste PC em toda linha (a importacao pula a de pasta inexistente). Devolve o caminho da copia."""
    destino = caminho_preparado(sessao, cwd, raiz)
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    tmp = destino + ".novo"
    with open(caminho, "rb") as fonte, open(tmp, "wb") as saida:
        for bruta in fonte:
            try:
                o = json.loads(bruta)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(o, dict) or o.get("type") not in TIPOS_DA_FONTE:
                continue
            if "cwd" in o:
                o["cwd"] = cwd
            saida.write(json.dumps(o, ensure_ascii=False).encode("utf-8") + b"\n")
    os.replace(tmp, destino)
    return destino


def importar(srv, sessoes, a_cada_lote=None):
    """sessoes = [(caminho preparado, cwd, titulo)]. Devolve ({caminho normalizado: id}, {caminho normalizado: erro}).
    Fonte igual a ultima importada (ou de conversa apagada no Codex) nao aparece em nenhum dos dois."""
    feitos, falhas = {}, {}
    lote, tamanho = [], 0
    for item in sessoes + [None]:
        if item is not None:
            lote.append(item)
            tamanho += os.path.getsize(item[0])
        if lote and (item is None or len(lote) >= LOTE_IMPORTACAO or tamanho >= MAX_LOTE_BYTES):
            if a_cada_lote:
                a_cada_lote()                     # quem chama renova a trava dele (1a rodada num PC demora)
            _importar_lote(srv, lote, feitos, falhas)
            lote, tamanho = [], 0
    return feitos, falhas


def _importar_lote(srv, lote, feitos, falhas):
    itens = [{"itemType": "SESSIONS", "description": "tagteam", "cwd": None,
              "details": {"sessions": [{"cwd": cwd, "path": caminho, "title": titulo} for caminho, cwd, titulo in lote]}}]
    r = srv.chamar("externalAgentConfig/import", {"migrationItems": itens}) or {}
    iid = r.get("importId")
    fim = srv.esperar_aviso("externalAgentConfig/import/completed", lambda p: p.get("importId") == iid,
                            PRAZO_IMPORTACAO_S)
    for res in fim.get("itemTypeResults") or []:
        for s in res.get("successes") or []:
            if isinstance(s, dict) and isinstance(s.get("source"), str) and isinstance(s.get("target"), str):
                feitos[normalizar(s["source"])] = s["target"]
        for f in res.get("failures") or []:
            if isinstance(f, dict):
                chave = normalizar(f["source"]) if isinstance(f.get("source"), str) else "?"
                falhas[chave] = f"{f.get('subErrorType') or f.get('failureStage')}: {str(f.get('message'))[:200]}"


_SQL_ALINHAR_MS = (
    "update threads set updated_at = recency_at, created_at = min(created_at, recency_at), "
    "updated_at_ms = recency_at_ms, created_at_ms = min(created_at_ms, recency_at_ms) "
    "where id = ? and recency_at > 0 and abs(updated_at - recency_at) > 60"
)
_SQL_ALINHAR = (
    "update threads set updated_at = recency_at, created_at = min(created_at, recency_at) "
    "where id = ? and recency_at > 0 and abs(updated_at - recency_at) > 60"
)


def alinhar_datas(ids, home=None):
    """O importador do Codex grava a data real em recency_at, mas em muitas sessoes deixa created_at/updated_at e o
    mtime do rollout com a hora da importacao (empurrando conversas antigas para o topo do 'codex resume'). Alinha o
    mtime do rollout (senao o app-server reverte o banco) e updated_at/created_at em state_*.sqlite."""
    ids = [i for i in ids if isinstance(i, str) and i]
    if not ids:
        return 0
    bancos = sorted(glob.glob(os.path.join(home or base(), "state_*.sqlite")))
    if not bancos:
        return 0
    total = 0
    with contextlib.closing(sqlite3.connect(bancos[-1], timeout=10)) as c:
        cols = {r[1] for r in c.execute("pragma table_info(threads)")}
        if not {"id", "created_at", "updated_at", "recency_at"}.issubset(cols):
            return 0
        com_ms = {"created_at_ms", "updated_at_ms", "recency_at_ms"}.issubset(cols)
        com_rp = "rollout_path" in cols
        for tid in ids:
            if com_rp:
                row = c.execute("select recency_at, rollout_path from threads where id = ? and recency_at > 0",
                                (tid,)).fetchone()
                if row and row[1]:
                    with contextlib.suppress(OSError):
                        if abs(os.path.getmtime(row[1]) - row[0]) > 60:
                            os.utime(row[1], (row[0], row[0]))
            total += c.execute(_SQL_ALINHAR_MS if com_ms else _SQL_ALINHAR, (tid,)).rowcount
        c.commit()
    return total
