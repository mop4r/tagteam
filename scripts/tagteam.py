"""TagTeam: instalador e monitor no terminal (python -I tagteam.py).

Primeira vez (sem ~/.tagteam/config.json): assistente. Mostra o que esta instalado, a pessoa marca as IAs e as
opcoes, e a instalacao roda com o progresso na tela. Depois: monitor ao vivo (saude, conversas por IA, rodadas,
falhas) com as acoes de sincronizar agora, mudar IAs e opcoes, reparar, ver o log, atualizar e desinstalar.

  --simples   perguntas numeradas, sem tela cheia (tambem o padrao fora de um console do Windows)
  --status    imprime a saude e sai (codigo 1 se houver erro)
"""
import argparse
import json
import os
import subprocess
import sys
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import config  # noqa: E402
import saude  # noqa: E402
import tela  # noqa: E402
from tela import cor  # noqa: E402

VERSAO = "0.3.0"
SKILL = os.path.dirname(AQUI)
ATUALIZA_S = 2.0
PRAZO_SIMULAR_S = 600
NIVEL_COR = {"ok": "verde", "aviso": "amarelo", "erro": "vermelho", "info": "ciano"}
NIVEL_ROTULO = {"ok": ("OK", "OK"), "aviso": ("AVISO", "WARNING"), "erro": ("ERRO", "ERROR"), "info": ("INFO", "INFO")}
LOGIN = {"antigravity": ("agy",), "grok": ("grok",), "codex": ("codex", "login"), "cursor": ("cursor-agent", "login")}

TEXTOS = {
    "titulo": ("TagTeam — uma conversa entre Claude Code, Gemini, Grok, Codex e Cursor",
               "TagTeam — one conversation across Claude Code, Gemini, Grok, Codex and Cursor"),
    "bemvindo": ("Vamos configurar a sincronização neste PC. Nada sai da sua máquina.",
                 "Let's set up sync on this PC. Nothing leaves your machine."),
    "sem_claude": ("O Claude Code não foi encontrado. Ele é o centro da sincronização: instale em "
                   "https://claude.com/claude-code e abra o TagTeam de novo.",
                   "Claude Code was not found. It is the hub of the sync: install it from "
                   "https://claude.com/claude-code and open TagTeam again."),
    "escolha_ias": ("Quais IAs sincronizar com o Claude Code?", "Which AIs should sync with Claude Code?"),
    "teclas_lista": ("↑↓ mover · Espaço marcar · Enter continuar · Esc voltar",
                     "↑↓ move · Space toggle · Enter continue · Esc back"),
    "instalado_app_cli": ("app e CLI instalados", "app and CLI installed"),
    "instalado_app": ("app instalado", "app installed"),
    "instalado_cli": ("CLI instalado", "CLI installed"),
    "nao_instalado_inst": ("não instalado — o TagTeam instala o {x} oficial", "not installed — TagTeam installs the official {x}"),
    "nao_instalado": ("não instalado — instale o Cursor antes", "not installed — install Cursor first"),
    "opcoes": ("Opções", "Options"),
    "teclas_opcoes": ("↑↓ mover · Enter mudar · C continuar · Esc voltar",
                      "↑↓ move · Enter change · C continue · Esc back"),
    "o_pasta": ("Pasta padrão (atalhos abrem nela)", "Default folder (shortcuts open here)"),
    "o_ambiente": ("Mesmo ambiente do Claude nas IAs marcadas (CLAUDE.md, skills, plugins, MCP)",
                   "Same Claude environment in the checked AIs (CLAUDE.md, skills, plugins, MCP)"),
    "o_clis": ("Instalar os CLIs que faltam (instaladores oficiais)", "Install missing CLIs (official installers)"),
    "o_atalhos": ("Atalhos no Menu Iniciar", "Start Menu shortcuts"),
    "o_admin": ("Atalhos como administrador", "Shortcuts run as administrator"),
    "o_livre": ("Atalhos sem pedir aprovação a cada comando (cuidado)", "Shortcuts skip approval prompts (careful)"),
    "o_status": ("Status line do uso do limite no Claude", "Usage status line in Claude"),
    "o_passagem": ("Passagem automática perto do limite", "Automatic handoff near the limit"),
    "o_pct": ("Limite da passagem (%)", "Handoff threshold (%)"),
    "o_excluidas": ("Pastas que nunca saem do Claude", "Folders that never leave Claude"),
    "home": ("sua pasta pessoal", "your home folder"),
    "nenhuma": ("nenhuma", "none"),
    "pergunta_pasta": ("Pasta (Enter vazio = pasta pessoal): ", "Folder (empty = home): "),
    "pergunta_pct": ("Porcentagem de 1 a 100: ", "Percentage from 1 to 100: "),
    "pasta_invalida": ("Essa pasta não existe.", "That folder doesn't exist."),
    "excluidas_titulo": ("Pastas que nunca saem do Claude (e as subpastas)", "Folders that never leave Claude (and subfolders)"),
    "teclas_excluidas": ("A acrescentar · Del/X tirar · Enter/Esc voltar", "A add · Del/X remove · Enter/Esc back"),
    "resumo": ("Resumo", "Summary"),
    "sim": ("sim", "yes"), "nao": ("não", "no"),
    "enter_instalar": ("Enter instala · Esc volta", "Enter installs · Esc goes back"),
    "instalando": ("Instalando", "Installing"),
    "concluido": ("Pronto.", "Done."),
    "com_falhas": ("Terminou com falhas (veja acima). O resto foi instalado.", "Finished with failures (see above). The rest was installed."),
    "login": ("Faça login uma vez em cada CLI. Aperte o número para abrir numa janela nova:",
              "Sign in once to each CLI. Press the number to open it in a new window:"),
    "enter_monitor": ("Enter abre o monitor · Q sai", "Enter opens the monitor · Q quits"),
    "monitor": ("Monitor", "Monitor"),
    "conversas": ("conversas", "chats"),
    "hook": ("hook", "hook"),
    "nunca": ("nunca", "never"),
    "ha": ("há {x}", "{x} ago"),
    "rodadas_24h": ("Últimas 24 h: {r} rodadas · {m} mudanças · {e} com erro", "Last 24 h: {r} runs · {m} changes · {e} with errors"),
    "ultimas": ("Últimas rodadas", "Recent runs"),
    "sem_rodadas": ("nenhuma rodada ainda", "no runs yet"),
    "teclas_monitor": ("S sincronizar · T simular · I IAs · C opções · R reparar · L log · U atualizar · D desinstalar · Q sair",
                       "S sync · T dry run · I AIs · C options · R repair · L log · U update · D uninstall · Q quit"),
    "sincronizando": ("Sincronização completa iniciada em segundo plano.", "Full sync started in the background."),
    "desmarcada": ("desmarcada", "unchecked"),
    "nao_inst": ("não instalada", "not installed"),
    "so_conversas": ("só conversas", "chats only"),
    "so_ambiente": ("só ambiente", "environment only"),
    "ias_titulo": ("IAs: Espaço liga/desliga · C conversas · A ambiente · Enter aplica · Esc volta",
                   "AIs: Space on/off · C chats · A environment · Enter applies · Esc back"),
    "confirmar_des": ("Desinstalar tira hooks, atalhos e blocos gerados (nenhuma conversa é apagada). Digite SIM para confirmar: ",
                      "Uninstall removes hooks, shortcuts and generated blocks (no chat is deleted). Type YES to confirm: "),
    "palavra_sim": ("SIM", "YES"),
    "desinstalado": ("Desinstalado. Para usar de novo, abra o TagTeam.", "Uninstalled. To use it again, open TagTeam."),
    "procurando": ("Consultando a release mais nova no GitHub...", "Checking the latest release on GitHub..."),
    "em_dia": ("Você já está na versão mais nova ({x}).", "You already have the latest version ({x})."),
    "nova_versao": ("Versão nova: {x}. Atualizar agora? (s/n) ", "New version: {x}. Update now? (y/n) "),
    "atualizado": ("Atualizado para {x}. Reaplicando a instalação...", "Updated to {x}. Re-applying the setup..."),
    "tecla_voltar": ("Aperte uma tecla para voltar.", "Press any key to go back."),
    "simulando": ("Simulando (nada é gravado)...", "Dry run (nothing is written)..."),
    "abrir_log": ("Log aberto: {x}", "Log opened: {x}"),
    "sem_log": ("Ainda não há log.", "No log yet."),
    "tudo_certo": ("tudo certo", "all good"),
    "erro_ev": ("erro: ", "error: "),
    "legenda": ("→ do Claude para a IA · ← da IA para o Claude · hook: última chamada neste PC",
                "→ from Claude to the AI · ← from the AI to Claude · hook: last call on this PC"),
    "atencao": ("{n} ponto(s) de atenção", "{n} item(s) need attention"),
    "erros_n": ("{n} erro(s)", "{n} error(s)"),
    "mudancas_n": ("{n} mudança(s)", "{n} change(s)"),
    "em_dia_ev": ("em dia", "up to date"),
    "arquivos_n": ("{n} arquivo(s)", "{n} file(s)"),
    "completa": ("sincronização completa", "full sync"),
    "instalar": ("instalação", "setup"),
    "desinstalar": ("desinstalação", "uninstall"),
    "arquivos": ("arquivos", "files"),
}
IDIOMA = "pt"


def t(chave, **kw):
    pt, en = TEXTOS[chave]
    texto = pt if IDIOMA == "pt" else en
    return texto.format(**kw) if kw else texto


def definir_idioma(cfg):
    global IDIOMA
    IDIOMA = cfg.get("idioma") if cfg.get("idioma") in ("pt", "en") else tela.idioma_do_sistema()


def tempo(segundos):
    s = max(0, int(segundos))
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{s // 60} min"
    if s < 172800:
        return f"{s // 3600} h"
    return f"{s // 86400} d"


def situacao_instalada(det, ia):
    d = det[ia]
    if d["app"] and d["cli"]:
        return t("instalado_app_cli")
    if d["app"]:
        return t("instalado_app")
    if d["cli"]:
        return t("instalado_cli")
    if ia in saude.INSTALAVEL:
        return t("nao_instalado_inst", x=saude.INSTALAVEL[ia])
    return t("nao_instalado")


def pode_marcar(det, ia):
    return det[ia]["instalada"] or ia in saude.INSTALAVEL


# ---------------------------------------------------------------- telas genericas
def lista_marcavel(titulo, itens, rodape, extras=None):
    """Lista com caixas de marcar. itens: [{"rotulo", "desc", "marcado", "pode"}]. Devolve os itens (Enter) ou None
    (Esc). extras: {tecla: funcao(item)} para teclas a mais (ex.: C e A na tela de IAs)."""
    pos = 0
    while True:
        linhas = [cor(t("titulo"), "negrito"), "", cor(titulo, "negrito"), ""]
        for i, it in enumerate(itens):
            caixa = "[x]" if it["marcado"] else "[ ]"
            texto = f" {caixa} {it['rotulo']:<24} {cor(it['desc'], 'fraco')}"
            linhas.append(cor(texto, "inverso") if i == pos else texto)
        linhas += ["", cor(rodape, "fraco")]
        tela.Tela.desenhar(linhas)
        k = tela.tecla()
        if k in ("cima", "baixo"):
            pos = (pos + (1 if k == "baixo" else -1)) % len(itens)
        elif k == "espaco" and itens[pos].get("pode", True):
            itens[pos]["marcado"] = not itens[pos]["marcado"]
        elif extras and k in extras:
            extras[k](itens[pos])
        elif k == "enter":
            return itens
        elif k in ("esc", "sair"):
            return None


def mensagem(linhas):
    tela.Tela.desenhar([cor(t("titulo"), "negrito"), "", *linhas, "", cor(t("tecla_voltar"), "fraco")])
    tela.tecla()


# ---------------------------------------------------------------- opcoes
def _linhas_opcoes(cfg):
    sim_nao = lambda v: cor(t("sim"), "verde") if v else cor(t("nao"), "fraco")  # noqa: E731
    ambiente = all(cfg["ambiente"][ia] for ia in config.IAS if cfg["conversas"][ia]) and any(cfg["ambiente"].values())
    return [
        ("pasta", t("o_pasta"), cfg["pasta"] or t("home")),
        ("ambiente", t("o_ambiente"), sim_nao(ambiente)),
        ("instalar_clis", t("o_clis"), sim_nao(cfg["instalar_clis"])),
        ("atalhos", t("o_atalhos"), sim_nao(cfg["atalhos"])),
        ("atalhos_admin", t("o_admin"), sim_nao(cfg["atalhos_admin"])),
        ("atalhos_sem_aprovacao", t("o_livre"), sim_nao(cfg["atalhos_sem_aprovacao"])),
        ("statusline", t("o_status"), sim_nao(cfg["statusline"])),
        ("passagem", t("o_passagem"), sim_nao(cfg["passagem"])),
        ("passagem_pct", t("o_pct"), f"{cfg['passagem_pct']:g}"),
        ("pastas_excluidas", t("o_excluidas"), str(len(cfg["pastas_excluidas"])) if cfg["pastas_excluidas"] else t("nenhuma")),
    ]


def mudar_opcao(cfg, chave):
    """Muda uma opcao (Enter na tela de opcoes). Booleana inverte; pasta e % perguntam; excluidas abre a lista."""
    if chave == "ambiente":
        ligado = not all(cfg["ambiente"][ia] for ia in config.IAS if cfg["conversas"][ia])
        for ia in config.IAS:
            cfg["ambiente"][ia] = ligado and cfg["conversas"][ia]
    elif chave == "pasta":
        resp = tela.perguntar("\n" + t("pergunta_pasta"))
        if resp is not None:
            resp = resp.strip().strip('"')
            if not resp or os.path.isdir(resp):
                cfg["pasta"] = os.path.abspath(resp) if resp else ""
            else:
                mensagem([t("pasta_invalida")])
    elif chave == "passagem_pct":
        resp = tela.perguntar("\n" + t("pergunta_pct"))
        if resp and resp.strip().isdigit() and 1 <= int(resp) <= 100:
            cfg["passagem_pct"] = int(resp)
    elif chave == "pastas_excluidas":
        tela_excluidas(cfg)
    else:
        cfg[chave] = not cfg[chave]


def tela_opcoes(cfg):
    """Devolve a config com as opcoes mudadas (C) ou None (Esc)."""
    cfg = json.loads(json.dumps(cfg))
    pos = 0
    while True:
        itens = _linhas_opcoes(cfg)
        linhas = [cor(t("titulo"), "negrito"), "", cor(t("opcoes"), "negrito"), ""]
        largura = max(len(r) for _c, r, _v in itens) + 2
        for i, (_c, rotulo, valor) in enumerate(itens):
            texto = f"  {rotulo:<{largura}} {valor}"
            linhas.append(cor(texto, "inverso") if i == pos else texto)
        linhas += ["", cor(t("teclas_opcoes"), "fraco")]
        tela.Tela.desenhar(linhas)
        k = tela.tecla()
        if k in ("cima", "baixo"):
            pos = (pos + (1 if k == "baixo" else -1)) % len(itens)
        elif k in ("enter", "espaco"):
            mudar_opcao(cfg, itens[pos][0])
        elif k == "c":
            return cfg
        elif k in ("esc", "sair"):
            return None


def tela_excluidas(cfg):
    pos = 0
    while True:
        lista = cfg["pastas_excluidas"]
        linhas = [cor(t("titulo"), "negrito"), "", cor(t("excluidas_titulo"), "negrito"), ""]
        linhas += [cor(f"  {p}", "inverso") if i == pos else f"  {p}" for i, p in enumerate(lista)] or \
            [cor(f"  ({t('nenhuma')})", "fraco")]
        linhas += ["", cor(t("teclas_excluidas"), "fraco")]
        tela.Tela.desenhar(linhas)
        k = tela.tecla()
        if k in ("cima", "baixo") and lista:
            pos = (pos + (1 if k == "baixo" else -1)) % len(lista)
        elif k == "a":
            resp = tela.perguntar("\n" + t("pergunta_pasta").split("(")[0].strip() + " ")
            resp = (resp or "").strip().strip('"')
            if resp and os.path.isabs(resp) and resp not in lista and len(lista) < config.MAX_PASTAS_EXCLUIDAS:
                lista.append(resp)
        elif k in ("del", "x") and lista:
            lista.pop(pos)
            pos = max(0, pos - 1)
        elif k in ("enter", "esc", "sair"):
            return


# ---------------------------------------------------------------- instalacao com progresso
def rodar_instalador(extra=()):
    """Roda o instalar.py --progresso e desenha cada passo. Devolve True se terminou sem falha."""
    # nosemgrep: python36-compatibility-Popen1, python36-compatibility-Popen2 -- o TagTeam exige Python 3.11+
    proc = subprocess.Popen([sys.executable, "-I", os.path.join(AQUI, "instalar.py"), "--progresso", *extra],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                            text=True, encoding="utf-8", errors="replace")
    feitos, atual, total, inicio = [], "", 1, time.monotonic()
    for linha in proc.stdout:
        try:
            p = json.loads(linha)
        except ValueError:
            continue
        total = p.get("total") or total
        if p.get("estado") == "rodando":
            atual = titulo_do_passo(p)
        else:
            feitos.append(p)
        _desenhar_progresso(feitos, atual, total, time.monotonic() - inicio)
    erro = proc.stderr.read()
    proc.wait()
    falhou = proc.returncode != 0 or any(str(p.get("estado", "")).startswith("erro") for p in feitos)
    linhas = _linhas_progresso(feitos, "", total, time.monotonic() - inicio)
    linhas += ["", cor(t("com_falhas"), "amarelo") if falhou else cor(t("concluido"), "verde")]
    if falhou and erro.strip():
        linhas += [cor(x, "fraco") for x in erro.strip().splitlines()[-4:]]
    tela.Tela.desenhar(linhas)
    return not falhou, linhas


def _linhas_progresso(feitos, atual, total, decorrido):
    linhas = [cor(t("titulo"), "negrito"), "", cor(t("instalando"), "negrito"),
              f"  {tela.barra(len(feitos), total)} {len(feitos)}/{total} · {tempo(decorrido)}", ""]
    for p in feitos:
        estado = str(p.get("estado", ""))
        marca = cor("✓", "verde") if estado == "ok" else cor("✗", "vermelho")
        detalhe = f" ({len(p.get('mudou') or [])})" if p.get("mudou") else ""
        linhas.append(f"  {marca} {titulo_do_passo(p)}{cor(detalhe, 'fraco')}")
        if estado != "ok":
            linhas.append(cor(f"      {estado[:150]}", "vermelho"))
    if atual:
        linhas.append(f"  {cor('…', 'ciano')} {atual}")
    return linhas


def titulo_do_passo(p):
    return str(p.get("title") if IDIOMA == "en" and p.get("title") else p.get("titulo", ""))


def _desenhar_progresso(feitos, atual, total, decorrido):
    tela.Tela.desenhar(_linhas_progresso(feitos, atual, total, decorrido))


def abrir_login(ia):
    """Abre o CLI da IA numa janela nova para o login (comando fixo, sem shell)."""
    caminho = {"antigravity": saude.PARTES["antigravity"]["cli"][0], "grok": saude.PARTES["grok"]["cli"][0],
               "cursor": saude.PARTES["cursor"]["cli"][0]}.get(ia)
    import shutil
    exe = caminho if caminho and os.path.exists(caminho) else shutil.which(LOGIN[ia][0])
    if exe:
        subprocess.Popen([exe, *LOGIN[ia][1:]], creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
                         cwd=config.pasta_trabalho(config.carregar()))


def tela_fim_instalacao(linhas, cfg):
    ias = [ia for ia in config.IAS if config.ligada(cfg, ia)]
    while True:
        tela.Tela.desenhar(linhas + ["", t("login")] +
                           [f"  {i}. {config.NOMES[ia]}  ({' '.join(LOGIN[ia])})" for i, ia in enumerate(ias, 1)] +
                           ["", cor(t("enter_monitor"), "fraco")])
        k = tela.tecla()
        if k.isdigit() and 1 <= int(k) <= len(ias):
            abrir_login(ias[int(k) - 1])
        elif k == "enter":
            return True
        elif k in ("q", "esc", "sair"):
            return False


# ---------------------------------------------------------------- assistente
def itens_das_ias(det, cfg=None):
    itens = []
    for ia in config.IAS:
        marcado = config.ligada(cfg, ia) if cfg else det[ia]["instalada"]
        itens.append({"ia": ia, "rotulo": config.NOMES[ia], "desc": situacao_instalada(det, ia),
                      "marcado": marcado and pode_marcar(det, ia), "pode": pode_marcar(det, ia)})
    return itens


def config_das_ias(cfg, itens):
    """Aplica as marcas da lista: IA marcada sincroniza conversas (e ambiente, se a opcao geral estiver ligada)."""
    cfg = json.loads(json.dumps(cfg))
    for it in itens:
        ia = it["ia"]
        cfg["conversas"][ia] = it["marcado"]
        cfg["ambiente"][ia] = it["marcado"] and it.get("ambiente", True)
    return cfg


def assistente():
    det = saude.detectar()
    cfg = config.normalizar({}, config.PADRAO_NOVO)
    if not det["claude"]["instalada"] and not os.path.isdir(saude.CLAUDE):
        mensagem([t("bemvindo"), "", cor(t("sem_claude"), "vermelho")])
        return False
    etapa, itens = 0, itens_das_ias(det)
    while True:
        if etapa == 0:
            novo = lista_marcavel(t("escolha_ias"), itens, t("teclas_lista"))
            if novo is None:
                return False
            itens, etapa = novo, 1
        elif etapa == 1:
            cfg = config_das_ias(cfg, itens)
            novo = tela_opcoes(cfg)
            if novo is None:
                etapa = 0
                continue
            cfg, etapa = novo, 2
        else:
            linhas = [cor(t("titulo"), "negrito"), "", cor(t("resumo"), "negrito"), ""]
            linhas += [f"  {'✓' if config.ligada(cfg, ia) else '·'} {config.NOMES[ia]}" for ia in config.IAS]
            linhas += [f"  {r}: {v}" for _c, r, v in _linhas_opcoes(cfg)]
            tela.Tela.desenhar(linhas + ["", cor(t("enter_instalar"), "fraco")])
            k = tela.tecla()
            if k == "enter":
                config.salvar(cfg)
                _ok, linhas = rodar_instalador()
                return tela_fim_instalacao(linhas, cfg)
            if k in ("esc", "sair"):
                etapa = 1


# ---------------------------------------------------------------- monitor
def _linha_ia(d, agora):
    if not d["instalada"]:
        estado = cor("● " + t("nao_inst"), "fraco")
    elif not (d["conversas"] or d["ambiente"]):
        estado = cor("○ " + t("desmarcada"), "fraco")
    elif d["erros_24h"]:
        estado = cor("● " + t("erros_n", n=d["erros_24h"]), "vermelho")
    else:
        estado = cor("● " + ("ok" if d["conversas"] else t("so_ambiente")), "verde")
    hook = t("ha", x=tempo(agora - d["gancho_quando"])) if d["gancho_quando"] else t("nunca")
    conversas = f"→{d['para']:<5} ←{d['de']:<5}" if d["conversas"] else " " * 13
    return f"  {d['nome']:<22} {estado:<30} {t('conversas')} {conversas} {t('hook')} {hook}"


def _linha_evento(e, agora):
    ia = saude.ia_do_evento(e) if e.get("tipo") == "sincronizar" else e.get("tipo", "?")
    nome = config.NOMES.get(ia) or (t(ia) if ia in TEXTOS else ia)
    cont = e.get("cont") or {}
    mudou = sum(v for k, v in cont.items() if isinstance(v, int) and k.split()[-1] in
                ("novas", "atualizadas", "versao_nova", "exportadas", "importadas"))
    if e.get("saida") not in (0, None) or cont.get("erro"):
        resultado = cor(t("erro_ev") + ((e.get("erros") or ["?"])[-1])[:90], "vermelho")
    elif e.get("tipo") != "sincronizar":
        resultado = t("arquivos_n", n=len(e.get("mudou") or []))
    else:
        resultado = cor(t("mudancas_n", n=mudou), "verde") if mudou else cor(t("em_dia_ev"), "fraco")
    return f"  {time.strftime('%H:%M:%S', time.localtime(e['ts']))}  {nome:<24} {resultado}  " \
           f"{cor(str(e.get('duracao_s', '')) + 's', 'fraco')}"


def linhas_monitor(p):
    agora = p["agora"]
    geral = [c for c in p["checagens"] if c[1] in ("erro", "aviso")]
    topo = cor("● " + t("tudo_certo"), "verde") if not geral else cor("● " + t("atencao", n=len(geral)), "amarelo")
    linhas = [f"{cor(t('titulo'), 'negrito')}  v{VERSAO}", f"{cor(t('monitor'), 'negrito')} · {topo}", ""]
    linhas += [_linha_ia(p["ias"][ia], agora) for ia in config.IAS]
    linhas.append(cor("  " + t("legenda"), "fraco"))
    r = p["resumo"]
    linhas += ["", "  " + t("rodadas_24h", r=r["rodadas"], m=r["mudou"], e=r["com_erro"]), ""]
    for assunto, nivel, msg, kw in p["checagens"]:
        rotulo = NIVEL_ROTULO[nivel][0 if IDIOMA == "pt" else 1]
        linhas.append(f"  {cor(rotulo, NIVEL_COR[nivel])} {saude.assunto(assunto, IDIOMA)}: "
                      f"{saude.texto(msg, IDIOMA, **kw)}")
    linhas += ["", cor(t("ultimas"), "negrito")]
    eventos = [e for e in p["eventos"] if e.get("tipo") in ("sincronizar", "instalar", "desinstalar")][-8:]
    linhas += [_linha_evento(e, agora) for e in reversed(eventos)] or [cor("  " + t("sem_rodadas"), "fraco")]
    linhas += ["", cor(t("teclas_monitor"), "fraco")]
    return linhas


def tela_ias(cfg):
    det = saude.detectar()
    itens = itens_das_ias(det, cfg)
    for it in itens:
        it["conv"], it["amb"] = cfg["conversas"][it["ia"]], cfg["ambiente"][it["ia"]]

    def descrever(it):
        partes = [] if it["pode"] else [situacao_instalada(det, it["ia"])]
        if it["marcado"] and not (it["conv"] and it["amb"]):
            partes.append(t("so_conversas") if it["conv"] else t("so_ambiente"))
        it["desc"] = " · ".join(partes) or situacao_instalada(det, it["ia"])

    def alternar(chave):
        def f(it):
            if it["marcado"]:
                it[chave] = not it[chave]
                if not (it["conv"] or it["amb"]):
                    it["marcado"] = False
                descrever(it)
        return f
    for it in itens:
        descrever(it)
    novo = lista_marcavel(t("ias_titulo"), itens, t("teclas_lista"), {"c": alternar("conv"), "a": alternar("amb")})
    if novo is None:
        return None
    cfg = json.loads(json.dumps(cfg))
    for it in novo:
        ligado = it["marcado"]
        conv, amb = (it["conv"], it["amb"]) if (it["conv"] or it["amb"]) else (True, True)
        cfg["conversas"][it["ia"]], cfg["ambiente"][it["ia"]] = ligado and conv, ligado and amb
    return cfg


def aplicar_e_instalar(cfg):
    config.salvar(cfg)
    _ok, linhas = rodar_instalador()
    tela.Tela.desenhar(linhas + ["", cor(t("tecla_voltar"), "fraco")])
    tela.tecla()


def simular():
    tela.Tela.desenhar([cor(t("titulo"), "negrito"), "", t("simulando")])
    try:
        r = subprocess.run([sys.executable, "-I", os.path.join(AQUI, "sincronizar.py"), "--seco"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=PRAZO_SIMULAR_S, check=False, stdin=subprocess.DEVNULL)
        saida = (r.stdout + r.stderr).strip().splitlines()
    except subprocess.TimeoutExpired:
        saida = [f"> {PRAZO_SIMULAR_S}s"]
    linhas = []
    for linha in saida[-40:]:
        try:
            cont = json.loads(linha)
            linhas += [f"  {k}: {v}" for k, v in cont.items()]
        except ValueError:
            linhas.append("  " + linha)
    mensagem(linhas)


def abrir_log():
    import gancho
    log = os.path.join(gancho.PASTA, "sincronizar.log")
    if os.path.isfile(log):
        os.startfile(log)              # o editor padrao do Windows
        return t("abrir_log", x=log)
    return t("sem_log")


def tela_atualizar():
    import atualizar
    tela.Tela.desenhar([cor(t("titulo"), "negrito"), "", t("procurando")])
    try:
        rel = atualizar.release_mais_nova()
        if not atualizar.ha_versao_nova(VERSAO, rel):
            mensagem([t("em_dia", x=VERSAO)])
            return
        resp = tela.perguntar("\n" + t("nova_versao", x=rel["versao"]))
        if not resp or resp.strip().lower()[:1] not in ("s", "y"):
            return
        n = atualizar.aplicar(atualizar.baixar(rel), SKILL)
        tela.Tela.desenhar([cor(t("titulo"), "negrito"), "", t("atualizado", x=rel["versao"]), f"  {n} {t('arquivos')}"])
        rodar_instalador()
        mensagem([t("atualizado", x=rel["versao"])])
    except atualizar.ErroAtualizacao as e:
        mensagem([cor(str(e), "vermelho")])


def desinstalar():
    resp = tela.perguntar("\n" + t("confirmar_des"))
    if (resp or "").strip().upper() != t("palavra_sim"):
        return False
    ok, linhas = rodar_instalador(("--desinstalar",))
    tela.Tela.desenhar(linhas + ["", t("desinstalado") if ok else "", cor(t("tecla_voltar"), "fraco")])
    tela.tecla()
    return ok


def monitor():
    import gancho
    aviso = ""
    while True:
        cfg = config.carregar()
        linhas = linhas_monitor(saude.painel(cfg))
        tela.Tela.desenhar(linhas + ([cor(aviso, "ciano")] if aviso else []))
        k = tela.tecla(ATUALIZA_S)
        aviso = "" if k else aviso
        if k in ("q", "esc", "sair"):
            return
        if k == "s":
            gancho.lancar([])
            aviso = t("sincronizando")
        elif k == "t":
            simular()
        elif k in ("i", "c"):
            novo = tela_ias(cfg) if k == "i" else tela_opcoes(cfg)
            if novo is not None:
                aplicar_e_instalar(novo)
        elif k == "r":
            aplicar_e_instalar(cfg)
        elif k == "l":
            aviso = abrir_log()
        elif k == "u":
            tela_atualizar()
        elif k == "d" and desinstalar():
            return


# ---------------------------------------------------------------- modo simples e status
def imprimir_status():
    p = saude.painel()
    for linha in linhas_monitor(p)[:-2]:
        print(tela.ANSI_RE.sub("", linha))
    return 1 if any(n == "erro" for _a, n, _m, _k in p["checagens"]) else 0


def _sim(pergunta, padrao):
    resp = tela.perguntar(f"{pergunta} [{'S/n' if padrao else 's/N'}] ")
    if not resp or not resp.strip():
        return padrao
    return resp.strip().lower()[:1] in ("s", "y")


def modo_simples():
    """Sem tela cheia: perguntas numeradas. Sem config: instala; com config: mostra o status."""
    if config.existe():
        return imprimir_status()
    det = saude.detectar()
    print(t("titulo"), "\n", t("bemvindo"), sep="")
    if not det["claude"]["instalada"] and not os.path.isdir(saude.CLAUDE):
        print(t("sem_claude"))
        return 1
    cfg = config.normalizar({}, config.PADRAO_NOVO)
    for ia in config.IAS:
        marcar = pode_marcar(det, ia) and _sim(f"{config.NOMES[ia]} ({situacao_instalada(det, ia)})",
                                               det[ia]["instalada"])
        cfg["conversas"][ia] = cfg["ambiente"][ia] = marcar
    config.salvar(cfg)
    r = subprocess.run([sys.executable, "-I", os.path.join(AQUI, "instalar.py"), "--progresso"],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       check=False)
    for linha in r.stdout.splitlines():
        try:
            p = json.loads(linha)
        except ValueError:
            continue
        if p.get("estado") != "rodando":
            print(f"[{p['passo']}/{p['total']}] {'ok' if p['estado'] == 'ok' else p['estado']}  {p['titulo']}")
    print(t("concluido") if r.returncode == 0 else t("com_falhas"))
    return r.returncode


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--simples", action="store_true", help="perguntas numeradas, sem tela cheia")
    ap.add_argument("--status", action="store_true", help="imprime a saude e sai")
    a = ap.parse_args()
    if not sys.stdout.isatty():                    # pipe ou arquivo: o Windows usaria o cp1252 e quebraria no ●
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    definir_idioma(config.carregar())
    if a.status:
        return imprimir_status()
    if a.simples or not tela.interativo():
        return modo_simples()
    try:
        with tela.Tela():
            if not config.existe() and not assistente():
                return 0
            if config.existe():
                monitor()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
