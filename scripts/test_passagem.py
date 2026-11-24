import json
import os
import tempfile
import unittest
from unittest import mock

import gancho
import passagem
import statusline

AGORA = 1_800_000_000


class Base(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(gancho, "PASTA", tempfile.mkdtemp())
        p.start()
        self.addCleanup(p.stop)

    def uso(self, cinco, sete=10.0, idade=0):
        passagem.gravar_uso_claude({"rate_limits": {"five_hour": {"used_percentage": cinco, "resets_at": 111},
                                                    "seven_day": {"used_percentage": sete, "resets_at": 222}}},
                                   AGORA - idade)


class TestClaude(Base):
    def test_abaixo_do_limiar_nao_pede(self):
        self.uso(94.9)
        self.assertIsNone(passagem.decidir_claude("s1", False, AGORA))

    def test_no_limiar_pede_uma_vez_por_janela(self):
        self.uso(95.0)
        texto = passagem.decidir_claude("s1", False, AGORA)
        self.assertIn("## Handoff to Gemini or Grok", texto)
        self.assertIn("95%", texto)
        self.assertIn("5 h", texto)
        self.assertIsNone(passagem.decidir_claude("s1", False, AGORA))      # mesma janela: nao repete
        self.assertIsNotNone(passagem.decidir_claude("s2", False, AGORA))   # outra conversa: pede

    def test_janela_de_7_dias_tambem_conta(self):
        self.uso(10.0, sete=97.0)
        self.assertIn("7 day", passagem.decidir_claude("s1", False, AGORA))

    def test_leitura_velha_ou_ja_continuando_nao_pede(self):
        self.uso(99.0, idade=passagem.VALIDADE_USO_S + 1)
        self.assertIsNone(passagem.decidir_claude("s1", False, AGORA))
        self.uso(99.0)
        self.assertIsNone(passagem.decidir_claude("s1", True, AGORA))

    def test_sem_arquivo_e_id_ruim(self):
        self.assertIsNone(passagem.decidir_claude("s1", False, AGORA))
        self.uso(99.0)
        self.assertIsNone(passagem.decidir_claude("../x", False, AGORA))

    def test_hook_stop_bloqueia_e_libera_a_sincronizacao(self):
        import io
        sessao = "6e940537-9f5e-4579-aa79-c76070a1c08a"
        self.uso(96.0)
        open(os.path.join(gancho.PASTA, f"ultimo-{sessao}"), "w").close()   # dentro do intervalo
        entrada = json.dumps({"hook_event_name": "Stop", "session_id": sessao}).encode()
        saida = io.StringIO()
        with mock.patch("sys.stdin", io.TextIOWrapper(io.BytesIO(entrada))), mock.patch("sys.stdout", saida), \
                mock.patch("os.path.isdir", return_value=True), mock.patch.object(gancho, "lancar") as lancar:
            gancho.main()
        d = json.loads(saida.getvalue())
        self.assertEqual(d["decision"], "block")
        lancar.assert_not_called()
        self.assertFalse(os.path.exists(os.path.join(gancho.PASTA, f"ultimo-{sessao}")))  # proximo Stop sincroniza


class TestScriptDeVerdade(unittest.TestCase):
    """Roda os hooks como o Claude e o Antigravity rodam (python -I, processo novo): pega import que so
    funciona no teste, onde a pasta ja esta no caminho."""

    def rodar(self, script, entrada, temp):
        import subprocess
        import sys
        env = dict(os.environ, TEMP=temp, TMP=temp)
        r = subprocess.run([sys.executable, "-I", os.path.join(os.path.dirname(os.path.abspath(__file__)), script)],
                           input=json.dumps(entrada).encode(), capture_output=True, env=env, timeout=60)
        return r.stdout.decode("utf-8")

    def test_gancho_do_claude_pede_a_passagem(self):
        temp = tempfile.mkdtemp()
        pasta = os.path.join(temp, "tagteam")
        with mock.patch.object(gancho, "PASTA", pasta):
            passagem.gravar_uso_claude({"rate_limits": {"five_hour": {"used_percentage": 97}}}, __import__("time").time())
        saida = self.rodar("gancho.py", {"hook_event_name": "Stop", "session_id": "6e940537-9f5e-4579-aa79-c76070a1c08a"},
                           temp)
        self.assertEqual(json.loads(saida)["decision"], "block")

    def test_gancho_do_antigravity_responde_json(self):
        saida = self.rodar("gancho_antigravity.py", {"conversationId": "x"}, tempfile.mkdtemp())
        self.assertEqual(saida, "{}")


class TestGemini(Base):
    RESUMO = {"response": {"groups": [
        {"displayName": "Gemini Models", "buckets": [
            {"window": "weekly", "remainingFraction": 0.5, "resetTime": "a"},
            {"window": "5h", "remainingFraction": 0.04, "resetTime": "b"}]},
        {"displayName": "Claude and GPT models", "buckets": [
            {"window": "5h", "remainingFraction": 1}]}]}}

    def test_grupo_do_modelo(self):
        janela, pct, renova = passagem.uso_gemini(self.RESUMO, "gemini-4-neon-high")
        self.assertEqual((janela, round(pct), renova), ("5h", 96, "b"))
        self.assertEqual(round(passagem.uso_gemini(self.RESUMO, "claude-opus-5-5")[1]), 0)

    def test_pede_uma_vez_e_falha_da_consulta_nao_pede(self):
        texto = passagem.decidir_gemini("c1", False, "auto", lambda: self.RESUMO)
        self.assertIn("## Handoff to Claude", texto)
        self.assertIsNone(passagem.decidir_gemini("c1", False, "auto", lambda: self.RESUMO))
        self.assertIsNone(passagem.decidir_gemini("c2", False, "auto", lambda: None))
        self.assertIsNone(passagem.decidir_gemini("c3", True, "auto", lambda: self.RESUMO))


class TestStatusline(Base):
    def test_linha(self):
        d = {"model": {"display_name": "Opus 5.5"}, "context_window": {"used_percentage": 41.6},
             "rate_limits": {"five_hour": {"used_percentage": 96}, "seven_day": {"used_percentage": 20.2}}}
        self.assertEqual(statusline.linha(d), "Opus 5.5 · contexto 42% · 5h 96% ⚠ passagem · 7d 20%")
        self.assertEqual(statusline.linha({}), "")


if __name__ == "__main__":
    unittest.main()
