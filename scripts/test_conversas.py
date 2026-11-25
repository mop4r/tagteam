import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

import antigravity_db as antigravity
import claude_jsonl
import pb

CASCADE_M = "11111111-1111-4111-8111-111111111111"
TRAJ_M = "22222222-2222-4222-8222-222222222222"


def linha(**kw):
    base = {"sessionId": "s", "timestamp": "2026-11-20T10:00:00.000Z", "entrypoint": "cli", "cwd": "C:/x"}
    base.update(kw)
    return json.dumps(base, ensure_ascii=False)


class TestClaudeJsonl(unittest.TestCase):
    def ler(self, linhas, quebrada=False):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            f.write("\n".join(linhas) + "\n")
            if quebrada:
                f.write('{"type":"user","message":{"content":"pela met')
        self.addCleanup(os.remove, f.name)
        return claude_jsonl.ler_conversa(f.name, "s")

    def test_conversa_tipica(self):
        c = self.ler([
            linha(type="user", message={"content": "<system-reminder>lixo</system-reminder>arrume o bug"}),
            linha(type="assistant", message={"content": [{"type": "thinking", "thinking": "x"},
                                                         {"type": "text", "text": "Vou olhar."}]}),
            linha(type="assistant", message={"content": [{"type": "tool_use", "name": "Bash",
                                                          "input": {"command": "ls", "description": "Lista arquivos"}}]}),
            linha(type="user", message={"content": [{"type": "tool_result", "content": "a b"}]}),
            linha(type="assistant", message={"content": [{"type": "text", "text": "Pronto."}]}),
            linha(type="ai-title", aiTitle="Bug do arquivo"),
        ], quebrada=True)
        self.assertEqual([t.papel for t in c.turnos], ["usuario", "assistente"])
        self.assertEqual(c.turnos[0].texto, "arrume o bug")
        self.assertIn("Vou olhar.", c.turnos[1].texto)
        self.assertIn("`[Bash]` Lista arquivos", c.turnos[1].texto)
        self.assertIn("Pronto.", c.turnos[1].texto)
        self.assertNotIn("x", c.turnos[1].texto.replace("Bash", "").replace("Pronto", "").replace("arquivos", ""))
        self.assertEqual(c.titulo, "Bug do arquivo")
        self.assertEqual(c.entrypoint, "cli")

    def test_titulo_proprio_vence_e_sem_titulo_usa_primeira_linha(self):
        c = self.ler([linha(type="user", message={"content": "primeira\nsegunda"}),
                      linha(type="ai-title", aiTitle="IA"), linha(type="custom-title", customTitle="Meu")])
        self.assertEqual(c.titulo, "Meu")
        c = self.ler([linha(type="user", message={"content": "primeira\nsegunda"})])
        self.assertEqual(c.titulo, "primeira")

    def test_conversa_que_continuou_em_outro_arquivo_ganha_marca_no_titulo(self):
        # a continuacao tem o mesmo titulo; sem a marca as outras IAs mostram duas iguais e a antiga parece cortada
        c = self.ler([linha(type="user", message={"content": "oi"}), linha(type="ai-title", aiTitle="T" * 80),
                      linha(type="continued-in", continuedInSessionId="outra")])
        self.assertTrue(c.titulo.endswith(claude_jsonl.SUFIXO_CONTINUA))
        self.assertLessEqual(len(c.titulo), 80)
        c = self.ler([linha(type="user", message={"content": "oi"}), linha(type="ai-title", aiTitle="Bug")])
        self.assertEqual(c.titulo, "Bug")

    def test_pula_subagente_meta_e_comando_vira_texto(self):
        c = self.ler([
            linha(type="user", isSidechain=True, message={"content": "do subagente"}),
            linha(type="user", isMeta=True, message={"content": "meta"}),
            linha(type="user", message={"content": "<command-name>/model</command-name><command-args>opus</command-args>"}),
        ])
        self.assertEqual([t.texto for t in c.turnos], ["/model opus"])

    def test_texto_enorme_e_cortado(self):
        c = self.ler([linha(type="user", message={"content": "a" * 50_000})])
        self.assertLess(len(c.turnos[0].texto), 13_000)
        self.assertIn("cortado", c.turnos[0].texto)

    def test_arquivo_vazio(self):
        c = self.ler([])
        self.assertEqual(c.turnos, [])
        self.assertEqual(c.titulo, "Conversa do Claude")


def molde_falso(pasta):
    ts = pb.timestamp(1795525259, 5)
    meta_u = pb.gravar([[1, 2, ts], [3, 0, 4], [12, 2, b"exec"],
                        [20, 2, pb.gravar([[1, 2, TRAJ_M.encode()], [4, 2, CASCADE_M.encode()]])], [35, 2, b"h"]])
    usuario = pb.gravar([[1, 0, 14], [4, 0, 3], [5, 2, meta_u],
                         [19, 2, pb.gravar([[2, 2, b"oi"], [3, 2, pb.gravar([[1, 2, b"oi"]])], [12, 2, b"cfg"], [19, 2, b"u"]])]])
    meta_r = pb.gravar([[1, 2, ts], [3, 0, 2], [9, 2, b"uso"], [20, 2, pb.gravar([[1, 2, TRAJ_M.encode()], [2, 0, 7]])]])
    resposta = pb.gravar([[1, 0, 15], [4, 0, 3], [5, 2, meta_r],
                          [20, 2, pb.gravar([[1, 2, b"Oi!"], [8, 2, b"Oi!"], [14, 2, b"assinatura"]])]])
    meta_t = pb.gravar([[2, 2, ts], [6, 2, CASCADE_M.encode()], [15, 2, b"opaco"]])
    schema = [
        "CREATE TABLE trajectory_meta (trajectory_id text, cascade_id text, trajectory_type integer, source integer, PRIMARY KEY (trajectory_id))",
        "CREATE TABLE steps (idx integer, step_type integer NOT NULL DEFAULT 0, status integer NOT NULL DEFAULT 0, "
        "has_subtrajectory numeric NOT NULL DEFAULT false, metadata blob, error_details blob, permissions blob, "
        "task_details blob, render_info blob, step_payload blob, step_format integer NOT NULL DEFAULT 0, PRIMARY KEY (idx))",
        "CREATE TABLE trajectory_metadata_blob (id text DEFAULT 'main', data blob, PRIMARY KEY (id))",
    ]
    os.makedirs(os.path.join(pasta, "conversations"))
    c = sqlite3.connect(os.path.join(pasta, "conversation_summaries.db"))
    c.execute("CREATE TABLE conversation_summaries (conversation_id text PRIMARY KEY, title text, preview text, step_count integer, "
              "last_modified_time datetime, workspace_uris text, status text, source text, project_id text, agent_name text, "
              "parent_conversation_id text, nesting_depth integer, battle_id text, winning_conversation_id text, "
              "not_fully_idle integer, killed integer, last_user_input_time datetime, last_user_input_step_index integer, "
              "app_data_dir text, raw_summary blob, group_id text)")
    c.commit()
    c.close()
    return antigravity.Molde(schema, usuario, resposta, meta_t, 4, 1, pb.gravar([[1, 2, b"t"], [2, 0, 2]]),
                             "proj", '["file:///w"]', CASCADE_M, TRAJ_M)


class TestAntigravity(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.mkdtemp()
        self.molde = molde_falso(self.pasta)
        q = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)
        self.conv = claude_jsonl.Conversa("sessao-1", titulo="Título ação 🙂", entrypoint="cli", turnos=[
            claude_jsonl.Turno("usuario", "pergunta com acento é 🙂", q),
            claude_jsonl.Turno("assistente", "resposta\n\n`[Bash]` ls", q.replace(minute=1)),
            claude_jsonl.Turno("usuario", "segunda", q.replace(minute=2)),
        ])

    def gravar(self):
        cascade, traj, meta = antigravity.gravar_conversa(self.conv, self.molde, base=self.pasta, marca=(10, 1.5))
        antigravity.gravar_resumo(self.conv, self.molde, cascade, traj, meta, base=self.pasta)
        return cascade, traj

    def test_grava_passos_com_texto_ids_e_horarios(self):
        cascade, traj = self.gravar()
        c = sqlite3.connect(os.path.join(self.pasta, "conversations", cascade + ".db"))
        passos = c.execute("select idx, step_type, metadata, step_payload from steps order by idx").fetchall()
        self.assertEqual([p[1] for p in passos], [14, 15, 14])
        self.assertEqual(pb.obter(passos[0][3], [19, 2]).decode(), "pergunta com acento é 🙂")
        self.assertEqual(pb.obter(passos[0][3], [19, 3, 1]).decode(), "pergunta com acento é 🙂")
        self.assertEqual(pb.obter(passos[1][3], [20, 1]).decode(), "resposta\n\n`[Bash]` ls")
        self.assertIsNone(pb.obter(passos[1][3], [20, 14]))          # assinatura do molde nao vai junto
        self.assertIsNone(pb.obter(passos[1][2], [9]))
        self.assertEqual(pb.obter(passos[2][2], [20, 2]), 2)
        self.assertEqual(pb.obter(passos[1][2], [1, 1]), int(self.conv.turnos[1].quando.timestamp()))
        self.assertEqual(passos[0][2], pb.obter(passos[0][3], [5]))
        tudo = b"".join(p[2] + p[3] for p in passos)
        self.assertNotIn(CASCADE_M.encode(), tudo)
        self.assertNotIn(TRAJ_M.encode(), tudo)
        self.assertIn(traj.encode(), tudo)
        self.assertEqual(c.execute("select trajectory_id, cascade_id from trajectory_meta").fetchone(), (traj, cascade))
        self.assertEqual(c.execute("pragma user_version").fetchone()[0], 1)   # sem isso o app recusa o arquivo
        self.assertEqual(c.execute("pragma page_size").fetchone()[0], 32768)
        c.close()

    def test_indice_com_titulo_e_situacao(self):
        cascade, _ = self.gravar()
        c = sqlite3.connect(os.path.join(self.pasta, "conversation_summaries.db"))
        titulo, passos, ultimo, raw, proj = c.execute(
            "select title, step_count, last_user_input_step_index, raw_summary, project_id from conversation_summaries").fetchone()
        c.close()
        self.assertEqual((titulo, passos, ultimo, proj), ("Título ação 🙂", 3, 2, "proj"))
        self.assertEqual(pb.obter(raw, [1]).decode(), "Título ação 🙂")
        self.assertEqual(pb.obter(raw, [15, 1]).decode(), "Título ação 🙂")
        self.assertIsNone(pb.obter(raw, [17, 15]))
        self.assertEqual(antigravity.situacao(cascade, base=self.pasta), (3, 3, 10, 1.5))

    def test_rodar_de_novo_atualiza_sem_duplicar(self):
        c1, t1 = self.gravar()
        self.conv.turnos.append(claude_jsonl.Turno("assistente", "mais", self.conv.turnos[-1].quando))
        c2, t2 = self.gravar()
        self.assertEqual((c1, t1), (c2, t2))
        self.assertEqual(len(os.listdir(os.path.join(self.pasta, "conversations"))), 1)
        c = sqlite3.connect(os.path.join(self.pasta, "conversation_summaries.db"))
        self.assertEqual(c.execute("select count(*), max(step_count) from conversation_summaries").fetchone(), (1, 4))
        c.close()

    def test_id_estavel_por_sessao_e_versao(self):
        self.assertEqual(antigravity.id_para_sessao("abc"), antigravity.id_para_sessao("abc"))
        self.assertNotEqual(antigravity.id_para_sessao("abc"), antigravity.id_para_sessao("abd"))
        self.assertNotEqual(antigravity.id_para_sessao("abc", 1), antigravity.id_para_sessao("abc"))
        # app e CLI nunca usam o mesmo id (o indice do CLI espelha as linhas do app)
        self.assertNotEqual(antigravity.id_para_sessao("abc", 0, "antigravity-cli"), antigravity.id_para_sessao("abc"))

    def test_atualizar_com_app_fechado_troca_versao_sem_duplicar(self):
        import sincronizar
        e1 = sincronizar.importar(self.conv, self.molde, (1, 1.0), None, None, self.pasta)
        self.assertEqual(e1["versao"], 0)
        self.conv.turnos.append(claude_jsonl.Turno("assistente", "mais", self.conv.turnos[-1].quando))
        e2 = sincronizar.importar(self.conv, self.molde, (2, 2.0), None, e1, self.pasta)
        self.assertEqual(e2["versao"], 1)
        self.assertNotEqual(e1["cascade"], e2["cascade"])
        self.assertEqual(os.listdir(os.path.join(self.pasta, "conversations")), [e2["cascade"] + ".db"])
        # titulo vai na anotacao (o app e o CLI refazem o indice a partir dela); a da versao velha sai
        self.assertEqual(os.listdir(os.path.join(self.pasta, "annotations")), [e2["cascade"] + ".pbtxt"])
        with open(os.path.join(self.pasta, "annotations", e2["cascade"] + ".pbtxt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), 'title:"Título ação 🙂" tags:"claude"')
        self.assertTrue(antigravity.no_indice(e2["cascade"], self.pasta))
        self.assertFalse(antigravity.no_indice(e1["cascade"], self.pasta))
        self.assertEqual(antigravity.situacao(e2["cascade"], self.pasta), (4, 4, 2, 2.0))

    def test_anotacao_escapa_aspas_e_barra(self):
        antigravity.gravar_anotacao("x", 'diz "oi"\\ali\nfim', self.pasta, ())
        with open(os.path.join(self.pasta, "annotations", "x.pbtxt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), 'title:"diz \\"oi\\"\\\\ali fim"')


class TestMoldePadrao(unittest.TestCase):
    """PC novo: o molde guardado na skill vem com a pasta do usuario de outro PC e e adaptado."""

    def test_salvar_e_carregar_trocando_a_pasta_do_usuario(self):
        pasta = tempfile.mkdtemp()
        molde = molde_falso(pasta)
        ws_antigo = "file:///c%3A/Users/Jane%20Doe/workspace"
        molde.meta_trajetoria = pb.trocar(molde.meta_trajetoria, [7], ws_antigo)
        molde.meta_trajetoria = pb.trocar(molde.meta_trajetoria, [1, 1], "file:///c:/Users/Jane%20Doe/workspace")
        molde.passo_usuario = pb.trocar(molde.passo_usuario, [19, 40, 3], "C:\\Users\\Jane Doe\\.gemini\\skills")
        molde.workspaces = json.dumps([ws_antigo])
        molde.passo_resposta = pb.trocar(molde.passo_resposta, [5, 9], b"uso")
        with mock.patch.object(antigravity, "PASTA_MOLDES", os.path.join(pasta, "moldes")):
            antigravity.salvar_molde(molde, "antigravity-cli", home="C:\\Users\\Jane Doe")
            with mock.patch("os.path.expanduser", side_effect=lambda p: p.replace("~", "C:\\Users\\bob")):
                m = antigravity.molde_padrao("antigravity-cli")
        self.assertEqual(pb.obter(m.meta_trajetoria, [7]).decode(), "file:///c%3A/Users/bob/workspace")
        self.assertEqual(pb.obter(m.meta_trajetoria, [1, 1]).decode(), "file:///c:/Users/bob/workspace")
        self.assertEqual(pb.obter(m.passo_usuario, [19, 40, 3]).decode(), "C:\\Users\\bob\\.gemini\\skills")
        self.assertEqual(m.workspaces, json.dumps(["file:///c%3A/Users/bob/workspace"]))
        self.assertIsNone(pb.obter(m.passo_resposta, [20, 14]))       # assinatura do modelo nao viaja
        self.assertIsNone(pb.obter(m.passo_resposta, [5, 9]))
        self.assertEqual(pb.obter(m.passo_usuario, [19, 2]), b"oi")   # o resto fica igual

    def test_indice_que_nao_existe_nao_quebra(self):
        pasta = tempfile.mkdtemp()
        molde = molde_falso(pasta)
        os.remove(os.path.join(pasta, "conversation_summaries.db"))
        q = datetime(2026, 11, 20, tzinfo=timezone.utc)
        conv = claude_jsonl.Conversa("s", titulo="t", turnos=[claude_jsonl.Turno("usuario", "a", q)])
        cascade, traj, meta = antigravity.gravar_conversa(conv, molde, base=pasta)
        antigravity.gravar_resumo(conv, molde, cascade, traj, meta, base=pasta)   # sem erro
        import para_claude
        self.assertEqual(para_claude.ler_conversa_antigravity(cascade, pasta).turnos[0].texto, "a")


class TestEstado(unittest.TestCase):
    def test_formato_antigo_e_lixo(self):
        import sincronizar
        pasta = tempfile.mkdtemp()
        antigo = sincronizar.ESTADO
        sincronizar.ESTADO = os.path.join(pasta, "estado.json")
        try:
            with open(sincronizar.ESTADO, "w", encoding="utf-8") as f:
                json.dump({"importadas": {"s1": CASCADE_M, "s2": {"cascade": "x", "versao": 1},
                                          "s3": {"cascade": TRAJ_M, "versao": 2}}}, f)
            imp = sincronizar.ler_estado()["importadas"]       # formato antigo vira o do app
            self.assertEqual(imp, {"antigravity": {"s1": {"cascade": CASCADE_M, "versao": 0},
                                                   "s3": {"cascade": TRAJ_M, "versao": 2}}})
            with open(sincronizar.ESTADO, "w", encoding="utf-8") as f:
                json.dump({"importadas": {"antigravity-cli": {"s4": {"cascade": CASCADE_M, "versao": 1}}}}, f)
            self.assertEqual(sincronizar.ler_estado()["importadas"],
                             {"antigravity-cli": {"s4": {"cascade": CASCADE_M, "versao": 1}}})
            with open(sincronizar.ESTADO, "w", encoding="utf-8") as f:
                f.write("{quebrado")
            self.assertEqual(sincronizar.ler_estado(), {"importadas": {}, "exportadas": {}, "importadas_grok": {},
                                                        "exportadas_grok": {}, "importadas_codex": {},
                                                        "exportadas_codex": {}, "vistas_codex": {},
                                                        "importadas_cursor": {}, "exportadas_cursor": {}})
        finally:
            sincronizar.ESTADO = antigo


class TestParaClaude(unittest.TestCase):
    """Ida e volta: Claude -> Antigravity -> Claude preserva os turnos; a sessao gerada e reconhecida como espelho."""

    def setUp(self):
        self.pasta = tempfile.mkdtemp()
        self.molde = molde_falso(self.pasta)
        q = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)
        self.conv = claude_jsonl.Conversa("sessao-x", titulo="Meu título", entrypoint="cli", turnos=[
            claude_jsonl.Turno("usuario", "pergunta é 🙂", q),
            claude_jsonl.Turno("assistente", "resposta", q.replace(minute=1)),
            claude_jsonl.Turno("usuario", "segunda", q.replace(minute=2)),
            claude_jsonl.Turno("assistente", "fim", q.replace(minute=3)),
        ])
        cascade, traj, meta = antigravity.gravar_conversa(self.conv, self.molde, base=self.pasta)
        antigravity.gravar_resumo(self.conv, self.molde, cascade, traj, meta, base=self.pasta)
        self.cascade = cascade

    def test_le_conversa_do_antigravity(self):
        import para_claude
        c = para_claude.ler_conversa_antigravity(self.cascade, self.pasta)
        self.assertEqual([(t.papel, t.texto) for t in c.turnos], [(t.papel, t.texto) for t in self.conv.turnos])
        self.assertEqual(c.turnos[1].quando, self.conv.turnos[1].quando)
        self.assertEqual(c.titulo, "Meu título")

    def test_sessao_gerada_volta_igual_e_e_espelho(self):
        import para_claude
        import sincronizar
        c = para_claude.ler_conversa_antigravity(self.cascade, self.pasta)
        sessao = para_claude.id_sessao(self.cascade)
        destino = para_claude.gravar_sessao(c, sessao, os.path.join(self.pasta, "proj"), "C:\\w", "2.1.0")
        with open(destino, encoding="utf-8") as f:
            linhas = [json.loads(x) for x in f]
        msgs = [x for x in linhas if x.get("type") in ("user", "assistant")]
        self.assertEqual([m["type"] for m in msgs], ["user", "assistant", "user", "assistant"])
        self.assertIsNone(msgs[0]["parentUuid"])
        self.assertEqual([m["parentUuid"] for m in msgs[1:]], [m["uuid"] for m in msgs[:-1]])
        self.assertTrue(all(m["sessionId"] == sessao and m["entrypoint"] == para_claude.ENTRYPOINT for m in msgs))
        self.assertIn({"type": "ai-title", "aiTitle": "[Antigravity] Meu título", "sessionId": sessao}, linhas)
        volta = claude_jsonl.ler_conversa(destino, sessao)
        self.assertEqual([(t.papel, t.texto) for t in volta.turnos], [(t.papel, t.texto) for t in self.conv.turnos])
        estado = {"exportadas": {self.cascade: {"sessao": sessao, "versao": 0, "passos": 4, "turnos": 4}}}
        self.assertTrue(sincronizar._espelho_do_antigravity(volta, sessao, estado))
        volta.turnos.append(claude_jsonl.Turno("usuario", "continuei no Claude", volta.turnos[-1].quando))
        self.assertFalse(sincronizar._espelho_do_antigravity(volta, sessao, estado))
        self.assertFalse(sincronizar._espelho_do_antigravity(volta, sessao, {"exportadas": {}}))

    def test_assinatura_muda_quando_o_ultimo_passo_termina_sem_passo_novo(self):
        c = sqlite3.connect(os.path.join(self.pasta, "conversations", self.cascade + ".db"))
        c.execute("update steps set status=2, step_payload=x'0a' where idx=3")     # resposta ainda sendo escrita
        c.commit()
        antes = antigravity.assinatura(self.cascade, self.pasta)
        c.execute("update steps set status=3, step_payload=x'0a0b0c' where idx=3")  # terminou: mesma contagem
        c.commit()
        c.close()
        self.assertNotEqual(antes, antigravity.assinatura(self.cascade, self.pasta))
        self.assertEqual(antigravity.assinatura(self.cascade, self.pasta), antigravity.assinatura(self.cascade, self.pasta))

    def test_respostas_seguidas_viram_uma(self):
        import para_claude
        q = self.conv.turnos[0].quando
        self.conv.turnos = [claude_jsonl.Turno("usuario", "a", q), claude_jsonl.Turno("assistente", "b", q),
                            claude_jsonl.Turno("assistente", "c", q)]
        self.conv.sessao = "outra"
        cascade, _, _ = antigravity.gravar_conversa(self.conv, self.molde, base=self.pasta)
        c = para_claude.ler_conversa_antigravity(cascade, self.pasta)
        self.assertEqual([(t.papel, t.texto) for t in c.turnos], [("usuario", "a"), ("assistente", "b\n\nc")])

    def test_pasta_do_projeto_reaproveita_a_existente_com_outra_caixa(self):
        import para_claude
        raiz = os.path.join(self.pasta, "projects")
        os.makedirs(os.path.join(raiz, "c--w"))
        self.assertEqual(os.path.basename(para_claude.pasta_do_projeto("C:\\w", raiz)), "c--w")
        self.assertEqual(os.path.basename(para_claude.pasta_do_projeto("C:\\Meu Dir", raiz)), "C--Meu-Dir")


class TestApagarSemIndice(unittest.TestCase):
    def test_sem_indice_apaga_conversa_e_nao_cria_indice(self):
        base = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, base)
        os.makedirs(os.path.join(base, "conversations"))
        db = os.path.join(base, "conversations", CASCADE_M + ".db")
        open(db, "wb").close()
        antigravity.apagar_com_app_fechado(CASCADE_M, base)
        self.assertFalse(os.path.exists(db))
        self.assertFalse(os.path.exists(os.path.join(base, "conversation_summaries.db")))

    def test_indice_vazio_sem_tabela_nao_falha(self):
        base = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, base)
        indice = os.path.join(base, "conversation_summaries.db")
        open(indice, "wb").close()
        antigravity.apagar_com_app_fechado(CASCADE_M, base)
        self.assertEqual(os.path.getsize(indice), 0)

class TestProjetoDoApp(unittest.TestCase):
    """The app changes the project id when the folder is recreated; an old template sent copies to a missing project."""

    def projetos(self, home, itens):
        pasta = os.path.join(home, ".gemini", "config", "projects")
        os.makedirs(pasta)
        for pid, recurso in itens:
            with open(os.path.join(pasta, pid + ".json"), "w", encoding="utf-8") as f:
                json.dump({"id": pid, "projectResources": {"resources": [recurso]}}, f)

    def test_acha_projeto_no_formato_gitfolder(self):
        home = tempfile.mkdtemp()
        ws = "file:///c%3A/Users/jane/workspace"
        self.projetos(home, [("novo-id", {"gitFolder": {"folderUri": ws, "allowWrite": True}}),
                             ("outro", {"folderUri": "file:///c%3A/outra"})])
        with mock.patch("os.path.expanduser", side_effect=lambda p: p.replace("~", home)):
            self.assertEqual(antigravity._projeto_da_workspace(ws.upper()), "novo-id")
            self.assertEqual(antigravity._projeto_da_workspace("file:///c%3A/outra"), "outro")
            self.assertEqual(antigravity._projeto_da_workspace("file:///nada"), "")

    def test_molde_com_projeto_sumido_vai_para_o_projeto_atual_da_workspace(self):
        home, pasta = tempfile.mkdtemp(), tempfile.mkdtemp()
        ws = "file:///c%3A/Users/jane/workspace"
        velho, novo = "aaaaaaaa-0000-4000-8000-000000000001", "bbbbbbbb-0000-4000-8000-000000000002"
        self.projetos(home, [(novo, {"gitFolder": {"folderUri": ws}})])
        m = molde_falso(pasta)
        m.projeto, m.workspaces = velho, json.dumps([ws])
        m.resumo = m.resumo + velho.encode()
        m.meta_trajetoria = m.meta_trajetoria + velho.encode()
        with mock.patch("os.path.expanduser", side_effect=lambda p: p.replace("~", home)):
            antigravity._ajustar_projeto(m)
        self.assertEqual(m.projeto, novo)
        self.assertNotIn(velho.encode(), m.resumo)
        self.assertNotIn(velho.encode(), m.meta_trajetoria)

    def test_projeto_que_existe_ou_cli_fica_igual(self):
        home = tempfile.mkdtemp()
        ws = "file:///c%3A/Users/jane/workspace"
        self.projetos(home, [("atual", {"gitFolder": {"folderUri": ws}}), ("outro", {"gitFolder": {"folderUri": "x"}})])
        for pid in ("outro", "default-cli-project", ""):
            m = molde_falso(tempfile.mkdtemp())
            m.projeto, m.workspaces = pid, json.dumps([ws])
            with mock.patch("os.path.expanduser", side_effect=lambda p: p.replace("~", home)):
                antigravity._ajustar_projeto(m)
            self.assertEqual(m.projeto, pid)

    def test_molde_guardado_de_outro_pc_troca_o_projeto_tambem_dentro_dos_blobs(self):
        """The stored template comes from another PC: its project id is inside the summary and metadata, not only the field."""
        home, pasta = tempfile.mkdtemp(), tempfile.mkdtemp()
        ws = "file:///c%3A/Users/jane/workspace"
        velho, novo = "aaaaaaaa-0000-4000-8000-000000000001", "bbbbbbbb-0000-4000-8000-000000000002"
        self.projetos(home, [(novo, {"gitFolder": {"folderUri": ws}})])
        m = molde_falso(pasta)
        m.projeto, m.workspaces = velho, json.dumps([ws])
        m.resumo, m.meta_trajetoria = m.resumo + velho.encode(), m.meta_trajetoria + velho.encode()
        with mock.patch.object(antigravity, "PASTA_MOLDES", os.path.join(pasta, "moldes")), \
                mock.patch("os.path.expanduser", side_effect=lambda p: p.replace("~", home)), \
                mock.patch.object(antigravity, "_achar_molde_local", side_effect=RuntimeError("sem nativa")):
            antigravity.salvar_molde(m, "antigravity", home=home)
            r = antigravity.achar_molde(os.path.join(pasta, "antigravity"))
        self.assertEqual(r.projeto, novo)
        self.assertNotIn(velho.encode(), r.resumo)
        self.assertNotIn(velho.encode(), r.meta_trajetoria)


class TestApagarTiraDoCache(unittest.TestCase):
    """Deleted with the app closed must also leave agyhub_summaries_proto.pb: the app rebuilds the row from it on
    start, and the conversation becomes a ghost in the list ("could not be loaded because its data was not found")."""

    def cache(self, pasta, ids):
        caminho = os.path.join(pasta, "agyhub_summaries_proto.pb")
        with open(caminho, "wb") as f:
            f.write(pb.gravar([[1, 2, pb.gravar([[1, 2, i.encode()], [2, 2, b"resumo"]])] for i in ids]))
        return caminho

    def ids_no_cache(self, caminho):
        with open(caminho, "rb") as f:
            return [pb.obter(v, [1]).decode() for (_, _, v) in pb.ler(f.read())]

    def test_tira_so_a_apagada(self):
        pasta = tempfile.mkdtemp()
        caminho = self.cache(pasta, [CASCADE_M, TRAJ_M])
        antigravity.apagar_com_app_fechado(CASCADE_M, base=pasta)
        self.assertEqual(self.ids_no_cache(caminho), [TRAJ_M])

    def test_sem_cache_ou_cache_estragado_nao_falha_nem_mexe(self):
        pasta = tempfile.mkdtemp()
        antigravity.apagar_com_app_fechado(CASCADE_M, base=pasta)          # no cache
        caminho = os.path.join(pasta, "agyhub_summaries_proto.pb")
        with open(caminho, "wb") as f:
            f.write(b"\xff\xff\xff")
        antigravity.apagar_com_app_fechado(CASCADE_M, base=pasta)
        with open(caminho, "rb") as f:
            self.assertEqual(f.read(), b"\xff\xff\xff")


class TestNenhumModuloComNomeDaBibliotecaPadrao(unittest.TestCase):
    def test_nenhum_script_da_pasta_tem_nome_de_modulo_padrao(self):
        # um script local com nome de modulo padrao (ex.: antigravity.py) so e achado quando a pasta esta no
        # sys.path; com python -I ou "python -m unittest scripts/x.py" o import pega o da biblioteca padrao
        # (o antigravity padrao abre a xkcd 353 no navegador)
        import sys
        pasta = os.path.dirname(os.path.abspath(__file__))
        nomes = {os.path.splitext(n)[0] for n in os.listdir(pasta) if n.endswith(".py")}
        self.assertEqual(sorted(nomes & set(sys.stdlib_module_names)), [])


if __name__ == "__main__":
    unittest.main()
