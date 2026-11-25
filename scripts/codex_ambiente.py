"""Ambiente do Codex igual ao do Claude Code: skills, plugins e servidores MCP (as regras vem pelo AGENTS.md).

- **Skills** (as da sua pasta de skills, as de terceiros, as proprias do ~/.claude/skills e as da conta do claude.ai): uma juncao por skill em
  ~/.codex/skills/<nome>, apontando para a pasta original (o Codex le a juncao; nada e copiado). As de projeto
  (escopo "projeto:<pasta>") vao em <projeto>/.codex/skills. A skill nova, editada ou apagada aparece sozinha.
- **Plugins** ligados no Claude: instalados nativamente no Codex a partir dos marketplaces que o Claude ja mantem em
  ~/.claude/plugins/marketplaces (marketplace "local" no Codex, por apontamento). Versao nova no Claude = reinstala.
- **MCP** globais do ~/.claude.json: gravados no config.toml do Codex pela API (config/value/write), no formato que a
  importacao oficial do Codex usa (stdio: command/args/env/cwd; http: url/http_headers). Mudou no Claude, muda aqui.
- **Copias antigas** (skills que a importacao de junho/2026 copiou para ~/.codex/skills): movidas para
  ~/.codex/skills-desativadas, fora da leitura do Codex. Nada e apagado; a pasta .system (do Codex) fica.
Tudo pela API oficial do Codex (codex app-server), exceto as juncoes, que sao arquivos do proprio usuario.
"""
import json
import os
import re
import shutil
import tomllib

HOME = os.path.expanduser("~")
CLAUDE = os.path.join(HOME, ".claude")
NOME_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
PASTA_SISTEMA = ".system"
DESATIVADAS = "skills-desativadas"


def _ler_json(caminho, padrao):
    try:
        with open(caminho, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return padrao


def _eh_juncao(caminho):
    return os.path.isjunction(caminho) or os.path.islink(caminho)


def _alvo(caminho):
    try:
        return os.path.normcase(os.path.realpath(caminho))
    except OSError:
        return ""


def _skills_em(pasta, incluir=None, excluir=()):
    """{nome: pasta} das skills (subpastas com SKILL.md) de uma pasta; nome validado (vira nome de juncao)."""
    saida = {}
    if not os.path.isdir(pasta):
        return saida
    for nome in sorted(os.listdir(pasta)):
        caminho = os.path.join(pasta, nome)
        if not NOME_OK.match(nome) or nome in excluir or (incluir is not None and nome not in incluir):
            continue
        if os.path.isfile(os.path.join(caminho, "SKILL.md")):
            saida[nome] = os.path.realpath(caminho)
    return saida


# ---------------------------------------------------------------- skills
def skills_das_entradas(entradas):
    """{nome: pasta} a partir das mesmas entradas do skills.json do Antigravity ({path, include_only|exclude}), menos as
    de plugin (os plugins entram inteiros, nativos, no Codex). A primeira fonte que traz um nome fica com ele."""
    plugins = os.path.normcase(os.path.join(CLAUDE, "plugins")) + os.sep
    desejadas = {}
    for e in entradas:
        caminho = str(e.get("path", "")).replace("/", os.sep)
        if not caminho or os.path.normcase(os.path.abspath(caminho)).startswith(plugins):
            continue
        incluir = set(e["include_only"]) if isinstance(e.get("include_only"), list) else None
        for nome, pasta in _skills_em(caminho, incluir=incluir, excluir=set(e.get("exclude") or [])).items():
            desejadas.setdefault(nome, pasta)
    return desejadas


def skills_de_projeto(ws, projetos, pasta_skills):
    """{pasta do projeto: {nome: pasta da skill}} para as skills de escopo "projeto:<pasta>"."""
    saida = {}
    for pasta, nomes in sorted((projetos or {}).items()):
        raiz = os.path.join(ws, pasta)
        achadas = _skills_em(pasta_skills, incluir=set(nomes))
        if os.path.isdir(raiz) and achadas:
            saida[raiz] = achadas
    return saida


def _acao_existente(pasta, nome, desejadas, raizes):
    """O que fazer com o que ja esta na pasta de skills (None: deixa)."""
    caminho = os.path.join(pasta, nome)
    if nome == PASTA_SISTEMA or nome.startswith("."):
        return None
    if not _eh_juncao(caminho):
        return ("desativar", nome) if os.path.isdir(caminho) else None
    if not any(_alvo(caminho).startswith(r) for r in raizes):
        return None                                # juncao de outra pessoa
    if nome not in desejadas:
        return ("tirar", nome)
    if not os.path.isdir(caminho) or _alvo(caminho) != os.path.normcase(desejadas[nome]):
        return ("trocar", nome, desejadas[nome])   # quebrada ou apontando para outra pasta
    return None


def plano_skills(pasta, desejadas, raizes_nossas):
    """Acoes para a pasta de skills ficar com uma juncao por skill desejada:
    ("desativar", nome) pasta de verdade (copia antiga) sai da leitura; ("criar", nome, alvo); ("trocar", nome, alvo)
    juncao nossa apontando para outro lugar; ("tirar", nome) juncao nossa que nao e mais desejada ou sem alvo.
    Juncao que aponta para fora das raizes_nossas e de outra pessoa: nao mexe."""
    raizes = [os.path.normcase(os.path.realpath(r)) + os.sep for r in raizes_nossas]
    existentes = sorted(os.listdir(pasta)) if os.path.isdir(pasta) else []
    acoes = [a for a in (_acao_existente(pasta, n, desejadas, raizes) for n in existentes) if a]
    for nome, alvo in sorted(desejadas.items()):
        if not os.path.lexists(os.path.join(pasta, nome)) or ("desativar", nome) in acoes:
            acoes.append(("criar", nome, alvo))
    return acoes


def _criar_juncao(alvo, link):
    import _winapi
    _winapi.CreateJunction(alvo, link)


def aplicar_skills(pasta, acoes, desativadas, mudou, seco, rotulo="codex"):
    for acao in acoes:
        nome = acao[1]
        caminho = os.path.join(pasta, nome)
        mudou.append(f"{rotulo} skill {acao[0]}: {nome}")
        if seco:
            continue
        if acao[0] == "desativar":
            os.makedirs(desativadas, exist_ok=True)
            destino = os.path.join(desativadas, nome)
            n = 1
            while os.path.lexists(destino):
                n += 1
                destino = os.path.join(desativadas, f"{nome}-{n}")
            shutil.move(caminho, destino)
        elif acao[0] in ("tirar", "trocar"):
            os.rmdir(caminho)                      # remove so a juncao; a pasta de verdade nao e tocada
        if acao[0] in ("criar", "trocar"):
            os.makedirs(pasta, exist_ok=True)
            _criar_juncao(acao[2], caminho)


# ---------------------------------------------------------------- MCP
def _textos(d):
    return {str(k): str(v) for k, v in d.items()} if isinstance(d, dict) and d else None


def _mcp_um(cfg):
    """Um servidor do ~/.claude.json no formato do Codex, ou None (desligado, sse ou sem url/comando)."""
    if isinstance(cfg.get("url"), str):
        novo = {"url": cfg["url"], "http_headers": _textos(cfg.get("headers"))}
    elif isinstance(cfg.get("command"), str):
        novo = {"command": cfg["command"], "env": _textos(cfg.get("env")),
                "args": [str(a) for a in cfg["args"]] if isinstance(cfg.get("args"), list) else None,
                "cwd": cfg.get("cwd") if isinstance(cfg.get("cwd"), str) else None}
    else:
        return None
    return {k: v for k, v in novo.items() if v is not None}


def mcp_para_codex(claude_mcp):
    """Servidores MCP do ~/.claude.json no formato do config.toml do Codex (o mesmo da importacao oficial)."""
    saida = {}
    for nome, cfg in sorted((claude_mcp or {}).items()):
        if isinstance(cfg, dict) and NOME_OK.match(nome) and not cfg.get("disabled"):
            novo = _mcp_um(cfg)
            if novo:
                saida[nome] = novo
    return saida


def plano_mcp(desejados, atuais):
    """Servidores a gravar: os que faltam ou estao diferentes no Codex. Os do Codex que nao vem do Claude ficam."""
    return {n: v for n, v in desejados.items() if (atuais or {}).get(n) != v}


# ---------------------------------------------------------------- plugins
def plugins_do_claude():
    """[(plugin, marketplace, versao, pasta do marketplace)] dos plugins ligados no Claude, com a versao instalada."""
    ligados = (_ler_json(os.path.join(CLAUDE, "settings.json"), {}) or {}).get("enabledPlugins") or {}
    instalados = (_ler_json(os.path.join(CLAUDE, "plugins", "installed_plugins.json"), {}) or {}).get("plugins") or {}
    saida = []
    for pid, ligado in sorted(ligados.items()):
        if not ligado or "@" not in pid:
            continue
        plugin, marketplace = pid.split("@", 1)
        if not (NOME_OK.match(plugin) and NOME_OK.match(marketplace)):
            continue
        versoes = [x.get("version") for x in instalados.get(pid) or [] if isinstance(x, dict)]
        pasta = os.path.join(CLAUDE, "plugins", "marketplaces", marketplace)
        if os.path.isfile(os.path.join(pasta, ".claude-plugin", "marketplace.json")):
            saida.append((plugin, marketplace, versoes[0] if versoes else None, pasta))
    return saida


def plano_plugins(claude, marketplaces_codex, instalados_codex, cache_codex, caminhos_codex=None):
    """Acoes no Codex: ("marketplace", nome, pasta) para o que falta (local, apontando para a pasta do Claude);
    ("instalar", plugin, marketplace, pasta) para o que nao esta instalado, ou cuja versao do Claude ainda nao esta
    no cache do Codex quando o marketplace dele e o local do Claude; ("ligar", id) para o instalado e desligado.
    caminhos_codex: {marketplace: marketplace.json que o Codex usa} (os que ele ja tinha, por git)."""
    acoes, vistos = [], set()
    for plugin, marketplace, versao, pasta in claude:
        mk = marketplaces_codex.get(marketplace)
        local = bool(mk) and mk.get("source_type") == "local"
        if not mk and marketplace not in vistos:
            acoes.append(("marketplace", marketplace, pasta))
            local = True
        vistos.add(marketplace)
        pid = f"{plugin}@{marketplace}"
        estado = instalados_codex.get(pid)
        sem_versao = local and versao and not os.path.isdir(os.path.join(cache_codex, marketplace, plugin, versao))
        if not estado or not estado.get("installed") or sem_versao:
            manifesto = (caminhos_codex or {}).get(marketplace) or os.path.join(pasta, ".claude-plugin", "marketplace.json")
            acoes.append(("instalar", plugin, marketplace, manifesto))
        elif estado.get("enabled") is False:
            acoes.append(("ligar", pid))
    return acoes


# ---------------------------------------------------------------- tudo junto
def ler_toml(caminho):
    try:
        with open(caminho, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def instalados(srv, ws):
    """({id do plugin: estado no Codex}, {marketplace: caminho do marketplace.json que o Codex usa})."""
    saida, caminhos = {}, {}
    r = srv.chamar("plugin/list", {"cwds": [ws]}, prazo=300) or {}
    for mk in r.get("marketplaces") or []:
        if isinstance(mk.get("name"), str) and isinstance(mk.get("path"), str):
            caminhos[mk["name"]] = mk["path"]
        for p in mk.get("plugins") or []:
            if isinstance(p, dict) and isinstance(p.get("id"), str):
                saida[p["id"]] = p
    return saida, caminhos


def aplicar(srv, codex_dir, ws, fontes, seco, mudou):
    """Deixa skills, plugins e MCP do Codex iguais aos do Claude. srv: codex.Servidor aberto. fontes: {"entradas": as do
    skills.json do Antigravity, "projetos": {pasta do projeto: [skills]} (escopo "projeto:"), "pasta_projeto": onde
    estao as skills de projeto}."""
    entradas, pasta_skills_projeto = fontes.get("entradas") or [], fontes.get("pasta_projeto") or ""
    raizes = [str(e.get("path", "")).replace("/", os.sep) for e in entradas if e.get("path")]
    pasta = os.path.join(codex_dir, "skills")
    aplicar_skills(pasta, plano_skills(pasta, skills_das_entradas(entradas), raizes),
                   os.path.join(codex_dir, DESATIVADAS), mudou, seco)
    for raiz, desejadas in skills_de_projeto(ws, fontes.get("projetos"), pasta_skills_projeto).items():
        pasta_projeto = os.path.join(raiz, ".codex", "skills")
        aplicar_skills(pasta_projeto, plano_skills(pasta_projeto, desejadas, [pasta_skills_projeto]),
                       os.path.join(raiz, ".codex", DESATIVADAS), mudou, seco)
    cfg = ler_toml(os.path.join(codex_dir, "config.toml"))
    claude_mcp = (_ler_json(os.path.join(HOME, ".claude.json"), {}) or {}).get("mcpServers") or {}
    for nome, valor in sorted(plano_mcp(mcp_para_codex(claude_mcp), cfg.get("mcp_servers")).items()):
        mudou.append(f"codex mcp: {nome}")
        if not seco:
            srv.chamar("config/value/write", {"keyPath": f"mcp_servers.{nome}", "mergeStrategy": "replace", "value": valor})
    estados, caminhos = instalados(srv, ws)
    acoes = plano_plugins(plugins_do_claude(), cfg.get("marketplaces") or {}, estados,
                          os.path.join(codex_dir, "plugins", "cache"), caminhos)
    for acao in acoes:
        mudou.append(f"codex plugin {acao[0]}: {acao[1]}")
        if seco:
            continue
        if acao[0] == "marketplace":
            srv.chamar("marketplace/add", {"source": acao[2]}, prazo=300)
        elif acao[0] == "instalar":
            srv.chamar("plugin/install", {"pluginName": acao[1], "marketplacePath": acao[3]}, prazo=600)
        elif acao[0] == "ligar":
            srv.chamar("config/value/write", {"keyPath": "plugins", "mergeStrategy": "upsert",
                                              "value": {acao[1]: {"enabled": True}}})
