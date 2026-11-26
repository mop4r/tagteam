"""Roda um script do TagTeam e grava a saida dele no log de uma vez so, no fim (python -I rodar.py <script> [args]).

Usado pelos hooks (gancho.lancar). Com a saida inteira gravada num bloco so, sob a trava do registro.py, duas
rodadas ao mesmo tempo nao misturam as linhas no log. --prazo N: encerra o script depois de N segundos (sem ele,
PRAZO_PADRAO_S: uma rodada travada nao fica para sempre como processo invisivel, com a saida nunca gravada).
"""
import os
import subprocess
import sys
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import gancho  # noqa: E402
import registro  # noqa: E402

LOG = os.path.join(gancho.PASTA, "sincronizar.log")
SCRIPTS = ("sincronizar.py", "instalar.py")
MAX_SAIDA = 256 * 1024
PRAZO_PADRAO_S = 2 * 3600       # bem acima de uma rodada completa (minutos), so para nao ficar preso para sempre


def main(argv):
    prazo = PRAZO_PADRAO_S
    if argv[:1] == ["--prazo"] and len(argv) > 1 and argv[1].isdigit():
        prazo, argv = int(argv[1]), argv[2:]
    if not argv or argv[0] not in SCRIPTS:
        return 2                                    # so roda os scripts do proprio TagTeam
    script, args = argv[0], argv[1:]
    cabecalho = f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} {script} {' '.join(args)}\n"
    try:
        r = subprocess.run([sys.executable, "-I", os.path.join(AQUI, script), *args], stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=prazo, check=False)
        saida, codigo = (r.stdout + r.stderr).decode("utf-8", "replace"), r.returncode
    except subprocess.TimeoutExpired as e:
        parcial = (e.stdout or b"") + (e.stderr or b"")
        saida = parcial.decode("utf-8", "replace") + \
            f"\nprazo de {prazo}s estourado; o proximo Stop ou SessionEnd tenta de novo\n"
        codigo = 124
    if len(saida) > MAX_SAIDA:
        saida = saida[:MAX_SAIDA // 2] + "\n[...]\n" + saida[-MAX_SAIDA // 2:]
    registro.anexar(LOG, cabecalho + saida.rstrip("\n") + "\n", registro.MAX_LOG)
    return codigo


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
