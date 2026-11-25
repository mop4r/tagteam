"""Sincroniza conversas entre Claude Code, Antigravity (app e CLI agy) e Grok CLI. O Claude e o centro:
cada conversa vira sessao do Claude e dali vai para as outras duas (levar as sessoes do Claude aos outros PCs fica com a sincronizacao de pastas que voce usar).

Uso:
  python -I sincronizar.py                            # tudo, em todas as direcoes
  python -I sincronizar.py --direcao antigravity      # so Claude -> Antigravity
  python -I sincronizar.py --direcao claude           # so Antigravity -> Claude (e segue para o Grok)
  python -I sincronizar.py --direcao grok             # so Claude -> Grok
  python -I sincronizar.py --direcao claude-de-grok   # so Grok -> Claude (e segue para o Antigravity)
  python -I sincronizar.py --sessao <id>              # uma sessao do Claude (para Antigravity e Grok)
  python -I sincronizar.py --cascade <id>             # uma conversa do Antigravity
  python -I sincronizar.py --grok <id>                # uma sessao do Grok
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
import grok  # noqa: E402
import para_claude  # noqa: E402
import servidor_local  # noqa: E402

PROJETOS = os.path.join(os.path.expanduser("~"), ".claude", "projects")
# pasta usada quando a de uma sessao nao existe neste PC (TAGTEAM_WORKSPACE; padrao: a home)
WORKSPACE = os.environ.get("TAGTEAM_WORKSPACE") or os.path.expanduser("~")
ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
TRAVA_VELHA_S = 30 * 60
ESPERA_S = 10
ETIQUETA = "claude"
PC = re.sub(r"[^A-Za-z0-9_-]", "_", os.environ.get("COMPUTERNAME", "pc"))[:40]
PASTA_ESTADO = os.environ.get("TAGTEAM_STATE_DIR") or os.path.join(os.path.expanduser("~"), ".tagteam")
ESTADO = os.path.join(PASTA_ESTADO, f"estado-{PC}.json")
TRAVA = os.path.join(PASTA_ESTADO, f".trava-{PC}")


# ---------------------------------------------------------------- estado e trava
def _entrada_valida(v, chave_id):
    return isinstance(v, dict) and ID_RE.match(str(v.get(chave_id, ""))) and isinstance(v.get("versao"), int)


def ler_estado():
    try:
        with open(ESTADO, encoding="utf-8") as f:
            d = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        d = {}
    d = d if isinstance(d, dict) else {}
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
    exp = d.get("exportadas") if isinstance(d.get("exportadas"), dict) else {}
    for cascade, v in list(exp.items()):
        if not (_entrada_valida(v, "sessao") and isinstance(v.get("passos"), int) and isinstance(v.get("turnos"), int)):
            del exp[cascade]
    imp_g = {s: v for s, v in (d.get("importadas_grok") or {}).items()
             if _entrada_valida(v, "id") and isinstance(v.get("pasta"), str)} if isinstance(d.get("importadas_grok"), dict) else {}
    exp_g = {g: v for g, v in (d.get("exportadas_grok") or {}).items()
             if _entrada_valida(v, "sessao") and isinstance(v.get("turnos"), int)} if isinstance(d.get("exportadas_grok"), dict) else {}
    return {"importadas": imp, "exportadas": exp, "importadas_grok": imp_g, "exportadas_grok": exp_g}


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
    chave = "exportadas" if origem == "antigravity" else "exportadas_grok"
    rec = next((r for r in estado[chave].values() if r["sessao"] == sessao), None)
    return rec is not None and len(conv.turnos) <= rec["turnos"]


def _espelho_do_antigravity(conv, sessao, estado):
    return _espelho(conv, sessao, estado, "antigravity")


def _fora_do_filtro(a, sessao):
    """Filtro de sessao do Claude: --sessao, ou o conjunto que acabou de ser exportado (propagacao)."""
    if a.so is not None:
        return sessao not in a.so
    return bool((a.sessao and sessao != a.sessao) or a.cascade or a.grok)


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
    for caminho in glob.glob(os.path.join(PROJETOS, "*", "*.jsonl")):
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
            if _espelho(conv, sessao, estado, "antigravity"):
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
        if (a.cascade and cascade != a.cascade) or a.sessao or a.grok:
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
    for caminho in glob.glob(os.path.join(PROJETOS, "*", "*.jsonl")):
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
        if (a.grok and sid != a.grok) or a.sessao or a.cascade:
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


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--direcao", choices=("ambas", "antigravity", "claude", "grok", "claude-de-grok"), default="ambas")
    ap.add_argument("--sessao", help="id de uma sessao do Claude")
    ap.add_argument("--cascade", help="id de uma conversa do Antigravity")
    ap.add_argument("--grok", help="id de uma sessao do Grok")
    ap.add_argument("--seco", action="store_true", help="so mostra o que faria")
    ap.add_argument("--forcar", action="store_true",
                    help="regrava mesmo sem mudanca e recria a apagada no destino (nunca a continuada)")
    ap.add_argument("--atraso", type=int, default=0, help="segundos de espera antes (deixa outra sincronizacao de pastas terminar)")
    a = ap.parse_args()
    a.so = None
    for valor in (a.sessao, a.cascade, a.grok):
        if valor and not ID_RE.match(valor):
            sys.exit("id invalido")
    if not 0 <= a.atraso <= 600:
        sys.exit("atraso fora de 0..600")
    time.sleep(a.atraso)
    if not antigravity.bases() and not grok.instalado():
        print("nem Antigravity nem Grok instalados neste PC; nada a fazer")
        return 0
    if not a.seco and not pegar_trava():
        print("outra sincronizacao esta rodando; saindo")
        return 0
    from collections import Counter
    cont = Counter()
    try:
        estado = ler_estado()
        d = a.direcao
        if d in ("ambas", "antigravity"):
            para_antigravity(a, estado, cont)
        if d in ("ambas", "grok"):
            para_grok(a, estado, cont)
        de_agy, de_grok = set(), set()
        if d in ("ambas", "claude"):
            para_claude_(a, estado, cont, de_agy)
        if d in ("ambas", "claude-de-grok"):
            grok_para_claude(a, estado, cont, de_grok)
        # o que acabou de virar sessao do Claude segue para a terceira IA na mesma rodada
        if de_agy and not a.seco:
            a.so = de_agy
            para_grok(a, estado, cont)
        if de_grok and not a.seco:
            a.so = de_grok
            para_antigravity(a, estado, cont)
        print(json.dumps(dict(sorted(cont.items())), ensure_ascii=False))
        return 1 if cont["erro"] else 0
    finally:
        if not a.seco and os.path.exists(TRAVA):
            os.remove(TRAVA)


if __name__ == "__main__":
    sys.exit(main())
