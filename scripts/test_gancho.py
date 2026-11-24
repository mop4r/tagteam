import io
import os
import tempfile
import unittest
from unittest import mock

import gancho

SESSAO = "6e940537-9f5e-4579-aa79-c76070a1c08a"


class TestGancho(unittest.TestCase):
    def setUp(self):
        self.p = mock.patch.object(gancho, "PASTA", tempfile.mkdtemp())
        self.p.start()
        self.addCleanup(self.p.stop)

    def test_intervalo_por_sessao(self):
        self.assertTrue(gancho.deve_rodar(SESSAO, 1_000_000))
        import os
        marca = os.path.join(gancho.PASTA, f"ultimo-{SESSAO}")
        os.utime(marca, (1_000_000, 1_000_000))
        self.assertFalse(gancho.deve_rodar(SESSAO, 1_000_000 + gancho.INTERVALO_S - 1))
        self.assertTrue(gancho.deve_rodar(SESSAO, 1_000_000 + gancho.INTERVALO_S + 1))

    def rodar(self, entrada):
        bruto = entrada.encode("utf-8") if isinstance(entrada, str) else entrada
        stdin = io.TextIOWrapper(io.BytesIO(bruto), encoding="cp1252")
        with mock.patch.object(gancho, "lancar") as lancar, mock.patch("sys.stdin", stdin), \
                mock.patch("os.path.isdir", return_value=True):
            self.assertEqual(gancho.main(), 0)
        return lancar

    def test_eventos(self):
        self.rodar('{"hook_event_name": "SessionEnd", "session_id": "%s"}' % SESSAO).assert_called_once_with(["--sessao", SESSAO])
        inicio = self.rodar('{"hook_event_name": "SessionStart"}')
        self.assertEqual(inicio.call_args_list, [mock.call([], "instalar.py"), mock.call(["--atraso", "90"])])
        self.rodar('{"hook_event_name": "Stop", "session_id": "%s"}' % SESSAO).assert_called_once()
        self.rodar('{"hook_event_name": "Stop", "session_id": "%s"}' % SESSAO).assert_not_called()   # dentro do intervalo

    def test_entrada_com_bom_e_acento(self):
        # bytes reais de um pipe do PowerShell (BOM UTF-8) com acento, lidos num stdin cp1252
        bruto = b"\xef\xbb\xbf" + ('{"hook_event_name": "SessionEnd", "cwd": "C:/Usuários", "session_id": "%s"}' % SESSAO).encode()
        self.rodar(bruto).assert_called_once_with(["--sessao", SESSAO])

    def test_dentro_do_grok_leva_a_sessao_do_grok_e_nao_pede_passagem(self):
        grok_id = "01a20eea-af2c-7040-bb65-365b4c0074ce"
        with mock.patch.dict(os.environ, {"GROK_SESSION_ID": grok_id}):
            lancar = self.rodar('{"hook_event_name": "Stop", "session_id": "%s"}' % grok_id)
            lancar.assert_called_once_with(["--direcao", "claude-de-grok", "--grok", grok_id], esperar=True)
            self.rodar('{"hook_event_name": "SessionStart", "session_id": "%s"}' % grok_id).assert_not_called()

    def test_esperando_roda_ate_o_fim_e_registra_o_prazo_estourado(self):
        with mock.patch.object(gancho.subprocess, "run") as run, mock.patch.object(gancho.subprocess, "Popen") as popen:
            gancho.lancar(["--grok", SESSAO], esperar=True)
            run.assert_called_once()
            self.assertEqual(run.call_args.kwargs["timeout"], gancho.PRAZO_ESPERANDO_S)
            popen.assert_not_called()
            run.side_effect = gancho.subprocess.TimeoutExpired("x", 1)
            gancho.lancar(["--grok", SESSAO], esperar=True)
        with open(os.path.join(gancho.PASTA, "sincronizar.log"), encoding="utf-8") as f:
            self.assertIn("prazo de", f.read())

    def test_entrada_ruim_nao_lanca(self):
        for ruim in ("", "{quebrado", '{"hook_event_name": "Stop", "session_id": "../../x"}', "[]"):
            self.rodar(ruim).assert_not_called()


if __name__ == "__main__":
    unittest.main()
