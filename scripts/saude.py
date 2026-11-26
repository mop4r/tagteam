"""O que esta instalado neste PC e como a sincronizacao vai: dados para o assistente e o monitor (tagteam.py).

Tudo aqui so le arquivos locais (nada de rede, nada grava). As funcoes recebem o agora e os caminhos por parametro
quando o teste precisa trocar; o resto vem dos mesmos modulos que o instalar.py e o sincronizar.py usam.
"""
import glob
import json
import os
import re
import shutil
import sys
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import config  # noqa: E402
import gancho  # noqa: E402
import registro  # noqa: E402

CASA = os.path.expanduser("~")
LOCAL = os.environ.get("LOCALAPPDATA", "")
CLAUDE = os.path.join(CASA, ".claude")
PYTHON_MINIMO = (3, 11)
HOOK_PARADO_S = 3 * 24 * 3600          # IA marcada cujo hook nao chama ha tanto tempo: vale olhar
TRAVA_VELHA_S = 30 * 60                # igual ao sincronizar.py
MAX_ARQUIVO = 20 * 1024 * 1024
OK, AVISO, ERRO, INFO = "ok", "aviso", "erro", "info"

# o que conta como "instalado", por parte (app e CLI), nos caminhos padrao dos instaladores oficiais
PARTES = {
    "claude": {"cli": [os.path.join(CASA, ".local", "bin", "claude.exe")], "which": "claude"},
    "antigravity": {"app": [os.path.join(LOCAL, "Programs", "Antigravity", "Antigravity.exe")],
                    "cli": [os.path.join(LOCAL, "agy", "bin", "agy.exe")]},
    "grok": {"cli": [os.path.join(CASA, ".grok", "bin", "grok.exe")]},
    "codex": {"cli": [os.path.join(LOCAL, "Programs", "OpenAI", "Codex", "bin", "codex.exe")], "which": "codex",
              "app": glob.glob(os.path.join(LOCAL, "Packages", "OpenAI.Codex*"))},
    "cursor": {"app": [os.path.join(LOCAL, "Programs", "cursor", "Cursor.exe")],
               "cli": [os.path.join(LOCAL, "cursor-agent", "cursor-agent.cmd")]},
}
# a IA pode ser instalada pelo TagTeam (instalador oficial do CLI) mesmo sem estar no PC
INSTALAVEL = {"antigravity": "CLI agy", "grok": "Grok CLI", "codex": "Codex CLI"}


def detectar():
    """{ia: {"app": bool, "cli": bool, "instalada": bool}} para o Claude e as quatro IAs."""
    saida = {}
    for ia, partes in PARTES.items():
        d = {}
        for parte in ("app", "cli"):
            d[parte] = any(os.path.exists(c) for c in partes.get(parte, []))
        if not d["cli"] and partes.get("which"):
            d["cli"] = bool(shutil.which(partes["which"]))
        d["instalada"] = d["app"] or d["cli"]
        saida[ia] = d
    return saida


def _ler(caminho):
    try:
        if os.path.getsize(caminho) > MAX_ARQUIVO:
            return ""
        with open(caminho, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _json(caminho):
    try:
        return json.loads(_ler(caminho) or "null")
    except ValueError:
        return None


def hook_registrado(ia):
    """O hook de conversas do TagTeam esta no arquivo de hooks da IA (None: a IA nao tem onde registrar ainda)."""
    if ia == "claude":
        s = _json(os.path.join(CLAUDE, "settings.json"))
        gancho_py = os.path.join(AQUI, "gancho.py").replace("\\", "/")
        texto = json.dumps((s or {}).get("hooks") or {}) if isinstance(s, dict) else ""
        return gancho_py in texto.replace("\\\\", "/")
    arquivos = {"antigravity": (os.path.join(CASA, ".gemini", "config", "hooks.json"), '"claude-sync"'),
                "grok": (os.path.join(CASA, ".grok", "config.toml"), "[[hooks.Stop]]"),
                "codex": (os.path.join(os.environ.get("CODEX_HOME") or os.path.join(CASA, ".codex"), "hooks.json"),
                          "gancho_codex.py"),
                "cursor": (os.path.join(CASA, ".cursor", "hooks.json"), "gancho_cursor.py")}
    caminho, marca = arquivos[ia]
    if not os.path.isfile(caminho):
        return None
    return marca in _ler(caminho)


def ultimo_gancho(ia):
    """(instante, evento) da ultima chamada do hook da IA neste PC, ou (None, None)."""
    caminho = os.path.join(gancho.PASTA, f"gancho-{ia}.txt")
    try:
        return os.path.getmtime(caminho), re.sub(r"[^A-Za-z]", "", _ler(caminho))[:30]
    except OSError:
        return None, None


def contagens(estado):
    """{ia: (do Claude para a IA, da IA para o Claude)} a partir do estado-<PC>.json do sincronizar."""
    estado = estado if isinstance(estado, dict) else {}

    def n(chave):
        v = estado.get(chave)
        return len(v) if isinstance(v, dict) else 0
    imp = estado.get("importadas") if isinstance(estado.get("importadas"), dict) else {}
    para_antigravity = sum(len(v) for v in imp.values() if isinstance(v, dict))
    return {"antigravity": (para_antigravity, n("exportadas")), "grok": (n("importadas_grok"), n("exportadas_grok")),
            "codex": (n("importadas_codex"), n("exportadas_codex")),
            "cursor": (n("importadas_cursor") + n("importadas_cursor_app"), n("exportadas_cursor"))}   # CLI + app


def arquivo_estado():
    pc = re.sub(r"[^A-Za-z0-9_-]", "_", os.environ.get("COMPUTERNAME", "pc"))[:40]
    return os.path.join(config.pasta_estado(), f"estado-{pc}.json")


def falhas_de_atualizacao():
    """IAs cujo CLI nao atualizou na ultima rodada de atualizacao automatica (instalar.passo_atualizar_clis)."""
    dado = _json(os.path.join(config.pasta_estado(), "ultima-atualizacao-clis.txt"))
    falhas = dado.get("falhas") if isinstance(dado, dict) else None
    return [ia for ia in falhas if ia in config.IAS] if isinstance(falhas, list) else []


def ia_do_evento(ev):
    """De onde veio a rodada (pelos argumentos): a IA do hook, "claude" para uma sessao do Claude, "completa" sem filtro."""
    args = ev.get("args") or []
    for flag, ia in (("--cascade", "antigravity"), ("--grok", "grok"), ("--codex", "codex"), ("--cursor", "cursor"),
                     ("--sessao", "claude")):
        if flag in args:
            return ia
    return "completa"


def resumo_eventos(eventos, agora, janela_s=24 * 3600):
    """Totais das rodadas de sincronizacao na janela: rodadas, com erro, e o que mudou (somando os contadores)."""
    rodadas = [e for e in eventos if e.get("tipo") == "sincronizar" and agora - e["ts"] <= janela_s]
    mudou = 0
    for e in rodadas:
        for chave, v in (e.get("cont") or {}).items():
            if isinstance(v, int) and re.search(r"(novas|atualizadas|versao_nova|exportadas|importadas)$", chave):
                mudou += v
    com_erro = [e for e in rodadas if e.get("saida") not in (0, None) or (e.get("cont") or {}).get("erro")]
    return {"rodadas": len(rodadas), "com_erro": len(com_erro), "mudou": mudou,
            "ultima": rodadas[-1] if rodadas else None, "ultimo_erro": com_erro[-1] if com_erro else None}


MENSAGENS = {
    "tudo_certo": ("tudo certo", "all good"),
    "python_velho": ("Python {v}: o TagTeam precisa do 3.11 ou mais novo", "Python {v}: TagTeam needs 3.11 or newer"),
    "config_ilegivel": ("config.json ilegível: valendo os padrões até salvar de novo",
                        "config.json unreadable: defaults apply until it is saved again"),
    "sem_claude": ("Claude Code não encontrado: ele é o centro da sincronização",
                   "Claude Code not found: it is the hub of the sync"),
    "hooks_claude": ("hooks do TagTeam fora do settings.json do Claude: use Reparar (R)",
                     "TagTeam hooks missing from Claude's settings.json: use Repair (R)"),
    "trava_presa": ("trava presa há {m} min (a próxima rodada limpa)", "lock stuck for {m} min (the next run clears it)"),
    "sincronizando": ("sincronizando agora", "syncing now"),
    "agendadas": ("{n} rodada(s) agendada(s) para o fim do intervalo de 2 min",
                  "{n} run(s) scheduled for the end of the 2-minute window"),
    "erros_24h": ("{n} rodada(s) com erro em 24 h; última: {e}", "{n} run(s) with errors in 24 h; last: {e}"),
    "veja_log": ("veja o log", "see the log"),
    "nao_instalada": ("marcada, mas não está instalada neste PC", "checked, but not installed on this PC"),
    "hook_fora": ("hook de conversas fora do lugar: use Reparar (R)", "chat hook missing: use Repair (R)"),
    "hook_nunca": ("hook ainda não foi chamado neste PC (abra uma conversa nela)",
                   "hook not called on this PC yet (open a chat there)"),
    "hook_parado": ("hook não é chamado há {d} dias", "hook not called for {d} days"),
    "cli_nao_atualizou": ("o CLI não atualizou na última tentativa (aberto? tenta de novo amanhã)",
                          "the CLI did not update on the last try (open? it retries tomorrow)"),
}
ASSUNTOS = {"geral": ("Geral", "General"), "python": ("Python", "Python"), "configuracao": ("Configuração", "Settings"),
            "sincronizacao": ("Sincronização", "Sync"), "claude": ("Claude Code", "Claude Code")}


def texto(chave, idioma="pt", **kw):
    """Mensagem de uma checagem no idioma (pt ou en)."""
    pt, en = MENSAGENS[chave]
    return (pt if idioma == "pt" else en).format(**kw)


def assunto(chave, idioma="pt"):
    if chave in config.NOMES and chave not in ASSUNTOS:
        return config.NOMES[chave]
    pt, en = ASSUNTOS[chave]
    return pt if idioma == "pt" else en


def checagens(cfg, detectado, agora=None, eventos=None):
    """Lista de (assunto, nivel, mensagem, parametros) para o painel de saude. nivel: ok, aviso, erro ou info; assunto
    e mensagem sao chaves (assunto() e texto() dao o texto no idioma)."""
    agora = time.time() if agora is None else agora
    eventos = registro.ler_eventos(500) if eventos is None else eventos
    saida = _checagens_gerais(cfg, detectado, agora, eventos)
    for ia in config.IAS:
        if config.ligada(cfg, ia):
            saida += _checagens_da_ia(cfg, ia, detectado, agora)
    if not any(n in (ERRO, AVISO) for _a, n, _m, _k in saida):
        saida.insert(0, ("geral", OK, "tudo_certo", {}))
    return saida


def _checagens_gerais(cfg, detectado, agora, eventos):
    saida = []
    if sys.version_info < PYTHON_MINIMO:
        saida.append(("python", ERRO, "python_velho", {"v": sys.version.split()[0]}))
    if config.ler_bruto()[1] in ("invalida", "grande"):
        saida.append(("configuracao", ERRO, "config_ilegivel", {}))
    if not detectado["claude"]["instalada"] and not os.path.isdir(CLAUDE):
        saida.append(("claude", ERRO, "sem_claude", {}))
    elif hook_registrado("claude") is False and any(config.ligada(cfg, ia) for ia in config.IAS):
        saida.append(("claude", ERRO, "hooks_claude", {}))
    return saida + _checagens_das_rodadas(agora, eventos)


def _checagens_das_rodadas(agora, eventos):
    """Trava, rodadas agendadas, CLIs que nao atualizaram e erros das rodadas das ultimas 24 h."""
    saida = []
    trava = os.path.join(config.pasta_estado(), f".trava-{registro.PC}")
    if os.path.exists(trava):
        idade = agora - os.path.getmtime(trava)
        if idade > TRAVA_VELHA_S:
            saida.append(("sincronizacao", AVISO, "trava_presa", {"m": f"{idade / 60:.0f}"}))
        else:
            saida.append(("sincronizacao", INFO, "sincronizando", {}))
    pendentes = [p for p in glob.glob(os.path.join(gancho.PASTA, "pendente-*"))
                 if agora - os.path.getmtime(p) < 2 * gancho.INTERVALO_S]
    if pendentes:
        saida.append(("sincronizacao", INFO, "agendadas", {"n": len(pendentes)}))
    for ia in falhas_de_atualizacao():
        saida.append((ia, INFO, "cli_nao_atualizou", {}))
    res = resumo_eventos(eventos, agora)
    if res["ultimo_erro"]:
        erros = res["ultimo_erro"].get("erros") or []
        saida.append(("sincronizacao", AVISO, "erros_24h", {"n": res["com_erro"], "e": (erros[-1] if erros else "?")[:160]}))
    return saida


def _checagens_da_ia(cfg, ia, detectado, agora):
    if not detectado[ia]["instalada"]:
        return [(ia, AVISO, "nao_instalada", {})]
    if not config.conversas(cfg, ia):
        return []
    saida = []
    if hook_registrado(ia) is False:
        saida.append((ia, ERRO, "hook_fora", {}))
    quando, _ev = ultimo_gancho(ia)
    if quando is None:
        saida.append((ia, INFO, "hook_nunca", {}))
    elif agora - quando > HOOK_PARADO_S:
        saida.append((ia, INFO, "hook_parado", {"d": f"{(agora - quando) / 86400:.0f}"}))
    return saida


def painel(cfg=None, agora=None):
    """Tudo o que o monitor mostra, de uma vez (le os arquivos uma vez por atualizacao)."""
    cfg = config.carregar() if cfg is None else cfg
    agora = time.time() if agora is None else agora
    detectado = detectar()
    eventos = registro.ler_eventos(500)
    estado = _json(arquivo_estado())
    cont = contagens(estado)
    ias = {}
    for ia in config.IAS:
        quando, ev = ultimo_gancho(ia)
        ias[ia] = {"nome": config.NOMES[ia], "instalada": detectado[ia]["instalada"], "app": detectado[ia]["app"],
                   "cli": detectado[ia]["cli"], "conversas": config.conversas(cfg, ia),
                   "ambiente": config.ambiente(cfg, ia), "para": cont[ia][0], "de": cont[ia][1],
                   "gancho_quando": quando, "gancho_evento": ev,
                   "erros_24h": sum(1 for e in eventos if ia_do_evento(e) == ia and agora - e["ts"] <= 86400
                                    and (e.get("saida") not in (0, None) or (e.get("cont") or {}).get("erro")))}
    return {"agora": agora, "cfg": cfg, "detectado": detectado, "ias": ias, "eventos": eventos[-30:],
            "resumo": resumo_eventos(eventos, agora),
            "checagens": checagens(cfg, detectado, agora, eventos)}
