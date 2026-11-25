"""Cursor: leitura e gravacao do armazenamento do CLI (~/.cursor/chats), as regras do para_cursor/cursor_para_claude,
o gancho e o que o instalar.py grava no ~/.cursor. Tudo numa pasta temporaria (TAGTEAM_CURSOR_HOME)."""
import collections
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import cursor  # noqa: E402
import gancho  # noqa: E402
import gancho_cursor  # noqa: E402
import instalar  # noqa: E402
import pb  # noqa: E402
import sincronizar  # noqa: E402
from claude_jsonl import Conversa, Turno  # noqa: E402

CWD = "C:\\Users\\Fulano\\workspace"
QUANDO = datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc)


def conversa(perguntas, titulo="Minha conversa", cwd=CWD):
    c = Conversa(sessao="s", cwd=cwd)
    for p in perguntas:
        c.turnos += [Turno("usuario", p, QUANDO), Turno("assistente", "r:" + p, QUANDO)]
    c.titulo = titulo
    return c


MOLDE = {"cwd": CWD, "raiz": [[22, 2, b"cli"], [9, 2, b"file:///app/backend/server/x"]],
         "fixos": [json.dumps({"role": "system", "content": "voce trabalha em " + CWD}).encode()],
         "turno_extra": [], "contexto": None}


def nativa(perguntas, aid, cwd=CWD):
    """Conversa como o Cursor grava: a mesma estrutura, sem a marca do tagteam."""
    pasta = cursor.gravar_conversa(conversa(perguntas, cwd=cwd), cwd, MOLDE, aid, "New Agent", {})
    db = sqlite3.connect(os.path.join(pasta, "store.db"))
    db.execute("delete from meta where key = ?", (cursor.MARCA,))
    db.commit()
    db.close()
    return pasta


def continuar_no_cursor(pasta, perguntas):
    """Simula o Cursor respondendo mais: mais turnos, a marca (com o numero antigo de turnos) fica."""
    marca = cursor.marca(pasta)
    meta, _m, _b = cursor._ler(pasta)
    cursor.gravar_conversa(conversa(perguntas), CWD, MOLDE, meta["agentId"], meta["name"], {})
    db = sqlite3.connect(os.path.join(pasta, "store.db"))
    db.execute("update meta set value = ? where key = ?", (json.dumps(marca), cursor.MARCA))
    db.commit()
    db.close()


class Base(unittest.TestCase):
    def setUp(self):
        self.raiz = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.raiz, True)
        p = mock.patch.dict(os.environ, {"TAGTEAM_CURSOR_HOME": os.path.join(self.raiz, "cursor"),
                                         "TAGTEAM_CURSOR_APP_DB": os.path.join(self.raiz, "app", "state.vscdb")})
        p.start()
        self.addCleanup(p.stop)
        os.makedirs(cursor.pasta_chats())


class TestArmazenamento(Base):
    def test_gravar_e_ler_devolvem_a_mesma_conversa(self):
        pasta = cursor.gravar_conversa(conversa(["oi", "e o deploy?"]), CWD, MOLDE, cursor.id_para_sessao("s"),
                                       "Minha conversa", {"fonte": "claude:s"})
        lida = cursor.ler_conversa(pasta)
        self.assertEqual([(t.papel, t.texto) for t in lida.turnos],
                         [("usuario", "oi"), ("assistente", "r:oi"), ("usuario", "e o deploy?"),
                          ("assistente", "r:e o deploy?")])
        self.assertEqual(lida.titulo, "Minha conversa")
        self.assertEqual(lida.cwd, CWD)
        self.assertEqual(cursor.turnos(pasta), 2)
        self.assertEqual(cursor.marca(pasta), {"fonte": "claude:s", "turnos": 2})
        self.assertEqual(os.path.dirname(pasta), cursor.pasta_do_cwd(CWD))
        with open(os.path.join(pasta, "meta.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["title"], "Minha conversa")       # e dali que a lista do CLI le o nome

    def test_um_f8_por_turno_e_pasta_com_dois_pontos_sem_codificar(self):
        pasta = cursor.gravar_conversa(conversa(["a", "b", "c"]), CWD, MOLDE, cursor.id_para_sessao("s"), "t", {})
        meta, _m, blobs = cursor._ler(pasta)
        raiz = cursor._raiz(meta, blobs)
        self.assertEqual(sum(1 for c, _t, _v in raiz if c == 8), 3)
        f9 = next(v for c, _t, v in raiz if c == 9).decode()
        self.assertTrue(f9.endswith("C:%5CUsers%5CFulano%5Cworkspace"), f9)   # ':' codificado faz o Cursor avisar

    def test_titulo_padrao_do_cursor_vira_a_primeira_pergunta(self):
        pasta = nativa(["arrumar o build\nlinha 2"], "11111111-1111-4111-8111-111111111111")
        self.assertEqual(cursor.ler_conversa(pasta).titulo, "arrumar o build")

    def test_molde_vem_da_nativa_mais_nova_e_troca_a_pasta(self):
        nativa(["x"], "11111111-1111-4111-8111-111111111111")
        cursor.gravar_conversa(conversa(["y"]), CWD, MOLDE, cursor.id_para_sessao("s"), "t", {"fonte": "f"})
        molde = cursor.achar_molde()
        self.assertEqual(molde["cwd"], CWD)
        self.assertEqual(len(molde["fixos"]), 1)
        outra = "D:\\outra"
        pasta = cursor.gravar_conversa(conversa(["z"]), outra, molde, cursor.id_para_sessao("s2"), "t", {})
        meta, _m, blobs = cursor._ler(pasta)
        sistema = blobs[next(v for c, _t, v in cursor._raiz(meta, blobs) if c == 1).hex()].decode()
        self.assertIn("D:\\\\outra", sistema)
        self.assertNotIn("Fulano", sistema)

    def test_molde_guardado_vale_quando_as_nativas_somem(self):
        guardado = os.path.join(self.raiz, "molde.json")
        nativa(["x"], "11111111-1111-4111-8111-111111111111")
        original = cursor.achar_molde(guardado)
        shutil.rmtree(cursor.pasta_chats())
        os.makedirs(cursor.pasta_chats())
        self.assertIsNone(cursor.achar_molde())
        self.assertEqual(cursor.achar_molde(guardado), original)
        pasta = cursor.gravar_conversa(conversa(["y"]), CWD, cursor.achar_molde(guardado), cursor.id_para_sessao("s"),
                                       "t", {})
        self.assertEqual(cursor.ler_conversa(pasta).turnos[0].texto, "y")

    def test_molde_guardado_quebrado_e_ignorado(self):
        guardado = os.path.join(self.raiz, "molde.json")
        with open(guardado, "w", encoding="utf-8") as f:
            f.write("{lixo")
        self.assertIsNone(cursor.achar_molde(guardado))

    def test_sem_nativa_nao_ha_molde(self):
        cursor.gravar_conversa(conversa(["y"]), CWD, MOLDE, cursor.id_para_sessao("s"), "t", {"fonte": "f"})
        self.assertIsNone(cursor.achar_molde())

    def test_apagar_nunca_tira_conversa_nativa(self):
        pasta = nativa(["x"], "11111111-1111-4111-8111-111111111111")
        cursor.apagar(pasta)
        self.assertTrue(os.path.isdir(pasta))
        nossa = cursor.gravar_conversa(conversa(["y"]), CWD, MOLDE, cursor.id_para_sessao("s"), "t", {"fonte": "f"})
        cursor.apagar(nossa)
        self.assertFalse(os.path.exists(nossa))

    def test_store_quebrado_nao_tem_marca(self):
        pasta = os.path.join(cursor.pasta_do_cwd(CWD), "11111111-1111-4111-8111-111111111111")
        os.makedirs(pasta)
        with open(os.path.join(pasta, "store.db"), "wb") as f:
            f.write(b"lixo")
        self.assertIsNone(cursor.marca(pasta))
        self.assertIsNone(cursor.achar_molde())

    def test_id_por_versao_e_estavel_e_diferente(self):
        self.assertEqual(cursor.id_para_sessao("s"), cursor.id_para_sessao("s", 0))
        self.assertNotEqual(cursor.id_para_sessao("s"), cursor.id_para_sessao("s", 1))
        self.assertRegex(cursor.id_para_sessao("s", 3), cursor.ID_RE)


def args(**kw):
    base = dict(sessao=None, cascade=None, grok=None, codex=None, cursor=None, so=None, seco=False, forcar=False)
    base.update(kw)
    return SimpleNamespace(**base)


class TestDecisoes(Base):
    def setUp(self):
        super().setUp()
        self.projetos = os.path.join(self.raiz, "projects")
        os.makedirs(os.path.join(self.projetos, "C--w"))
        for nome, valor in (("PROJETOS", self.projetos), ("PASTA_ESTADO", self.raiz),
                            ("ESTADO", os.path.join(self.raiz, "estado.json")),
                            ("MOLDE_CURSOR", os.path.join(self.raiz, "molde-cursor.json"))):
            p = mock.patch.object(sincronizar, nome, valor)
            p.start()
            self.addCleanup(p.stop)
        for nome, valor in (("projetos_claude", lambda: self.projetos), ("versao_claude", lambda: "9.9.9")):
            p = mock.patch.object(sincronizar.para_claude, nome, valor)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(sincronizar, "cwd_local", lambda c: c)
        p.start()
        self.addCleanup(p.stop)
        nativa(["molde"], "99999999-9999-4999-8999-999999999999")
        self.sessao = "22222222-2222-2222-2222-222222222222"
        self.caminho = os.path.join(self.projetos, "C--w", self.sessao + ".jsonl")
        self.escrever_sessao(["oi", "tudo bem?"])

    def escrever_sessao(self, perguntas, entrypoint="cli"):
        with open(self.caminho, "w", encoding="utf-8") as f:
            for i, p in enumerate(perguntas):
                for tipo, msg in (("user", {"role": "user", "content": p}),
                                  ("assistant", {"role": "assistant", "content": [{"type": "text", "text": "r" + p}]})):
                    f.write(json.dumps({"type": tipo, "entrypoint": entrypoint, "cwd": CWD, "sessionId": self.sessao,
                                        "timestamp": f"2026-11-25T10:0{i}:00Z", "message": msg}) + "\n")
        os.utime(self.caminho, (1795600000 + len(perguntas), 1795600000 + len(perguntas)))

    def rodar(self, funcao, estado, *extra, **kw):
        cont = collections.Counter()
        with mock.patch("sys.stdout", io.StringIO()):
            funcao(args(**kw), estado, cont, *extra)
        return cont

    def test_cria_e_depois_fica_igual(self):
        estado = sincronizar.ler_estado()
        self.assertEqual(self.rodar(sincronizar.para_cursor, estado)["c>u criadas"], 1)
        rec = estado["importadas_cursor"][self.sessao]
        self.assertEqual(cursor.ler_conversa(rec["pasta"]).turnos[0].texto, "oi")
        self.assertEqual(self.rodar(sincronizar.para_cursor, estado)["c>u iguais"], 1)
        self.assertEqual(sincronizar.ler_estado()["importadas_cursor"], estado["importadas_cursor"])   # gravado

    def test_mudou_no_claude_e_ninguem_continuou_reescreve_a_mesma(self):
        estado = sincronizar.ler_estado()
        self.rodar(sincronizar.para_cursor, estado)
        antes = dict(estado["importadas_cursor"][self.sessao])
        self.escrever_sessao(["oi", "tudo bem?", "mais uma"])
        self.assertEqual(self.rodar(sincronizar.para_cursor, estado)["c>u atualizadas"], 1)
        depois = estado["importadas_cursor"][self.sessao]
        self.assertEqual((depois["id"], depois["versao"]), (antes["id"], 0))
        self.assertEqual(cursor.turnos(depois["pasta"]), 3)

    def test_continuada_no_cursor_nunca_e_sobrescrita(self):
        estado = sincronizar.ler_estado()
        self.rodar(sincronizar.para_cursor, estado)
        velha = estado["importadas_cursor"][self.sessao]["pasta"]
        continuar_no_cursor(velha, ["oi", "tudo bem?", "pergunta no cursor"])
        self.escrever_sessao(["oi", "tudo bem?", "pergunta no claude"])
        self.rodar(sincronizar.para_cursor, estado)
        nova = estado["importadas_cursor"][self.sessao]
        self.assertEqual(nova["versao"], 1)
        self.assertNotEqual(os.path.normcase(nova["pasta"]), os.path.normcase(velha))
        self.assertEqual(cursor.ler_conversa(velha).turnos[4].texto, "pergunta no cursor")   # a continuada ficou
        self.assertEqual(cursor.ler_conversa(nova["pasta"]).turnos[4].texto, "pergunta no claude")

    def test_apagada_no_cursor_nao_volta_sem_forcar(self):
        estado = sincronizar.ler_estado()
        self.rodar(sincronizar.para_cursor, estado)
        shutil.rmtree(estado["importadas_cursor"][self.sessao]["pasta"])
        self.escrever_sessao(["oi", "outra"])
        self.assertEqual(self.rodar(sincronizar.para_cursor, estado)["c>u apagadas_no_cursor"], 1)
        self.assertEqual(self.rodar(sincronizar.para_cursor, estado, forcar=True)["c>u atualizadas"], 1)
        self.assertTrue(os.path.isdir(estado["importadas_cursor"][self.sessao]["pasta"]))

    def test_automatica_e_vazia_nao_vao(self):
        self.escrever_sessao(["oi"], entrypoint="sdk-cli")
        estado = sincronizar.ler_estado()
        self.assertEqual(self.rodar(sincronizar.para_cursor, estado)["c>u automaticas"], 1)
        with open(self.caminho, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "summary", "entrypoint": "cli"}) + "\n")
        self.assertEqual(self.rodar(sincronizar.para_cursor, estado)["c>u vazias"], 1)
        self.assertEqual(estado["importadas_cursor"], {})

    def test_sem_conversa_nativa_nao_grava(self):
        shutil.rmtree(cursor.pasta_chats())
        os.makedirs(cursor.pasta_chats())
        estado = sincronizar.ler_estado()
        self.assertEqual(self.rodar(sincronizar.para_cursor, estado)["c>u sem conversa nativa para molde"], 1)

    def test_seco_nao_grava(self):
        estado = sincronizar.ler_estado()
        self.assertEqual(self.rodar(sincronizar.para_cursor, estado, seco=True)["c>u criadas"], 1)
        self.assertEqual(estado["importadas_cursor"], {})
        self.assertEqual(len(cursor.conversas()), 1)                         # so o molde

    def test_ida_e_volta_espelho_nao_volta_e_continuada_vira_sessao(self):
        estado = sincronizar.ler_estado()
        self.rodar(sincronizar.para_cursor, estado)
        os.remove(self.caminho)                                              # so a nativa "molde" vai ao Claude
        novas = set()
        cont = self.rodar(sincronizar.cursor_para_claude, estado, novas)
        self.assertEqual((cont["u>c espelhos"], cont["u>c criadas"]), (1, 1))
        self.assertEqual(len(novas), 1)
        sessao = next(iter(novas))
        caminho = sincronizar.para_claude.achar_sessao(sessao)
        with open(caminho, encoding="utf-8") as f:
            linhas = [json.loads(x) for x in f]
        self.assertTrue(all(x.get("entrypoint") == "cursor-import" for x in linhas if "entrypoint" in x))
        self.assertEqual(self.rodar(sincronizar.cursor_para_claude, estado, set())["u>c iguais"], 1)
        # a sessao [Cursor] no Claude nao volta para o Cursor (espelho)
        self.assertEqual(self.rodar(sincronizar.para_cursor, estado)["c>u espelhos"], 1)
        # a copia do Claude continuada no Cursor vira sessao nova no Claude
        copia = estado["importadas_cursor"][self.sessao]["pasta"]
        continuar_no_cursor(copia, ["oi", "tudo bem?", "no cursor"])
        novas = set()
        self.assertEqual(self.rodar(sincronizar.cursor_para_claude, estado, novas)["u>c criadas"], 1)
        self.assertEqual(len(novas), 1)

    def test_apagada_no_claude_nao_volta(self):
        estado = sincronizar.ler_estado()
        novas = set()
        self.rodar(sincronizar.cursor_para_claude, estado, novas)
        os.remove(sincronizar.para_claude.achar_sessao(next(iter(novas))))
        pasta = next(p for p, _a in cursor.conversas())
        continuar_no_cursor(pasta, ["molde", "de novo"])
        db = sqlite3.connect(os.path.join(pasta, "store.db"))
        db.execute("delete from meta where key = ?", (cursor.MARCA,))
        db.commit()
        db.close()
        self.assertEqual(self.rodar(sincronizar.cursor_para_claude, estado, set())["u>c apagadas_no_claude"], 1)

    def test_conversa_do_app_vai_ao_claude_e_depois_fica_igual(self):
        cid = TestApp.CID
        banco_app(cursor.banco_app(), {cid: ("Saudacao", [(1, "oi"), (2, "Oi.")])})
        estado = sincronizar.ler_estado()
        novas = set()
        self.assertEqual(self.rodar(sincronizar.cursor_para_claude, estado, novas, cursor=cid)["u>c criadas"], 1)
        self.assertEqual(estado["exportadas_cursor"][cid]["turnos"], 2)
        self.assertIsNotNone(sincronizar.para_claude.achar_sessao(next(iter(novas))))
        self.assertEqual(self.rodar(sincronizar.cursor_para_claude, estado, set(), cursor=cid)["u>c iguais"], 1)
        banco_app(cursor.banco_app(), {cid: ("Saudacao", [(1, "oi"), (2, "Oi."), (1, "mais"), (2, "ok")])})
        self.assertEqual(self.rodar(sincronizar.cursor_para_claude, estado, set(), cursor=cid)["u>c atualizadas"], 1)

    def test_filtro_por_id_do_cursor(self):
        estado = sincronizar.ler_estado()
        cont = self.rodar(sincronizar.cursor_para_claude, estado, set(), cursor="00000000-0000-4000-8000-000000000000")
        self.assertEqual(sum(cont.values()), 0)
        self.assertEqual(sum(self.rodar(sincronizar.para_cursor, estado, cursor="x").values()), 0)


def banco_app(caminho, conversas):
    """state.vscdb do app com {id: (nome, [(tipo, texto)])}: tipo 1 = usuario, 2 = assistente, 'pensa' = raciocinio."""
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    db = sqlite3.connect(caminho)
    db.execute("create table if not exists cursorDiskKV (key text unique on conflict replace, value blob)")
    for cid, (nome, msgs) in conversas.items():
        cab = []
        for i, (tipo, texto) in enumerate(msgs):
            bid = f"b{i}"
            corpo = {"thinking": {"text": texto}} if tipo == "pensa" else {"text": texto}
            db.execute("insert into cursorDiskKV values (?, ?)", (f"bubbleId:{cid}:{bid}", json.dumps(
                {"bubbleId": bid, "startedAtMs": 1795636720318 + i, **corpo})))
            cab.append({"bubbleId": bid, "type": 1 if tipo == 1 else 2})
        db.execute("insert into cursorDiskKV values (?, ?)", (f"composerConvHeaders:{cid}:000000", json.dumps(cab)))
        db.execute("insert into cursorDiskKV values (?, ?)", (f"composerData:{cid}", json.dumps({
            "composerId": cid, "name": nome, "lastUpdatedAt": len(msgs),
            "workspaceIdentifier": {"uri": {"fsPath": CWD}}})))
    db.commit()
    db.close()


class TestApp(Base):
    CID = "2f68c782-98fc-4fd7-8eee-7139b28359ef"

    def test_le_a_conversa_do_app_sem_raciocinio(self):
        banco_app(cursor.banco_app(), {self.CID: ("Saudacao", [(1, "oi"), ("pensa", "segredo do modelo"),
                                                               (2, "Oi."), (2, "Ajudo?")])})
        self.assertEqual([c for c, _d in cursor.conversas_app()], [self.CID])
        conv, assin = cursor.ler_conversa_app(self.CID)
        self.assertEqual([(t.papel, t.texto) for t in conv.turnos], [("usuario", "oi"), ("assistente", "Oi.\n\nAjudo?")])
        self.assertEqual((conv.titulo, conv.cwd), ("Saudacao", CWD))
        banco_app(cursor.banco_app(), {self.CID: ("Saudacao", [(1, "oi"), (2, "Oi."), (1, "e ai?")])})
        self.assertNotEqual(cursor.ler_conversa_app(self.CID)[1], assin)

    def test_sem_app_ou_rascunho_nao_lista(self):
        self.assertEqual(cursor.conversas_app(), [])
        banco_app(cursor.banco_app(), {self.CID: ("x", [])})
        self.assertEqual(cursor.conversas_app(), [])


class TestGancho(unittest.TestCase):
    def chamar(self, entrada):
        with mock.patch.object(gancho, "lancar") as lancar, mock.patch.object(gancho, "quando_rodar", return_value=("agora", 0)), \
                mock.patch("sys.stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(entrada).encode()))):
            gancho_cursor.main()
        return lancar

    def test_session_end_e_stop_lancam_so_a_conversa(self):
        aid = "11111111-1111-4111-8111-111111111111"
        for evento in ("sessionEnd", "stop"):
            lancar = self.chamar({"hook_event_name": evento, "conversation_id": aid})
            self.assertEqual(lancar.call_args.args[0], ["--direcao", "claude-de-cursor", "--cursor", aid])

    def test_id_invalido_ou_outro_evento_nao_lanca(self):
        self.chamar({"hook_event_name": "stop", "conversation_id": "../x"}).assert_not_called()
        self.chamar({"hook_event_name": "afterFileEdit", "conversation_id": "11111111-1111-4111-8111-111111111111"}
                    ).assert_not_called()
        self.chamar(["nao", "e", "objeto"]).assert_not_called()


class TestInstalar(unittest.TestCase):
    def test_hooks_mantem_os_dos_outros_e_nao_duplica(self):
        atual = {"version": 1, "hooks": {"stop": [{"command": "outro.exe"},
                                                  {"command": "py antigo/" + instalar.GANCHO_CURSOR}],
                                         "afterFileEdit": [{"command": "fmt"}]}}
        novo = instalar.montar_hooks_cursor(atual)
        self.assertEqual([h["command"] for h in novo["hooks"]["stop"]], ["outro.exe", instalar.comando_gancho_cursor()])
        self.assertEqual(novo["hooks"]["afterFileEdit"], [{"command": "fmt"}])
        self.assertEqual(len(novo["hooks"]["sessionEnd"]), 1)
        self.assertEqual(instalar.montar_hooks_cursor(novo), novo)
        self.assertEqual(atual["hooks"]["stop"][1]["command"], "py antigo/" + instalar.GANCHO_CURSOR)   # sem efeito colateral

    def test_hooks_que_nao_se_entende_nao_mexe(self):
        self.assertIsNone(instalar.montar_hooks_cursor({"hooks": []}))
        self.assertIsNone(instalar.montar_hooks_cursor({"hooks": {"stop": "x"}}))
        self.assertEqual(instalar.montar_hooks_cursor(None)["version"], 1)

    def test_mcp_do_claude_por_cima_e_o_do_cursor_fica(self):
        claude = {"mcpServers": {"a": {"type": "stdio", "command": "x", "args": ["1"]},
                                 "b": {"type": "http", "url": "https://h", "headers": {"H": "v"}},
                                 "ruim": "texto", "vazio": {"type": "stdio"}}}
        with mock.patch.object(instalar, "ler_json", return_value=claude):
            novo = instalar.montar_mcp_cursor({"mcpServers": {"a": {"command": "velho"}, "so-cursor": {"url": "u"}}})
        self.assertEqual(novo["mcpServers"], {"a": {"command": "x", "args": ["1"]},
                                              "b": {"url": "https://h", "headers": {"H": "v"}},
                                              "so-cursor": {"url": "u"}})


if __name__ == "__main__":
    unittest.main()
