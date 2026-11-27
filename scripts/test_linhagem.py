"""Testes da Linhagem Unica: fast-forward multi-hop sem duplicatas, fork explicito em divergencia real e origem nativa."""
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
import antigravity_db as antigravity  # noqa: E402
import claude_jsonl  # noqa: E402
import codex  # noqa: E402
import cursor  # noqa: E402
import cursor_app  # noqa: E402
import grok  # noqa: E402
import linhagem  # noqa: E402
import para_claude  # noqa: E402
import sincronizar  # noqa: E402
import test_conversas  # noqa: E402
import test_cursor  # noqa: E402
import test_cursor_app  # noqa: E402
from claude_jsonl import Turno  # noqa: E402

CWD = test_cursor_app.CWD
T0 = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)


def args(**kw):
    base = dict(sessao=None, cascade=None, grok=None, codex=None, cursor=None, so=None, seco=False, forcar=False)
    base.update(kw)
    return SimpleNamespace(**base)


def escrever_claude(caminho, sessao, pares, titulo="Conversa unica", entrypoint="cli", minuto_base=0):
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    pai = None
    with open(caminho, "w", encoding="utf-8", newline="\n") as f:
        for i, (u, a) in enumerate(pares):
            for papel, texto in (("user", u), ("assistant", a)):
                uid = f"u-{i}-{papel}"
                ts = f"2026-11-26T12:{minuto_base + i:02d}:{0 if papel == 'user' else 30:02d}.000Z"
                msg = {"role": "user", "content": texto} if papel == "user" else {
                    "role": "assistant", "content": [{"type": "text", "text": texto}]}
                f.write(json.dumps({"type": papel, "parentUuid": pai, "uuid": uid, "isSidechain": False,
                                    "entrypoint": entrypoint, "cwd": CWD, "sessionId": sessao,
                                    "version": "2.1.0", "timestamp": ts, "message": msg}, ensure_ascii=False) + "\n")
                pai = uid
        f.write(json.dumps({"type": "ai-title", "aiTitle": titulo, "sessionId": sessao}, ensure_ascii=False) + "\n")
        f.write(json.dumps({"type": "cost-state", "cost": 0.01}, ensure_ascii=False) + "\n")
    ts_m = T0.timestamp() + (minuto_base + len(pares)) * 60
    os.utime(caminho, (ts_m, ts_m))


def acrescentar_no_antigravity(base, cascade, molde, pares, minuto_base=10):
    """Adiciona passos concluidos no .db do Antigravity mantendo a tabela claude_import intacta."""
    db_path = os.path.join(base, "conversations", cascade + ".db")
    c = sqlite3.connect(db_path)
    try:
        traj = c.execute("select trajectory_id from trajectory_meta").fetchone()[0]
        prox = (c.execute("select max(idx) from steps").fetchone()[0] or -1) + 1
        for i, (u, a) in enumerate(pares):
            q_u = datetime(2026, 11, 26, 12, minuto_base + i, 0, tzinfo=timezone.utc)
            q_a = datetime(2026, 11, 26, 12, minuto_base + i, 30, tzinfo=timezone.utc)
            for t in (Turno("usuario", u, q_u), Turno("assistente", a, q_a)):
                tipo, meta, payload = antigravity.montar_passo(t, prox, f"exec-{prox}", molde, cascade, traj)
                c.execute("insert into steps (idx, step_type, status, has_subtrajectory, metadata, step_payload, step_format) "
                          "values (?,?,?,0,?,?,0)", (prox, tipo, antigravity.STATUS_FEITO, meta, payload))
                prox += 1
        c.commit()
    finally:
        c.close()


def acrescentar_no_grok(pasta, sid, pergunta, resposta, minuto=20):
    """Acrescenta um par pergunta/resposta no updates.jsonl do Grok (como o Grok CLI faz)."""
    ts_u = int(datetime(2026, 11, 26, 12, minuto, 0, tzinfo=timezone.utc).timestamp() * 1_000_000)
    ts_a = ts_u + 10_000_000
    linhas = [
        {"timestamp": ts_u // 1_000_000, "timestamp_us": ts_u, "method": "session/update",
         "params": {"sessionId": sid, "update": {"sessionUpdate": "user_message_chunk",
                                                 "content": {"type": "text", "text": pergunta}}}},
        {"timestamp": ts_a // 1_000_000, "timestamp_us": ts_a, "method": "session/update",
         "params": {"sessionId": sid, "update": {"sessionUpdate": "agent_message_chunk",
                                                 "content": {"type": "text", "text": resposta}}}},
    ]
    with open(os.path.join(pasta, "updates.jsonl"), "a", encoding="utf-8", newline="\n") as f:
        for ln in linhas:
            f.write(json.dumps(ln, ensure_ascii=False) + "\n")


def acrescentar_no_cursor_app(db_path, cid, pergunta, resposta, minuto=30):
    """Adiciona uma pergunta e uma resposta no state.vscdb do app do Cursor."""
    c = sqlite3.connect(db_path)
    try:
        cab = json.loads(c.execute("select value from cursorDiskKV where key = ?",
                                   (f"composerConvHeaders:{cid}:000000",)).fetchone()[0])
        ms_u = int(datetime(2026, 11, 26, 12, minuto, 0, tzinfo=timezone.utc).timestamp() * 1000)
        ms_a = ms_u + 15_000
        bu, ba = f"bu-{minuto}", f"ba-{minuto}"
        cab.append({"bubbleId": bu, "type": 1, "startedAtMs": ms_u})
        cab.append({"bubbleId": ba, "type": 2, "startedAtMs": ms_a})
        dados = json.loads(c.execute("select value from cursorDiskKV where key = ?",
                                     (f"composerData:{cid}",)).fetchone()[0])
        dados["headerLog"]["count"] = len(cab)
        dados["lastUpdatedAt"] = ms_a
        c.execute("update cursorDiskKV set value = ? where key = ?", (json.dumps(cab), f"composerConvHeaders:{cid}:000000"))
        c.execute("update cursorDiskKV set value = ? where key = ?", (json.dumps(dados), f"composerData:{cid}"))
        c.execute("insert into cursorDiskKV values (?, ?)", (f"bubbleId:{cid}:{bu}", json.dumps({"text": pergunta, "type": 1, "startedAtMs": ms_u})))
        c.execute("insert into cursorDiskKV values (?, ?)", (f"bubbleId:{cid}:{ba}", json.dumps({"text": resposta, "type": 2, "startedAtMs": ms_a})))
        c.commit()
    finally:
        c.close()


class TestAcrescentarTurnos(unittest.TestCase):
    def setUp(self):
        self.raiz = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.raiz, True)
        self.sessao = "11111111-1111-4111-8111-111111111111"
        self.arq = os.path.join(self.raiz, self.sessao + ".jsonl")
        escrever_claude(self.arq, self.sessao, [("oi", "ola")])

    def test_preserva_bytes_originais_e_encadeia_parent_uuid(self):
        with open(self.arq, "rb") as f:
            antes_bytes = f.read()
        marca = linhagem.marca_arquivo(self.arq)
        q2 = datetime(2026, 11, 26, 12, 10, tzinfo=timezone.utc)
        novos = [Turno("usuario", "no agy", q2), Turno("assistente", "resp agy", q2)]
        res = para_claude.acrescentar_turnos(self.arq, self.sessao, novos, "2.1.0", "antigravity", marca_esperada=marca)
        self.assertIsNotNone(res)
        with open(self.arq, "rb") as f:
            depois_bytes = f.read()
        self.assertTrue(depois_bytes.startswith(antes_bytes))
        linhas = [json.loads(x) for x in depois_bytes.decode("utf-8").splitlines()]
        msgs = [x for x in linhas if x.get("type") in ("user", "assistant")]
        self.assertEqual(len(msgs), 4)
        self.assertEqual(msgs[2]["parentUuid"], "u-0-assistant")
        self.assertEqual(msgs[3]["parentUuid"], msgs[2]["uuid"])
        conv = claude_jsonl.ler_conversa(self.arq, self.sessao)
        self.assertEqual([(t.papel, t.texto) for t in conv.turnos],
                         [("usuario", "oi"), ("assistente", "ola"), ("usuario", "no agy"), ("assistente", "resp agy")])
        self.assertEqual(conv.entrypoint, "cli")

    def test_linha_quebrada_ou_toctou_recusa_sem_corromper(self):
        marca = linhagem.marca_arquivo(self.arq)
        q2 = datetime(2026, 11, 26, 12, 10, tzinfo=timezone.utc)
        self.assertIsNone(para_claude.acrescentar_turnos(
            self.arq, self.sessao, [Turno("usuario", "x", q2)], "2.1.0", "grok", marca_esperada=(marca[0] + 1, marca[1])))
        with open(self.arq, "ab") as f:
            f.write(b'{"type":"user","incompleta')
        self.assertIsNone(para_claude.acrescentar_turnos(self.arq, self.sessao, [Turno("usuario", "x", q2)], "2.1.0", "grok"))


class TestLinhagemMultiHop(unittest.TestCase):
    """Testa o fluxo completo de revezamento (fast-forward) e divergencia real entre todas as IAs."""

    def setUp(self):
        self.raiz = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.raiz, True)
        self.projetos = os.path.join(self.raiz, "projects")
        os.makedirs(os.path.join(self.projetos, "C--w"))
        self.agy_cli = os.path.join(self.raiz, "antigravity-cli")
        self.molde_agy = test_conversas.molde_falso(self.agy_cli)
        self.grok_home = os.path.join(self.raiz, ".grok")
        os.makedirs(os.path.join(self.grok_home, "sessions"))
        self.cursor_home = os.path.join(self.raiz, ".cursor")
        self.cursor_db = os.path.join(self.raiz, "cursor-app", "state.vscdb")
        test_cursor_app.banco(self.cursor_db)

        patches = [
            mock.patch.dict(os.environ, {"TAGTEAM_CURSOR_HOME": self.cursor_home, "TAGTEAM_CURSOR_APP_DB": self.cursor_db}),
            mock.patch.object(sincronizar, "PROJETOS", self.projetos),
            mock.patch.object(sincronizar, "PASTA_ESTADO", self.raiz),
            mock.patch.object(sincronizar, "ESTADO", os.path.join(self.raiz, "estado.json")),
            mock.patch.object(sincronizar, "MOLDE_CURSOR", os.path.join(self.raiz, "molde-cursor.json")),
            mock.patch.object(sincronizar, "MOLDE_CURSOR_APP", os.path.join(self.raiz, "molde-app.json")),
            mock.patch.object(sincronizar, "PREPARO", os.path.join(self.raiz, "preparo")),
            mock.patch.object(sincronizar, "cwd_local", lambda c: c),
            mock.patch.object(para_claude, "projetos_claude", lambda: self.projetos),
            mock.patch.object(para_claude, "versao_claude", lambda: "2.1.0"),
            mock.patch.object(antigravity, "bases", lambda: [self.agy_cli]),
            mock.patch.object(antigravity, "achar_molde", lambda _b=None: self.molde_agy),
            mock.patch.object(grok, "base", lambda: self.grok_home),
            mock.patch.object(cursor_app, "app_aberto", lambda: False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        os.makedirs(cursor.pasta_chats(), exist_ok=True)
        test_cursor.nativa(["molde cursor"], "99999999-9999-4999-8999-999999999999")
        self.sessao = "22222222-2222-4222-8222-222222222222"
        self.arq = os.path.join(self.projetos, "C--w", self.sessao + ".jsonl")
        escrever_claude(self.arq, self.sessao, [("oi claude", "ola do claude")], titulo="Unica")

    def rodar(self, fn, estado, *extra, **kw):
        cont = collections.Counter()
        with mock.patch("sys.stdout", io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            fn(args(**kw), estado, cont, *extra)
        return cont

    def sessoes_claude(self):
        return [os.path.basename(p)[:-6] for p in sincronizar._sessoes_do_claude()]

    def test_revezamento_4_ias_sem_duplicar_e_sem_ping_pong(self):
        estado = sincronizar.ler_estado()
        # 0. Claude -> todas
        for fn in (sincronizar.para_antigravity, sincronizar.para_grok, sincronizar.para_cursor):
            self.rodar(fn, estado)
        self.assertEqual(self.sessoes_claude(), [self.sessao])

        # 1. Continua no Antigravity CLI -> fast-forward em self.sessao + propaga sem duplicar nem ping-pong
        agy_id = estado["importadas"]["antigravity-cli"][self.sessao]["cascade"]
        acrescentar_no_antigravity(self.agy_cli, agy_id, self.molde_agy, [("passo agy", "resp agy")], minuto_base=5)
        novas = set()
        c_a = self.rodar(sincronizar.para_claude_, estado, novas)
        self.assertEqual(c_a["a>c atualizadas"], 1)
        self.assertEqual(novas, {self.sessao})
        self.assertEqual(self.sessoes_claude(), [self.sessao])   # zero duplicata no Claude!
        self.assertEqual(len(claude_jsonl.ler_conversa(self.arq, self.sessao).turnos), 4)
        # propagacao: Agy CLI e igual (sem ping-pong); Grok, Cursor CLI e Cursor App atualizam
        self.assertEqual(self.rodar(sincronizar.para_antigravity, estado, so=novas)["c>a iguais"], 1)
        self.assertEqual(self.rodar(sincronizar.para_grok, estado, so=novas)["c>g atualizadas"], 1)
        c_u = self.rodar(sincronizar.para_cursor, estado, so=novas)
        self.assertEqual((c_u["c>u atualizadas"], c_u["c>ua atualizadas"]), (1, 1))
        self.assertEqual(len(os.listdir(os.path.join(self.agy_cli, "conversations"))), 1)
        self.assertEqual(len(grok.sessoes()), 1)

        # 2. Continua no Grok -> fast-forward em self.sessao + substitui a copia do Agy CLI (que ja tinha sido entregue!)
        g_rec = estado["importadas_grok"][self.sessao]
        acrescentar_no_grok(g_rec["pasta"], g_rec["id"], "passo grok", "resp grok", minuto=10)
        novas = set()
        self.assertEqual(self.rodar(sincronizar.grok_para_claude, estado, novas)["g>c atualizadas"], 1)
        self.assertEqual(novas, {self.sessao})
        self.assertEqual(len(claude_jsonl.ler_conversa(self.arq, self.sessao).turnos), 6)
        # Agy CLI substitui agy_id (que fora continuado no passo 1, mas ja estava entregue!) -> continua com 1 conversa!
        self.assertEqual(self.rodar(sincronizar.para_antigravity, estado, so=novas)["c>a atualizadas"], 1)
        self.assertEqual(len(os.listdir(os.path.join(self.agy_cli, "conversations"))), 1)
        self.assertEqual(self.rodar(sincronizar.para_grok, estado, so=novas)["c>g iguais"], 1)
        self.rodar(sincronizar.para_cursor, estado, so=novas)

        # 3. Continua no Cursor CLI -> fast-forward em self.sessao + substitui a copia do Grok -> 1 conversa em cada IA
        u_rec = estado["importadas_cursor"][self.sessao]
        test_cursor.continuar_no_cursor(u_rec["pasta"], ["oi claude", "passo agy", "passo grok", "passo cursor cli"])
        novas = set()
        self.assertEqual(self.rodar(sincronizar.cursor_para_claude, estado, novas, cursor=u_rec["id"])["u>c atualizadas"], 1)
        self.assertEqual(novas, {self.sessao})
        self.assertEqual(len(claude_jsonl.ler_conversa(self.arq, self.sessao).turnos), 8)
        self.assertEqual(self.rodar(sincronizar.para_grok, estado, so=novas)["c>g atualizadas"], 1)
        self.assertEqual(len(grok.sessoes()), 1)                 # zero duplicata no Grok!
        self.assertEqual(self.rodar(sincronizar.para_antigravity, estado, so=novas)["c>a atualizadas"], 1)
        self.assertEqual(len(os.listdir(os.path.join(self.agy_cli, "conversations"))), 1)
        c_u2 = self.rodar(sincronizar.para_cursor, estado, so=novas)
        self.assertEqual((c_u2["c>u iguais"], c_u2["c>ua atualizadas"]), (1, 1))

        # 4. Continua no app do Cursor -> fast-forward em self.sessao -> propaga para CLI do Cursor sem duplicar
        ua_rec = estado["importadas_cursor_app"][self.sessao]
        acrescentar_no_cursor_app(self.cursor_db, ua_rec["id"], "passo cursor app", "resp cursor app", minuto=20)
        novas = set()
        self.assertEqual(self.rodar(sincronizar.cursor_para_claude, estado, novas, cursor=ua_rec["id"])["u>c atualizadas"], 1)
        self.assertEqual(novas, {self.sessao})
        self.assertEqual(len(claude_jsonl.ler_conversa(self.arq, self.sessao).turnos), 10)
        c_u3 = self.rodar(sincronizar.para_cursor, estado, so=novas)
        self.assertEqual((c_u3["c>u atualizadas"], c_u3["c>ua iguais"]), (1, 1))
        self.rodar(sincronizar.para_antigravity, estado, so=novas)
        self.rodar(sincronizar.para_grok, estado, so=novas)

        # 5. Rodada seguinte e 100% idempotente (zero ping-pong, zero criadas/atualizadas)
        for fn, extra in ((sincronizar.para_antigravity, ()), (sincronizar.para_grok, ()), (sincronizar.para_cursor, ()),
                          (sincronizar.para_claude_, (set(),)), (sincronizar.grok_para_claude, (set(),))):
            c_idem = self.rodar(fn, estado, *extra)
            for k in c_idem:
                self.assertNotIn("criadas", k)
                self.assertNotIn("atualizadas", k)
        self.assertEqual(self.sessoes_claude(), [self.sessao])
        self.assertEqual(len(os.listdir(os.path.join(self.agy_cli, "conversations"))), 1)
        self.assertEqual(len(grok.sessoes()), 1)
        # Cursor CLI tem so o molde + a nossa (2 no total); Cursor App tem so o molde + a nossa (2 no total)
        self.assertEqual(len(cursor.conversas()), 2)
        self.assertEqual(len(cursor.conversas_app()), 2)

    def test_divergencia_real_faz_fork_com_titulo_de_ramo_sem_perder_nada(self):
        estado = sincronizar.ler_estado()
        self.rodar(sincronizar.para_antigravity, estado)
        agy_id = estado["importadas"]["antigravity-cli"][self.sessao]["cascade"]
        # As DUAS IAs recebem mensagens novas ao mesmo tempo!
        escrever_claude(self.arq, self.sessao, [("oi claude", "ola do claude"), ("no claude", "resp claude")], titulo="Unica")
        acrescentar_no_antigravity(self.agy_cli, agy_id, self.molde_agy, [("no agy", "resp agy")], minuto_base=5)
        novas = set()
        self.assertEqual(self.rodar(sincronizar.para_claude_, estado, novas)["a>c criadas"], 1)
        self.assertEqual(len(novas), 1)
        fork_id = next(iter(novas))
        self.assertNotEqual(fork_id, self.sessao)
        orig = claude_jsonl.ler_conversa(self.arq, self.sessao)
        fork = claude_jsonl.ler_conversa(para_claude.achar_sessao(fork_id), fork_id)
        self.assertEqual([t.texto for t in orig.turnos], ["oi claude", "ola do claude", "no claude", "resp claude"])
        self.assertEqual([t.texto for t in fork.turnos], ["oi claude", "ola do claude", "no agy", "resp agy"])
        self.assertIn("(Antigravity branch)", fork.titulo)

    def test_codex_fast_forward_e_substituicao_ao_voltar_do_claude(self):
        estado = sincronizar.ler_estado()
        tid1 = "01a2a000-0000-7000-8000-000000000011"
        tid2 = "01a2a000-0000-7000-8000-000000000022"
        st = linhagem.marca_arquivo(self.arq)
        estado["importadas_codex"][self.sessao] = {
            "id": tid1, "versao": 0, "fonte_tamanho": st[0], "fonte_mtime": st[1], "turnos_claude": 2, "titulo": "Unica"}
        th_cont = {
            "id": tid1, "name": "Unica", "cwd": CWD, "createdAt": int(T0.timestamp()),
            "turns": [
                {"id": "external-import-turn-1", "startedAt": int(T0.timestamp()), "status": "completed",
                 "items": [{"type": "userMessage", "content": [{"type": "text", "text": "oi claude"}]}]},
                {"id": "01a2b000-0000-7000-8000-0000000000aa", "startedAt": int(T0.timestamp()) + 120,
                 "status": "completed",
                 "items": [{"type": "userMessage", "content": [{"type": "text", "text": "passo codex"}]},
                           {"type": "agentMessage", "text": "resp codex"}]},
            ],
        }
        novas = set()
        with mock.patch.object(codex, "listar", return_value={tid1: {"updatedAt": 100}}), \
                mock.patch.object(codex, "ler", return_value=th_cont):
            c_x = self.rodar(sincronizar.codex_para_claude, estado, novas, None)
        self.assertEqual(c_x["x>c atualizadas"], 1)
        self.assertEqual(novas, {self.sessao})
        self.assertEqual(len(claude_jsonl.ler_conversa(self.arq, self.sessao).turnos), 4)
        # Nao volta pro Codex na propagacao (iguais)
        with mock.patch.object(codex, "importar") as imp:
            self.assertEqual(self.rodar(sincronizar.para_codex, estado, None, so=novas)["c>x iguais"], 1)
            imp.assert_not_called()
        # Agora o Claude anda para 6 turnos -> Codex reimporta limpo em tid2 e apaga tid1 (zero duplicata!)
        escrever_claude(self.arq, self.sessao,
                        [("oi claude", "ola do claude"), ("passo codex", "resp codex"), ("passo claude", "resp claude")],
                        titulo="Unica", minuto_base=5)
        srv = mock.Mock()
        with mock.patch.object(codex, "listar", return_value={tid1: {"updatedAt": 100}}), \
                mock.patch.object(codex, "ler", return_value=th_cont), \
                mock.patch.object(codex, "esquecer_registro"), \
                mock.patch.object(codex, "importar", side_effect=lambda _s, sess, a_cada_lote=None: (
                    {codex.normalizar(sess[0][0]): tid2}, {})):
            c_back = self.rodar(sincronizar.para_codex, estado, srv)
        self.assertEqual(c_back["c>x atualizadas"], 1)
        self.assertEqual(estado["importadas_codex"][self.sessao]["id"], tid2)
        srv.chamar.assert_called_once_with("thread/delete", {"threadId": tid1})

    def test_origem_nativa_continuada_no_claude_substitui_e_arquiva_backup(self):
        os.remove(self.arq)                                      # so uma nativa do Grok existe
        sid_nat = "33333333-3333-4333-8333-333333333333"
        pasta_nat = os.path.join(self.grok_home, "sessions", "C%3A%5Cw", sid_nat)
        os.makedirs(pasta_nat)
        with open(os.path.join(pasta_nat, "summary.json"), "w", encoding="utf-8") as f:
            json.dump({"info": {"id": sid_nat, "cwd": CWD}, "generated_title": "Nativa Grok"}, f)
        acrescentar_no_grok(pasta_nat, sid_nat, "nativa", "resp nativa", minuto=1)
        estado = sincronizar.ler_estado()
        novas = set()
        self.assertEqual(self.rodar(sincronizar.grok_para_claude, estado, novas)["g>c criadas"], 1)
        s_nat = next(iter(novas))
        # Nao volta pro Grok enquanto igual (espelho da nativa)
        self.assertEqual(self.rodar(sincronizar.para_grok, estado, so=novas)["c>g espelhos"], 1)
        # Usuario continua s_nat no Claude -> substitui a nativa no Grok (1 conversa) e guarda backup em substituidas/
        arq_nat = para_claude.achar_sessao(s_nat)
        escrever_claude(arq_nat, s_nat, [("nativa", "resp nativa"), ("continuei no claude", "ok")],
                        titulo="[Grok] Nativa Grok", entrypoint="grok-import", minuto_base=5)
        self.assertEqual(self.rodar(sincronizar.para_grok, estado)["c>g atualizadas"], 1)
        self.assertEqual(len(grok.sessoes()), 1)
        self.assertFalse(os.path.exists(pasta_nat))
        self.assertTrue(os.path.isdir(os.path.join(self.raiz, "substituidas", "grok")))
        self.assertEqual(len(os.listdir(os.path.join(self.raiz, "substituidas", "grok"))), 1)

    def test_divergencia_entre_duas_ias_sem_mexer_no_claude(self):
        """Grok e Antigravity continuam a mesma conversa ao mesmo tempo: a 1a faz fast-forward e a 2a faz fork."""
        estado = sincronizar.ler_estado()
        self.rodar(sincronizar.para_antigravity, estado)
        self.rodar(sincronizar.para_grok, estado)
        agy_id = estado["importadas"]["antigravity-cli"][self.sessao]["cascade"]
        g_rec = estado["importadas_grok"][self.sessao]
        acrescentar_no_grok(g_rec["pasta"], g_rec["id"], "caminho grok", "resp grok", minuto=6)
        acrescentar_no_antigravity(self.agy_cli, agy_id, self.molde_agy, [("caminho agy", "resp agy")], minuto_base=7)
        # Grok chega primeiro -> fast-forward na sessao original
        n1 = set()
        self.assertEqual(self.rodar(sincronizar.grok_para_claude, estado, n1)["g>c atualizadas"], 1)
        self.assertEqual(n1, {self.sessao})
        # Antigravity chega depois -> ve que self.sessao ja passou de turnos_base -> fork com (Antigravity branch)
        n2 = set()
        self.assertEqual(self.rodar(sincronizar.para_claude_, estado, n2)["a>c criadas"], 1)
        fork_id = next(iter(n2))
        self.assertNotEqual(fork_id, self.sessao)
        self.assertIn("caminho grok", [t.texto for t in claude_jsonl.ler_conversa(self.arq, self.sessao).turnos])
        fork = claude_jsonl.ler_conversa(para_claude.achar_sessao(fork_id), fork_id)
        self.assertIn("caminho agy", [t.texto for t in fork.turnos])
        self.assertIn("(Antigravity branch)", fork.titulo)

    def test_estado_legado_sem_turnos_claude_faz_fast_forward_e_fork_com_seguranca(self):
        """Copias gravadas antes da v0.5.0 (so com a marca no disco) tambem fazem fast-forward se o Claude nao andou."""
        estado = sincronizar.ler_estado()
        self.rodar(sincronizar.para_antigravity, estado)
        agy_id = estado["importadas"]["antigravity-cli"][self.sessao]["cascade"]
        # Simula estado legado (so cascade e versao, sem turnos_claude nem fonte_tamanho)
        estado["importadas"]["antigravity-cli"][self.sessao] = {"cascade": agy_id, "versao": 0}
        acrescentar_no_antigravity(self.agy_cli, agy_id, self.molde_agy, [("legado ok", "resp legado")], minuto_base=8)
        novas = set()
        self.assertEqual(self.rodar(sincronizar.para_claude_, estado, novas)["a>c atualizadas"], 1)
        self.assertEqual(novas, {self.sessao})
        self.assertEqual(claude_jsonl.ler_conversa(self.arq, self.sessao).turnos[-1].texto, "resp legado")

    def test_titulo_ramo_com_titulo_longo_nao_corta_sufixo_apos_prefixo_da_ia(self):
        longo = "X" * 80
        t = para_claude.titulo_ramo(longo, "antigravity")
        conv = claude_jsonl.Conversa(sessao="s-longo", titulo=t, cwd=CWD,
                                     turnos=[Turno("usuario", "oi", T0), Turno("assistente", "ok", T0)])
        arq = para_claude.gravar_sessao(conv, "44444444-4444-4444-8444-444444444444",
                                        os.path.join(self.projetos, "C--w"), CWD, "2.1.0", "antigravity")
        lida = claude_jsonl.ler_conversa(arq, "44444444-4444-4444-8444-444444444444")
        self.assertLessEqual(len(lida.titulo), 80)
        self.assertTrue(lida.titulo.startswith("[Antigravity] "))
        self.assertTrue(lida.titulo.endswith("(Antigravity branch)"), lida.titulo)


if __name__ == "__main__":
    unittest.main()
