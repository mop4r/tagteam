"""saude.py: contagens, eventos, checagens e deteccao dos hooks, tudo em pastas descartaveis."""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import config
import saude

TODAS = {ia: {"app": False, "cli": True, "instalada": True} for ia in ("claude", *config.IAS)}


class TestSaude(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.pasta, True)
        self.temp = os.path.join(self.pasta, "temp")
        os.makedirs(self.temp)
        ps = [mock.patch.dict(os.environ, {"TAGTEAM_STATE_DIR": self.pasta}),
              mock.patch.object(saude.gancho, "PASTA", self.temp),
              mock.patch.object(saude, "CASA", self.pasta), mock.patch.object(saude, "CLAUDE", os.path.join(self.pasta, ".claude")),
              mock.patch.object(saude, "hook_registrado", lambda ia: True)]
        for p in ps:
            p.start()
            self.addCleanup(p.stop)
        self.cfg = config.carregar()

    def niveis(self, lista):
        return [(a, n) for a, n, _t in lista]

    def test_contagens_pelo_estado(self):
        estado = {"importadas": {"antigravity": {"a": {}, "b": {}}, "antigravity-cli": {"a": {}}},
                  "exportadas": {"x": {}}, "importadas_grok": {"a": {}}, "exportadas_codex": {"y": {}, "z": {}},
                  "importadas_cursor": "lixo"}
        c = saude.contagens(estado)
        self.assertEqual(c["antigravity"], (3, 1))
        self.assertEqual(c["grok"], (1, 0))
        self.assertEqual(c["codex"], (0, 2))
        self.assertEqual(c["cursor"], (0, 0))
        self.assertEqual(saude.contagens(None)["grok"], (0, 0))

    def test_de_onde_veio_a_rodada(self):
        self.assertEqual(saude.ia_do_evento({"args": ["--direcao", "claude-de-codex", "--codex", "x"]}), "codex")
        self.assertEqual(saude.ia_do_evento({"args": ["--sessao", "x"]}), "claude")
        self.assertEqual(saude.ia_do_evento({"args": []}), "completa")

    def test_resumo_so_conta_a_janela_e_soma_o_que_mudou(self):
        ev = [{"tipo": "sincronizar", "ts": 10.0, "saida": 0, "cont": {"c>g novas": 5}},          # fora da janela
              {"tipo": "sincronizar", "ts": 90000.0, "saida": 0, "cont": {"c>g atualizadas": 2, "c>a iguais": 400}},
              {"tipo": "instalar", "ts": 90001.0, "saida": 0},
              {"tipo": "sincronizar", "ts": 90002.0, "saida": 1, "cont": {"erro": 1}, "erros": ["deu ruim"]}]
        r = saude.resumo_eventos(ev, agora=90010.0)
        self.assertEqual((r["rodadas"], r["com_erro"], r["mudou"]), (2, 1, 2))
        self.assertEqual(r["ultimo_erro"]["erros"], ["deu ruim"])

    def test_tudo_certo(self):
        for ia in config.IAS:
            open(os.path.join(self.temp, f"gancho-{ia}.txt"), "w").close()
        self.assertEqual(self.niveis(saude.checagens(self.cfg, TODAS, eventos=[])), [("Geral", "ok")])

    def test_ia_marcada_e_nao_instalada_avisa(self):
        det = json.loads(json.dumps(TODAS))
        det["grok"] = {"app": False, "cli": False, "instalada": False}
        lista = saude.checagens(self.cfg, det, eventos=[])
        self.assertIn(("Grok CLI", "aviso"), self.niveis(lista))
        self.assertNotIn(("Geral", "ok"), self.niveis(lista))

    def test_ia_desmarcada_nao_aparece(self):
        self.cfg["conversas"]["grok"] = self.cfg["ambiente"]["grok"] = False
        det = json.loads(json.dumps(TODAS))
        det["grok"]["instalada"] = False
        self.assertNotIn("Grok CLI", [a for a, _n, _t in saude.checagens(self.cfg, det, eventos=[])])

    def test_hook_fora_do_lugar_e_erro(self):
        with mock.patch.object(saude, "hook_registrado", lambda ia: ia != "codex"):
            lista = saude.checagens(self.cfg, TODAS, eventos=[])
        self.assertIn(("OpenAI Codex", "erro"), self.niveis(lista))

    def test_trava_velha_avisa_e_nova_e_informacao(self):
        trava = os.path.join(self.pasta, f".trava-{saude.registro.PC}")
        open(trava, "w").close()
        agora = os.path.getmtime(trava)
        self.assertIn(("Sincronizacao", "info"), self.niveis(saude.checagens(self.cfg, TODAS, agora=agora + 5, eventos=[])))
        self.assertIn(("Sincronizacao", "aviso"),
                      self.niveis(saude.checagens(self.cfg, TODAS, agora=agora + 3600, eventos=[])))

    def test_config_ilegivel_e_erro(self):
        with open(config.caminho(), "w", encoding="utf-8") as f:
            f.write("{x")
        self.assertIn(("Configuracao", "erro"), self.niveis(saude.checagens(self.cfg, TODAS, eventos=[])))


class TestHookRegistrado(unittest.TestCase):
    def test_procura_a_marca_de_cada_ia(self):
        casa = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, casa, True)
        os.makedirs(os.path.join(casa, ".cursor"))
        with open(os.path.join(casa, ".cursor", "hooks.json"), "w", encoding="utf-8") as f:
            json.dump({"hooks": {"stop": [{"command": "py gancho_cursor.py"}]}}, f)
        os.makedirs(os.path.join(casa, ".grok"))
        with open(os.path.join(casa, ".grok", "config.toml"), "w", encoding="utf-8") as f:
            f.write("[cli]\n")
        with mock.patch.object(saude, "CASA", casa), mock.patch.dict(os.environ, {"CODEX_HOME": os.path.join(casa, "x")}):
            self.assertTrue(saude.hook_registrado("cursor"))
            self.assertFalse(saude.hook_registrado("grok"))
            self.assertIsNone(saude.hook_registrado("codex"))       # sem arquivo: ainda nao ha onde registrar


if __name__ == "__main__":
    unittest.main()
