"""Grava uma conversa no formato do app Antigravity (um SQLite por conversa + linha no indice).

O formato interno nao e documentado: usamos uma conversa nativa do proprio app como molde (um passo
de pergunta e um de resposta so com texto) e trocamos so texto, ids e horarios. O app descobre o
arquivo novo sozinho ao iniciar ("reconcile ... restored").
"""
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import pb

TIPO_USUARIO = 14
TIPO_RESPOSTA = 15
STATUS_FEITO = 3
TABELA_MARCA = "claude_import"       # marca as conversas que vieram do Claude (o app ignora tabela a mais)
# formatos conhecidos do banco do app; valor fora daqui = o app mudou e o import para (falha fechada)
PRAGMA_PAGINA = {4096: "pragma page_size = 4096", 8192: "pragma page_size = 8192",
                 16384: "pragma page_size = 16384", 32768: "pragma page_size = 32768", 65536: "pragma page_size = 65536"}
PRAGMA_VERSAO = {1: "pragma user_version = 1"}
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
EPOCA_MIN, EPOCA_MAX = 1_500_000_000, 2_500_000_000
NAMESPACE = uuid.UUID("7c0e4a52-3d1f-4f6e-9c55-6a1f0b8e2d31")   # ids estaveis: mesma sessao -> mesmo id


def base_padrao():
    """Pasta do app. O CLI agy tem a dele (antigravity-cli) e lista as conversas do app sozinho."""
    return os.path.join(os.path.expanduser("~"), ".gemini", "antigravity")


def bases():
    """Pastas de conversa que existem neste PC: app e CLI."""
    g = os.path.join(os.path.expanduser("~"), ".gemini")
    return [os.path.join(g, n) for n in ("antigravity", "antigravity-cli") if os.path.isdir(os.path.join(g, n, "conversations"))]


def id_para_sessao(sessao, versao=0, alvo="antigravity"):
    """Id da conversa no Antigravity. Cada atualizacao usa versao nova: o app aberto nao aceita de
    volta um id que acabou de apagar (lembra ate reiniciar). App e CLI tem ids diferentes: o indice do
    CLI espelha as linhas do app com o mesmo id, e o CLI nao abre conversa do app."""
    prefixo = "claude:" if alvo == "antigravity" else f"claude-{alvo}:"
    return str(uuid.uuid5(NAMESPACE, prefixo + sessao + (f":{versao}" if versao else "")))


@dataclass
class Molde:
    schema: list            # comandos CREATE das tabelas
    passo_usuario: bytes    # step_payload de um passo tipo 14
    passo_resposta: bytes   # step_payload de um passo tipo 15 so com texto
    meta_trajetoria: bytes  # trajectory_metadata_blob
    traj_tipo: int
    traj_fonte: int
    resumo: bytes           # raw_summary da conversa molde
    projeto: str
    workspaces: str
    cascade_molde: str
    traj_molde: str
    versao_db: int = 1       # PRAGMA user_version: sem ele o app diz "trajectory db is not initialized"
    pagina_db: int = 32768


def _ro(caminho):
    return sqlite3.connect(f"file:{caminho}?mode=ro", uri=True, timeout=10)


def eh_importada(caminho):
    c = _ro(caminho)
    try:
        return c.execute("select 1 from sqlite_master where type='table' and name=?", (TABELA_MARCA,)).fetchone() is not None
    finally:
        c.close()


PASTA_MOLDES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "moldes")
CAMPOS_MOLDE_BYTES = ("passo_usuario", "passo_resposta", "meta_trajetoria", "resumo")


def _variantes_home(home):
    """Formas em que a pasta do usuario aparece nos blobs: caminho e URI (c:/, C:/, c%3A/)."""
    from urllib.parse import quote
    home = home.rstrip("\\/")
    resto = quote(home[2:].replace("\\", "/"))                 # /Users/Jane%20Doe
    letra = home[0]
    return [home, f"file:///{letra.lower()}:{resto}", f"file:///{letra.upper()}:{resto}",
            f"file:///{letra.lower()}%3A{resto}", f"file:///{letra.upper()}%3A{resto}"]


def _trocar_textos(blob, pares, prof=0):
    """Troca, dentro de campos de texto, cada forma antiga da pasta do usuario pela nova (recursivo)."""
    if prof > 16:
        return blob
    try:
        campos = pb.ler(blob)
    except ValueError:
        return blob
    mudou = False
    for c in campos:
        if c[1] != 2 or not any(a.encode() in c[2] for a, _ in pares):
            continue
        try:
            texto = c[2].decode("utf-8")
            eh_texto = texto.isprintable()
        except UnicodeDecodeError:
            eh_texto = False
        if eh_texto:
            for antigo, novo in pares:
                texto = texto.replace(antigo, novo)
            novo_valor = texto.encode("utf-8")
        else:
            novo_valor = _trocar_textos(c[2], pares, prof + 1)
        if novo_valor != c[2]:
            c[2] = novo_valor
            mudou = True
    return pb.gravar(campos) if mudou else blob


def salvar_molde(molde, alvo, home=None):
    """Guarda o molde na skill (vai para os outros PCs pelo sync), sem raciocinio, assinatura nem uso."""
    import base64
    import json
    resposta = molde.passo_resposta
    for campo in (3, 7, 11, 14):
        resposta = pb.trocar(resposta, [20, campo], None)
    resposta = pb.trocar(resposta, [5, 9], None)
    d = {k: getattr(molde, k) for k in Molde.__dataclass_fields__}
    d["passo_resposta"] = resposta
    d = {k: (base64.b64encode(v).decode() if isinstance(v, bytes) else v) for k, v in d.items()}
    d["home"] = home or os.path.expanduser("~")
    os.makedirs(PASTA_MOLDES, exist_ok=True)
    destino = os.path.join(PASTA_MOLDES, f"{alvo}.json")
    tmp = destino + ".novo"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1, ensure_ascii=False)
    os.replace(tmp, destino)
    return destino


def _projeto_da_workspace(uri_ws):
    """Projeto do app cuja pasta e a workspace deste PC (cada PC tem os seus ids)."""
    import glob
    import json
    for f in glob.glob(os.path.join(os.path.expanduser("~"), ".gemini", "config", "projects", "*.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        for r in (d.get("projectResources") or {}).get("resources") or []:
            if str(r.get("folderUri", "")).lower() == uri_ws.lower():
                return str(d.get("id", ""))
    return ""


def molde_padrao(alvo):
    """Molde guardado na skill, adaptado a pasta do usuario deste PC (PC novo, sem conversa nativa)."""
    import base64
    import json
    caminho = os.path.join(PASTA_MOLDES, f"{alvo}.json")
    if not os.path.isfile(caminho):
        return None
    with open(caminho, encoding="utf-8") as f:
        d = json.load(f)
    home_antigo, home_novo = d.pop("home"), os.path.expanduser("~")
    pares = [(a, n) for a, n in zip(_variantes_home(home_antigo), _variantes_home(home_novo)) if a != n]
    for k in CAMPOS_MOLDE_BYTES:
        d[k] = base64.b64decode(d[k])
        if pares:
            d[k] = _trocar_textos(d[k], pares)
    for a, n in pares:
        d["workspaces"] = d["workspaces"].replace(a, n)
    if alvo == "antigravity":
        uris = json.loads(d["workspaces"] or "[]")
        d["projeto"] = _projeto_da_workspace(uris[0]) if uris else ""
    return Molde(**d)


def achar_molde(base=None):
    """Conversa nativa deste PC como molde; sem nenhuma (PC novo), o molde guardado na skill."""
    base = base or base_padrao()
    try:
        return _achar_molde_local(base)
    except RuntimeError:
        m = molde_padrao(os.path.basename(base))
        if m is None:
            raise
        return m


def _achar_molde_local(base):
    """Acha uma conversa nativa (nao importada) com pergunta e resposta so em texto."""
    pasta = os.path.join(base, "conversations")
    arquivos = sorted((os.path.getmtime(os.path.join(pasta, n)), n) for n in os.listdir(pasta)
                      if n.endswith(".db") and UUID_RE.match(n[:-3]))
    for _, nome in reversed(arquivos):
        caminho = os.path.join(pasta, nome)
        if eh_importada(caminho):
            continue
        c = _ro(caminho)
        try:
            usuario = resposta = None
            for tipo, payload in c.execute("select step_type, step_payload from steps where status=? order by idx", (STATUS_FEITO,)):
                if not isinstance(payload, bytes):
                    continue
                if tipo == TIPO_USUARIO and usuario is None and pb.obter(payload, [19, 2]) is not None:
                    usuario = payload
                elif tipo == TIPO_RESPOSTA and resposta is None:
                    r = pb.obter(payload, [20])
                    if r is not None and pb.obter(r, [1]) is not None and pb.obter(r, [7]) is None:
                        resposta = payload
            if usuario is None or resposta is None:
                continue
            cascade = nome[:-3]
            traj, ttipo, tfonte = c.execute("select trajectory_id, trajectory_type, source from trajectory_meta").fetchone()
            meta = c.execute("select data from trajectory_metadata_blob where id='main'").fetchone()[0]
            schema = [s for (s,) in c.execute("select sql from sqlite_master where type in ('table','index') and sql is not null")]
            versao = c.execute("pragma user_version").fetchone()[0]
            pagina = c.execute("pragma page_size").fetchone()[0]
        finally:
            c.close()
        idx = _ro(os.path.join(base, "conversation_summaries.db"))
        try:
            linha = idx.execute("select raw_summary, project_id, workspace_uris from conversation_summaries where conversation_id=?",
                                (cascade,)).fetchone()
        finally:
            idx.close()
        if not linha or not UUID_RE.match(traj or ""):
            continue
        if not versao:
            continue
        return Molde(schema, usuario, resposta, meta, ttipo, tfonte, linha[0], linha[1], linha[2], cascade, traj,
                     versao, pagina)
    raise RuntimeError("nenhuma conversa nativa do Antigravity serve de molde: abra o app e mande um 'oi' numa conversa nova")


def _ts(dt):
    return pb.timestamp(int(dt.timestamp()), dt.microsecond * 1000)


def _retimbrar(blob, dt, prof=0):
    """Troca todo Timestamp ({1: segundos[, 2: nanos]}) da mensagem pelo horario dado."""
    if prof > 12:
        return blob
    try:
        campos = pb.ler(blob)
    except ValueError:
        return blob
    mudou = False
    for c in campos:
        if c[1] != 2 or not c[2]:
            continue
        try:
            sub = pb.ler(c[2])
        except ValueError:
            continue
        nums = {s[0] for s in sub}
        if nums and nums <= {1, 2} and all(s[1] == 0 for s in sub) and \
                any(s[0] == 1 and EPOCA_MIN <= s[2] <= EPOCA_MAX for s in sub):
            c[2] = _ts(dt)
            mudou = True
        else:
            novo = _retimbrar(c[2], dt, prof + 1)
            if novo != c[2]:
                c[2] = novo
                mudou = True
    return pb.gravar(campos) if mudou else blob


def _ids(blob, molde, cascade, traj):
    blob = pb.trocar_bytes_iguais(blob, molde.cascade_molde.encode(), cascade.encode())
    return pb.trocar_bytes_iguais(blob, molde.traj_molde.encode(), traj.encode())


def _metadados(payload, idx, dt, execucao, molde, cascade, traj):
    meta = pb.obter(payload, [5])
    meta = _ids(_retimbrar(meta, dt), molde, cascade, traj)
    meta = pb.trocar(meta, [9], None)                       # uso de tokens do molde nao vale aqui
    meta = pb.trocar(meta, [12], execucao)
    meta = pb.trocar(meta, [35], uuid.uuid4().hex)
    meta = pb.trocar(meta, [20, 2], idx if idx else None)
    return meta


def montar_passo(turno, idx, execucao, molde, cascade, traj):
    if turno.papel == "usuario":
        p = molde.passo_usuario
        p = pb.trocar(p, [19, 2], turno.texto)
        p = pb.trocar(p, [19, 3, 1], turno.texto)
        p = pb.trocar(p, [19, 19], str(uuid.uuid4()))
        tipo = TIPO_USUARIO
    else:
        p = molde.passo_resposta
        for campo in (3, 7, 11, 14):
            p = pb.trocar(p, [20, campo], None)
        p = pb.trocar(p, [20, 1], turno.texto)
        p = pb.trocar(p, [20, 8], turno.texto)
        tipo = TIPO_RESPOSTA
    meta = _metadados(p, idx, turno.quando, execucao, molde, cascade, traj)
    p = pb.trocar(p, [5], meta)
    return tipo, meta, p


def _quando_texto(dt):
    dt = dt.astimezone(timezone.utc)
    return f"{dt:%Y-%m-%d %H:%M:%S}.{dt.microsecond:06d}0+00:00"


def gravar_conversa(conv, molde, base=None, marca=None, versao=0):
    """Grava <base>/conversations/<id>.db de uma vez (temporario + troca). Devolve (id, traj, meta)."""
    base = base or base_padrao()
    cascade = id_para_sessao(conv.sessao, versao, os.path.basename(base))
    traj = str(uuid.uuid5(NAMESPACE, "traj:" + cascade))
    destino = os.path.join(base, "conversations", cascade + ".db")
    tmp = destino + ".novo"
    if os.path.exists(tmp):
        os.remove(tmp)
    c = sqlite3.connect(tmp)
    try:
        if molde.pagina_db not in PRAGMA_PAGINA or molde.versao_db not in PRAGMA_VERSAO:
            raise RuntimeError(f"formato do app mudou (pagina {molde.pagina_db}, versao {molde.versao_db}): "
                               "conferir antes de importar")
        c.execute(PRAGMA_PAGINA[molde.pagina_db])
        c.execute(PRAGMA_VERSAO[molde.versao_db])
        for sql in molde.schema:
            c.execute(sql)
        c.execute("insert into trajectory_meta (trajectory_id, cascade_id, trajectory_type, source) values (?,?,?,?)",
                  (traj, cascade, molde.traj_tipo, molde.traj_fonte))
        meta_traj = _ids(molde.meta_trajetoria, molde, cascade, traj)
        meta_traj = pb.trocar(meta_traj, [2], _ts(conv.turnos[0].quando))
        c.execute("insert into trajectory_metadata_blob (id, data) values ('main', ?)", (meta_traj,))
        execucao = str(uuid.uuid4())
        for idx, turno in enumerate(conv.turnos):
            if turno.papel == "usuario":
                execucao = str(uuid.uuid4())
            tipo, meta, payload = montar_passo(turno, idx, execucao, molde, cascade, traj)
            c.execute("insert into steps (idx, step_type, status, has_subtrajectory, metadata, step_payload, step_format) "
                      "values (?,?,?,0,?,?,0)", (idx, tipo, STATUS_FEITO, meta, payload))
        c.execute("create table claude_import (sessao text primary key, fonte_tamanho integer, fonte_mtime real, passos integer)")
        tamanho, mtime = marca or (0, 0.0)
        c.execute("insert into claude_import values (?,?,?,?)", (conv.sessao, tamanho, mtime, len(conv.turnos)))
        c.commit()
    finally:
        c.close()
    os.replace(tmp, destino)
    return cascade, traj, meta_traj


def gravar_resumo(conv, molde, cascade, traj, meta_traj, base=None):
    """Grava a linha do indice com o titulo, para a conversa aparecer com nome certo na lista."""
    base = base or base_padrao()
    turnos = conv.turnos
    ultimo_usuario = max((i for i, t in enumerate(turnos) if t.papel == "usuario"), default=0)
    inicio, fim, t_usuario = turnos[0].quando, turnos[-1].quando, turnos[ultimo_usuario].quando
    r = molde.resumo
    r = pb.trocar(r, [1], conv.titulo)
    r = pb.trocar(r, [2], len(turnos))
    r = pb.trocar(r, [3], _ts(fim))
    r = pb.trocar(r, [4], traj)
    r = pb.trocar(r, [7], _ts(inicio))
    r = pb.trocar(r, [10], _ts(t_usuario))
    r = pb.trocar(r, [15], pb.gravar([[1, 2, conv.titulo.encode("utf-8")], [7, 2, _ts(fim)]]))
    r = pb.trocar(r, [16], ultimo_usuario)
    r = pb.trocar(r, [17], pb.trocar(meta_traj, [15], None))
    indice = os.path.join(base, "conversation_summaries.db")
    if not os.path.exists(indice):
        return            # instalacao nova: o app/CLI cria o indice e reconstroi a linha ao abrir (titulo pela anotacao)
    c = sqlite3.connect(indice, timeout=15)
    try:
        if not c.execute("select 1 from sqlite_master where name='conversation_summaries'").fetchone():
            return
        c.execute(
            "insert into conversation_summaries (conversation_id, title, preview, step_count, last_modified_time, "
            "workspace_uris, status, source, project_id, agent_name, parent_conversation_id, nesting_depth, battle_id, "
            "winning_conversation_id, not_fully_idle, killed, last_user_input_time, last_user_input_step_index, "
            "app_data_dir, raw_summary, group_id) values (?,?,?,?,?,?,?,'',?,'','',0,'','',0,0,?,?,?,?,'') "
            "on conflict(conversation_id) do update set title=excluded.title, preview=excluded.preview, "
            "step_count=excluded.step_count, last_modified_time=excluded.last_modified_time, "
            "last_user_input_time=excluded.last_user_input_time, "
            "last_user_input_step_index=excluded.last_user_input_step_index, raw_summary=excluded.raw_summary",
            (cascade, conv.titulo, conv.titulo, len(turnos), _quando_texto(fim), molde.workspaces,
             "CASCADE_RUN_STATUS_IDLE", molde.projeto, _quando_texto(t_usuario), ultimo_usuario,
             os.path.basename(base), r))
        c.commit()
    finally:
        c.close()


def _texto_pbtxt(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ") + '"'


def gravar_anotacao(cascade, titulo, base=None, tags=("claude",)):
    """annotations/<id>.pbtxt: e daqui que o app e o CLI tiram o titulo ao refazer o indice.

    Mesmo formato que o app grava pela chamada UpdateConversationAnnotations."""
    base = base or base_padrao()
    pasta = os.path.join(base, "annotations")
    os.makedirs(pasta, exist_ok=True)
    texto = "title:" + _texto_pbtxt(titulo) + "".join(" tags:" + _texto_pbtxt(t) for t in tags)
    tmp = os.path.join(pasta, cascade + ".pbtxt.novo")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(texto)
    os.replace(tmp, os.path.join(pasta, cascade + ".pbtxt"))


def apagar_com_app_fechado(cascade, base=None):
    """Tira a versao antiga direto do disco e do indice. So com o app fechado."""
    base = base or base_padrao()
    for caminho in [os.path.join(base, "conversations", cascade + ".db" + s) for s in ("", "-wal", "-shm")] + \
            [os.path.join(base, "annotations", cascade + ".pbtxt")]:
        if os.path.exists(caminho):
            os.remove(caminho)
    c = sqlite3.connect(os.path.join(base, "conversation_summaries.db"), timeout=15)
    try:
        c.execute("delete from conversation_summaries where conversation_id=?", (cascade,))
        c.commit()
    finally:
        c.close()


def assinatura(cascade, base=None):
    """Muda quando qualquer passo muda (inclusive o ultimo terminando de ser escrito, sem passo novo)."""
    import hashlib
    base = base or base_padrao()
    c = _ro(os.path.join(base, "conversations", cascade + ".db"))
    try:
        linhas = c.execute("select idx, status, length(step_payload) from steps order by idx").fetchall()
    finally:
        c.close()
    return hashlib.sha256(repr(linhas).encode()).hexdigest()[:16]


def no_indice(cascade, base=None):
    base = base or base_padrao()
    c = _ro(os.path.join(base, "conversation_summaries.db"))
    try:
        return c.execute("select 1 from conversation_summaries where conversation_id=?", (cascade,)).fetchone() is not None
    finally:
        c.close()


def situacao(cascade, base=None):
    """None se nao existe; senao (passos_gravados_pelo_import, passos_no_arquivo, tamanho, mtime)."""
    base = base or base_padrao()
    caminho = os.path.join(base, "conversations", cascade + ".db")
    if not os.path.exists(caminho):
        return None
    c = _ro(caminho)
    try:
        total = c.execute("select count(*) from steps").fetchone()[0]
        marca = c.execute("select passos, fonte_tamanho, fonte_mtime from claude_import").fetchone() \
            if c.execute("select 1 from sqlite_master where name=?", (TABELA_MARCA,)).fetchone() else None
    finally:
        c.close()
    if marca is None:
        return (None, total, None, None)
    return (marca[0], total, marca[1], marca[2])
