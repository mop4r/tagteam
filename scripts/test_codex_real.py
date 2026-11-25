"""Com o Codex de verdade (codex app-server), numa pasta isolada: CODEX_HOME e a home do filho temporarias, nada toca
o ~/.codex. Pulado se o Codex nao estiver instalado. Continuar uma conversa no Codex exige o modelo (conta logada):
isso fica no teste de ponta a ponta, feito a mao no Codex real (references/codex.md)."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from collections import Counter
from types import SimpleNamespace
from unittest import mock

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import codex  # noqa: E402
import sincronizar  # noqa: E402

SESSAO_A = "33333333-3333-4333-8333-333333333333"
SESSAO_B = "44444444-4444-4444-8444-444444444444"


def args(**kw):
    base = dict(sessao=None, cascade=None, grok=None, codex=None, cursor=None, so=None, seco=False, forcar=False)
    base.update(kw)
    return SimpleNamespace(**base)


@unittest.skipUnless(codex.executavel(), "Codex nao instalado")
class TestCodexReal(unittest.TestCase):
    def setUp(self):
        self.raiz = tempfile.mkdtemp(prefix="tagteam-codex-")
        self.addCleanup(shutil.rmtree, self.raiz, True)
        self.home_codex = os.path.join(self.raiz, "codex-home")
        os.makedirs(self.home_codex)
        self.projetos = os.path.join(self.raiz, "claude", "projects")
        self.workspace = os.path.join(self.raiz, "workspace")
        os.makedirs(self.workspace)
        trocas = {"PROJETOS": self.projetos, "PASTA_ESTADO": self.raiz, "WORKSPACE": self.workspace,
                  "ESTADO": os.path.join(self.raiz, "estado.json"), "TRAVA": os.path.join(self.raiz, ".trava"),
                  "PREPARO": os.path.join(self.raiz, "preparo")}
        for nome, valor in trocas.items():
            p = mock.patch.object(sincronizar, nome, valor)
            p.start()
            self.addCleanup(p.stop)
        for alvo, valor in ((sincronizar.para_claude, "projetos_claude"),):
            p = mock.patch.object(alvo, valor, lambda: self.projetos)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.dict(os.environ, {"CODEX_HOME": self.home_codex})
        p.start()
        self.addCleanup(p.stop)
        self.srv = codex.Servidor(home=self.home_codex,
                                  env_extra={"USERPROFILE": sincronizar.PREPARO, "HOME": sincronizar.PREPARO})
        os.makedirs(sincronizar.PREPARO, exist_ok=True)
        self.srv.__enter__()
        self.addCleanup(self.srv.fechar)

    def sessao(self, sid, perguntas, cwd="C:\\Users\\outro\\workspace", titulo=None):
        """Sessao do Claude como as de outro PC: pasta de trabalho que nao existe aqui."""
        pasta = os.path.join(self.projetos, "C--Users-outro-workspace")
        os.makedirs(pasta, exist_ok=True)
        caminho = os.path.join(pasta, sid + ".jsonl")
        with open(caminho, "w", encoding="utf-8") as f:
            pai = None
            for i, p in enumerate(perguntas):
                for tipo, msg in (("user", {"role": "user", "content": p}),
                                  ("assistant", {"role": "assistant", "content": [{"type": "text", "text": "resp " + p}]})):
                    uid = f"{sid[:8]}-{i}-{tipo}"
                    f.write(json.dumps({"parentUuid": pai, "type": tipo, "entrypoint": "cli", "cwd": cwd, "uuid": uid,
                                        "sessionId": sid, "timestamp": f"2026-11-25T10:{i:02d}:00Z", "message": msg},
                                       ensure_ascii=False) + "\n")
                    pai = uid
            if titulo:
                f.write(json.dumps({"type": "ai-title", "aiTitle": titulo, "sessionId": sid}) + "\n")
        return caminho

    def rodada(self, **kw):
        estado = sincronizar.ler_estado()
        cont = Counter()
        sincronizar.para_codex(args(**kw), estado, cont, self.srv)
        return estado, cont

    def perguntas_no_codex(self, tid):
        th = codex.ler(self.srv, tid)
        return [t.texto for t in codex.conversa_de_thread(th).turnos if t.papel == "usuario"]

    def test_ciclo_completo(self):
        self.sessao(SESSAO_A, ["primeira pergunta", "segunda com acento ç e emoji 🙂"], titulo="Teste do ciclo")
        self.sessao(SESSAO_B, ["outra conversa"])
        # 1) cria as duas, mesmo com a pasta de outro PC
        estado, cont = self.rodada()
        self.assertEqual(cont["c>x criadas"], 2, cont)
        tid_a = estado["importadas_codex"][SESSAO_A]["id"]
        self.assertEqual(self.perguntas_no_codex(tid_a), ["primeira pergunta", "segunda com acento ç e emoji 🙂"])
        th = codex.ler(self.srv, tid_a)
        self.assertEqual(th.get("name"), "Teste do ciclo")
        self.assertEqual(codex.cwd_de(th), sincronizar.cwd_local(r"C:\Users\outro\workspace"))   # pasta deste PC
        self.assertTrue(os.path.isdir(codex.cwd_de(th)))
        self.assertEqual(os.listdir(sincronizar.PREPARO), [])           # copias apagadas
        # 2) nada mudou: nem importa
        estado, cont = self.rodada()
        self.assertEqual(cont["c>x iguais"], 2, cont)
        # 3) a sessao andou no Claude: a MESMA conversa recebe o turno novo
        self.sessao(SESSAO_A, ["primeira pergunta", "segunda com acento ç e emoji 🙂", "terceira"], titulo="Teste do ciclo")
        estado, cont = self.rodada()
        self.assertEqual(cont["c>x atualizadas"], 1, cont)
        self.assertEqual(estado["importadas_codex"][SESSAO_A]["id"], tid_a)
        self.assertEqual(self.perguntas_no_codex(tid_a)[-1], "terceira")
        self.assertEqual(len(codex.listar(self.srv)), 2)                # sem duplicar
        # 4) Codex -> Claude: espelho puro nao volta
        cont2, novas = Counter(), set()
        sincronizar.codex_para_claude(args(), estado, cont2, novas, self.srv)
        self.assertEqual((cont2["x>c espelhos"], len(novas)), (2, 0), cont2)
        # 5) apagada no Codex nao volta; com --forcar volta como conversa nova
        self.srv.chamar("thread/delete", {"threadId": tid_a})
        self.sessao(SESSAO_A, ["primeira pergunta", "segunda com acento ç e emoji 🙂", "terceira", "quarta"])
        estado, cont = self.rodada()
        self.assertEqual(cont["c>x apagadas_no_codex"], 1, cont)
        estado, cont = self.rodada(forcar=True)
        novo = estado["importadas_codex"][SESSAO_A]["id"]
        self.assertNotEqual(novo, tid_a)
        self.assertEqual(self.perguntas_no_codex(novo)[-1], "quarta")
        self.assertEqual(estado["importadas_codex"][SESSAO_A]["versao"], 1)

    def test_resposta_que_continua_depois_da_importacao_nao_vira_versao_nova(self):
        """Sessao importada no meio de uma resposta: a importacao seguinte acrescenta um turno "rollout-N" (so
        respostas). Isso nao e conversa continuada no Codex: a mesma conversa segue sendo atualizada."""
        caminho = self.sessao(SESSAO_A, ["primeira"])
        estado, _ = self.rodada()
        tid = estado["importadas_codex"][SESSAO_A]["id"]
        with open(caminho, encoding="utf-8") as f:
            ultima = json.loads(f.read().splitlines()[-1])
        for i in range(2):                                   # mais respostas do assistente, sem pergunta nova
            ultima = dict(ultima, uuid=f"extra-{i}", parentUuid=ultima["uuid"], timestamp=f"2026-11-25T11:0{i}:00Z",
                          message={"role": "assistant", "content": [{"type": "text", "text": f"continuacao {i}"}]})
            with open(caminho, "a", encoding="utf-8") as f:
                f.write(json.dumps(ultima) + "\n")
            estado, cont = self.rodada()
            self.assertEqual(cont["c>x versoes_novas"], 0, cont)
            self.assertEqual(estado["importadas_codex"][SESSAO_A]["id"], tid)
        th = codex.ler(self.srv, tid)
        self.assertTrue(any(str(x["id"]).startswith("rollout-") for x in th["turns"]),
                        [x["id"] for x in th["turns"]])           # o caso que enganou a regra antiga aconteceu
        self.assertEqual(codex.turnos_nativos(th), 0)
        self.assertIn("continuacao 1", codex.conversa_de_thread(th).turnos[-1].texto)
        self.assertEqual(len(codex.listar(self.srv)), 1)

    def test_estado_perdido_nao_duplica(self):
        self.sessao(SESSAO_A, ["uma"])
        estado, _ = self.rodada()
        tid = estado["importadas_codex"][SESSAO_A]["id"]
        os.remove(sincronizar.ESTADO)                                   # PC perdeu o estado
        self.sessao(SESSAO_A, ["uma", "duas"])
        estado, cont = self.rodada()
        self.assertEqual(estado["importadas_codex"][SESSAO_A]["id"], tid, cont)
        self.assertEqual(len(codex.listar(self.srv)), 1)

    def test_importacao_antiga_por_outro_caminho_e_adotada(self):
        """Conversa importada pelo proprio Codex (outro caminho da mesma sessao): atualiza a que existe."""
        antigo = os.path.join(sincronizar.PREPARO, ".claude", "projects", "velho")
        os.makedirs(antigo)
        fonte = self.sessao(SESSAO_A, ["uma"], cwd=self.workspace)
        shutil.copy(fonte, os.path.join(antigo, SESSAO_A + ".jsonl"))
        feitos, _ = codex.importar(self.srv, [(os.path.join(antigo, SESSAO_A + ".jsonl"), self.workspace, "x")])
        tid = next(iter(feitos.values()))
        shutil.rmtree(antigo)
        self.sessao(SESSAO_A, ["uma", "duas"], cwd=self.workspace)
        estado, cont = self.rodada()
        self.assertEqual(estado["importadas_codex"][SESSAO_A]["id"], tid, cont)
        self.assertEqual(len(codex.listar(self.srv)), 1)
        self.assertEqual(self.perguntas_no_codex(tid), ["uma", "duas"])


if __name__ == "__main__":
    unittest.main()
