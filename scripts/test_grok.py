import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

import claude_jsonl
import grok
import para_claude
import sincronizar

Q = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)


def conversa():
    return claude_jsonl.Conversa("sessao-g", titulo="Título ação 🙂", entrypoint="cli", cwd="c:\\Users\\X\\workspace",
                                 turnos=[claude_jsonl.Turno("usuario", "pergunta é 🙂", Q),
                                         claude_jsonl.Turno("assistente", "resposta\n\n`[Bash]` ls", Q.replace(minute=1)),
                                         claude_jsonl.Turno("usuario", "segunda", Q.replace(minute=2)),
                                         claude_jsonl.Turno("assistente", "fim", Q.replace(minute=3))])


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        p = mock.patch.object(grok, "base", lambda: os.path.join(self.home, ".grok"))
        p.start()
        self.addCleanup(p.stop)


class TestGrok(Base):
    def test_gravar_e_ler_de_volta(self):
        pasta = grok.gravar_sessao(conversa(), "11111111-2222-5333-8444-555555555555", "C:\\w", {"sessao": "s"})
        self.assertTrue(pasta.endswith(os.path.join("C%3A%5Cw", "11111111-2222-5333-8444-555555555555")))
        volta = grok.ler_sessao(pasta)
        self.assertEqual([(t.papel, t.texto) for t in volta.turnos], [(t.papel, t.texto) for t in conversa().turnos])
        self.assertEqual(volta.turnos[1].quando, Q.replace(minute=1))
        with open(os.path.join(pasta, "summary.json"), encoding="utf-8") as f:
            s = json.load(f)
        self.assertEqual((s["info"]["cwd"], s["generated_title"], s["title_is_manual"]), ("C:\\w", "Título ação 🙂", True))
        m = grok.marca(pasta)
        self.assertEqual((m["sessao"], m["turnos"]), ("s", 4))
        self.assertEqual(m["bytes_updates"], os.path.getsize(os.path.join(pasta, "updates.jsonl")))
        self.assertEqual(grok.sessoes(), [(pasta, "11111111-2222-5333-8444-555555555555")])

    def test_ler_sessao_nativa_junta_pedacos_e_resume_ferramenta(self):
        pasta = os.path.join(grok.base(), "sessions", "C%3A%5Cw", "nativa")
        os.makedirs(pasta)
        with open(os.path.join(pasta, "summary.json"), "w", encoding="utf-8") as f:
            json.dump({"info": {"id": "nativa", "cwd": "C:\\w"}, "generated_title": "Título do Grok"}, f)

        def linha(us, upd):
            return json.dumps({"timestamp_us": us, "params": {"update": upd}}) + "\n"
        with open(os.path.join(pasta, "updates.jsonl"), "w", encoding="utf-8") as f:
            f.write(linha(1_800_000_000_000_000, {"sessionUpdate": "user_message_chunk", "content": {"type": "text", "text": "o"}}))
            f.write(linha(1_800_000_000_000_001, {"sessionUpdate": "user_message_chunk", "content": {"type": "text", "text": "i"}}))
            f.write(linha(1_800_000_001_000_000, {"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "pensando"}}))
            f.write(linha(1_800_000_002_000_000, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Vou "}}))
            f.write(linha(1_800_000_002_000_001, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "olhar."}}))
            f.write(linha(1_800_000_003_000_000, {"sessionUpdate": "tool_call", "title": "run_terminal_command",
                                                  "rawInput": {"command": "dir", "description": "Lista arquivos"}}))
            f.write(linha(1_800_000_004_000_000, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Pronto."}}))
            f.write('{"quebrada": ')
        c = grok.ler_sessao(pasta)
        self.assertEqual([(t.papel, t.texto) for t in c.turnos],
                         [("usuario", "oi"), ("assistente", "Vou olhar.\n\n`[run_terminal_command]` Lista arquivos\n\nPronto.")])
        self.assertEqual((c.titulo, c.cwd, c.sessao), ("Título do Grok", "C:\\w", "nativa"))
        self.assertIsNone(grok.marca(pasta))

    def test_apagar_so_apaga_sessao_importada(self):
        nativa = os.path.join(grok.base(), "sessions", "g", "nativa")
        os.makedirs(nativa)
        grok.apagar(nativa)
        self.assertTrue(os.path.isdir(nativa))
        pasta = grok.gravar_sessao(conversa(), "11111111-2222-5333-8444-555555555556", "C:\\w", {"sessao": "s"})
        grok.apagar(pasta)
        self.assertFalse(os.path.isdir(pasta))

    def test_sessao_do_claude_vinda_do_grok_tem_marca_e_titulo_proprios(self):
        sessao = para_claude.id_sessao("g1", 0, "grok")
        self.assertNotEqual(sessao, para_claude.id_sessao("g1"))
        linhas = para_claude.linhas_sessao(conversa(), sessao, "C:\\w", "2.1.0", "grok")
        msgs = [x for x in linhas if x.get("type") in ("user", "assistant")]
        self.assertTrue(all(m["entrypoint"] == "grok-import" for m in msgs))
        self.assertIn({"type": "ai-title", "aiTitle": "[Grok] Título ação 🙂", "sessionId": sessao}, linhas)


class TestCwdLocal(unittest.TestCase):
    def test_pasta_de_outro_pc_vira_a_deste(self):
        existe = {"C:\\Users\\Jane Doe\\workspace", "C:\\Users\\Jane Doe\\workspace\\repos", "D:\\dados"}
        with mock.patch("os.path.expanduser", side_effect=lambda p: p.replace("~", "C:\\Users\\Jane Doe")), \
                mock.patch("os.path.isdir", side_effect=lambda p: p in existe):
            self.assertEqual(sincronizar.cwd_local("C:\\Users\\bob\\workspace"), "C:\\Users\\Jane Doe\\workspace")
            self.assertEqual(sincronizar.cwd_local("c:\\Users\\Jane Doe\\workspace\\repos"),
                             "C:\\Users\\Jane Doe\\workspace\\repos")
            self.assertEqual(sincronizar.cwd_local("D:\\dados"), "D:\\dados")
            self.assertEqual(sincronizar.cwd_local(""), sincronizar.WORKSPACE)
            # pasta que mudou de lugar ou so existe no outro PC: o Grok recusa retomar, vai para a workspace
            self.assertEqual(sincronizar.cwd_local("C:\\Users\\Jane Doe\\Documents\\old-project"),
                             sincronizar.WORKSPACE)


class TestEspelhos(unittest.TestCase):
    def test_espelho_por_origem(self):
        conv = conversa()
        conv.entrypoint = "grok-import"
        estado = {"exportadas": {}, "exportadas_grok": {"g1": {"sessao": "s1", "versao": 0, "turnos": 4}}}
        self.assertTrue(sincronizar._espelho(conv, "s1", estado, "grok"))
        self.assertFalse(sincronizar._espelho(conv, "s1", estado, "antigravity"))   # do Grok vai para o Antigravity
        conv.turnos.append(claude_jsonl.Turno("usuario", "continuei", Q))
        self.assertFalse(sincronizar._espelho(conv, "s1", estado, "grok"))


if __name__ == "__main__":
    unittest.main()
