"""Linhagem unica entre as 5 IAs: fast-forward (continuacao linear sem duplicar) e fork explicito (divergencia real).

Quando uma copia de uma sessao do Claude e continuada em outra IA:
- se a sessao do Claude nao mudou desde o ultimo sync (95% dos casos: revezamento), acrescenta so os turnos novos
  direto na mesma sessao do Claude (fast-forward) e atualiza a marca da IA no estado (sem ping-pong, sem duplicata);
- se a sessao do Claude tambem andou por conta propria (aberta nas duas IAs ao mesmo tempo), nao mistura: sinaliza
  divergencia para criar um fork separado com o sufixo de ramo no titulo, sem perder nenhuma mensagem.
"""
import contextlib
import os
import re
import shutil
import sys
import time

import claude_jsonl
import para_claude

ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
DIAS_ARQUIVO_MORTO = 7


def marca_arquivo(caminho):
    """(tamanho, mtime arredondado em 3 casas) do arquivo."""
    st = os.stat(caminho)
    return (st.st_size, round(st.st_mtime, 3))


def sessao_da_marca(valor):
    """Extrai o UUID da sessao do Claude de 'claude:<uuid>' ou '<uuid>'; None se invalido."""
    if not isinstance(valor, str):
        return None
    bruto = valor[len("claude:"):] if valor.startswith("claude:") else valor
    return bruto if ID_RE.match(bruto) else None


def achar_importada(mapa, chave_id, valor_id):
    """Busca reversa em importadas_* ({sessao: rec}): devolve (sessao, rec) onde rec[chave_id] == valor_id."""
    for sessao, rec in mapa.items():
        if isinstance(rec, dict) and rec.get(chave_id) == valor_id:
            return sessao, rec
    return None, None


def _tem_conteudo(turnos):
    return any(getattr(t, "texto", "").strip() for t in turnos)


def tentar_avancar_claude(sessao_origem, turnos_base, marca_base, novos_turnos, origem, versao_cc, caminho=None):
    """Tenta fast-forward dos turnos novos sobre a sessao do Claude de origem.

    Devolve:
    - ("sem_novos", None) se nao ha turno novo com texto;
    - ("divergiu", None) se a sessao sumiu, recebeu turnos proprios ou mudou durante a escrita;
    - ("avancou", (novo_tamanho, novo_mtime, total_turnos)) se os turnos foram anexados com sucesso.
    """
    if not _tem_conteudo(novos_turnos):
        return "sem_novos", None
    caminho = caminho or para_claude.achar_sessao(sessao_origem)
    if caminho is None or not os.path.isfile(caminho):
        return "divergiu", None
    marca_atual = marca_arquivo(caminho)
    conv_s0 = claude_jsonl.ler_conversa(caminho, sessao_origem)
    mesma_marca = marca_base is not None and marca_atual == tuple(marca_base)
    if not mesma_marca:
        if turnos_base is None or len(conv_s0.turnos) > turnos_base:
            return "divergiu", None
    nova_marca = para_claude.acrescentar_turnos(caminho, sessao_origem, novos_turnos, versao_cc, origem,
                                                marca_esperada=marca_atual)
    if nova_marca is None:
        return "divergiu", None
    total = len(claude_jsonl.ler_conversa(caminho, sessao_origem).turnos)
    return "avancou", (nova_marca[0], nova_marca[1], total)


def _limpar_arquivo_morto(pasta_ia, agora):
    limite = agora - DIAS_ARQUIVO_MORTO * 86400
    for nome in os.listdir(pasta_ia):
        alvo = os.path.join(pasta_ia, nome)
        with contextlib.suppress(OSError):
            if os.path.getmtime(alvo) < limite:
                if os.path.isdir(alvo):
                    shutil.rmtree(alvo)
                else:
                    os.remove(alvo)


def arquivar_substituida(pasta_estado, ia, item_id, caminho_origem):
    """Guarda uma copia de seguranca de conversa nativa substituida em fast-forward (retida por 7 dias)."""
    if not caminho_origem or not os.path.exists(caminho_origem):
        return
    try:
        pasta_ia = os.path.join(pasta_estado, "substituidas", ia)
        os.makedirs(pasta_ia, exist_ok=True)
        agora = time.time()
        destino = os.path.join(pasta_ia, f"{int(agora)}-{item_id}")
        if os.path.isdir(caminho_origem):
            shutil.copytree(caminho_origem, destino, dirs_exist_ok=True)
        else:
            shutil.copy2(caminho_origem, destino + os.path.splitext(caminho_origem)[1])
        _limpar_arquivo_morto(pasta_ia, agora)
    except OSError as e:
        print(f"aviso: nao arquivou copia substituida ({ia}/{item_id}): {e}", file=sys.stderr)
