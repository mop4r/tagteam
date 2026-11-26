"""tagteam.py e atualizar.py: o assistente dirigido por teclas, o monitor, as opcoes e a atualizacao (sem rede)."""
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
import zipfile
from unittest import mock

import atualizar
import config
import tagteam
import tela

DET = {"claude": {"app": False, "cli": True, "instalada": True},
       "antigravity": {"app": True, "cli": False, "instalada": True},
       "grok": {"app": False, "cli": False, "instalada": False},
       "codex": {"app": False, "cli": True, "instalada": True},
       "cursor": {"app": False, "cli": False, "instalada": False}}


class ComTeclas(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.pasta, True)
        self.telas = []
        ps = [mock.patch.dict(os.environ, {"TAGTEAM_STATE_DIR": self.pasta}),
              mock.patch.object(tela.Tela, "desenhar", staticmethod(lambda linhas: self.telas.append(list(linhas)))),
              mock.patch.object(tagteam.saude, "detectar", lambda: json.loads(json.dumps(DET))),
              mock.patch.object(tagteam, "IDIOMA", "pt")]
        for p in ps:
            p.start()
            self.addCleanup(p.stop)

    def teclas(self, *seq):
        fila = list(seq)
        p = mock.patch.object(tela, "tecla", lambda espera=None: fila.pop(0) if fila else "esc")
        p.start()
        self.addCleanup(p.stop)

    def texto(self, i=-1):
        return "\n".join(tela.ANSI_RE.sub("", x) for x in self.telas[i])


class TestAssistente(ComTeclas):
    def test_marca_o_que_esta_instalado_e_o_que_da_para_instalar(self):
        itens = tagteam.itens_das_ias(DET)
        por_ia = {it["ia"]: it for it in itens}
        self.assertTrue(por_ia["antigravity"]["marcado"] and por_ia["codex"]["marcado"])
        self.assertFalse(por_ia["grok"]["marcado"])
        self.assertTrue(por_ia["grok"]["pode"])                     # o TagTeam instala o Grok CLI
        self.assertFalse(por_ia["cursor"]["pode"])                  # o Cursor a pessoa instala antes
        self.assertIn("instale o Cursor", por_ia["cursor"]["desc"])

    def test_fluxo_inteiro_grava_a_config_e_instala(self):
        # baixo -> Grok, espaco marca; baixo -> Codex, espaco desmarca; Enter; opcoes: C continua; resumo: Enter
        self.teclas("baixo", "espaco", "baixo", "espaco", "enter", "c", "enter", "enter")
        with mock.patch.object(tagteam, "rodar_instalador", return_value=(True, ["ok"])) as inst:
            self.assertTrue(tagteam.assistente())
        inst.assert_called_once_with()
        cfg = config.carregar()
        self.assertEqual({ia for ia in config.IAS if cfg["conversas"][ia]}, {"antigravity", "grok"})
        self.assertFalse(cfg["atalhos_admin"] or cfg["atalhos_sem_aprovacao"])       # padrao de instalacao nova
        self.assertIn("Resumo", "\n".join(self.texto(i) for i in range(len(self.telas))))

    def test_cursor_nao_instalado_nao_se_marca(self):
        self.teclas("baixo", "baixo", "baixo", "espaco", "enter", "c", "enter", "enter")
        with mock.patch.object(tagteam, "rodar_instalador", return_value=(True, [])):
            tagteam.assistente()
        self.assertFalse(config.carregar()["conversas"]["cursor"])

    def test_esc_na_primeira_tela_nao_grava_nada(self):
        self.teclas("esc")
        self.assertFalse(tagteam.assistente())
        self.assertFalse(config.existe())

    def test_sem_claude_avisa_e_para(self):
        det = json.loads(json.dumps(DET))
        det["claude"]["instalada"] = False
        self.teclas("enter")
        with mock.patch.object(tagteam.saude, "detectar", lambda: det), \
                mock.patch.object(tagteam.saude, "CLAUDE", os.path.join(self.pasta, "nao-existe")):
            self.assertFalse(tagteam.assistente())
        self.assertIn("Claude Code não foi encontrado", self.texto())


class TestOpcoes(ComTeclas):
    def test_inverter_opcoes_e_porcentagem(self):
        cfg = config.normalizar({}, config.PADRAO_NOVO)
        tagteam.mudar_opcao(cfg, "atalhos_admin")
        self.assertTrue(cfg["atalhos_admin"])
        with mock.patch.object(tela, "perguntar", return_value="70"):
            tagteam.mudar_opcao(cfg, "passagem_pct")
        self.assertEqual(cfg["passagem_pct"], 70)
        with mock.patch.object(tela, "perguntar", return_value="500"):
            tagteam.mudar_opcao(cfg, "passagem_pct")
        self.assertEqual(cfg["passagem_pct"], 70)

    def test_ambiente_liga_e_desliga_so_nas_ias_marcadas(self):
        cfg = config.normalizar({"conversas": {"grok": False}, "ambiente": {"grok": False}})
        tagteam.mudar_opcao(cfg, "ambiente")
        self.assertFalse(any(cfg["ambiente"].values()))
        tagteam.mudar_opcao(cfg, "ambiente")
        self.assertEqual({ia for ia in config.IAS if cfg["ambiente"][ia]}, {"antigravity", "codex", "cursor"})

    def test_pasta_que_nao_existe_nao_entra(self):
        cfg = config.normalizar({})
        self.teclas("x")
        with mock.patch.object(tela, "perguntar", return_value="C:\\nao\\existe\\mesmo"):
            tagteam.mudar_opcao(cfg, "pasta")
        self.assertEqual(cfg["pasta"], "")
        with mock.patch.object(tela, "perguntar", return_value=f'"{self.pasta}"'):
            tagteam.mudar_opcao(cfg, "pasta")
        self.assertEqual(cfg["pasta"], self.pasta)

    def test_pastas_excluidas_acrescenta_e_tira(self):
        cfg = config.normalizar({})
        self.teclas("a", "a", "a", "x", "enter")
        respostas = iter(["C:\\trabalho", "relativa", "D:\\cliente"])
        with mock.patch.object(tela, "perguntar", lambda _t: next(respostas)):
            tagteam.tela_excluidas(cfg)
        self.assertEqual(cfg["pastas_excluidas"], ["D:\\cliente"])

    def test_tela_de_ias_so_conversas(self):
        cfg = config.normalizar({})
        self.teclas("a", "enter")          # Antigravity: tira o ambiente, fica so conversas
        novo = tagteam.tela_ias(cfg)
        self.assertTrue(novo["conversas"]["antigravity"])
        self.assertFalse(novo["ambiente"]["antigravity"])


class TestMonitor(ComTeclas):
    def painel(self, **mudar):
        cfg = config.normalizar({})
        ias = {ia: {"nome": config.NOMES[ia], "instalada": True, "app": True, "cli": True, "conversas": True,
                    "ambiente": True, "para": 10, "de": 2, "gancho_quando": 1000.0, "gancho_evento": "Stop",
                    "erros_24h": 0} for ia in config.IAS}
        p = {"agora": 1300.0, "cfg": cfg, "ias": ias, "checagens": [("geral", "ok", "tudo_certo", {})],
             "resumo": {"rodadas": 3, "mudou": 4, "com_erro": 0},
             "eventos": [{"tipo": "sincronizar", "ts": 1200.0, "args": ["--grok", "x"], "cont": {"c>g atualizadas": 2},
                          "saida": 0, "duracao_s": 1.5},
                         {"tipo": "sincronizar", "ts": 1250.0, "args": [], "cont": {"erro": 1}, "saida": 1,
                          "erros": ["Codex fora do ar"], "duracao_s": 3}]}
        p.update(mudar)
        return p

    def test_mostra_ias_rodadas_e_erros(self):
        texto = "\n".join(tela.ANSI_RE.sub("", x) for x in tagteam.linhas_monitor(self.painel()))
        self.assertIn("tudo certo", texto)
        self.assertIn("→10", texto)
        self.assertIn("hook há 5 min", texto)
        self.assertIn("3 rodadas · 4 mudanças · 0 com erro", texto)
        self.assertIn("Grok CLI", texto)
        self.assertIn("2 mudança(s)", texto)
        self.assertIn("erro: Codex fora do ar", texto)
        self.assertLess(texto.index("Codex fora do ar"), texto.index("2 mudança(s)"))   # mais nova primeiro

    def test_ia_desmarcada_e_nao_instalada(self):
        p = self.painel()
        p["ias"]["grok"].update(conversas=False, ambiente=False)
        p["ias"]["cursor"]["instalada"] = False
        texto = "\n".join(tela.ANSI_RE.sub("", x) for x in tagteam.linhas_monitor(p))
        self.assertIn("desmarcada", texto)
        self.assertIn("não instalada", texto)

    def test_teclas_do_monitor(self):
        config.salvar(config.normalizar({}))
        self.teclas(None, "s", "q")
        with mock.patch.object(tagteam.saude, "painel", lambda cfg: self.painel()), \
                mock.patch("gancho.lancar") as lancar:
            tagteam.monitor()
        lancar.assert_called_once_with([])
        self.assertIn("Sincronização completa iniciada", self.texto())

    def test_desinstalar_pede_a_palavra(self):
        with mock.patch.object(tela, "perguntar", return_value="talvez"), \
                mock.patch.object(tagteam, "rodar_instalador") as inst:
            self.assertFalse(tagteam.desinstalar())
        inst.assert_not_called()
        self.teclas("x")
        with mock.patch.object(tela, "perguntar", return_value="sim"), \
                mock.patch.object(tagteam, "rodar_instalador", return_value=(True, [])) as inst:
            self.assertTrue(tagteam.desinstalar())
        inst.assert_called_once_with(("--desinstalar",))

    def test_tempo_legivel(self):
        self.assertEqual([tagteam.tempo(s) for s in (5, 300, 7200, 400000)], ["5s", "5 min", "2 h", "4 d"])


class TestProgresso(ComTeclas):
    def test_le_as_linhas_do_instalador(self):
        saida = [json.dumps({"passo": 1, "total": 2, "titulo": "Hooks", "estado": "rodando", "mudou": []}) + "\n",
                 json.dumps({"passo": 1, "total": 2, "titulo": "Hooks", "estado": "ok", "mudou": ["a"]}) + "\n",
                 "lixo\n",
                 json.dumps({"passo": 2, "total": 2, "titulo": "Codex", "estado": "erro: OSError: x", "mudou": []}) + "\n"]
        proc = mock.Mock(stdout=iter(saida), stderr=io.StringIO("detalhe do erro\n"), returncode=1)
        with mock.patch.object(tagteam.subprocess, "Popen", return_value=proc):
            ok, linhas = tagteam.rodar_instalador()
        self.assertFalse(ok)
        texto = "\n".join(tela.ANSI_RE.sub("", x) for x in linhas)
        self.assertIn("✓ Hooks (1)", texto)
        self.assertIn("✗ Codex", texto)
        self.assertIn("2/2", texto)
        self.assertIn("detalhe do erro", texto)


def _pacote(arquivos):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for nome, conteudo in arquivos.items():
            z.writestr(nome, conteudo)
    return buf.getvalue()


class _Resposta(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class TestAtualizar(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.pasta, True)

    def release(self, pacote, digest=None, nome="tagteam-v0.4.0.zip", url="https://github.com/x/y.zip"):
        digest = digest or "sha256:" + hashlib.sha256(pacote).hexdigest()
        return {"tag_name": "v0.4.0", "body": "notas",
                "assets": [{"name": nome, "browser_download_url": url, "digest": digest}]}

    def test_versoes(self):
        self.assertTrue(atualizar.ha_versao_nova("0.3.0", {"versao": "v0.10.0"}))
        self.assertFalse(atualizar.ha_versao_nova("0.3.0", {"versao": "v0.3.0"}))
        self.assertIsNone(atualizar.versao("latest"))

    def test_release_valida_e_pacote_conferido(self):
        pacote = _pacote({"tagteam/SKILL.md": "x", "tagteam/scripts/a.py": "print(1)"})
        abrir = lambda req, timeout: _Resposta(json.dumps(self.release(pacote)).encode())  # noqa: E731
        rel = atualizar.release_mais_nova(abrir)
        self.assertEqual(rel["versao"], "v0.4.0")
        self.assertEqual(atualizar.baixar(rel, lambda req, timeout: _Resposta(pacote)), pacote)
        with self.assertRaises(atualizar.ErroAtualizacao):
            atualizar.baixar(rel, lambda req, timeout: _Resposta(pacote + b"x"))     # hash nao bate

    def test_release_sem_pacote_ou_de_outro_host_e_recusada(self):
        for rel in (self.release(b"x", nome="outro.zip"), self.release(b"x", url="https://evil.example/x.zip"),
                    self.release(b"x", digest="md5:abc"), {"tag_name": "nightly"}):
            with self.assertRaises(atualizar.ErroAtualizacao):
                atualizar.release_mais_nova(lambda req, timeout, r=rel: _Resposta(json.dumps(r).encode()))

    def test_aplicar_copia_so_o_que_e_do_tagteam(self):
        pacote = _pacote({"tagteam/SKILL.md": "novo", "tagteam/scripts/a.py": "print(2)", "tagteam/.github/x": "nao",
                          "outro/b.py": "nao"})
        self.assertEqual(atualizar.aplicar(pacote, self.pasta), 2)
        with open(os.path.join(self.pasta, "scripts", "a.py"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "print(2)")
        self.assertFalse(os.path.exists(os.path.join(self.pasta, ".github")))

    def test_caminho_que_sai_da_pasta_e_recusado(self):
        for ruim in ("tagteam/../fora.py", "tagteam/C:/x.py", "tagteam/scripts/../../fora.py"):
            pacote = _pacote({"tagteam/SKILL.md": "x", "tagteam/scripts/a.py": "x", ruim: "x"})
            with self.assertRaises(atualizar.ErroAtualizacao):
                atualizar.aplicar(pacote, self.pasta)
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.pasta), "fora.py")))

    def test_instalacao_com_git_nao_e_tocada(self):
        os.makedirs(os.path.join(self.pasta, ".git"))
        with self.assertRaises(atualizar.ErroAtualizacao):
            atualizar.aplicar(_pacote({"tagteam/SKILL.md": "x", "tagteam/scripts/a.py": "x"}), self.pasta)


if __name__ == "__main__":
    unittest.main()
