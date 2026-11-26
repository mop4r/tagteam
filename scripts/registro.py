"""Log e eventos do TagTeam, gravados por varios processos ao mesmo tempo sem misturar linhas.

Cada gravacao pega uma trava de arquivo (msvcrt no Windows, fcntl no resto), confere o tamanho (passou do teto: o
atual vira .1) e escreve o bloco inteiro de uma vez. Antes, cada rodada escrevia direto no log enquanto rodava, e
duas rodadas ao mesmo tempo intercalavam as linhas uma da outra.

eventos-<PC>.jsonl (na pasta de estado): uma linha JSON por rodada de sincronizacao ou de instalacao, para o monitor.
"""
import json
import os
import re
import time

import config

MAX_LOG = 512 * 1024
MAX_EVENTOS = 1024 * 1024
MAX_LINHA_EVENTO = 16 * 1024
PC = re.sub(r"[^A-Za-z0-9_-]", "_", os.environ.get("COMPUTERNAME", "pc"))[:40]


def arquivo_eventos():
    return os.path.join(config.pasta_estado(), f"eventos-{PC}.jsonl")


class _Trava:
    """Trava exclusiva num arquivo <alvo>.trava (espera ate ~10 s; sem a trava, grava assim mesmo)."""

    def __init__(self, alvo):
        self.caminho = alvo + ".trava"
        self.fd = None

    def __enter__(self):
        try:
            self.fd = os.open(self.caminho, os.O_RDWR | os.O_CREAT)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.fd, msvcrt.LK_LOCK, 1)     # tenta 10 vezes, 1 s cada
            else:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_EX)
        except OSError:
            self._fechar()          # sem trava (disco, permissao): melhor gravar do que perder o registro
        return self

    def __exit__(self, *_):
        if self.fd is None:
            return
        try:
            if os.name == "nt":             # no resto, fechar o arquivo ja solta o flock
                import msvcrt
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
        finally:
            self._fechar()                  # se o destravar falhar, fechar o arquivo solta a trava assim mesmo

    def _fechar(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def anexar(caminho, texto, maximo):
    """Acrescenta texto ao arquivo de uma vez so, com a trava; passou de maximo, o atual vira <caminho>.1."""
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    with _Trava(caminho):
        if os.path.exists(caminho) and os.path.getsize(caminho) > maximo:
            os.replace(caminho, caminho + ".1")
        with open(caminho, "a", encoding="utf-8", newline="\n") as f:
            f.write(texto)


def evento(dado, agora=None):
    """Grava uma linha em eventos-<PC>.jsonl. dado: dict serializavel (contadores, argumentos, erros)."""
    dado = dict(dado, ts=round(time.time() if agora is None else agora, 3))
    linha = json.dumps(dado, ensure_ascii=False, sort_keys=True, default=str)
    if len(linha) > MAX_LINHA_EVENTO:
        dado["erros"] = [str(e)[:200] for e in (dado.get("erros") or [])[:5]]
        linha = json.dumps(dado, ensure_ascii=False, sort_keys=True, default=str)[:MAX_LINHA_EVENTO]
    anexar(arquivo_eventos(), linha + "\n", MAX_EVENTOS)


def ler_eventos(limite=200, caminho=None):
    """Os ultimos eventos (mais novo por ultimo). Linha que nao se entende e pulada."""
    caminho = caminho or arquivo_eventos()
    saida = []
    for arq in (caminho + ".1", caminho):
        try:
            with open(arq, encoding="utf-8", errors="replace") as f:
                linhas = f.readlines()
        except OSError:
            continue
        for linha in linhas:
            try:
                d = json.loads(linha)
            except ValueError:
                continue
            if isinstance(d, dict) and isinstance(d.get("ts"), (int, float)):
                saida.append(d)
    return saida[-limite:]


class Rabo:
    """Repassa o que se escreve (stderr) e guarda as ultimas linhas, para o evento dizer qual foi o erro."""

    def __init__(self, destino, linhas=8):
        self.destino, self.linhas, self.ultimas, self._parcial = destino, linhas, [], ""

    def write(self, texto):
        self._parcial += texto
        *completas, self._parcial = self._parcial.split("\n")
        self.ultimas = (self.ultimas + [c[:400] for c in completas if c.strip()])[-self.linhas:]
        if self.destino is not None:
            return self.destino.write(texto)
        return len(texto)

    def flush(self):
        if self.destino is not None:
            self.destino.flush()

    def erros(self):
        return self.ultimas + ([self._parcial[:400]] if self._parcial.strip() else [])
