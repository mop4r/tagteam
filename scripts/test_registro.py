"""registro.py e rodar.py: blocos inteiros no log (varios processos ao mesmo tempo), rotacao e eventos."""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import registro

AQUI = os.path.dirname(os.path.abspath(__file__))


class TestRegistro(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.pasta, True)
        p = mock.patch.dict(os.environ, {"TAGTEAM_STATE_DIR": self.pasta})
        p.start()
        self.addCleanup(p.stop)

    def test_varios_processos_ao_mesmo_tempo_nao_misturam_os_blocos(self):
        log = os.path.join(self.pasta, "x.log")
        codigo = ("import sys; sys.path.insert(0, sys.argv[1]); import registro\n"
                  "for i in range(40):\n"
                  "    registro.anexar(sys.argv[2], ''.join(f'{sys.argv[3]}-{i}-{j}\\n' for j in range(30)), 10**8)\n")
        procs = [subprocess.Popen([sys.executable, "-I", "-c", codigo, AQUI, log, letra]) for letra in "ABCD"]
        for pr in procs:
            self.assertEqual(pr.wait(timeout=120), 0)
        with open(log, encoding="utf-8") as f:
            linhas = f.read().splitlines()
        self.assertEqual(len(linhas), 4 * 40 * 30)
        for k in range(0, len(linhas), 30):         # cada bloco de 30 linhas veio inteiro, de um processo so
            bloco = linhas[k:k + 30]
            dono, rodada = bloco[0].split("-")[:2]
            self.assertEqual(bloco, [f"{dono}-{rodada}-{j}" for j in range(30)])

    def test_passou_do_teto_o_atual_vira_ponto_1(self):
        log = os.path.join(self.pasta, "y.log")
        registro.anexar(log, "a" * 100, 50)
        registro.anexar(log, "b\n", 50)
        with open(log + ".1", encoding="utf-8") as f:
            self.assertEqual(f.read(), "a" * 100)
        with open(log, encoding="utf-8") as f:
            self.assertEqual(f.read(), "b\n")

    def test_evento_vai_e_volta_e_linha_quebrada_e_pulada(self):
        registro.evento({"tipo": "sincronizar", "cont": {"c>g iguais": 3}}, agora=100.0)
        with open(registro.arquivo_eventos(), "a", encoding="utf-8") as f:
            f.write("{quebrada\n[1]\n")
        registro.evento({"tipo": "instalar"}, agora=200.0)
        ev = registro.ler_eventos()
        self.assertEqual([(e["tipo"], e["ts"]) for e in ev], [("sincronizar", 100.0), ("instalar", 200.0)])
        self.assertEqual(ev[0]["cont"], {"c>g iguais": 3})

    def test_evento_enorme_e_cortado(self):
        registro.evento({"tipo": "sincronizar", "erros": ["x" * 5000] * 20}, agora=1.0)
        with open(registro.arquivo_eventos(), encoding="utf-8") as f:
            linha = f.readline()
        self.assertLessEqual(len(linha), registro.MAX_LINHA_EVENTO + 1)
        self.assertEqual(len(json.loads(linha)["erros"]), 5)

    def test_rabo_guarda_as_ultimas_linhas_e_repassa(self):
        destino = io.StringIO()
        r = registro.Rabo(destino, linhas=2)
        r.write("um\ndois\n")
        r.write("tres\nquat")
        self.assertEqual(destino.getvalue(), "um\ndois\ntres\nquat")
        self.assertEqual(r.erros(), ["dois", "tres", "quat"])


class TestRodar(unittest.TestCase):
    def test_so_roda_scripts_do_tagteam(self):
        import rodar
        self.assertEqual(rodar.main(["../../outro.py"]), 2)
        self.assertEqual(rodar.main(["--prazo", "x", "sincronizar.py"]), 2)
        self.assertEqual(rodar.main([]), 2)

    def test_grava_cabecalho_e_saida_juntos_e_o_prazo(self):
        import rodar
        pasta = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, pasta, True)
        log = os.path.join(pasta, "sincronizar.log")
        r = subprocess.CompletedProcess([], 0, stdout=b"linha 1\n", stderr=b"aviso\n")
        with mock.patch.object(rodar, "LOG", log), mock.patch.object(rodar.subprocess, "run", return_value=r) as run:
            self.assertEqual(rodar.main(["sincronizar.py", "--sessao", "abc"]), 0)
            run.side_effect = subprocess.TimeoutExpired("x", 5, output=b"meio")
            self.assertEqual(rodar.main(["--prazo", "5", "sincronizar.py"]), 124)
        self.assertEqual(run.call_args.kwargs["timeout"], 5)
        with open(log, encoding="utf-8") as f:
            texto = f.read()
        self.assertRegex(texto, r"--- .* sincronizar.py --sessao abc\nlinha 1\naviso\n")
        self.assertIn("meio\nprazo de 5s estourado", texto)


if __name__ == "__main__":
    unittest.main()
