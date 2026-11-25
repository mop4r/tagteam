"""Codex: conversao, filtros, registro de importacao, preparo da fonte e o cliente da API (com servidor falso)."""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import claude_jsonl  # noqa: E402
import codex  # noqa: E402
import gancho  # noqa: E402
import sincronizar  # noqa: E402

T0 = 1795600000
PASTA_GANCHO = os.path.basename(gancho.PASTA)   # pasta do log dentro do TEMP


def item_usuario(texto, extra=None):
    return {"type": "userMessage", "id": "u", "content": [{"type": "text", "text": texto}] + (extra or [])}


def thread(turnos, nome="Minha conversa", cwd="\\\\?\\c:\\Users\\Fulano\\workspace", tid="01a2a000-0000-7000-8000-000000000001"):
    return {"id": tid, "name": nome, "cwd": cwd, "createdAt": T0, "turns": turnos}


class TestConversa(unittest.TestCase):
    def test_tipos_de_item_viram_os_turnos_certos(self):
        th = thread([
            {"id": "t1", "startedAt": T0 + 10, "status": "completed", "items": [
                item_usuario("faz o deploy", [{"type": "localImage", "path": "x.png"}]),
                {"type": "reasoning", "id": "r", "summary": ["pensando"]},
                {"type": "agentMessage", "id": "a1", "text": "Vou olhar o repo.", "phase": "commentary"},
                {"type": "commandExecution", "id": "c", "command": "pwsh -c git status",
                 "commandActions": [{"type": "unknown", "command": "git status\nmais"}]},
                {"type": "fileChange", "id": "f", "changes": [{"path": "C:\\r\\app.py", "kind": {"type": "add"}}]},
                {"type": "webSearch", "id": "w", "query": "codex hooks"},
                {"type": "mcpToolCall", "id": "m", "server": "projects", "tool": "quem_sou", "arguments": {"x": "oi"}},
                {"type": "contextCompaction", "id": "cc"},
                {"type": "tipoNovoQueNaoExiste", "id": "n"},
                {"type": "agentMessage", "id": "a2", "text": "Pronto."},
            ]},
            {"id": "external-import-turn-2", "startedAt": T0 + 20, "status": "completed", "items": [
                item_usuario("e agora?"),
                {"type": "agentMessage", "id": "a3", "text": codex.MARCA_IMPORTADA},
            ]},
        ])
        conv = codex.conversa_de_thread(th)
        self.assertEqual([t.papel for t in conv.turnos], ["usuario", "assistente", "usuario"])
        self.assertEqual(conv.turnos[0].texto, "faz o deploy\n[imagem anexada]")
        self.assertEqual(conv.turnos[1].texto, "Vou olhar o repo.\n\n`[comando]` git status\n\n`[arquivo]` app.py\n\n"
                                               "`[busca]` codex hooks\n\n`[projects.quem_sou]` oi\n\nPronto.")
        self.assertEqual(conv.turnos[0].quando, datetime.fromtimestamp(T0 + 10, tz=timezone.utc))
        self.assertEqual(conv.titulo, "Minha conversa")
        self.assertEqual(conv.cwd, "C:\\Users\\Fulano\\workspace")

    def test_titulo_sem_nome_e_horario_fora_da_faixa(self):
        th = thread([{"id": "t", "startedAt": -5, "items": [item_usuario("primeira linha\nsegunda")]}], nome=None)
        conv = codex.conversa_de_thread(th)
        self.assertEqual(conv.titulo, "primeira linha")
        self.assertEqual(conv.turnos[0].quando, datetime.fromtimestamp(T0, tz=timezone.utc))   # cai na criacao
        self.assertEqual(codex.conversa_de_thread(thread([], nome="")).titulo, "Conversa do Codex")

    def test_texto_enorme_e_cortado(self):
        conv = codex.conversa_de_thread(thread([{"id": "t", "items": [item_usuario("x" * 50_000)]}]))
        self.assertLess(len(conv.turnos[0].texto), claude_jsonl.MAX_TEXTO_TURNO + 100)

    def test_turnos_nativos_e_importada(self):
        so_importada = thread([{"id": "external-import-turn-1", "items": []}, {"id": "external-import-turn-2", "items": []}])
        continuada = thread(so_importada["turns"] + [{"id": "01a2b0c0-1111-7000-8000-000000000001", "items": []}])
        nativa = thread([{"id": "01a2b0c0-2222-7000-8000-000000000002", "items": []}])
        # a importacao, ao acrescentar so respostas numa conversa ja importada, cria turno "rollout-N": nao e nativo
        atualizada = thread(so_importada["turns"] + [{"id": "rollout-4451", "items": [{"type": "agentMessage"}]}])
        self.assertEqual((codex.importada(atualizada), codex.turnos_nativos(atualizada)), (True, 0))
        self.assertEqual((codex.importada(so_importada), codex.turnos_nativos(so_importada)), (True, 0))
        self.assertEqual((codex.importada(continuada), codex.turnos_nativos(continuada)), (True, 1))
        self.assertEqual((codex.importada(nativa), codex.turnos_nativos(nativa)), (False, 1))

    def test_assinatura_muda_com_item_novo_e_status(self):
        a = codex.assinatura(thread([{"id": "t", "status": "inProgress", "items": [1]}]))
        self.assertNotEqual(a, codex.assinatura(thread([{"id": "t", "status": "inProgress", "items": [1, 2]}])))
        self.assertNotEqual(a, codex.assinatura(thread([{"id": "t", "status": "completed", "items": [1]}])))
        self.assertEqual(a, codex.assinatura(thread([{"id": "t", "status": "inProgress", "items": [1]}])))

    def test_so_conversas_de_gente(self):
        base = {"source": "cli", "parentThreadId": None, "threadSource": "user", "ephemeral": False}
        self.assertTrue(codex.de_gente(base))
        self.assertTrue(codex.de_gente(dict(base, source="vscode")))
        self.assertFalse(codex.de_gente(dict(base, source="exec")))
        self.assertFalse(codex.de_gente(dict(base, source='{"subagent":{"thread_spawn":{}}}')))
        self.assertFalse(codex.de_gente(dict(base, parentThreadId="01a")))
        self.assertFalse(codex.de_gente(dict(base, threadSource="subagent")))
        self.assertFalse(codex.de_gente(dict(base, ephemeral=True)))


class TestRegistro(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        self.arq = os.path.join(self.home, codex.ARQ_REGISTROS)
        self.fonte_a = os.path.join(self.home, "a", "11111111-1111-1111-1111-111111111111.jsonl")
        self.fonte_b = os.path.join(self.home, "b", "11111111-1111-1111-1111-111111111111.jsonl")
        self.dados = {"records": [{"source_path": "\\\\?\\" + self.fonte_a, "imported_thread_id": "01a-t1",
                                   "content_sha256": "aa"},
                                  {"source_path": os.path.join(self.home, "outra.jsonl"), "imported_thread_id": "01a-t2"}],
                      "detected_connector_records": []}
        with open(self.arq, "w", encoding="utf-8") as f:
            json.dump(self.dados, f)

    def ler(self):
        with open(self.arq, encoding="utf-8") as f:
            return json.load(f)

    def test_registros_normaliza_o_prefixo_longo(self):
        regs = codex.registros(self.home)
        self.assertEqual(regs[codex.normalizar(self.fonte_a)], "01a-t1")
        self.assertEqual(len(regs), 2)

    def test_esquecer_tira_so_a_entrada_da_fonte_e_mantem_o_resto(self):
        self.assertEqual(codex.esquecer_registro(self.fonte_a.upper(), self.home), 1)
        d = self.ler()
        self.assertEqual([r["imported_thread_id"] for r in d["records"]], ["01a-t2"])
        self.assertIn("detected_connector_records", d)
        self.assertEqual(codex.esquecer_registro(self.fonte_a, self.home), 0)       # de novo: nada

    def test_adotar_aponta_a_entrada_antiga_para_a_fonte_nova(self):
        self.assertEqual(codex.adotar_registro(self.fonte_b, "11111111-1111-1111-1111-111111111111", self.home), "01a-t1")
        regs = codex.registros(self.home)
        self.assertEqual(regs.get(codex.normalizar(self.fonte_b)), "01a-t1")
        self.assertNotIn(codex.normalizar(self.fonte_a), regs)
        # ja apontada: nao mexe de novo
        self.assertIsNone(codex.adotar_registro(self.fonte_b, "11111111-1111-1111-1111-111111111111", self.home))

    def test_formato_desconhecido_nao_e_mexido(self):
        with open(self.arq, "w", encoding="utf-8") as f:
            json.dump({"outra_coisa": 1}, f)
        with self.assertRaises(codex.ErroCodex):
            codex.esquecer_registro(self.fonte_a, self.home)
        self.assertEqual(self.ler(), {"outra_coisa": 1})
        self.assertEqual(codex.registros(self.home), {})

    def test_sem_arquivo(self):
        os.remove(self.arq)
        self.assertEqual(codex.registros(self.home), {})
        self.assertEqual(codex.esquecer_registro(self.fonte_a, self.home), 0)
        self.assertIsNone(codex.adotar_registro(self.fonte_b, "x", self.home))


class TestPreparo(unittest.TestCase):
    def test_copia_enxuta_com_a_pasta_deste_pc(self):
        pasta = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, pasta, True)
        fonte = os.path.join(pasta, "s.jsonl")
        linhas = [{"type": "permission-mode", "x": 1},
                  {"type": "user", "cwd": "C:\\Users\\outro\\workspace", "sessionId": "s", "message": {"content": "oi"}},
                  {"type": "api-request-blob", "blob": "z" * 1000},
                  {"type": "assistant", "cwd": "C:\\Users\\outro\\workspace", "message": {"content": []}},
                  {"type": "ai-title", "aiTitle": "Título"}]
        with open(fonte, "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(x, ensure_ascii=False) for x in linhas) + "\n{quebrada\n")
        destino = codex.preparar(fonte, "s", "C:\\Users\\Eu\\workspace", os.path.join(pasta, "prep"))
        self.assertEqual(destino, codex.caminho_preparado("s", "C:\\Users\\Eu\\workspace", os.path.join(pasta, "prep")))
        self.assertTrue(destino.endswith(os.path.join(".claude", "projects", "C--Users-Eu-workspace", "s.jsonl")))
        with open(destino, encoding="utf-8") as f:
            saida = [json.loads(x) for x in f]
        self.assertEqual([x["type"] for x in saida], ["user", "assistant", "ai-title"])
        self.assertEqual({x.get("cwd") for x in saida[:2]}, {"C:\\Users\\Eu\\workspace"})
        self.assertEqual(saida[2]["aiTitle"], "Título")
        self.assertFalse(os.path.exists(destino + ".novo"))


FALSO = r'''
import json, sys, time
modo = sys.argv[1]
for linha in sys.stdin:
    m = json.loads(linha)
    if "id" not in m:
        continue
    if m["method"] == "initialize":
        print(json.dumps({"id": m["id"], "result": {}}), flush=True)
    elif modo == "erro":
        print(json.dumps({"id": m["id"], "error": {"code": -32600, "message": "thread not loaded: x"}}), flush=True)
    elif modo == "mudo":
        pass
    elif modo == "morre":
        sys.exit(3)
    elif modo == "importa":
        print(json.dumps({"method": "outro/aviso", "params": {}}), flush=True)
        print(json.dumps({"id": m["id"], "result": {"importId": "i1"}}), flush=True)
        print(json.dumps({"method": "externalAgentConfig/import/completed", "params": {"importId": "outro"}}), flush=True)
        print("lixo que nao e json", flush=True)
        src = m["params"]["migrationItems"][0]["details"]["sessions"][0]["path"]
        print(json.dumps({"method": "externalAgentConfig/import/completed", "params": {"importId": "i1",
              "itemTypeResults": [{"itemType": "SESSIONS", "successes": [{"source": "\\\\?\\" + src,
              "target": "01a2a000-0000-7000-8000-00000000000f"}], "failures": []}]}}), flush=True)
'''


class TestServidor(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.pasta, True)
        self.script = os.path.join(self.pasta, "falso.py")
        with open(self.script, "w", encoding="utf-8") as f:
            f.write(FALSO)

    def srv(self, modo):
        return codex.Servidor(home=self.pasta, comando=[sys.executable, "-I", self.script, modo])

    def test_erro_do_servidor_vira_ErroCodex_com_a_mensagem(self):
        with self.srv("erro") as s:
            with self.assertRaises(codex.ErroCodex) as e:
                s.chamar("thread/read", {"threadId": "x"})
            self.assertIn("thread not loaded", str(e.exception))

    def test_sem_resposta_estoura_o_prazo(self):
        with self.srv("mudo") as s:
            inicio = time.monotonic()
            with self.assertRaises(codex.ErroCodex) as e:
                s.chamar("thread/list", {}, prazo=1)
            self.assertIn("sem resposta", str(e.exception))
            self.assertLess(time.monotonic() - inicio, 5)

    def test_servidor_que_morre_nao_trava(self):
        with self.srv("morre") as s:
            with self.assertRaises(codex.ErroCodex):
                s.chamar("thread/list", {}, prazo=20)

    def test_importar_espera_o_aviso_da_propria_importacao(self):
        fonte = os.path.join(self.pasta, "s.jsonl")
        open(fonte, "w").close()
        with self.srv("importa") as s:
            feitos, falhas = codex.importar(s, [(fonte, "C:\\w", "t")])
        self.assertEqual(feitos, {codex.normalizar(fonte): "01a2a000-0000-7000-8000-00000000000f"})
        self.assertEqual(falhas, {})

    def test_fechar_encerra_o_processo(self):
        s = self.srv("mudo").__enter__()
        s.fechar()
        self.assertIsNotNone(s.proc.poll())


def args(**kw):
    base = dict(sessao=None, cascade=None, grok=None, codex=None, cursor=None, so=None, seco=False, forcar=False)
    base.update(kw)
    return SimpleNamespace(**base)


class TestNomeNoCodex(unittest.TestCase):
    def chamadas(self, atual, antigo, novo):
        srv = mock.Mock()
        sincronizar._atualizar_nome_codex(srv, "t1", atual, antigo, novo)
        return srv.chamar.call_args_list

    def test_troca_o_nome_que_nos_pusemos(self):
        for atual, antigo in ((None, None), ("", None), ("Velho", "Velho"), ("Bug", None)):
            self.assertEqual(self.chamadas(atual, antigo, "Bug (part 1, continued in another)"),
                             [mock.call("thread/name/set", {"threadId": "t1", "name": "Bug (part 1, continued in another)"})])

    def test_nome_trocado_a_mao_ou_igual_fica(self):
        self.assertEqual(self.chamadas("Meu nome", "Velho", "Novo"), [])
        self.assertEqual(self.chamadas("Novo", "Velho", "Novo"), [])


class TestDecisoes(unittest.TestCase):
    """As regras do para_codex/codex_para_claude com o Codex simulado (o Codex de verdade fica no test_codex_real)."""

    def setUp(self):
        self.raiz = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.raiz, True)
        self.projetos = os.path.join(self.raiz, "projects")
        os.makedirs(os.path.join(self.projetos, "C--w"))
        for nome, valor in (("PROJETOS", self.projetos), ("PASTA_ESTADO", self.raiz),
                            ("ESTADO", os.path.join(self.raiz, "estado.json")),
                            ("PREPARO", os.path.join(self.raiz, "preparo"))):
            p = mock.patch.object(sincronizar, nome, valor)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(sincronizar.para_claude, "projetos_claude", lambda: self.projetos)
        p.start()
        self.addCleanup(p.stop)
        self.sessao = "22222222-2222-2222-2222-222222222222"
        self.caminho = os.path.join(self.projetos, "C--w", self.sessao + ".jsonl")
        self.escrever_sessao(["oi", "tudo bem?"])

    def escrever_sessao(self, perguntas, entrypoint="cli"):
        with open(self.caminho, "w", encoding="utf-8") as f:
            for i, p in enumerate(perguntas):
                for tipo, msg in (("user", {"role": "user", "content": p}),
                                  ("assistant", {"role": "assistant", "content": [{"type": "text", "text": "r" + p}]})):
                    f.write(json.dumps({"type": tipo, "entrypoint": entrypoint, "cwd": self.raiz, "sessionId": self.sessao,
                                        "timestamp": f"2026-11-25T10:0{i}:00Z", "message": msg}) + "\n")

    def test_continuada_no_codex_gera_versao_nova_e_a_antiga_fica(self):
        estado = sincronizar.ler_estado()
        estado["importadas_codex"][self.sessao] = {"id": "01a2a000-0000-7000-8000-000000000001", "versao": 0,
                                                   "fonte_tamanho": 1, "fonte_mtime": 1.0}
        continuada = thread([{"id": "external-import-turn-1", "items": []}, {"id": "01a2b000-0000-7000-8000-0000000000aa", "items": []}])
        cont = __import__("collections").Counter()
        with mock.patch.object(codex, "listar", return_value={"01a2a000-0000-7000-8000-000000000001": {}}), \
                mock.patch.object(codex, "ler", return_value=continuada), \
                mock.patch.object(codex, "esquecer_registro") as esquecer, \
                mock.patch.object(codex, "importar", side_effect=lambda srv, s, a_cada_lote=None: (
                    {codex.normalizar(s[0][0]): "01a2a000-0000-7000-8000-000000000002"}, {})):
            sincronizar.para_codex(args(), estado, cont, srv=None)
        esquecer.assert_called_once()
        self.assertEqual(cont["c>x versoes_novas"], 1)
        self.assertEqual(estado["importadas_codex"][self.sessao]["id"], "01a2a000-0000-7000-8000-000000000002")
        self.assertEqual(estado["importadas_codex"][self.sessao]["versao"], 1)
        self.assertEqual(estado["importadas_codex"][self.sessao]["anteriores"], ["01a2a000-0000-7000-8000-000000000001"])
        self.assertEqual(os.listdir(sincronizar.PREPARO), [])           # copia apagada depois da importacao

    def test_apagada_no_codex_nao_volta_sem_forcar(self):
        estado = sincronizar.ler_estado()
        estado["importadas_codex"][self.sessao] = {"id": "01a2a000-0000-7000-8000-000000000001", "versao": 0,
                                                   "fonte_tamanho": 1, "fonte_mtime": 1.0}
        cont = __import__("collections").Counter()
        with mock.patch.object(codex, "listar", return_value={}), mock.patch.object(codex, "importar") as imp:
            sincronizar.para_codex(args(), estado, cont, srv=None)
            imp.assert_not_called()
        self.assertEqual(cont["c>x apagadas_no_codex"], 1)

    def test_sessao_que_veio_do_codex_nao_volta(self):
        self.escrever_sessao(["oi"], entrypoint="codex-import")
        estado = sincronizar.ler_estado()
        estado["exportadas_codex"]["01a2a000-0000-7000-8000-000000000009"] = {
            "sessao": self.sessao, "versao": 0, "turnos": 2, "assinatura": "x"}
        cont = __import__("collections").Counter()
        with mock.patch.object(codex, "importar") as imp:
            sincronizar.para_codex(args(), estado, cont, srv=None)
            imp.assert_not_called()
        self.assertEqual(cont["c>x espelhos"], 1)

    def test_fonte_igual_nao_reimporta(self):
        st = os.stat(self.caminho)
        estado = sincronizar.ler_estado()
        estado["importadas_codex"][self.sessao] = {"id": "01a2a000-0000-7000-8000-000000000001", "versao": 0,
                                                   "fonte_tamanho": st.st_size, "fonte_mtime": round(st.st_mtime, 3)}
        cont = __import__("collections").Counter()
        with mock.patch.object(codex, "importar") as imp:
            sincronizar.para_codex(args(), estado, cont, srv=None)
            imp.assert_not_called()
        self.assertEqual(cont["c>x iguais"], 1)

    def test_codex_para_claude_espelho_puro_nao_volta_e_nativa_vira_sessao(self):
        espelho = thread([{"id": "external-import-turn-1", "items": [item_usuario("oi")]}],
                         tid="01a2a000-0000-7000-8000-00000000000a")
        nativa = thread([{"id": "01a2c000-0000-7000-8000-000000000001", "startedAt": T0, "status": "completed",
                          "items": [item_usuario("pergunta no codex"), {"type": "agentMessage", "text": "resposta"}]}],
                        nome="Feita no Codex", cwd=self.raiz, tid="01a2a000-0000-7000-8000-00000000000b")
        lista = {espelho["id"]: {"updatedAt": 1}, nativa["id"]: {"updatedAt": 2}}
        estado, cont, novas = sincronizar.ler_estado(), __import__("collections").Counter(), set()
        with mock.patch.object(codex, "listar", return_value=lista), \
                mock.patch.object(codex, "ler", side_effect=lambda srv, tid: espelho if tid == espelho["id"] else nativa):
            sincronizar.codex_para_claude(args(), estado, cont, novas, srv=None)
            self.assertEqual((cont["x>c espelhos"], cont["x>c criadas"]), (1, 1))
            sessao = estado["exportadas_codex"][nativa["id"]]["sessao"]
            self.assertEqual(novas, {sessao})
            conv = claude_jsonl.ler_conversa(sincronizar.para_claude.achar_sessao(sessao), sessao)
            self.assertEqual(conv.titulo, "[Codex] Feita no Codex")
            self.assertEqual(conv.entrypoint, "codex-import")
            self.assertEqual([t.texto for t in conv.turnos], ["pergunta no codex", "resposta"])
            # segunda rodada: nada mudou (nem relê)
            cont2 = __import__("collections").Counter()
            with mock.patch.object(codex, "ler", side_effect=AssertionError("nao devia reler")):
                sincronizar.codex_para_claude(args(), estado, cont2, set(), srv=None)
            self.assertEqual(cont2["x>c iguais"], 2)
            # a sessao gerada nao vai de volta para o Codex
            cont3 = __import__("collections").Counter()
            with mock.patch.object(codex, "importar") as imp:
                sincronizar.para_codex(args(so={sessao}), estado, cont3, srv=None)
                imp.assert_not_called()
            self.assertEqual(cont3["c>x espelhos"], 1)

    def test_importada_continuada_sem_nome_recebe_o_titulo_da_sessao_do_claude(self):
        """O Codex apaga o nome da conversa importada quando ela e continuada: volta o titulo da sessao de origem."""
        self.escrever_sessao(["oi"])
        with open(self.caminho, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "ai-title", "aiTitle": "Titulo do Claude", "sessionId": self.sessao}) + "\n")
        tid = "01a2a000-0000-7000-8000-00000000000c"
        th = thread([{"id": "external-import-turn-1", "startedAt": T0, "items": [item_usuario("oi")]},
                     {"id": "01a2c000-0000-7000-8000-0000000000bb", "startedAt": T0 + 5, "items": [item_usuario("continuei"),
                                                                          {"type": "agentMessage", "text": "ok"}]}],
                    nome=None, cwd=self.raiz, tid=tid)
        estado, cont, novas = sincronizar.ler_estado(), __import__("collections").Counter(), set()
        # a continuada ja virou "anterior": a sessao ganhou versao nova no Codex
        estado["importadas_codex"][self.sessao] = {"id": "01a2a000-0000-7000-8000-0000000000ff", "versao": 1,
                                                   "fonte_tamanho": 1, "fonte_mtime": 1.0, "anteriores": [tid]}
        srv = mock.Mock()
        with mock.patch.object(codex, "listar", return_value={tid: {"updatedAt": 5}}), \
                mock.patch.object(codex, "ler", return_value=th):
            sincronizar.codex_para_claude(args(), estado, cont, novas, srv=srv)
        sessao = estado["exportadas_codex"][tid]["sessao"]
        conv = claude_jsonl.ler_conversa(sincronizar.para_claude.achar_sessao(sessao), sessao)
        self.assertEqual(conv.titulo, "[Codex] Titulo do Claude")
        srv.chamar.assert_called_once_with("thread/name/set", {"threadId": tid, "name": "Titulo do Claude"})


class TestInstalarCodex(unittest.TestCase):
    def setUp(self):
        import instalar
        self.instalar = instalar

    def test_hooks_json_troca_so_os_da_skill_e_mantem_os_outros(self):
        atual = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "outro.exe"}]},
                                    {"hooks": [{"type": "command", "command": "python -I velho/gancho_codex.py"}]}],
                           "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "x"}]}]}}
        novo = self.instalar.montar_hooks_codex(atual)
        stop = novo["hooks"]["Stop"]
        self.assertEqual(stop[0]["hooks"][0]["command"], "outro.exe")
        self.assertEqual(len(stop), 2)
        self.assertIn("gancho_codex.py", stop[1]["hooks"][0]["command"])
        self.assertIn(" -I ", stop[1]["hooks"][0]["command"])
        # o Codex roda o hook pelo PowerShell: caminho entre aspas so EXECUTA com o operador & na frente
        self.assertTrue(stop[1]["hooks"][0]["command"].startswith('& "'))
        self.assertEqual(novo["hooks"]["PreToolUse"], atual["hooks"]["PreToolUse"])
        self.assertIn("gancho_codex.py", novo["hooks"]["SessionEnd"][0]["hooks"][0]["command"])
        self.assertEqual(self.instalar.montar_hooks_codex(novo), novo)            # idempotente
        self.assertEqual(len(atual["hooks"]["Stop"]), 2)                          # nao mexe no original

    def test_confia_nos_hooks_do_tagteam_e_dos_plugins_do_claude(self):
        caminho = os.path.normcase(os.path.realpath(os.path.join(tempfile.gettempdir(), "hooks.json")))
        nosso = {"key": "k-nosso", "source": "user", "command": '& "py.exe" -I "x/gancho_codex.py"',
                 "sourcePath": caminho, "trustStatus": "untrusted", "currentHash": "sha256:1", "enabled": True}
        do_plugin = {"key": "superpowers@spm:hooks/hooks.json:session_start:0:0", "source": "plugin",
                     "pluginId": "superpowers@spm", "command": "node x/inicio.js", "trustStatus": "untrusted",
                     "currentHash": "sha256:9", "enabled": True}
        grupos = [{"hooks": [
            nosso,
            dict(nosso, key="k-ja-confiado", trustStatus="trusted"),
            dict(nosso, key="k-outro-arquivo", sourcePath=os.path.join(tempfile.gettempdir(), "outro.json")),
            {"key": "k-de-outro", "source": "user", "command": "outro.exe", "sourcePath": caminho,
             "trustStatus": "untrusted", "currentHash": "sha256:2", "enabled": True},
            do_plugin,
            dict(do_plugin, key="outro@x:h:stop:0:0", pluginId="outro@x"),      # plugin que o Claude nao liga
        ]}]
        self.assertEqual(self.instalar._ajustes_de_hooks_codex(grupos, caminho, {"superpowers@spm"}), {
            "k-nosso": {"trusted_hash": "sha256:1"},
            "superpowers@spm:hooks/hooks.json:session_start:0:0": {"trusted_hash": "sha256:9"}})

    def test_hooks_json_que_nao_se_entende_nao_e_mexido(self):
        self.assertIsNone(self.instalar.montar_hooks_codex({"hooks": []}))
        self.assertIsNone(self.instalar.montar_hooks_codex({"hooks": {"Stop": "texto"}}))
        self.assertIsNotNone(self.instalar.montar_hooks_codex(None))

    def test_agents_md_bloco_no_topo_e_o_texto_antigo_fica(self):
        antigo = "# Minhas regras\n\nTexto que eu escrevi.\n"
        novo = self.instalar.montar_agents_codex(antigo)
        self.assertTrue(novo.startswith(self.instalar.INICIO_AGENTS))
        self.assertTrue(novo.rstrip().endswith("Texto que eu escrevi."))
        self.assertIn(os.path.join(".claude", "CLAUDE.md"), novo)
        self.assertNotIn("{HOME}", novo)
        self.assertNotIn("{MEMORIA}", novo)
        self.assertEqual(self.instalar.montar_agents_codex(novo), novo)          # idempotente
        self.assertEqual(novo.count(self.instalar.INICIO_AGENTS), 1)
        self.assertTrue(self.instalar.montar_agents_codex("").endswith(self.instalar.FIM_AGENTS + "\n"))


class TestGanchoCodex(unittest.TestCase):
    def rodar(self, entrada, temp, extra_env=None):
        import subprocess
        # home, TEMP e CODEX_HOME no temporario: a sincronizacao que o hook lanca nao encontra nada real
        env = dict(os.environ, TEMP=temp, TMP=temp, USERPROFILE=temp, HOME=temp,
                   CODEX_HOME=os.path.join(temp, "nao-existe"))
        env.pop("CODEX_THREAD_ID", None)
        env.update(extra_env or {})
        r = subprocess.run([sys.executable, "-I", os.path.join(AQUI, "gancho_codex.py")],
                           input=json.dumps(entrada).encode(), capture_output=True, env=env, timeout=60, check=False)
        return r

    @staticmethod
    def ler(caminho):
        with open(caminho, encoding="utf-8") as f:
            return f.read()

    def test_stop_lanca_a_sincronizacao_da_conversa_e_nao_imprime(self):
        temp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp, True)
        tid = "01a2a000-0000-7000-8000-0000000000aa"
        r = self.rodar({"hook_event_name": "Stop", "session_id": tid}, temp)
        self.assertEqual((r.returncode, r.stdout), (0, b""))
        self.assertTrue(os.path.exists(os.path.join(temp, PASTA_GANCHO, f"ultimo-codex-{tid}")))
        log = os.path.join(temp, PASTA_GANCHO, "sincronizar.log")
        prazo = time.monotonic() + 30
        while time.monotonic() < prazo and not (os.path.exists(log) and "nada a fazer" in self.ler(log)):
            # sleep proposital: espera com prazo o processo filho que o hook lancou terminar
            time.sleep(0.2)
        self.assertIn(f"--direcao claude-de-codex --codex {tid}", self.ler(log))
        # segundo Stop dentro do intervalo, com a rodada do fim da janela ja agendada, nao relanca (o agendamento em
        # si esta no test_gancho: aqui ele deixaria um processo esperando 2 min); SessionEnd lanca sempre
        with open(os.path.join(temp, PASTA_GANCHO, f"pendente-codex-{tid}"), "w", encoding="utf-8") as f:
            f.write("agendada")
        self.rodar({"hook_event_name": "Stop", "session_id": tid}, temp)
        self.assertEqual(self.ler(log).count("--codex"), 1)
        self.rodar({"hook_event_name": "SessionEnd", "session_id": tid}, temp)
        prazo = time.monotonic() + 30
        while time.monotonic() < prazo and self.ler(log).count("nada a fazer") < 2:
            # sleep proposital: espera com prazo o segundo processo filho terminar
            time.sleep(0.2)
        self.assertEqual(self.ler(log).count("--codex"), 2)

    def test_entrada_ruim_sai_calado(self):
        temp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp, True)
        for entrada in ({"hook_event_name": "Stop", "session_id": "nao-e-id"}, [], {"hook_event_name": "Stop"}):
            r = self.rodar(entrada, temp)
            self.assertEqual((r.returncode, r.stdout), (0, b""))
        self.assertFalse(os.path.exists(os.path.join(temp, PASTA_GANCHO, "sincronizar.log")))


if __name__ == "__main__":
    unittest.main()
