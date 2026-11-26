"""Configuracao do TagTeam neste PC: ~/.tagteam/config.json (TAGTEAM_STATE_DIR troca a pasta).

Quem le: instalar.py (o que instalar e onde), sincronizar.py (quais IAs e pastas), os hooks (IA desmarcada nao
dispara nada) e a passagem (limite). Quem grava: tagteam.py (assistente e monitor).

Sem o arquivo vale o comportamento de antes dele existir (todas as IAs, atalhos como administrador e sem
aprovacao): quem ja usava nao muda nada ao atualizar. O assistente grava os padroes de instalacao nova (PADRAO_NOVO),
mais conservadores. As variaveis de ambiente antigas continuam valendo por cima do arquivo.
"""
import json
import os
import tempfile

IAS = ("antigravity", "grok", "codex", "cursor")
NOMES = {"claude": "Claude Code", "antigravity": "Gemini (Antigravity)", "grok": "Grok CLI", "codex": "OpenAI Codex",
         "cursor": "Cursor"}
VERSAO = 1
MAX_CONFIG = 64 * 1024
MAX_PASTAS_EXCLUIDAS = 200
MAX_CAMINHO = 1024
IDIOMAS = ("auto", "pt", "en")


def pasta_estado():
    return os.environ.get("TAGTEAM_STATE_DIR") or os.path.join(os.path.expanduser("~"), ".tagteam")


def caminho():
    return os.path.join(pasta_estado(), "config.json")


def _padrao(admin_e_livre):
    return {
        "versao": VERSAO,
        "conversas": {ia: True for ia in IAS},     # sincronizar as conversas com esta IA
        "ambiente": {ia: True for ia in IAS},      # CLAUDE.md, skills, plugins e MCP do Claude nesta IA
        "instalar_clis": True,                     # instalar o CLI que falta de uma IA marcada (instalador oficial)
        "pasta": "",                               # pasta de trabalho padrao ("" = a home)
        "atalhos": True,                           # atalhos no Menu Iniciar
        "atalhos_admin": admin_e_livre,            # atalhos abrem como administrador
        "atalhos_sem_aprovacao": admin_e_livre,    # atalhos abrem a IA sem pedir aprovacao a cada comando
        "statusline": True,                        # status line do uso do limite no Claude
        "passagem": True,                          # passagem automatica perto do limite
        "passagem_pct": 95,
        "pastas_excluidas": [],                    # conversas destas pastas (e subpastas) nao saem do Claude
        "idioma": "auto",
    }


PADRAO_LEGADO = _padrao(True)     # sem config.json: como antes
PADRAO_NOVO = _padrao(False)      # o que o assistente propoe numa instalacao nova


def _bool(v, padrao):
    return v if isinstance(v, bool) else padrao


def _pasta_valida(v):
    return isinstance(v, str) and 0 < len(v) <= MAX_CAMINHO and os.path.isabs(v) and "\0" not in v


def normalizar(bruto, base=None):
    """Config valida a partir do que veio do arquivo: o que falta ou esta errado volta ao padrao (base)."""
    base = PADRAO_LEGADO if base is None else base
    d = bruto if isinstance(bruto, dict) else {}
    cfg = json.loads(json.dumps(base))
    for grupo in ("conversas", "ambiente"):
        dado = d.get(grupo) if isinstance(d.get(grupo), dict) else {}
        for ia in IAS:
            cfg[grupo][ia] = _bool(dado.get(ia), base[grupo][ia])
    for chave in ("instalar_clis", "atalhos", "atalhos_admin", "atalhos_sem_aprovacao", "statusline", "passagem"):
        cfg[chave] = _bool(d.get(chave), base[chave])
    pasta = d.get("pasta", "")
    cfg["pasta"] = pasta if _pasta_valida(pasta) else ""
    pct = d.get("passagem_pct")
    if isinstance(pct, (int, float)) and not isinstance(pct, bool) and 1 <= pct <= 100:
        cfg["passagem_pct"] = int(pct)
    excluidas = d.get("pastas_excluidas")
    if isinstance(excluidas, list):
        cfg["pastas_excluidas"] = [x for x in excluidas if _pasta_valida(x)][:MAX_PASTAS_EXCLUIDAS]
    cfg["idioma"] = d.get("idioma") if d.get("idioma") in IDIOMAS else base["idioma"]
    return cfg


def existe():
    return os.path.isfile(caminho())


def ler_bruto():
    """(dict do arquivo, problema). problema: None, "ausente", "grande" ou "invalida"."""
    arq = caminho()
    try:
        if os.path.getsize(arq) > MAX_CONFIG:
            return {}, "grande"
        with open(arq, encoding="utf-8-sig") as f:
            dado = json.load(f)
    except FileNotFoundError:
        return {}, "ausente"
    except (OSError, ValueError):
        return {}, "invalida"
    return (dado, None) if isinstance(dado, dict) else ({}, "invalida")


def carregar():
    """Config em vigor: o arquivo (normalizado) com as variaveis de ambiente antigas por cima."""
    bruto, _problema = ler_bruto()
    cfg = normalizar(bruto)
    pasta_env = os.environ.get("TAGTEAM_WORKSPACE", "")
    if _pasta_valida(pasta_env):
        cfg["pasta"] = pasta_env
    pct_env = os.environ.get("TAGTEAM_HANDOFF_THRESHOLD", "")
    try:
        if pct_env and 1 <= float(pct_env) <= 100:
            cfg["passagem_pct"] = float(pct_env)
    except ValueError:
        pass                    # valor invalido na variavel: fica o do arquivo
    return cfg


def salvar(cfg):
    """Grava de forma atomica (arquivo novo + troca): nunca deixa um config.json pela metade."""
    pasta = pasta_estado()
    os.makedirs(pasta, exist_ok=True)
    texto = json.dumps(normalizar(cfg), indent=2, ensure_ascii=False) + "\n"
    fd, tmp = tempfile.mkstemp(dir=pasta, prefix=".config.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(texto)
        os.replace(tmp, caminho())
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def pasta_trabalho(cfg):
    pasta = cfg.get("pasta") or ""
    return pasta if pasta and os.path.isdir(pasta) else os.path.expanduser("~")


def conversas(cfg, ia):
    return bool(cfg["conversas"].get(ia))


def ambiente(cfg, ia):
    return bool(cfg["ambiente"].get(ia))


def ligada(cfg, ia):
    """A IA participa de alguma forma (conversas ou ambiente)."""
    return conversas(cfg, ia) or ambiente(cfg, ia)


def chave_do_projeto(pasta):
    """Nome da pasta de projeto do Claude para uma pasta de trabalho (cada caractere fora de [A-Za-z0-9] vira '-')."""
    return "".join(c if c.isascii() and c.isalnum() else "-" for c in pasta)


def projeto_excluido(cfg, nome_projeto):
    """A pasta de projeto do Claude (~/.claude/projects/<nome>) e de uma pasta excluida ou de uma subpasta dela.
    O nome do projeto perde a diferenca entre separador e '-': na duvida exclui (o lado seguro, nada sai)."""
    nome = nome_projeto.lower()
    for pasta in cfg.get("pastas_excluidas") or []:
        chave = chave_do_projeto(pasta.rstrip("\\/")).lower()
        if chave and (nome == chave or nome.startswith(chave + "-")):
            return True
    return False
