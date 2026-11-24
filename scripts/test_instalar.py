import json
import os
import tempfile
import unittest
from unittest import mock

import instalar


class TestInstalar(unittest.TestCase):
    def test_versao_mais_nova_por_numero_nao_por_texto(self):
        nomes = ["1.2.3", "1.10.0", "1.9.9"]
        self.assertEqual(max(nomes, key=instalar._versao), "1.10.0")

    def test_hook_relativo_com_ponto_barra_e_mantem_os_outros(self):
        pasta = tempfile.mkdtemp()
        with open(os.path.join(pasta, "hooks.json"), "w", encoding="utf-8") as f:
            json.dump({"meu-hook": {"Stop": []}, "claude-sync": {"Stop": [{"command": "velho"}]}}, f)
        with mock.patch.object(instalar, "CONFIG", pasta):
            h = instalar.montar_hooks()
        self.assertIn("meu-hook", h)
        # sem o ".\" o cmd nao acha o .cmd quando NoDefaultCurrentDirectoryInExePath=1
        self.assertEqual(h["claude-sync"]["Stop"][0]["command"], ".\\gancho-antigravity.cmd")

    def test_gravar_so_quando_muda(self):
        pasta = tempfile.mkdtemp()
        alvo = os.path.join(pasta, "x.json")
        mudou = []
        instalar.gravar(alvo, {"a": 1}, False, mudou)
        instalar.gravar(alvo, {"a": 1}, False, mudou)
        self.assertEqual(mudou, [alvo])
        instalar.gravar(alvo, {"a": 2}, True, mudou)        # ensaio: anota, nao grava
        with open(alvo, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"a": 1})


class TestConfigGrok(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.home, ".grok"))
        with open(os.path.join(self.home, ".claude.json"), "w", encoding="utf-8") as f:
            json.dump({"mcpServers": {"projects": {"type": "http", "url": "https://x/mcp",
                                                   "headers": {"Authorization": 'Bearer a"b\\c'}},
                                      "local": {"command": "node"}, "nome ruim": {"url": "https://y"}}}, f)
        ps = [mock.patch.object(instalar, "HOME", self.home), mock.patch.object(instalar, "GROK", os.path.join(self.home, ".grok")),
              mock.patch.object(instalar, "plugins_do_claude", lambda: [("superpowers", "x"), ("my-plugin", "y")])]
        for p in ps:
            p.start()
            self.addCleanup(p.stop)
        self.config = os.path.join(self.home, ".grok", "config.toml")

    def ler(self):
        import tomllib
        return tomllib.loads(instalar.montar_config_grok())

    def test_bloco_traz_plugins_mcp_remoto_e_hooks_e_mantem_o_resto(self):
        with open(self.config, "w", encoding="utf-8") as f:
            f.write('[cli]\ntheme = "dark"\n\n[mcp_servers.mcp-atlassian]\ncommand = "uvx"\n')
        c = self.ler()
        self.assertEqual(c["cli"]["theme"], "dark")
        self.assertEqual(c["plugins"]["enabled"], ["my-plugin", "superpowers"])
        self.assertEqual(set(c["mcp_servers"]), {"mcp-atlassian", "projects"})   # local e nome invalido ficam de fora
        self.assertEqual(c["mcp_servers"]["projects"]["headers"]["Authorization"], 'Bearer a"b\\c')
        for evento in ("Stop", "SessionEnd"):
            cmd = c["hooks"][evento][0]["hooks"][0]["command"]
            self.assertTrue(cmd.startswith('"') and '.exe" -I "' in cmd and cmd.endswith('gancho.py"'))

    def test_rodar_de_novo_troca_o_bloco_sem_duplicar(self):
        with open(self.config, "w", encoding="utf-8") as f:
            f.write(instalar.montar_config_grok())
        segunda = instalar.montar_config_grok()
        self.assertEqual(segunda.count(instalar.INICIO_BLOCO), 1)
        with open(self.config, encoding="utf-8") as f:
            self.assertEqual(f.read(), segunda)

    def test_nao_duplica_tabela_que_o_usuario_ja_declarou_nem_grava_toml_quebrado(self):
        with open(self.config, "w", encoding="utf-8") as f:
            f.write('[plugins]\nenabled = ["outro"]\n')
        self.assertIsNone(instalar.montar_config_grok())
        with open(self.config, "w", encoding="utf-8") as f:
            f.write('[cli\n')
        self.assertIsNone(instalar.montar_config_grok())


class TestSettingsClaude(unittest.TestCase):
    def test_liga_os_hooks_sem_mexer_nos_outros_e_nao_duplica(self):
        claude = tempfile.mkdtemp()
        with open(os.path.join(claude, "settings.json"), "w", encoding="utf-8") as f:
            json.dump({"statusLine": {"type": "command", "command": "meu"},
                       "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "outro.sh"}]}]}}, f)
        with mock.patch.object(instalar, "CLAUDE", claude):
            s = instalar.montar_settings_claude()
            self.assertEqual(s["statusLine"]["command"], "meu")              # nunca troca o status line de quem tem
            self.assertEqual(s["hooks"]["Stop"][0]["hooks"][0]["command"], "outro.sh")
            for evento in instalar.EVENTOS_CLAUDE:
                self.assertIn("gancho.py", s["hooks"][evento][-1]["hooks"][0]["command"])
            with open(os.path.join(claude, "settings.json"), "w", encoding="utf-8") as f:
                json.dump(s, f)
            self.assertIsNone(instalar.montar_settings_claude())            # segunda vez: nada muda


if __name__ == "__main__":
    unittest.main()
