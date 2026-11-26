"""Sincroniza conversas entre Claude Code, Antigravity (app e CLI agy), Grok CLI e Codex (app e CLI). O Claude e o
centro: cada conversa vira sessao do Claude e dali vai para as outras tres (levar as sessoes do Claude aos outros PCs
fica com a sincronizacao de pastas que voce usar).

Uso:
  python -I sincronizar.py                            # tudo, em todas as direcoes
  python -I sincronizar.py --direcao antigravity      # so Claude -> Antigravity
  python -I sincronizar.py --direcao claude           # so Antigravity -> Claude (e segue para o Grok)
  python -I sincronizar.py --direcao grok             # so Claude -> Grok
  python -I sincronizar.py --direcao claude-de-grok   # so Grok -> Claude (e segue para as outras)
  python -I sincronizar.py --direcao codex            # so Claude -> Codex
  python -I sincronizar.py --direcao claude-de-codex  # so Codex -> Claude (e segue para as outras)
  python -I sincronizar.py --sessao <id>              # uma sessao do Claude (para Antigravity e Grok)
  python -I sincronizar.py --cascade <id>             # uma conversa do Antigravity
  python -I sincronizar.py --grok <id>                # uma sessao do Grok
  python -I sincronizar.py --codex <id>               # uma conversa do Codex
  python -I sincronizar.py --seco                     # mostra o que faria

Regras (as mesmas nos dois sentidos):
- conversa que mudou na origem entra de novo no destino (sem duplicar);
- espelho puro (veio de um lado e ninguem continuou no outro) nao volta;
- conversa continuada no destino nunca e sobrescrita: se a origem andou tambem, nasce uma versao nova
  e a continuada fica;
- conversa importada que voce apagou no destino nao volta (use --forcar);
- sessoes automaticas do Claude (claude -p / SDK) ficam de fora.
Claude -> Antigravity entra no app (o CLI agy lista as conversas do app sozinho); com o app aberto
entra na hora. Antigravity -> Claude le o app e o CLI e grava em ~/.claude/projects/<pasta>/.
Estado de cada PC em ~/.tagteam/estado-<PC>.json (TAGTEAM_STATE_DIR troca a pasta).
"""
import argparse
import glob
import hashlib
import json
import os
import re
import sqlite3
import sys
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import antigravity_db as antigravity  # noqa: E402
import claude_jsonl  # noqa: E402
import codex  # noqa: E402
import config  # noqa: E402
import cursor  # noqa: E402
import grok  # noqa: E402
import para_claude  # noqa: E402
import servidor_local  # noqa: E402

PROJETOS = os.path.join(os.path.expanduser("~"), ".claude", "projects")
# pasta usada quando a de uma sessao nao existe neste PC (pasta do config.json ou TAGTEAM_WORKSPACE; padrao: a home)
WORKSPACE = config.pasta_trabalho(config.carregar())
ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
TRAVA_VELHA_S = 30 * 60
ESPERA_S = 10
ESPERA_TRAVA_S = 20 * 60
ETIQUETA = "claude"
PC = re.sub(r"[^A-Za-z0-9_-]", "_", os.environ.get("COMPUTERNAME", "pc"))[:40]
PASTA_ESTADO = config.pasta_estado()
ESTADO = os.path.join(PASTA_ESTADO, f"estado-{PC}.json")
MOLDE_CURSOR = os.path.join(PASTA_ESTADO, f"molde-cursor-{PC}.json")   # o Cursor exige partes de uma conversa nativa
TRAVA = os.path.join(PASTA_ESTADO, f".trava-{PC}")
# "home" so do app-server filho: copias enxutas das sessoes do Claude com a pasta deste PC (codex.preparar)
PREPARO = os.path.join(PASTA_ESTADO, f"codex-preparo-{PC}")
CHAVE_EXPORTADAS = {"antigravity": "exportadas", "grok": "exportadas_grok", "codex": "exportadas_codex",
                    "cursor": "exportadas_cursor"}


# ---------------------------------------------------------------- estado e trava
def _entrada_valida(v, chave_id):
    return isinstance(v, dict) and ID_RE.match(str(v.get(chave_id, ""))) and isinstance(v.get("versao"), int)


def _filtrar(d, chave, valido):
    """Entradas validas de d[chave] (estado de outra versao, ou editado a mao, nao derruba a rodada)."""
    bruto = d.get(chave)
    return {k: v for k, v in bruto.items() if valido(k, v)} if isinstance(bruto, dict) else {}


def _importadas_antigravity(d):
    bruto = d.get("importadas") if isinstance(d.get("importadas"), dict) else {}
    if any(isinstance(v, str) or (isinstance(v, dict) and "cascade" in v) for v in bruto.values()):
        bruto = {"antigravity": bruto}              # formato antigo: so o app, sem separar por pasta
    imp = {}
    for alvo, sessoes_alvo in bruto.items():
        if not isinstance(sessoes_alvo, dict):
            continue
        imp[alvo] = {}
        for sessao, v in sessoes_alvo.items():
            if isinstance(v, str):                   # formato mais antigo: so o id
                v = {"cascade": v, "versao": 0}
            if _entrada_valida(v, "cascade"):
                imp[alvo][sessao] = v
    return imp


def ler_estado():
    try:
        with open(ESTADO, encoding="utf-8") as f:
            d = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        d = {}
    d = d if isinstance(d, dict) else {}
    com_turnos = lambda _k, v: _entrada_valida(v, "sessao") and isinstance(v.get("turnos"), int)  # noqa: E731
    return {
        "importadas": _importadas_antigravity(d),
        "exportadas": _filtrar(d, "exportadas", lambda k, v: com_turnos(k, v) and isinstance(v.get("passos"), int)),
        "importadas_grok": _filtrar(d, "importadas_grok",
                                    lambda _k, v: _entrada_valida(v, "id") and isinstance(v.get("pasta"), str)),
        "exportadas_grok": _filtrar(d, "exportadas_grok", com_turnos),
        "importadas_codex": _filtrar(d, "importadas_codex", lambda _k, v: _entrada_valida(v, "id")),
        "exportadas_codex": _filtrar(d, "exportadas_codex", com_turnos),
        "vistas_codex": _filtrar(d, "vistas_codex", lambda k, v: ID_RE.match(str(k)) and isinstance(v, (int, float, str))),
        "importadas_cursor": _filtrar(d, "importadas_cursor",
                                      lambda _k, v: _entrada_valida(v, "id") and isinstance(v.get("pasta"), str)),
        "exportadas_cursor": _filtrar(d, "exportadas_cursor", com_turnos),
    }


def cwd_local(cwd):
    """Pasta de trabalho de outro PC (C:\\Users\\outro\\...) vira a deste PC; letra do disco em maiuscula (o Grok
    agrupa as sessoes pelo texto exato da pasta, e o seletor so mostra as da pasta atual)."""
    if not cwd:
        return WORKSPACE
    m = re.match(r"^([A-Za-z]):\\Users\\[^\\]+(\\.*)?$", cwd)
    if m:
        cwd = os.path.expanduser("~") + (m.group(2) or "")
    cwd = cwd[0].upper() + cwd[1:] if len(cwd) > 1 and cwd[1] == ":" else cwd
    # pasta que nao existe neste PC (mudou de lugar, ou so existe no outro): o Grok recusa retomar a sessao
    return cwd if os.path.isdir(cwd) else WORKSPACE


def gravar_estado(estado):
    os.makedirs(PASTA_ESTADO, exist_ok=True)
    tmp = ESTADO + ".novo"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(estado, f, indent=1, sort_keys=True)
    os.replace(tmp, ESTADO)


def pegar_trava():
    os.makedirs(PASTA_ESTADO, exist_ok=True)
    try:
        if os.path.exists(TRAVA) and time.time() - os.path.getmtime(TRAVA) > TRAVA_VELHA_S:
            os.remove(TRAVA)
        fd = os.open(TRAVA, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        return False


def esperar_trava(argumentos, prazo_s=None, pausa_s=2.0):
    """A trava esta com outra rodada: espera por ela em vez de desistir (desistir perdia a atualizacao que chegou no
    meio de uma rodada completa). So uma rodada espera por combinacao de argumentos: as outras iguais saem, porque a que
    espera vai rodar depois e ler tudo ja atualizado. True quando pegou a trava; False se ja havia uma igual esperando
    ou o prazo acabou."""
    prazo_s = ESPERA_TRAVA_S if prazo_s is None else prazo_s
    chave = hashlib.sha256(" ".join(argumentos).encode()).hexdigest()[:16]
    fila = os.path.join(PASTA_ESTADO, f".fila-{PC}-{chave}")
    try:
        if os.path.exists(fila) and time.time() - os.path.getmtime(fila) > TRAVA_VELHA_S:
            os.remove(fila)
        os.close(os.open(fila, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        return False
    try:
        limite = time.monotonic() + prazo_s
        while time.monotonic() < limite:
            time.sleep(pausa_s)
            if pegar_trava():
                return True
        return False
    finally:
        os.remove(fila)


def _argumentos_da_fila(argv):
    """Os argumentos que dizem o que a rodada faz (sem o --atraso)."""
    saida, pular = [], False
    for x in argv:
        if pular:
            pular = False
        elif x == "--atraso":
            pular = True
        elif not x.startswith("--atraso="):
            saida.append(x)
    return saida


def _renovar_trava():
    """Rodada longa (1a importacao para o Codex num PC) nao deixa a trava parecer abandonada."""
    if os.path.exists(TRAVA):
        os.utime(TRAVA)


def _esperar(condicao, motivo, segundos=ESPERA_S):
    limite = time.monotonic() + segundos
    while not condicao():
        if time.monotonic() > limite:
            raise RuntimeError(motivo)
        time.sleep(0.2)


# ---------------------------------------------------------------- Claude -> Antigravity
def carregar_no_app(servidor, conv, cascade, base):
    servidor_local.chamar(servidor, "LoadTrajectory", {"cascadeId": cascade})
    servidor_local.chamar(servidor, "UpdateConversationAnnotations", {
        "cascadeId": cascade, "mergeAnnotations": True,
        "annotations": {"title": conv.titulo, "tags": [ETIQUETA]}})
    _esperar(lambda: antigravity.no_indice(cascade, base), "o app carregou a conversa mas ela nao entrou no indice")


def tirar_versao_antiga(servidor, cascade, base):
    if servidor:
        # o app segura o arquivo aberto: so ele pode apagar; termina depois de responder
        servidor_local.chamar(servidor, "DeleteCascadeTrajectory", {"cascadeId": cascade})
        _esperar(lambda: not os.path.exists(os.path.join(base, "conversations", cascade + ".db")),
                 "o app nao apagou a versao antiga a tempo")
    else:
        antigravity.apagar_com_app_fechado(cascade, base)


def importar(conv, molde, marca, servidor, anterior, base, manter_anterior=False):
    """Grava a versao nova, poe no app e so entao tira a anterior (se nao for para manter)."""
    versao = anterior["versao"] + 1 if anterior else 0
    cascade, traj, meta = antigravity.gravar_conversa(conv, molde, base=base, marca=marca, versao=versao)
    if servidor:
        carregar_no_app(servidor, conv, cascade, base)
    else:
        # sem servidor aberto (CLI, ou app fechado): o indice e refeito ao abrir, e o titulo sai da anotacao
        antigravity.gravar_anotacao(cascade, conv.titulo, base, (ETIQUETA,))
        antigravity.gravar_resumo(conv, molde, cascade, traj, meta, base=base)
    if anterior and not manter_anterior and anterior["cascade"] != cascade and \
            os.path.exists(os.path.join(base, "conversations", anterior["cascade"] + ".db")):
        tirar_versao_antiga(servidor, anterior["cascade"], base)
    return {"cascade": cascade, "versao": versao}


def _espelho(conv, sessao, estado, origem):
    """Sessao que este PC gerou a partir de <origem> e que ninguem continuou no Claude: nao volta para la."""
    if conv.entrypoint != para_claude.ORIGENS[origem][0]:
        return False
    chave = CHAVE_EXPORTADAS[origem]
    rec = next((r for r in estado[chave].values() if r["sessao"] == sessao), None)
    return rec is not None and len(conv.turnos) <= rec["turnos"]


def _espelho_do_antigravity(conv, sessao, estado):
    return _espelho(conv, sessao, estado, "antigravity")


def _espelho_na_base(conv, sessao, estado, base):
    """Espelho do Antigravity so para a base de onde a conversa veio. App e CLI sao destinos diferentes: a conversa
    nascida no CLI tem de ir para o app (o app nao lista as do CLI), e a do app vai para o CLI (que lista as do app
    mas so abre as da pasta dele)."""
    if not _espelho(conv, sessao, estado, "antigravity"):
        return False
    origem = next((c for c, r in estado["exportadas"].items() if r["sessao"] == sessao), None)
    return origem is not None and os.path.exists(os.path.join(base, "conversations", origem + ".db"))


def _sessoes_do_claude():
    """Arquivos das sessoes do Claude que podem sair dele: as de pasta excluida no config.json ficam de fora."""
    cfg = config.carregar()
    return [c for c in glob.glob(os.path.join(PROJETOS, "*", "*.jsonl"))
            if not config.projeto_excluido(cfg, os.path.basename(os.path.dirname(c)))]


def _fora_do_filtro(a, sessao):
    """Filtro de sessao do Claude: --sessao, ou o conjunto que acabou de ser exportado (propagacao)."""
    if a.so is not None:
        return sessao not in a.so
    return bool((a.sessao and sessao != a.sessao) or a.cascade or a.grok or a.codex or a.cursor)


def para_antigravity(a, estado, cont):
    """Grava no app e no CLI: o CLI lista as conversas do app, mas so abre as da pasta dele."""
    for base in antigravity.bases():
        alvo = os.path.basename(base)
        try:
            molde = antigravity.achar_molde(base)
        except RuntimeError:
            cont[f"c>a {alvo}: sem conversa nativa para molde"] += 1
            continue
        # so o app recebe pelo servidor local; no CLI grava direto (o seletor le o indice na hora e o agy
        # carrega ao abrir). O servidor do CLI se registra na mesma pasta daemon: achar() filtra pelo pai.
        servidor = servidor_local.achar(base) if alvo == "antigravity" else None
        if alvo == "antigravity":
            cont["app_aberto"] = bool(servidor)
        _para_um_alvo(a, estado["importadas"].setdefault(alvo, {}), estado, cont, base, alvo, molde, servidor)


def _para_um_alvo(a, imp, estado, cont, base, alvo, molde, servidor):
    for caminho in _sessoes_do_claude():
        sessao = os.path.basename(caminho)[:-6]
        if not ID_RE.match(sessao) or _fora_do_filtro(a, sessao):
            continue
        try:
            st = os.stat(caminho)
            marca = (st.st_size, round(st.st_mtime, 3))
            anterior = imp.get(sessao)
            if anterior is None and antigravity.situacao(antigravity.id_para_sessao(sessao, 0, alvo), base):
                anterior = {"cascade": antigravity.id_para_sessao(sessao, 0, alvo), "versao": 0}   # estado perdido
            sit = antigravity.situacao(anterior["cascade"], base) if anterior else None
            if sit is None and anterior and not a.forcar:
                cont["c>a apagadas_no_antigravity"] += 1
                continue
            manter = False
            if sit is not None:
                passos_import, passos_agora, tam, mt = sit
                if passos_import is None:
                    cont["erro"] += 1              # id ocupado por conversa que nao e nossa
                    continue
                if (tam, mt) == marca and not a.forcar:
                    cont["c>a iguais"] += 1
                    continue
                manter = passos_agora > passos_import  # voce continuou la: fica, e entra versao nova
            conv = claude_jsonl.ler_conversa(caminho, sessao)
            if conv.entrypoint.startswith("sdk"):
                cont["c>a automaticas"] += 1
                continue
            if _espelho_na_base(conv, sessao, estado, base):
                cont["c>a espelhos"] += 1
                continue
            if not any(t.papel == "usuario" for t in conv.turnos):
                cont["c>a vazias"] += 1
                continue
            rotulo = "c>a atualizadas" if sit else "c>a criadas"
            if a.seco:
                print(f"{rotulo} ({alvo}): {conv.titulo} ({len(conv.turnos)} passos)")
            else:
                imp[sessao] = importar(conv, molde, marca, servidor, anterior, base, manter)
                gravar_estado(estado)
            cont[rotulo] += 1
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as e:
            cont["erro"] += 1
            print(f"erro na sessao {sessao}: {type(e).__name__}: {e}", file=sys.stderr)


# ---------------------------------------------------------------- Antigravity -> Claude
def _conversas_antigravity():
    for base in antigravity.bases():
        for caminho in glob.glob(os.path.join(base, "conversations", "*.db")):
            cascade = os.path.basename(caminho)[:-3]
            if ID_RE.match(cascade):
                yield base, cascade


def exportar(conv, cascade, total, rec, versao_cc, forcar):
    """Grava a sessao do Claude. Continuada no Claude -> versao nova (a continuada fica)."""
    versao = 0
    if rec:
        caminho = para_claude.achar_sessao(rec["sessao"])
        continuada = caminho is not None and \
            len(claude_jsonl.ler_conversa(caminho, rec["sessao"]).turnos) > rec["turnos"]
        versao = rec["versao"] + 1 if continuada else rec["versao"]
    sessao = para_claude.id_sessao(cascade, versao)
    cwd = conv.cwd or WORKSPACE
    para_claude.gravar_sessao(conv, sessao, para_claude.pasta_do_projeto(cwd), cwd, versao_cc)
    return {"sessao": sessao, "versao": versao, "passos": total, "turnos": len(conv.turnos)}


def para_claude_(a, estado, cont, novas):
    versao_cc = None
    for base, cascade in _conversas_antigravity():
        if (a.cascade and cascade != a.cascade) or a.sessao or a.grok or a.codex or a.cursor:
            continue
        try:
            sit = antigravity.situacao(cascade, base)
            if sit is None:
                continue
            passos_import, total = sit[0], sit[1]
            if passos_import is not None and total <= passos_import:
                cont["a>c espelhos"] += 1                 # veio do Claude e ninguem continuou aqui
                continue
            rec = estado["exportadas"].get(cascade)
            # pela assinatura dos passos, nao pela contagem: o ultimo passo termina de ser escrito sem passo novo
            assin = antigravity.assinatura(cascade, base)
            if rec and rec.get("assinatura", rec["passos"]) == assin and not a.forcar:
                cont["a>c iguais"] += 1
                continue
            if rec and para_claude.achar_sessao(rec["sessao"]) is None and not a.forcar:
                cont["a>c apagadas_no_claude"] += 1
                continue
            conv = para_claude.ler_conversa_antigravity(cascade, base)
            if not any(t.papel == "usuario" for t in conv.turnos):
                cont["a>c vazias"] += 1
                continue
            rotulo = "a>c atualizadas" if rec else "a>c criadas"
            if a.seco:
                print(f"{rotulo}: {conv.titulo} ({len(conv.turnos)} turnos, {os.path.basename(base)})")
            else:
                versao_cc = versao_cc or para_claude.versao_claude()
                estado["exportadas"][cascade] = dict(exportar(conv, cascade, total, rec, versao_cc, a.forcar),
                                                     assinatura=assin)
                gravar_estado(estado)
                novas.add(estado["exportadas"][cascade]["sessao"])
            cont[rotulo] += 1
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as e:
            cont["erro"] += 1
            print(f"erro na conversa {cascade}: {type(e).__name__}: {e}", file=sys.stderr)


# ---------------------------------------------------------------- Claude -> Grok
def para_grok(a, estado, cont):
    if not grok.instalado():
        return
    imp = estado["importadas_grok"]
    for caminho in _sessoes_do_claude():
        sessao = os.path.basename(caminho)[:-6]
        if not ID_RE.match(sessao) or _fora_do_filtro(a, sessao):
            continue
        try:
            st = os.stat(caminho)
            fonte = {"sessao": sessao, "fonte_tamanho": st.st_size, "fonte_mtime": round(st.st_mtime, 3)}
            anterior = imp.get(sessao)
            m = grok.marca(anterior["pasta"]) if anterior else None
            if anterior and m is None and not a.forcar:
                cont["c>g apagadas_no_grok"] += 1
                continue
            manter = False
            if m is not None:
                if (m.get("fonte_tamanho"), m.get("fonte_mtime")) == (fonte["fonte_tamanho"], fonte["fonte_mtime"]) \
                        and not a.forcar:
                    cont["c>g iguais"] += 1
                    continue
                manter = os.path.getsize(os.path.join(anterior["pasta"], "updates.jsonl")) != m.get("bytes_updates")
            conv = claude_jsonl.ler_conversa(caminho, sessao)
            if conv.entrypoint.startswith("sdk"):
                cont["c>g automaticas"] += 1
                continue
            if _espelho(conv, sessao, estado, "grok"):
                cont["c>g espelhos"] += 1
                continue
            if not any(t.papel == "usuario" for t in conv.turnos):
                cont["c>g vazias"] += 1
                continue
            rotulo = "c>g atualizadas" if anterior else "c>g criadas"
            if a.seco:
                print(f"{rotulo}: {conv.titulo} ({len(conv.turnos)} turnos)")
            else:
                versao = anterior["versao"] + 1 if anterior else 0
                sid = grok.id_para_sessao(sessao, versao)
                pasta = grok.gravar_sessao(conv, sid, cwd_local(conv.cwd), fonte)
                if anterior and not manter and anterior["pasta"] != pasta:
                    grok.apagar(anterior["pasta"])           # voce continuou la? (manter) entao a antiga fica
                imp[sessao] = {"id": sid, "versao": versao, "pasta": pasta}
                gravar_estado(estado)
            cont[rotulo] += 1
        except (OSError, ValueError, RuntimeError) as e:
            cont["erro"] += 1
            print(f"erro na sessao {sessao} (grok): {type(e).__name__}: {e}", file=sys.stderr)


# ---------------------------------------------------------------- Grok -> Claude
def grok_para_claude(a, estado, cont, novas):
    versao_cc = None
    for pasta, sid in grok.sessoes():
        if (a.grok and sid != a.grok) or a.sessao or a.cascade or a.codex or a.cursor:
            continue
        try:
            m = grok.marca(pasta)
            if m is not None and os.path.getsize(os.path.join(pasta, "updates.jsonl")) == m.get("bytes_updates"):
                cont["g>c espelhos"] += 1                 # veio do Claude e ninguem continuou no Grok
                continue
            rec = estado["exportadas_grok"].get(sid)
            assin = grok.assinatura(pasta)
            if rec and rec.get("assinatura") == assin and not a.forcar:
                cont["g>c iguais"] += 1
                continue
            if rec and para_claude.achar_sessao(rec["sessao"]) is None and not a.forcar:
                cont["g>c apagadas_no_claude"] += 1
                continue
            conv = grok.ler_sessao(pasta)
            if m is not None and len(conv.turnos) <= m["turnos"]:
                cont["g>c espelhos"] += 1
                continue
            if not any(t.papel == "usuario" for t in conv.turnos):
                cont["g>c vazias"] += 1
                continue
            rotulo = "g>c atualizadas" if rec else "g>c criadas"
            if a.seco:
                print(f"{rotulo}: {conv.titulo} ({len(conv.turnos)} turnos)")
            else:
                versao = 0
                if rec:
                    caminho = para_claude.achar_sessao(rec["sessao"])
                    continuada = caminho is not None and \
                        len(claude_jsonl.ler_conversa(caminho, rec["sessao"]).turnos) > rec["turnos"]
                    versao = rec["versao"] + 1 if continuada else rec["versao"]
                sessao = para_claude.id_sessao(sid, versao, "grok")
                cwd = cwd_local(conv.cwd)
                versao_cc = versao_cc or para_claude.versao_claude()
                para_claude.gravar_sessao(conv, sessao, para_claude.pasta_do_projeto(cwd), cwd, versao_cc, "grok")
                estado["exportadas_grok"][sid] = {"sessao": sessao, "versao": versao, "assinatura": assin,
                                                  "turnos": len(conv.turnos)}
                gravar_estado(estado)
                novas.add(sessao)
            cont[rotulo] += 1
        except (OSError, ValueError, RuntimeError) as e:
            cont["erro"] += 1
            print(f"erro na sessao {sid} (grok -> claude): {type(e).__name__}: {e}", file=sys.stderr)


# ---------------------------------------------------------------- Claude -> Codex
def _limpar_preparo():
    """As copias so existem durante a importacao (o Codex guarda a conversa dele; a fonte so e lida na hora)."""
    for raiz, _, arquivos in os.walk(PREPARO, topdown=False):
        for nome in arquivos:
            if nome.endswith((".jsonl", ".novo")):
                os.remove(os.path.join(raiz, nome))
        if raiz != PREPARO and not os.listdir(raiz):
            os.rmdir(raiz)


def para_codex(a, estado, cont, srv):
    """Pela importacao nativa do Codex. Continuada no Codex nunca recebe a atualizacao: a entrada do registro e
    esquecida e a fonte entra como conversa nova (versao nova); a continuada fica, e vai para o Claude como [Codex]."""
    ctx = {"regs": codex.registros(), "vivas": None}
    ctx["por_nome"] = {os.path.basename(p).lower(): (p, tid) for p, tid in ctx["regs"].items()}
    pendentes = []
    try:
        for caminho in _sessoes_do_claude():
            sessao = os.path.basename(caminho)[:-6]
            if not ID_RE.match(sessao) or _fora_do_filtro(a, sessao):
                continue
            try:
                item = _preparar_para_codex(a, estado, cont, srv, ctx, caminho, sessao)
                if item:
                    pendentes.append(item)
            except (OSError, ValueError, codex.ErroCodex) as e:
                cont["erro"] += 1
                print(f"erro na sessao {sessao} (codex): {type(e).__name__}: {e}", file=sys.stderr)
        if pendentes:
            _importar_pendentes(estado, cont, srv, pendentes)
    finally:
        _limpar_preparo()


def _sessao_para_codex(a, estado, cont, caminho, sessao):
    """A conversa do Claude que deve ir para o Codex nesta rodada (marca, conversa), ou None (contada)."""
    st = os.stat(caminho)
    marca = (st.st_size, round(st.st_mtime, 3))
    anterior = estado["importadas_codex"].get(sessao)
    if anterior and (anterior.get("fonte_tamanho"), anterior.get("fonte_mtime")) == marca and not a.forcar:
        cont["c>x iguais"] += 1
        return None
    conv = claude_jsonl.ler_conversa(caminho, sessao)
    motivo = ("automaticas" if conv.entrypoint.startswith("sdk") else
              "espelhos" if _espelho(conv, sessao, estado, "codex") else
              "vazias" if not any(t.papel == "usuario" for t in conv.turnos) else None)
    if motivo:
        cont["c>x " + motivo] += 1
        return None
    return marca, conv


def _conversa_existente(estado, ctx, sessao, prep):
    """(id da conversa que ja existe no Codex para a sessao, caminho do registro antigo a adotar ou None)."""
    existente = (estado["importadas_codex"].get(sessao) or {}).get("id")
    if existente:
        return existente, None
    existente = ctx["regs"].get(codex.normalizar(prep))          # estado perdido neste PC
    if existente:
        return existente, None
    antigo = ctx["por_nome"].get((sessao + ".jsonl").lower())    # importada antes por outro caminho
    return (antigo[1], antigo[0]) if antigo else (None, None)


def _versao_nova_no_codex(a, srv, ctx, existente):
    """A conversa que ja existe no Codex pede versao nova? True: continuada la (ou apagada, com --forcar);
    False: atualiza a mesma; None: apagada no Codex, nao volta."""
    ctx["vivas"] = codex.listar(srv) if ctx["vivas"] is None else ctx["vivas"]
    if existente not in ctx["vivas"]:
        return True if a.forcar else None
    return bool(codex.turnos_nativos(codex.ler(srv, existente)))


def _preparar_para_codex(a, estado, cont, srv, ctx, caminho, sessao):
    achado = _sessao_para_codex(a, estado, cont, caminho, sessao)
    if not achado:
        return None
    marca, conv = achado
    anterior = estado["importadas_codex"].get(sessao) or {}
    cwd = cwd_local(conv.cwd)
    prep = codex.caminho_preparado(sessao, cwd, PREPARO)
    existente, registro_antigo = _conversa_existente(estado, ctx, sessao, prep)
    versao_nova = _versao_nova_no_codex(a, srv, ctx, existente) if existente else False
    if versao_nova is None:
        cont["c>x apagadas_no_codex"] += 1
        return None
    rotulo = "c>x versoes_novas" if versao_nova else ("c>x atualizadas" if existente else "c>x criadas")
    if a.seco:
        print(f"{rotulo}: {conv.titulo} ({len(conv.turnos)} turnos)")
        cont[rotulo] += 1
        return None
    codex.preparar(caminho, sessao, cwd, PREPARO)
    if versao_nova:
        codex.esquecer_registro(prep)
    elif registro_antigo:
        codex.adotar_registro(prep, sessao)
    # a conversa antiga (continuada no Codex) continua ligada a esta sessao: o titulo dela vem daqui
    anteriores = list(anterior.get("anteriores") or []) + ([existente] if versao_nova else [])
    return {"sessao": sessao, "prep": prep, "cwd": cwd, "titulo": conv.titulo, "marca": marca, "rotulo": rotulo,
            "versao": anterior.get("versao", 0) + (1 if versao_nova else 0), "anteriores": anteriores}


def _importar_pendentes(estado, cont, srv, pendentes):
    feitos, falhas = codex.importar(srv, [(p["prep"], p["cwd"], p["titulo"]) for p in pendentes],
                                    a_cada_lote=_renovar_trava)
    regs = codex.registros()
    nomes = None
    for p in pendentes:
        k = codex.normalizar(p["prep"])
        if k in falhas:
            cont["erro"] += 1
            print(f"erro na sessao {p['sessao']} (codex): {falhas[k]}", file=sys.stderr)
            continue
        tid = feitos.get(k) or regs.get(k)        # fonte igual a importada: sem aviso, mas o registro diz qual
        if not tid or not ID_RE.match(tid):
            cont["c>x recusadas_pelo_codex"] += 1
            print(f"o Codex nao importou a sessao {p['sessao']} ({p['titulo']})", file=sys.stderr)
            continue
        antes = estado["importadas_codex"].get(p["sessao"]) or {}
        if antes.get("id") == tid and p["titulo"] != antes.get("titulo"):   # conversa nova ja nasce com o nome
            nomes = codex.listar(srv) if nomes is None else nomes
            _atualizar_nome_codex(srv, tid, (nomes.get(tid) or {}).get("name"), antes.get("titulo"), p["titulo"])
        rec = {"id": tid, "versao": p["versao"], "fonte_tamanho": p["marca"][0], "fonte_mtime": p["marca"][1],
               "titulo": p["titulo"]}
        anteriores = [x for x in p["anteriores"] if isinstance(x, str) and ID_RE.match(x)][-50:]
        if anteriores:
            rec["anteriores"] = anteriores
        estado["importadas_codex"][p["sessao"]] = rec
        cont[p["rotulo"] if k in feitos else "c>x iguais"] += 1
    gravar_estado(estado)


def _atualizar_nome_codex(srv, tid, atual, titulo_antigo, titulo):
    """A importacao do Codex nao troca o nome de uma conversa que ja existe. Troca aqui quando o titulo do Claude mudou,
    mas so se o nome no Codex ainda e o que nos pusemos (vazio, o titulo antigo, ou o comeco do novo): nome trocado a
    mao no Codex fica."""
    if atual == titulo:
        return
    if not atual or atual == titulo_antigo or titulo.startswith(atual):
        srv.chamar("thread/name/set", {"threadId": tid, "name": titulo})


# ---------------------------------------------------------------- Codex -> Claude
def _origens_codex(estado):
    """{id de conversa do Codex: sessao do Claude de onde ela foi importada}, incluindo as versoes antigas."""
    origem = {}
    for s, v in estado["importadas_codex"].items():
        for x in [*(v.get("anteriores") or []), v["id"]]:
            origem[x] = s
    return origem


def _devolver_titulo(a, srv, th, sessao):
    """O Codex apaga o nome da importada quando ela e continuada: volta o titulo da sessao de origem."""
    fonte = para_claude.achar_sessao(sessao) if sessao else None
    if th.get("name") or not codex.importada(th) or not fonte:
        return
    th["name"] = claude_jsonl.ler_conversa(fonte, sessao).titulo
    if not a.seco:
        srv.chamar("thread/name/set", {"threadId": th["id"], "name": th["name"]})


def _versao_no_claude(rec):
    """Versao da sessao do Claude: a gravada antes, ou a seguinte se ela foi continuada no Claude."""
    if not rec:
        return 0
    caminho = para_claude.achar_sessao(rec["sessao"])
    continuada = caminho is not None and len(claude_jsonl.ler_conversa(caminho, rec["sessao"]).turnos) > rec["turnos"]
    return rec["versao"] + 1 if continuada else rec["versao"]


def codex_para_claude(a, estado, cont, novas, srv):
    origem = _origens_codex(estado)
    ctx = {"a": a, "estado": estado, "cont": cont, "srv": srv, "versao_cc": None}
    for tid, resumo in codex.listar(srv).items():
        if not ID_RE.match(tid) or (a.codex and tid != a.codex) or a.sessao or a.cascade or a.grok or a.cursor:
            continue
        try:
            sessao = _codex_para_claude_uma(ctx, tid, resumo.get("updatedAt"), origem.get(tid))
            if sessao:
                novas.add(sessao)
        except (OSError, ValueError, codex.ErroCodex) as e:
            cont["erro"] += 1
            print(f"erro na conversa {tid} (codex -> claude): {type(e).__name__}: {e}", file=sys.stderr)
    if not a.seco:
        gravar_estado(estado)


def _pular_codex(ctx, tid, atual, rec, th, assin):
    """Motivo para nao levar a conversa ao Claude (contado em x>c <motivo>), ou None."""
    a, vistas = ctx["a"], ctx["estado"]["vistas_codex"]
    if codex.importada(th) and not codex.turnos_nativos(th):
        vistas[tid] = atual                        # veio do Claude e ninguem continuou no Codex
        return "espelhos"
    if rec and rec.get("assinatura") == assin and not a.forcar:
        rec["atualizado"] = atual
        return "iguais"
    if rec and para_claude.achar_sessao(rec["sessao"]) is None and not a.forcar:
        return "apagadas_no_claude"
    return None


def _codex_para_claude_uma(ctx, tid, atual, sessao_origem):
    """Leva uma conversa do Codex para o Claude; devolve a sessao gravada ou None (contada)."""
    a, estado, cont, srv = ctx["a"], ctx["estado"], ctx["cont"], ctx["srv"]
    exp, vistas = estado["exportadas_codex"], estado["vistas_codex"]
    rec = exp.get(tid)
    if not a.forcar and atual is not None and ((rec and rec.get("atualizado") == atual) or vistas.get(tid) == atual):
        cont["x>c iguais"] += 1
        return None
    th = codex.ler(srv, tid)
    if codex.importada(th) and codex.turnos_nativos(th):
        _devolver_titulo(a, srv, th, sessao_origem)
    assin = f"{codex.assinatura(th)}|{th.get('name') or ''}"
    motivo = _pular_codex(ctx, tid, atual, rec, th, assin)
    conv = None if motivo else codex.conversa_de_thread(th)
    if conv is not None and not any(t.papel == "usuario" for t in conv.turnos):
        vistas[tid] = atual
        motivo = "vazias"
    if motivo:
        cont["x>c " + motivo] += 1
        return None
    rotulo = "x>c atualizadas" if rec else "x>c criadas"
    cont[rotulo] += 1
    if a.seco:
        print(f"{rotulo}: {conv.titulo} ({len(conv.turnos)} turnos)")
        return None
    versao = _versao_no_claude(rec)
    sessao = para_claude.id_sessao(tid, versao, "codex")
    cwd = cwd_local(conv.cwd)
    ctx["versao_cc"] = ctx["versao_cc"] or para_claude.versao_claude()
    para_claude.gravar_sessao(conv, sessao, para_claude.pasta_do_projeto(cwd), cwd, ctx["versao_cc"], "codex")
    exp[tid] = {"sessao": sessao, "versao": versao, "assinatura": assin, "turnos": len(conv.turnos), "atualizado": atual}
    gravar_estado(estado)
    return sessao


# ---------------------------------------------------------------- main
# ---------------------------------------------------------------- Claude -> Cursor
def _cursor_pular(a, estado, cont, caminho, sessao, fonte):
    """None se a sessao vai para o Cursor; senao conta o motivo e devolve True."""
    anterior = estado["importadas_cursor"].get(sessao)
    m = cursor.marca(anterior["pasta"]) if anterior and os.path.isdir(anterior["pasta"]) else None
    if anterior and m is None and not a.forcar:
        cont["c>u apagadas_no_cursor"] += 1
        return True
    if m is not None and (m.get("fonte_tamanho"), m.get("fonte_mtime")) == (fonte["fonte_tamanho"], fonte["fonte_mtime"]) \
            and not a.forcar:
        cont["c>u iguais"] += 1
        return True
    return None


def para_cursor(a, estado, cont):
    """Grava as sessoes do Claude como conversas do CLI do Cursor (o app le o mesmo armazenamento). Continuada no
    Cursor nunca e sobrescrita: a versao nova do Claude vira outra conversa e a continuada fica."""
    if not cursor.instalado():
        return
    molde = None
    imp = estado["importadas_cursor"]
    for caminho in _sessoes_do_claude():
        sessao = os.path.basename(caminho)[:-6]
        if not ID_RE.match(sessao) or _fora_do_filtro(a, sessao):
            continue
        try:
            st = os.stat(caminho)
            fonte = {"fonte": "claude:" + sessao, "fonte_tamanho": st.st_size, "fonte_mtime": round(st.st_mtime, 3)}
            if _cursor_pular(a, estado, cont, caminho, sessao, fonte):
                continue
            conv = claude_jsonl.ler_conversa(caminho, sessao)
            motivo = ("automaticas" if conv.entrypoint.startswith("sdk") else
                      "espelhos" if _espelho(conv, sessao, estado, "cursor") else
                      "vazias" if not any(t.papel == "usuario" for t in conv.turnos) else None)
            if motivo:
                cont["c>u " + motivo] += 1
                continue
            molde = molde or cursor.achar_molde(MOLDE_CURSOR)
            if molde is None:
                cont["c>u sem conversa nativa para molde"] += 1
                return
            anterior = imp.get(sessao)
            m = cursor.marca(anterior["pasta"]) if anterior and os.path.isdir(anterior["pasta"]) else None
            manter = m is not None and cursor.turnos(anterior["pasta"]) > m.get("turnos", 0)   # continuada no Cursor
            rotulo = "c>u atualizadas" if anterior else "c>u criadas"
            if a.seco:
                print(f"{rotulo}: {conv.titulo} ({len(conv.turnos)} turnos)")
                cont[rotulo] += 1
                continue
            versao = (anterior["versao"] + 1 if manter or m is None else anterior["versao"]) if anterior else 0
            aid = cursor.id_para_sessao(sessao, versao)
            pasta = cursor.gravar_conversa(conv, cwd_local(conv.cwd), molde, aid, conv.titulo, fonte)
            if anterior and not manter and os.path.normcase(anterior["pasta"]) != os.path.normcase(pasta):
                cursor.apagar(anterior["pasta"])
            imp[sessao] = {"id": aid, "versao": versao, "pasta": pasta}
            gravar_estado(estado)
            cont[rotulo] += 1
        except (OSError, ValueError, KeyError, sqlite3.Error) as e:
            cont["erro"] += 1
            print(f"erro na sessao {sessao} (cursor): {type(e).__name__}: {e}", file=sys.stderr)


# ---------------------------------------------------------------- Cursor -> Claude
def _cursor_exportar(a, estado, cont, novas, ctx, aid, assin, ler):
    """Grava no Claude uma conversa do Cursor (do CLI ou do app); ler() devolve a Conversa so se precisar."""
    exp = estado["exportadas_cursor"]
    rec = exp.get(aid)
    if rec and rec.get("assinatura") == assin and not a.forcar:
        cont["u>c iguais"] += 1
        return
    if rec and para_claude.achar_sessao(rec["sessao"]) is None and not a.forcar:
        cont["u>c apagadas_no_claude"] += 1
        return
    conv = ler()
    if not any(t.papel == "usuario" for t in conv.turnos):
        cont["u>c vazias"] += 1
        return
    rotulo = "u>c atualizadas" if rec else "u>c criadas"
    cont[rotulo] += 1
    if a.seco:
        print(f"{rotulo}: {conv.titulo} ({len(conv.turnos)} turnos)")
        return
    versao = _versao_no_claude(rec)
    sessao = para_claude.id_sessao(aid, versao, "cursor")
    cwd = cwd_local(conv.cwd)
    ctx["versao_cc"] = ctx.get("versao_cc") or para_claude.versao_claude()
    para_claude.gravar_sessao(conv, sessao, para_claude.pasta_do_projeto(cwd), cwd, ctx["versao_cc"], "cursor")
    exp[aid] = {"sessao": sessao, "versao": versao, "assinatura": assin, "turnos": len(conv.turnos)}
    gravar_estado(estado)
    novas.add(sessao)


def _cursor_pular_filtro(a, aid):
    return bool((a.cursor and aid != a.cursor) or a.sessao or a.cascade or a.grok or a.codex)


def cursor_para_claude(a, estado, cont, novas):
    """Conversas do CLI (~/.cursor/chats) e do app (state.vscdb, so leitura) viram sessoes "[Cursor] ..." no Claude."""
    ctx = {}
    for pasta, aid in cursor.conversas():
        if _cursor_pular_filtro(a, aid):
            continue
        try:
            m = cursor.marca(pasta)
            if m is not None and cursor.turnos(pasta) <= m.get("turnos", 0):
                cont["u>c espelhos"] += 1                # veio do Claude e ninguem continuou no Cursor
                continue
            _cursor_exportar(a, estado, cont, novas, ctx, aid, cursor.assinatura(pasta),
                             lambda p=pasta: cursor.ler_conversa(p))
        except (OSError, ValueError, KeyError, sqlite3.Error) as e:
            cont["erro"] += 1
            print(f"erro na conversa {aid} (cursor -> claude): {type(e).__name__}: {e}", file=sys.stderr)
    try:
        do_app = cursor.conversas_app()
    except sqlite3.Error as e:                           # app gravando no banco: fica para a proxima rodada
        cont["erro"] += 1
        print(f"erro ao ler as conversas do app do Cursor: {type(e).__name__}: {e}", file=sys.stderr)
        do_app = []
    for cid, _dados in do_app:
        if _cursor_pular_filtro(a, cid):
            continue
        try:
            conv, assin = cursor.ler_conversa_app(cid)
            _cursor_exportar(a, estado, cont, novas, ctx, cid, assin, lambda c=conv: c)
        except (OSError, ValueError, KeyError, sqlite3.Error) as e:
            cont["erro"] += 1
            print(f"erro na conversa {cid} (app do cursor -> claude): {type(e).__name__}: {e}", file=sys.stderr)


def _propagar(a, estado, cont, srv, ativas):
    """Leva o que acabou de virar sessao do Claude para as outras IAs marcadas."""
    for ia, fn in (("antigravity", para_antigravity), ("grok", para_grok), ("cursor", para_cursor)):
        if ia in ativas:
            fn(a, estado, cont)
    if "codex" in ativas:
        _codex(a, estado, cont, srv, para_codex)


class _CodexPreguicoso:
    """Um app-server do Codex por rodada, aberto so se alguma direcao precisar e sempre encerrado no fim."""

    def __init__(self):
        self.srv = None

    def __call__(self):
        if self.srv is None:
            os.makedirs(PREPARO, exist_ok=True)
            # a importacao procura as sessoes em <home>/.claude/projects: a home do filho e a area de preparo
            self.srv = codex.Servidor(env_extra={"USERPROFILE": PREPARO, "HOME": PREPARO}).__enter__()
        return self.srv

    def fechar(self):
        if self.srv is not None:
            self.srv.fechar()
            self.srv = None


def _codex(a, estado, cont, srv, fn, *args):
    """Direcao do Codex: falha do app-server (nao instalado, sem resposta) vira erro contado, nunca derruba a rodada."""
    if not codex.instalado():
        return
    try:
        fn(a, estado, cont, *args, srv())
    except codex.ErroCodex as e:
        cont["erro"] += 1
        print(f"codex: {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--direcao", choices=("ambas", "antigravity", "claude", "grok", "claude-de-grok", "codex",
                                          "claude-de-codex", "cursor", "claude-de-cursor"), default="ambas")
    ap.add_argument("--sessao", help="id de uma sessao do Claude")
    ap.add_argument("--cascade", help="id de uma conversa do Antigravity")
    ap.add_argument("--grok", help="id de uma sessao do Grok")
    ap.add_argument("--codex", help="id de uma conversa do Codex")
    ap.add_argument("--cursor", help="id de uma conversa do Cursor")
    ap.add_argument("--seco", action="store_true", help="so mostra o que faria")
    ap.add_argument("--forcar", action="store_true",
                    help="regrava mesmo sem mudanca e recria a apagada no destino (nunca a continuada)")
    ap.add_argument("--atraso", type=int, default=0, help="segundos de espera antes (deixa outra sincronizacao de pastas terminar)")
    a = ap.parse_args()
    a.so = None
    for valor in (a.sessao, a.cascade, a.grok, a.codex, a.cursor):
        if valor and not ID_RE.match(valor):
            sys.exit("id invalido")
    if not 0 <= a.atraso <= 600:
        sys.exit("atraso fora de 0..600")
    time.sleep(a.atraso)
    cfg = config.carregar()
    ativas = {ia for ia, instalada in (("antigravity", bool(antigravity.bases())), ("grok", grok.instalado()),
                                        ("codex", codex.instalado()), ("cursor", cursor.instalado()))
              if instalada and config.conversas(cfg, ia)}
    if not ativas:
        print("nenhuma IA marcada para sincronizar esta instalada neste PC; nada a fazer")
        return 0
    if not a.seco and not pegar_trava():
        if not esperar_trava(_argumentos_da_fila(sys.argv[1:])):
            print("outra sincronizacao igual ja esta esperando a vez (ou o prazo acabou); saindo")
            return 0
    from collections import Counter
    cont = Counter()
    srv = _CodexPreguicoso()
    try:
        estado = ler_estado()
        d = a.direcao
        if d in ("ambas", "antigravity") and "antigravity" in ativas:
            para_antigravity(a, estado, cont)
        if d in ("ambas", "grok") and "grok" in ativas:
            para_grok(a, estado, cont)
        if d in ("ambas", "codex") and "codex" in ativas:
            _codex(a, estado, cont, srv, para_codex)
        if d in ("ambas", "cursor") and "cursor" in ativas:
            para_cursor(a, estado, cont)
        novas = set()
        if d in ("ambas", "claude") and "antigravity" in ativas:
            para_claude_(a, estado, cont, novas)
        if d in ("ambas", "claude-de-grok") and "grok" in ativas:
            grok_para_claude(a, estado, cont, novas)
        if d in ("ambas", "claude-de-codex") and "codex" in ativas:
            _codex(a, estado, cont, srv, codex_para_claude, novas)
        if d in ("ambas", "claude-de-cursor") and "cursor" in ativas:
            cursor_para_claude(a, estado, cont, novas)
        # o que acabou de virar sessao do Claude segue para as outras IAs na mesma rodada (a de origem reconhece o
        # proprio espelho e nao o recebe de volta)
        if novas and not a.seco:
            a.so, a.sessao, a.cascade, a.grok, a.codex, a.cursor = novas, None, None, None, None, None
            _propagar(a, estado, cont, srv, ativas)
        print(json.dumps(dict(sorted(cont.items())), ensure_ascii=False))
        return 1 if cont["erro"] else 0
    finally:
        srv.fechar()
        if not a.seco and os.path.exists(TRAVA):
            os.remove(TRAVA)


if __name__ == "__main__":
    sys.exit(main())
