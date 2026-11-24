"""TagTeam setup: gives Gemini (Antigravity app + agy CLI) and the Grok CLI the same environment as Claude Code
and wires the hooks that carry conversations between the three (idempotent; only writes what changed).

Installs the agy CLI and the Grok CLI if missing (official installers, which verify checksums), compiles the
Grok session picker, creates Start Menu shortcuts and generates, from what exists on this PC:
  ~/.claude/settings.json           TagTeam hooks (SessionStart, Stop, SessionEnd) and the usage status line
  ~/.gemini/GEMINI.md               global rule: points Gemini at ~/.claude/CLAUDE.md
  ~/.gemini/config/skills.json      Claude skills and the skills of Claude plugins
  ~/.gemini/config/plugins.json     Claude plugins that already ship an Antigravity manifest
  ~/.gemini/config/mcp_config.json  global MCP servers from ~/.claude.json (adds, never removes)
  ~/.gemini/config/hooks.json       "claude-sync" Stop hook that carries the conversation to Claude
  ~/.grok/AGENTS.md, config.toml    rules, Claude plugins, remote MCP servers and hooks for Grok (marked block)
Usage: python -I instalar.py [--seco]   (--seco = dry run)
"""
import argparse
import glob
import json
import os
import re
import sys
import tempfile

HOME = os.path.expanduser("~")
# pasta de trabalho padrao (atalhos abrem nela; a memoria do Claude dessa pasta entra nas regras)
WS = os.environ.get("TAGTEAM_WORKSPACE") or HOME
GEMINI = os.path.join(HOME, ".gemini")
CONFIG = os.path.join(GEMINI, "config")
CLAUDE = os.path.join(HOME, ".claude")
AQUI = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(AQUI)
MODELO_GEMINI = os.path.join(SKILL, "references", "GEMINI.modelo.md")
MCP_CHAVES = ("command", "args", "env", "cwd", "url", "headers", "disabled")
MAX_JSON = 20 * 1024 * 1024
NOME_HOOK = "claude-sync"


def p(caminho):
    """Caminho absoluto com barras normais (os manifestos do Antigravity nao expandem ~)."""
    return os.path.abspath(caminho).replace("\\", "/")


def ler_json(caminho, padrao):
    try:
        if os.path.getsize(caminho) > MAX_JSON:
            return padrao
        with open(caminho, encoding="utf-8") as f:
            texto = f.read()
        return json.loads(texto) if texto.strip() else padrao
    except (OSError, json.JSONDecodeError):
        return padrao


def _versao(nome):
    return tuple(int(x) for x in re.findall(r"\d+", nome)) or (0,)


def plugins_do_claude():
    """[(nome, pasta da versao mais nova no cache)] dos plugins ligados no Claude."""
    ligados = ler_json(os.path.join(CLAUDE, "settings.json"), {}).get("enabledPlugins") or {}
    saida = []
    for chave, ligado in ligados.items():
        if not ligado or "@" not in chave:
            continue
        nome, mercado = chave.split("@", 1)
        versoes = [d for d in glob.glob(os.path.join(CLAUDE, "plugins", "cache", mercado, nome, "*")) if os.path.isdir(d)]
        if versoes:
            saida.append((nome, max(versoes, key=lambda d: _versao(os.path.basename(d)))))
    return sorted(saida)


def montar_skills_e_plugins():
    """skills.json e plugins.json do Antigravity: as skills do Claude e as dos plugins ligados nele."""
    entradas = []
    if os.path.isdir(os.path.join(CLAUDE, "skills")):
        entradas.append({"path": p(os.path.join(CLAUDE, "skills"))})
    plugins = []
    for nome, pasta in plugins_do_claude():
        if any(os.path.isfile(os.path.join(pasta, m)) for m in (".antigravity-plugin/plugin.json", ".gemini-plugin/plugin.json")):
            plugins.append({"path": p(pasta)})
        elif os.path.isdir(os.path.join(pasta, "skills")):
            entradas.append({"path": p(os.path.join(pasta, "skills"))})
    return {"entries": entradas}, {"entries": plugins}


def _eh_juncao(caminho):
    try:
        return os.path.isjunction(caminho)
    except AttributeError:          # Python sem isjunction
        return os.path.realpath(caminho) != os.path.abspath(caminho)


def montar_mcp():
    atual = ler_json(os.path.join(CONFIG, "mcp_config.json"), {})
    atual = atual if isinstance(atual, dict) else {}
    servidores = atual.setdefault("mcpServers", {})
    for nome, cfg in (ler_json(os.path.join(HOME, ".claude.json"), {}).get("mcpServers") or {}).items():
        if nome in servidores or not isinstance(cfg, dict) or cfg.get("type") == "sse":
            continue
        novo = {k: cfg[k] for k in MCP_CHAVES if k in cfg}
        if "command" in novo or "url" in novo:
            servidores[nome] = novo
    return atual


def montar_hooks():
    atual = ler_json(os.path.join(CONFIG, "hooks.json"), {})
    atual = atual if isinstance(atual, dict) else {}
    # o comando roda por "cmd /c" na pasta do hooks.json. Caminho relativo sem espaco evita o problema de
    # aspas do cmd /c; o ".\" e obrigatorio: com NoDefaultCurrentDirectoryInExePath=1 (o Claude Code liga)
    # o cmd nao procura programa na pasta atual
    atual[NOME_HOOK] = {"Stop": [{"type": "command", "command": ".\\gancho-antigravity.cmd", "timeout": 15}]}
    return atual


EVENTOS_CLAUDE = ("SessionStart", "Stop", "SessionEnd")


def _python():
    python = os.path.join(os.path.dirname(sys.executable), "python.exe")
    return python if os.path.exists(python) else sys.executable


def montar_settings_claude():
    """settings.json do Claude com os hooks do TagTeam e o status line do uso (so se ainda nao houver um status line:
    nunca troca o de quem ja tem). None se nada muda ou se o arquivo nao e um objeto JSON."""
    caminho = os.path.join(CLAUDE, "settings.json")
    s = ler_json(caminho, {})
    if not isinstance(s, dict):
        return None
    antes = json.dumps(s, sort_keys=True)
    gancho = p(os.path.join(AQUI, "gancho.py"))
    hooks = s.setdefault("hooks", {})
    for evento in EVENTOS_CLAUDE:
        lista = hooks.setdefault(evento, [])
        if not any(gancho in str(h.get("command", "")) for grupo in lista if isinstance(grupo, dict)
                   for h in grupo.get("hooks", []) if isinstance(h, dict)):
            lista.append({"hooks": [{"type": "command", "command": f'"{_python()}" -I "{gancho}"', "timeout": 15}]})
    if "statusLine" not in s:
        s["statusLine"] = {"type": "command", "command": f'"{_python()}" -I "{p(os.path.join(AQUI, "statusline.py"))}"'}
    return None if json.dumps(s, sort_keys=True) == antes else s


def montar_cmd():
    python = os.path.join(os.path.dirname(sys.executable), "python.exe")
    python = python if os.path.exists(python) else sys.executable
    return f'@"{python}" -I "{os.path.join(AQUI, "gancho_antigravity.py")}"\r\n'


def montar_gemini_md():
    memoria = os.path.join(CLAUDE, "projects", re.sub(r"[^A-Za-z0-9]", "-", WS), "memory", "MEMORY.md")
    with open(MODELO_GEMINI, encoding="utf-8") as f:
        return f.read().replace("{HOME}", HOME).replace("{MEMORIA}", memoria)


def gravar(caminho, conteudo, seco, mudou):
    texto = conteudo if isinstance(conteudo, str) else json.dumps(conteudo, indent=2, ensure_ascii=False) + "\n"
    if os.path.exists(caminho):
        with open(caminho, encoding="utf-8", newline="") as f:
            if f.read() == texto:
                return
    mudou.append(caminho)
    if seco:
        return
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(caminho), prefix=".instalar.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(texto)
        os.replace(tmp, caminho)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


AGY = os.path.join(os.environ.get("LOCALAPPDATA", ""), "agy", "bin", "agy.exe")
APP = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Antigravity", "Antigravity.exe")
URL_INSTALADOR_CLI = "https://antigravity.google/cli/install.ps1"
MAX_INSTALADOR = 256 * 1024
TIMEOUT_DOWNLOAD_S = 60
TIMEOUT_INSTALACAO_S = 900
MENU = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs")


def instalar_cli(seco, mudou):
    """CLI agy pelo instalador oficial (baixa do servico de atualizacao do Google e confere SHA-512)."""
    if os.path.isfile(AGY):
        return
    mudou.append(AGY)
    if seco:
        return
    import subprocess
    import urllib.request
    # nosemgrep: dynamic-urllib-use-detected -- URL constante https do instalador oficial, TLS verificado
    with urllib.request.urlopen(URL_INSTALADOR_CLI, timeout=TIMEOUT_DOWNLOAD_S) as r:
        script = r.read(MAX_INSTALADOR + 1)
    if len(script) > MAX_INSTALADOR or b"agy.exe" not in script or b"sha512" not in script:
        raise RuntimeError("instalador do agy veio diferente do esperado; nada foi executado")
    fd, tmp = tempfile.mkstemp(prefix="agy-install-", suffix=".ps1")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(script)
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", tmp],
                       check=False, capture_output=True, timeout=TIMEOUT_INSTALACAO_S)
    finally:
        os.remove(tmp)
    if not os.path.isfile(AGY):
        raise RuntimeError("o instalador do agy rodou mas o agy.exe nao apareceu")


def criar_atalhos(seco, mudou):
    """Menu Iniciar: 'Agy CLI' e 'Agy CLI --resume' (seletor de conversas), no Windows Terminal, como administrador."""
    import subprocess
    if not os.path.isfile(AGY) or not os.path.isdir(MENU):
        return
    wt = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WindowsApps", "wt.exe")
    python = os.path.join(os.path.dirname(sys.executable), "python.exe")
    python = python if os.path.exists(python) else sys.executable
    seletor = os.path.join(AQUI, "agy_resume.py")
    comandos = {
        "Agy CLI.lnk": f"& '{AGY}' --dangerously-skip-permissions",
        "Agy CLI --resume.lnk": f"& '{python}' -I '{seletor}' -- --dangerously-skip-permissions",
    }
    icone = APP + ",0" if os.path.isfile(APP) else AGY + ",0"
    for nome, comando in comandos.items():
        if os.path.isfile(wt):
            alvo, args = wt, f'-d "{WS}" powershell.exe -NoExit -Command "{comando}"'
        else:
            alvo, args = "powershell.exe", f'-NoExit -Command "{comando}"'
        lnk = os.path.join(MENU, nome)
        if seco:
            continue
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                            os.path.join(AQUI, "atalho.ps1"), "-Lnk", lnk, "-Alvo", alvo, "-Argumentos", args,
                            "-Pasta", WS, "-Icone", icone, "-Admin"],
                           capture_output=True, text=True, timeout=60, check=False)
        if r.returncode != 0:
            raise RuntimeError(f"atalho {nome}: {r.stderr.strip()[:200]}")
        if "gravado" in r.stdout:
            mudou.append(lnk)


# ---------------------------------------------------------------- Grok CLI
GROK = os.path.join(HOME, ".grok")
GROK_EXE = os.path.join(GROK, "bin", "grok.exe")
URL_INSTALADOR_GROK = "https://x.ai/cli/install.ps1"
PASTA_GROK_RESUME = os.path.join(AQUI, "grok-resume")
INICIO_BLOCO = "# >>> TagTeam (generated by instalar.py; do not edit inside this block) >>>"
FIM_BLOCO = "# <<< TagTeam <<<"
MODELO_GROK = os.path.join(SKILL, "references", "GROK.modelo.md")


def instalar_grok(seco, mudou):
    """Grok CLI pelo instalador oficial da x.ai (confere SHA-256 do que baixa)."""
    if os.path.isfile(GROK_EXE):
        return
    mudou.append(GROK_EXE)
    if seco:
        return
    import subprocess
    import urllib.request
    # nosemgrep: dynamic-urllib-use-detected -- URL constante https do instalador oficial, TLS verificado
    with urllib.request.urlopen(URL_INSTALADOR_GROK, timeout=TIMEOUT_DOWNLOAD_S) as r:
        script = r.read(MAX_INSTALADOR + 1)
    if len(script) > MAX_INSTALADOR or b"x.ai/cli" not in script or b".grok" not in script:
        raise RuntimeError("instalador do Grok veio diferente do esperado; nada foi executado")
    fd, tmp = tempfile.mkstemp(prefix="grok-install-", suffix=".ps1")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(script)
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", tmp],
                       check=False, capture_output=True, timeout=TIMEOUT_INSTALACAO_S)
    finally:
        os.remove(tmp)
    if not os.path.isfile(GROK_EXE):
        raise RuntimeError("o instalador do Grok rodou mas o grok.exe nao apareceu")


def _igual(a, b):
    import filecmp
    return os.path.isfile(b) and filecmp.cmp(a, b, shallow=False)


def copiar_grok_resume(seco, mudou):
    """grok-resume.exe (abre o Grok no seletor de sessoes) precisa ficar em ~/.grok, ao lado de bin/grok.exe.
    O repo traz so o codigo: o exe e compilado aqui com o csc do .NET Framework que vem no Windows."""
    if not os.path.isfile(GROK_EXE):
        return
    import shutil
    import subprocess
    fonte, exe = os.path.join(PASTA_GROK_RESUME, "grok-resume.cs"), os.path.join(GROK, "grok-resume.exe")
    csc = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Microsoft.NET", "Framework64", "v4.0.30319", "csc.exe")
    if os.path.isfile(fonte) and os.path.isfile(csc) and \
            (not os.path.isfile(exe) or os.path.getmtime(exe) < os.path.getmtime(fonte)):
        mudou.append(exe)
        if not seco:
            icone = os.path.join(PASTA_GROK_RESUME, "grok.ico")   # opcional: o repo nao traz o icone da marca
            r = subprocess.run([csc, "/nologo", "/target:exe", "/optimize+",
                                *(["/win32icon:" + icone] if os.path.isfile(icone) else []), "/out:" + exe, fonte],
                               capture_output=True, text=True, timeout=120, check=False)
            if r.returncode != 0:
                raise RuntimeError(f"csc falhou ao compilar o grok-resume: {r.stdout.strip()[:300]}")
    for nome in ("grok-resume.cs", "grok.ico"):
        origem, destino = os.path.join(PASTA_GROK_RESUME, nome), os.path.join(GROK, nome)
        if os.path.isfile(origem) and not _igual(origem, destino):
            mudou.append(destino)
            if not seco:
                shutil.copy2(origem, destino)


def criar_atalhos_grok(seco, mudou):
    """Menu Iniciar: 'Grok' e 'Grok --resume' (seletor de sessoes), no Windows Terminal, como administrador."""
    import subprocess
    if not os.path.isfile(GROK_EXE) or not os.path.isdir(MENU):
        return
    wt = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WindowsApps", "wt.exe")
    comandos = {"Grok.lnk": f"& '{GROK_EXE}' --always-approve",
                "Grok --resume.lnk": f"& '{os.path.join(GROK, 'grok-resume.exe')}'"}
    icone = (os.path.join(GROK, "grok.ico") if os.path.isfile(os.path.join(GROK, "grok.ico")) else GROK_EXE) + ",0"
    for nome, comando in comandos.items():
        if os.path.isfile(wt):
            alvo, args = wt, f'-d "{WS}" powershell.exe -NoExit -Command "{comando}"'
        else:
            alvo, args = "powershell.exe", f'-NoExit -Command "{comando}"'
        if seco:
            continue
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                            os.path.join(AQUI, "atalho.ps1"), "-Lnk", os.path.join(MENU, nome), "-Alvo", alvo,
                            "-Argumentos", args, "-Pasta", WS, "-Icone", icone, "-Admin"],
                           capture_output=True, text=True, timeout=60, check=False)
        if r.returncode != 0:
            raise RuntimeError(f"atalho {nome}: {r.stderr.strip()[:200]}")
        if "gravado" in r.stdout:
            mudou.append(os.path.join(MENU, nome))


def _toml_texto(v):
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def montar_bloco_grok():
    """Bloco do config.toml do Grok: plugins do Claude ligados, MCP que o Grok nao le do ~/.claude.json e o hook."""
    linhas = [INICIO_BLOCO]
    nomes = sorted(nome for nome, _ in plugins_do_claude())
    if nomes:
        linhas += ["[plugins]", "enabled = [" + ", ".join(_toml_texto(n) for n in nomes) + "]", ""]
    # o Grok nao conseguiu ler o ~/.claude.json (chaves repetidas nele): o MCP remoto vai declarado aqui
    for nome, cfg in sorted((ler_json(os.path.join(HOME, ".claude.json"), {}).get("mcpServers") or {}).items()):
        if isinstance(cfg, dict) and isinstance(cfg.get("url"), str) and re.match(r"^[A-Za-z0-9_-]+$", nome):
            linhas.append(f"[mcp_servers.{nome}]")
            linhas.append(f"url = {_toml_texto(cfg['url'])}")
            if isinstance(cfg.get("headers"), dict) and cfg["headers"]:
                linhas.append("headers = { " + ", ".join(f"{_toml_texto(k)} = {_toml_texto(v)}"
                                                         for k, v in cfg["headers"].items()) + " }")
            linhas.append("")
    python = os.path.join(os.path.dirname(sys.executable), "python.exe")
    python = python if os.path.exists(python) else sys.executable
    comando = f'"{python}" -I "{p(os.path.join(AQUI, "gancho.py"))}"'
    for evento in ("Stop", "SessionEnd"):
        linhas += [f"[[hooks.{evento}]]", f"  [[hooks.{evento}.hooks]]", '  type = "command"',
                   f"  command = {_toml_texto(comando)}", "  timeout = 75", ""]
    linhas.append(FIM_BLOCO)
    return "\n".join(linhas) + "\n"


def montar_config_grok():
    """config.toml do Grok com o bloco gerado no lugar do anterior. None se ficaria invalido ou se o usuario ja
    declarou por conta propria uma tabela que o bloco traria (nao duplica)."""
    import tomllib
    caminho = os.path.join(GROK, "config.toml")
    try:
        with open(caminho, encoding="utf-8") as f:
            atual = f.read()
    except FileNotFoundError:
        atual = ""
    fora = re.sub(re.escape(INICIO_BLOCO) + r".*?" + re.escape(FIM_BLOCO) + r"\n?", "", atual, flags=re.S)
    bloco = montar_bloco_grok()
    try:
        meu = tomllib.loads(bloco)
        dele = tomllib.loads(fora)
    except tomllib.TOMLDecodeError:
        return None
    for chave in ("plugins",):
        if chave in meu and chave in dele:
            return None
    if set((meu.get("mcp_servers") or {})) & set((dele.get("mcp_servers") or {})):
        return None
    novo = fora.rstrip("\n") + "\n\n" + bloco
    try:
        tomllib.loads(novo)
    except tomllib.TOMLDecodeError:
        return None
    return novo


def montar_agents_grok():
    memoria = os.path.join(CLAUDE, "projects", re.sub(r"[^A-Za-z0-9]", "-", WS), "memory", "MEMORY.md")
    with open(MODELO_GROK, encoding="utf-8") as f:
        return f.read().replace("{HOME}", HOME).replace("{MEMORIA}", memoria)


def instalar_grok_config(seco, mudou):
    if not os.path.isfile(GROK_EXE):
        return
    gravar(os.path.join(GROK, "AGENTS.md"), montar_agents_grok(), seco, mudou)
    config = montar_config_grok()
    if config is not None:
        gravar(os.path.join(GROK, "config.toml"), config, seco, mudou)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seco", action="store_true", help="so mostra o que mudaria")
    a = ap.parse_args()
    mudou = []
    instalar_cli(a.seco, mudou)
    criar_atalhos(a.seco, mudou)
    instalar_grok(a.seco, mudou)
    copiar_grok_resume(a.seco, mudou)
    criar_atalhos_grok(a.seco, mudou)
    instalar_grok_config(a.seco, mudou)
    skills, plugins = montar_skills_e_plugins()
    gravar(os.path.join(GEMINI, "GEMINI.md"), montar_gemini_md(), a.seco, mudou)
    gravar(os.path.join(CONFIG, "skills.json"), skills, a.seco, mudou)
    gravar(os.path.join(CONFIG, "plugins.json"), plugins, a.seco, mudou)
    gravar(os.path.join(CONFIG, "mcp_config.json"), montar_mcp(), a.seco, mudou)
    gravar(os.path.join(CONFIG, "hooks.json"), montar_hooks(), a.seco, mudou)
    gravar(os.path.join(CONFIG, "gancho-antigravity.cmd"), montar_cmd(), a.seco, mudou)
    settings = montar_settings_claude()
    if settings is not None:
        gravar(os.path.join(CLAUDE, "settings.json"), settings, a.seco, mudou)
    print(("mudaria: " if a.seco else "gravado: ") + (", ".join(mudou) if mudou else "nada (ja estava em dia)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
