import unittest
import urllib.error
from unittest import mock

import servidor_local

SRV = {"porta": 1, "token": "t"}


class Resposta:
    def __init__(self, corpo):
        self.corpo = corpo

    def read(self, _n):
        return self.corpo

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestChamar(unittest.TestCase):
    def test_recusada_repete_e_depois_responde(self):
        recusa = urllib.error.URLError(ConnectionRefusedError(10061, "recusada"))
        pausas = []
        with mock.patch("urllib.request.urlopen", side_effect=[recusa, recusa, Resposta(b'{"ok": 1}')]):
            r = servidor_local.chamar(SRV, "LoadTrajectory", {}, dormir=pausas.append)
        self.assertEqual(r, {"ok": 1})
        self.assertEqual(len(pausas), 2)

    def test_recusada_sempre_desiste_com_erro_claro(self):
        recusa = urllib.error.URLError(ConnectionRefusedError(10061, "recusada"))
        with mock.patch("urllib.request.urlopen", side_effect=recusa) as abrir:
            with self.assertRaises(servidor_local.ErroServidor):
                servidor_local.chamar(SRV, "LoadTrajectory", {}, tentativas=2, dormir=lambda _s: None)
        self.assertEqual(abrir.call_count, 3)

    def test_outro_erro_de_rede_nao_repete(self):
        # o pedido pode ter chegado: repetir as cegas poderia duplicar efeito
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError(OSError("reset"))) as abrir:
            with self.assertRaises(servidor_local.ErroServidor):
                servidor_local.chamar(SRV, "DeleteCascadeTrajectory", {}, dormir=lambda _s: None)
        self.assertEqual(abrir.call_count, 1)

    def test_dono_pelo_processo_pai(self):
        procs = {10: (5, "language_server.exe"), 5: (1, "antigravity.exe"),
                 20: (7, "language_server.exe"), 7: (2, "agy.exe"),
                 30: (8, "language_server.exe"), 8: (3, "explorer.exe")}
        self.assertEqual(servidor_local.dono(10, procs), "antigravity.exe")
        self.assertEqual(servidor_local.dono(20, procs), "agy.exe")
        self.assertIsNone(servidor_local.dono(30, procs))
        self.assertIsNone(servidor_local.dono(99, procs))

    def test_lista_de_processos_real_tem_este_processo(self):
        import os
        self.assertIn(os.getpid(), servidor_local._processos())

    def test_metodo_invalido(self):
        with self.assertRaises(ValueError):
            servidor_local.chamar(SRV, "a/b", {})


if __name__ == "__main__":
    unittest.main()
