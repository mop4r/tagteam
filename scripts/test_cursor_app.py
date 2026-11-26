"""Claude -> app do Cursor (cursor_app.py e sincronizar.para_cursor_app) num state.vscdb sintetico: cria, fica igual,
atualiza, versao nova quando continuada no app, apagada nao volta, app aberto e formato desconhecido nao gravam, e a
copia nao continuada nao volta ao Claude."""
import collections
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import cursor  # noqa: E402
import cursor_app  # noqa: E402
import sincronizar  # noqa: E402
from claude_jsonl import Conversa, Turno  # noqa: E402

CWD = "C:\\Users\\Fulano\\workspace"
WID = "6fe2a811ba455f648addbe8afca0b44b"
IDENT = {"id": WID, "uri": {"$mid": 1, "fsPath": "c:\\Users\\Fulano\\workspace", "scheme": "file",
                            "path": "/C:/Users/Fulano/workspace"}}
NATIVA = "2f68c782-98fc-4fd7-8eee-7139b28359ef"
SEGREDO = "texto privado da conversa nativa"


def banco(caminho, v=18, colunas=cursor_app.CAMPOS_HEADERS):
    """Banco do app com uma conversa nativa (o molde) na pasta CWD."""
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    db = sqlite3.connect(caminho)
    db.execute("create table cursorDiskKV (key text unique on conflict replace, value blob)")
    db.execute("create table ItemTable (key text unique on conflict replace, value blob)")
    db.execute(f"create table composerHeaders ({', '.join(c + (' text primary key' if c == 'composerId' else '') for c in colunas)})")
    bolhas = [("u1", 1, SEGREDO), ("a1", 2, "resposta " + SEGREDO)]
    for bid, tipo, texto in bolhas:
        db.execute("insert into cursorDiskKV values (?, ?)", (f"bubbleId:{NATIVA}:{bid}", json.dumps(
            {"_v": 3, "bubbleId": bid, "type": tipo, "text": texto, "requestId": "req-privado",
             "richText": json.dumps({"root": SEGREDO}), "conversationState": "~privado", "capabilities": []})))
    db.execute("insert into cursorDiskKV values (?, ?)", (f"composerConvHeaders:{NATIVA}:000000", json.dumps(
        [{"bubbleId": b, "type": t} for b, t, _x in bolhas])))
    db.execute("insert into cursorDiskKV values (?, ?)", (f"composerData:{NATIVA}", json.dumps(
        {"_v": v, "composerId": NATIVA, "name": SEGREDO, "conversationState": "~privado", "text": "",
         "status": "completed", "unifiedMode": "agent", "workspaceIdentifier": IDENT,
         "headerLog": {"chunkSize": 256, "chunkCount": 1, "count": 2}})))
    if colunas == cursor_app.CAMPOS_HEADERS:
        head = {"type": "head", "composerId": NATIVA, "name": SEGREDO, "subtitle": SEGREDO, "unifiedMode": "agent",
                "workspaceIdentifier": IDENT}
        db.execute("insert into composerHeaders values (?,?,?,?,0,0,?,NULL,'',?)",
                   (NATIVA, WID, 1, 2, 2, json.dumps(head)))
    db.commit()
    db.close()


def conversa(perguntas, titulo="Do Claude"):
    c = Conversa(sessao="s1", cwd=CWD)
    quando = datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc)
    for p in perguntas:
        c.turnos += [Turno("usuario", p, quando), Turno("assistente", "r:" + p, quando)]
    c.titulo = titulo
    return c


class Base(unittest.TestCase):
    def setUp(self):
        self.raiz = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.raiz, True)
        self.db = os.path.join(self.raiz, "app", "state.vscdb")
        p = mock.patch.dict(os.environ, {"TAGTEAM_CURSOR_APP_DB": self.db,
                                         "TAGTEAM_CURSOR_HOME": os.path.join(self.raiz, "cursor")})
        p.start()
        self.addCleanup(p.stop)

    def conexao(self):
        c = sqlite3.connect(self.db)
        self.addCleanup(c.close)
        return c


class TestCursorApp(Base):
    def test_grava_e_o_leitor_do_app_le_de_volta(self):
        banco(self.db)
        c = self.conexao()
        molde = cursor_app.achar_molde(c)
        cid = cursor_app.id_para_sessao("s1")
        n = cursor_app.gravar(c, conversa(["oi", "tudo bem?"]), cid, WID, IDENT, molde, 1795700000000)
        self.assertEqual(n, 4)
        self.assertEqual(cursor_app.mensagens(c, cid), 4)
        conv, _assin = cursor.ler_conversa_app(cid)
        self.assertEqual([(t.papel, t.texto) for t in conv.turnos],
                         [("usuario", "oi"), ("assistente", "r:oi"), ("usuario", "tudo bem?"), ("assistente", "r:tudo bem?")])
        self.assertEqual(conv.titulo, "Do Claude")
        linha = c.execute("select workspaceId, value from composerHeaders where composerId = ?", (cid,)).fetchone()
        self.assertEqual(linha[0], WID)
        self.assertEqual(json.loads(linha[1])["name"], "Do Claude")

    def test_nada_da_conversa_nativa_vaza_para_a_copia(self):
        banco(self.db)
        c = self.conexao()
        cid = cursor_app.id_para_sessao("s1")
        cursor_app.gravar(c, conversa(["oi"]), cid, WID, IDENT, cursor_app.achar_molde(c), 1795700000000)
        tudo = " ".join(str(v) for (v,) in c.execute("select value from cursorDiskKV where key like ?", (f"%{cid}%",)))
        tudo += " ".join(str(v) for (v,) in c.execute("select value from composerHeaders where composerId = ?", (cid,)))
        self.assertNotIn(SEGREDO, tudo)
        self.assertNotIn("req-privado", tudo)
        self.assertNotIn("~privado", tudo)

    def test_regravar_troca_as_mensagens_sem_sobrar_as_velhas(self):
        banco(self.db)
        c = self.conexao()
        molde, cid = cursor_app.achar_molde(c), cursor_app.id_para_sessao("s1")
        cursor_app.gravar(c, conversa(["a", "b", "c"]), cid, WID, IDENT, molde, 1)
        cursor_app.gravar(c, conversa(["x"]), cid, WID, IDENT, molde, 2)
        self.assertEqual(cursor_app.mensagens(c, cid), 2)
        self.assertEqual(c.execute("select count(*) from cursorDiskKV where key like ?", (f"bubbleId:{cid}:%",)).fetchone()[0], 2)
        self.assertEqual(c.execute("select count(*) from composerHeaders where composerId = ?", (cid,)).fetchone()[0], 1)

    def test_molde_ignora_as_copias_e_sem_nativa_nao_ha_molde(self):
        banco(self.db)
        c = self.conexao()
        self.assertIsNotNone(cursor_app.achar_molde(c))
        self.assertIsNone(cursor_app.achar_molde(c, nossos={NATIVA}))
        cursor_app.apagar(c, NATIVA)
        self.assertIsNone(cursor_app.achar_molde(c))

    def test_pastas_conhecidas_pelo_app(self):
        banco(self.db)
        c = self.conexao()
        outra = {"id": "abc", "uri": {"fsPath": "d:\\Projetos\\x"}}
        c.execute("insert into ItemTable values (?, ?)", ("cursor/glass.additionalProjects",
                                                          json.dumps([{"type": "workspace", "workspaceIdentifier": outra}])))
        c.commit()
        p = cursor_app.projetos(c)
        self.assertEqual(p[cursor_app.chave_da_pasta(CWD)][0], WID)        # a letra e as barras nao importam
        self.assertEqual(p[cursor_app.chave_da_pasta("D:/Projetos/x/")][0], "abc")

    def test_formato_mudado_e_recusado(self):
        banco(self.db, colunas=("composerId", "value"))
        with self.assertRaises(cursor_app.FormatoDesconhecido):
            cursor_app.conferir_formato(self.conexao())

    def test_muitas_mensagens_em_varios_pedacos(self):
        banco(self.db)
        c = self.conexao()
        cid = cursor_app.id_para_sessao("s1")
        cursor_app.gravar(c, conversa([f"p{i}" for i in range(200)]), cid, WID, IDENT, cursor_app.achar_molde(c), 1)
        self.assertEqual(cursor_app.mensagens(c, cid), 400)
        self.assertEqual(len(cursor.ler_conversa_app(cid)[0].turnos), 400)

    def test_id_deterministico(self):
        self.assertEqual(cursor_app.id_para_sessao("s1", 0), cursor_app.id_para_sessao("s1", 0))
        self.assertNotEqual(cursor_app.id_para_sessao("s1", 0), cursor_app.id_para_sessao("s1", 1))
        memoria = sqlite3.connect(":memory:")
        self.addCleanup(memoria.close)
        with self.assertRaises(ValueError):
            cursor_app.apagar(memoria, "x%")


def args(**kw):
    base = dict(sessao=None, cascade=None, grok=None, codex=None, cursor=None, so=None, seco=False, forcar=False)
    base.update(kw)
    return SimpleNamespace(**base)


class TestFluxo(Base):
    SESSAO = "22222222-2222-2222-2222-222222222222"

    def setUp(self):
        super().setUp()
        banco(self.db)
        self.projetos = os.path.join(self.raiz, "projects")
        os.makedirs(os.path.join(self.projetos, "C--w"))
        for nome, valor in (("PROJETOS", self.projetos), ("PASTA_ESTADO", self.raiz),
                            ("ESTADO", os.path.join(self.raiz, "estado.json")),
                            ("MOLDE_CURSOR_APP", os.path.join(self.raiz, "molde-app.json"))):
            p = mock.patch.object(sincronizar, nome, valor)
            p.start()
            self.addCleanup(p.stop)
        for alvo, nome, valor in ((sincronizar, "cwd_local", lambda c: c), (cursor_app, "app_aberto", lambda: False),
                                  (sincronizar.para_claude, "projetos_claude", lambda: self.projetos),
                                  (sincronizar.para_claude, "versao_claude", lambda: "9.9.9")):
            p = mock.patch.object(alvo, nome, valor)
            p.start()
            self.addCleanup(p.stop)
        self.caminho = os.path.join(self.projetos, "C--w", self.SESSAO + ".jsonl")
        self.escrever(["oi", "tudo bem?"])

    def escrever(self, perguntas, entrypoint="cli"):
        with open(self.caminho, "w", encoding="utf-8") as f:
            for i, p in enumerate(perguntas):
                for tipo, msg in (("user", {"role": "user", "content": p}),
                                  ("assistant", {"role": "assistant", "content": [{"type": "text", "text": "r" + p}]})):
                    f.write(json.dumps({"type": tipo, "entrypoint": entrypoint, "cwd": CWD, "sessionId": self.SESSAO,
                                        "timestamp": f"2026-11-26T10:0{i}:00Z", "message": msg}) + "\n")
        agora = time.time() - 60 + len(perguntas)
        os.utime(self.caminho, (agora, agora))

    def rodar(self, estado, funcao=None, **kw):
        cont = collections.Counter()
        with mock.patch("sys.stdout", io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            (funcao or sincronizar.para_cursor_app)(args(**kw), estado, cont)
        return cont

    def continuar_no_app(self, cid, texto):
        """Simula a pessoa continuando a copia no app: mais uma mensagem e o headerLog do app atualizado."""
        c = self.conexao()
        cab = json.loads(c.execute("select value from cursorDiskKV where key = ?", (f"composerConvHeaders:{cid}:000000",)).fetchone()[0])
        cab.append({"bubbleId": "nova", "type": 1})
        dados = json.loads(c.execute("select value from cursorDiskKV where key = ?", (f"composerData:{cid}",)).fetchone()[0])
        dados["headerLog"]["count"] = len(cab)
        dados.pop("tagteam", None)                      # o app pode descartar o campo da marca ao regravar
        c.execute("update cursorDiskKV set value = ? where key = ?", (json.dumps(cab), f"composerConvHeaders:{cid}:000000"))
        c.execute("update cursorDiskKV set value = ? where key = ?", (json.dumps(dados), f"composerData:{cid}"))
        c.execute("insert into cursorDiskKV values (?, ?)", (f"bubbleId:{cid}:nova", json.dumps({"text": texto, "type": 1})))
        c.commit()

    def test_cria_fica_igual_e_atualiza_no_mesmo_lugar(self):
        estado = sincronizar.ler_estado()
        self.assertEqual(self.rodar(estado)["c>ua criadas"], 1)
        rec = dict(estado["importadas_cursor_app"][self.SESSAO])
        self.assertEqual(self.rodar(estado)["c>ua iguais"], 1)
        self.escrever(["oi", "tudo bem?", "mais uma"])
        self.assertEqual(self.rodar(estado)["c>ua atualizadas"], 1)
        novo = estado["importadas_cursor_app"][self.SESSAO]
        self.assertEqual((novo["id"], novo["mensagens"]), (rec["id"], 6))
        self.assertEqual(sincronizar.ler_estado()["importadas_cursor_app"][self.SESSAO]["mensagens"], 6)   # gravado

    def test_continuada_no_app_vira_versao_nova_e_a_continuada_fica(self):
        estado = sincronizar.ler_estado()
        self.rodar(estado)
        velho = estado["importadas_cursor_app"][self.SESSAO]["id"]
        self.continuar_no_app(velho, "continuei no app")
        self.escrever(["oi", "tudo bem?", "andou no claude"])
        self.assertEqual(self.rodar(estado)["c>ua versao_nova"], 1)
        rec = estado["importadas_cursor_app"][self.SESSAO]
        self.assertNotEqual(rec["id"], velho)
        c = self.conexao()
        self.assertEqual(cursor_app.mensagens(c, velho), 5)                 # a continuada ficou intacta
        self.assertEqual(cursor_app.mensagens(c, rec["id"]), 6)

    def test_apagada_no_app_nao_volta_so_com_forcar(self):
        estado = sincronizar.ler_estado()
        self.rodar(estado)
        cursor_app.apagar(self.conexao(), estado["importadas_cursor_app"][self.SESSAO]["id"])
        self.escrever(["oi", "tudo bem?", "mais"])
        self.assertEqual(self.rodar(estado)["c>ua apagadas_no_app"], 1)
        self.assertEqual(self.rodar(estado, forcar=True)["c>ua atualizadas"], 1)

    def test_app_aberto_nao_grava_nada(self):
        estado = sincronizar.ler_estado()
        with mock.patch.object(cursor_app, "app_aberto", lambda: True):
            self.assertEqual(self.rodar(estado)["c>ua app_aberto"], 1)
        self.assertEqual(estado["importadas_cursor_app"], {})
        self.assertEqual(self.conexao().execute("select count(*) from composerHeaders").fetchone()[0], 1)

    def test_formato_desconhecido_nao_grava(self):
        os.remove(self.db)
        banco(self.db, v=99)
        estado = sincronizar.ler_estado()
        self.assertEqual(self.rodar(estado)["c>ua formato_desconhecido"], 1)
        self.assertEqual(self.conexao().execute("select count(*) from composerHeaders").fetchone()[0], 1)

    def test_pasta_que_o_app_nao_conhece_fica_de_fora(self):
        estado = sincronizar.ler_estado()
        with mock.patch.object(sincronizar, "cwd_local", lambda c: "D:\\outra"):
            self.assertEqual(self.rodar(estado)["c>ua fora_das_pastas_do_app"], 1)

    def test_so_as_mais_recentes_vao(self):
        mais_nova = "33333333-3333-3333-3333-333333333333"
        shutil.copy(self.caminho, os.path.join(self.projetos, "C--w", mais_nova + ".jsonl"))
        os.utime(self.caminho, (time.time() - 3600,) * 2)
        estado = sincronizar.ler_estado()
        with mock.patch.object(sincronizar, "MAX_APP_CURSOR", 1):
            self.assertEqual(self.rodar(estado)["c>ua criadas"], 1)
        self.assertEqual(list(estado["importadas_cursor_app"]), [mais_nova])

    def test_sessao_velha_nao_vai(self):
        os.utime(self.caminho, (time.time() - 40 * 86400,) * 2)
        self.assertEqual(sum(self.rodar(sincronizar.ler_estado()).values()), 0)

    def test_sem_nativa_usa_o_molde_guardado(self):
        estado = sincronizar.ler_estado()
        self.rodar(estado)                                  # guarda o molde
        cursor_app.apagar(self.conexao(), NATIVA)
        outra = "33333333-3333-3333-3333-333333333333"
        shutil.copy(self.caminho, os.path.join(self.projetos, "C--w", outra + ".jsonl"))
        self.assertEqual(self.rodar(estado)["c>ua criadas"], 1)
        with open(os.path.join(self.raiz, "molde-app.json"), encoding="utf-8") as f:
            self.assertNotIn(SEGREDO, f.read())

    def test_copia_nao_continuada_nao_volta_ao_claude_e_a_continuada_volta(self):
        estado = sincronizar.ler_estado()
        self.rodar(estado)
        cid = estado["importadas_cursor_app"][self.SESSAO]["id"]
        novas = set()
        cont = collections.Counter()
        with mock.patch("sys.stdout", io.StringIO()):
            sincronizar.cursor_para_claude(args(), estado, cont, novas)
        self.assertEqual((cont["u>c espelhos"], cont["u>c criadas"]), (1, 1))   # a nativa do molde vai; a copia nao
        novas.clear()
        self.continuar_no_app(cid, "continuei no app")
        cont = collections.Counter()
        with mock.patch("sys.stdout", io.StringIO()):
            sincronizar.cursor_para_claude(args(cursor=cid), estado, cont, novas)
        self.assertEqual(cont["u>c criadas"], 1)

    def test_marca_na_conversa_vale_sem_o_estado(self):
        estado = sincronizar.ler_estado()
        self.rodar(estado)
        cid = estado["importadas_cursor_app"][self.SESSAO]["id"]
        dados = json.loads(self.conexao().execute("select value from cursorDiskKV where key = ?",
                                                  (f"composerData:{cid}",)).fetchone()[0])
        self.assertTrue(sincronizar._copia_nao_continuada(cid, dados, {}))
        dados["headerLog"]["count"] += 1
        self.assertFalse(sincronizar._copia_nao_continuada(cid, dados, {}))

    def test_veio_do_app_nao_volta_para_o_app_mas_vai_ao_cli(self):
        self.escrever(["oi", "tudo bem?"], entrypoint="cursor-import")
        estado = sincronizar.ler_estado()
        estado["exportadas_cursor"]["44444444-4444-4444-4444-444444444444"] = {
            "sessao": self.SESSAO, "versao": 0, "turnos": 4, "assinatura": "x", "de": "app"}
        self.assertEqual(self.rodar(estado)["c>ua espelhos"], 1)
        conv = sincronizar.claude_jsonl.ler_conversa(self.caminho, self.SESSAO)
        self.assertFalse(sincronizar._espelho_cursor(conv, self.SESSAO, estado, "cli"))     # o CLI recebe

    def test_veio_do_cli_vai_ao_app(self):
        self.escrever(["oi", "tudo bem?"], entrypoint="cursor-import")
        estado = sincronizar.ler_estado()
        aid = "55555555-5555-4555-8555-555555555555"
        estado["exportadas_cursor"][aid] = {"sessao": self.SESSAO, "versao": 0, "turnos": 4, "assinatura": "x"}
        os.makedirs(os.path.join(cursor.pasta_chats(), "abc", aid))        # registro antigo: a origem vem da pasta
        self.assertEqual(self.rodar(estado)["c>ua criadas"], 1)
        conv = sincronizar.claude_jsonl.ler_conversa(self.caminho, self.SESSAO)
        self.assertTrue(sincronizar._espelho_cursor(conv, self.SESSAO, estado, "cli"))      # e nao volta ao CLI


if __name__ == "__main__":
    unittest.main()
