"""Atualizar o TagTeam pela release mais nova do GitHub, so quando a pessoa pede (nada consulta a rede sozinho).

A release traz o pacote tagteam-<versao>.zip (pasta tagteam/ com o SKILL.md, scripts/ e references/); o GitHub
publica o SHA-256 de cada pacote da release (campo "digest"), e o download so e aplicado se o hash bater. Instalacao
feita com git clone nao e tocada: para ela, git pull.
"""
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import urllib.request
import zipfile

URL_RELEASE = "https://api.github.com/repos/mop4r/tagteam/releases/latest"
HOST_PACOTE = ("https://github.com/", "https://objects.githubusercontent.com/")
TIMEOUT_S = 20
MAX_JSON = 2 * 1024 * 1024
MAX_PACOTE = 30 * 1024 * 1024
MAX_ARQUIVOS = 2000
PACOTE_RE = re.compile(r"^tagteam-v?\d+\.\d+\.\d+\.zip$")
DIGEST_RE = re.compile(r"^sha256:([0-9a-f]{64})$")
VERSAO_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
PASTAS_DO_PACOTE = ("scripts/", "references/", "assets/")
ARQUIVOS_DO_PACOTE = ("SKILL.md", "README.md", "README.pt-BR.md", "LICENSE")


class ErroAtualizacao(RuntimeError):
    pass


def versao(texto):
    """(maior, menor, correcao) de "v0.3.0" ou "0.3.0"; None se nao for uma versao."""
    m = VERSAO_RE.match(str(texto or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def _ler(url, maximo, abrir):
    # nosemgrep: dynamic-urllib-use-detected -- so URL https fixa (API do GitHub) ou de pacote conferida em HOST_PACOTE
    with abrir(urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                    "User-Agent": "tagteam-updater"}), timeout=TIMEOUT_S) as r:
        dado = r.read(maximo + 1)
    if len(dado) > maximo:
        raise ErroAtualizacao("resposta maior que o esperado")
    return dado


def release_mais_nova(abrir=urllib.request.urlopen):
    """{"versao": "v0.4.0", "url": <pacote>, "sha256": <hex>, "notas": <texto>} da ultima release, ou erro."""
    try:
        dado = json.loads(_ler(URL_RELEASE, MAX_JSON, abrir))
    except (OSError, ValueError) as e:
        raise ErroAtualizacao(f"nao consegui consultar o GitHub ({type(e).__name__})") from e
    tag = dado.get("tag_name") if isinstance(dado, dict) else None
    if not versao(tag):
        raise ErroAtualizacao("a release mais nova nao tem uma versao valida")
    for a in dado.get("assets") or []:
        if not isinstance(a, dict) or not PACOTE_RE.match(str(a.get("name", ""))):
            continue
        url, digest = str(a.get("browser_download_url", "")), DIGEST_RE.match(str(a.get("digest", "")))
        if url.startswith(HOST_PACOTE) and digest:
            return {"versao": tag, "url": url, "sha256": digest.group(1), "notas": str(dado.get("body") or "")[:4000]}
    raise ErroAtualizacao("a release mais nova nao traz o pacote tagteam-<versao>.zip com SHA-256")


def ha_versao_nova(atual, release):
    return versao(release["versao"]) > versao(atual)


def baixar(release, abrir=urllib.request.urlopen):
    """Bytes do pacote, conferidos pelo SHA-256 que o GitHub publicou."""
    if not str(release.get("url", "")).startswith(HOST_PACOTE):
        raise ErroAtualizacao("endereco do pacote fora do GitHub")
    try:
        dado = _ler(release["url"], MAX_PACOTE, abrir)
    except OSError as e:
        raise ErroAtualizacao(f"download falhou ({type(e).__name__})") from e
    if hashlib.sha256(dado).hexdigest() != release["sha256"]:
        raise ErroAtualizacao("o pacote baixado nao confere com o SHA-256 da release; nada foi mudado")
    return dado


def _membros(z):
    """[(nome dentro de tagteam/, ZipInfo)] so do que o TagTeam traz, sem caminho absoluto nem '..'."""
    saida = []
    infos = z.infolist()
    if len(infos) > MAX_ARQUIVOS:
        raise ErroAtualizacao("pacote com arquivos demais")
    for info in infos:
        nome = info.filename.replace("\\", "/")
        if info.is_dir() or not nome.startswith("tagteam/"):
            continue
        rel = nome[len("tagteam/"):]
        partes = rel.split("/")
        if not rel or rel.startswith("/") or ":" in rel or ".." in partes:
            raise ErroAtualizacao(f"caminho suspeito no pacote: {nome[:80]}")
        if rel in ARQUIVOS_DO_PACOTE or rel.startswith(PASTAS_DO_PACOTE):
            saida.append((rel, info))
    if "SKILL.md" not in {r for r, _i in saida} or not any(r.startswith("scripts/") for r, _i in saida):
        raise ErroAtualizacao("o pacote nao tem a estrutura do TagTeam")
    return saida


def eh_pasta_do_tagteam(pasta):
    """A pasta e uma instalacao do TagTeam: o SKILL.md dela se chama tagteam (uma copia adaptada nao e sobrescrita)."""
    try:
        with open(os.path.join(pasta, "SKILL.md"), encoding="utf-8") as f:
            cabeca = f.read(2000)
    except OSError:
        return False
    return re.search(r"^name:\s*tagteam\s*$", cabeca, flags=re.M) is not None


def aplicar(dado, pasta_skill):
    """Extrai num temporario e copia por cima da pasta do TagTeam (arquivo por arquivo: um hook rodando nao impede).
    Devolve quantos arquivos foram escritos."""
    if os.path.isdir(os.path.join(pasta_skill, ".git")):
        raise ErroAtualizacao("instalado com git: atualize com 'git pull' na pasta do TagTeam")
    if not eh_pasta_do_tagteam(pasta_skill):
        raise ErroAtualizacao("esta pasta nao e uma instalacao do TagTeam (SKILL.md sem name: tagteam); nada foi mudado")
    with zipfile.ZipFile(io.BytesIO(dado)) as z:
        membros = _membros(z)
        temp = tempfile.mkdtemp(prefix="tagteam-novo-")
        try:
            for rel, info in membros:
                destino = os.path.join(temp, *rel.split("/"))
                os.makedirs(os.path.dirname(destino), exist_ok=True)
                with z.open(info) as origem, open(destino, "wb") as saida:
                    shutil.copyfileobj(origem, saida)
            for rel, _info in membros:
                destino = os.path.join(pasta_skill, *rel.split("/"))
                os.makedirs(os.path.dirname(destino), exist_ok=True)
                shutil.copy2(os.path.join(temp, *rel.split("/")), destino)
        finally:
            shutil.rmtree(temp, ignore_errors=True)
    return len(membros)
