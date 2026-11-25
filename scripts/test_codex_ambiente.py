"""Ambiente do Codex igual ao do Claude: skills por juncao, plugins nativos dos marketplaces do Claude, MCP."""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import codex  # noqa: E402
import codex_ambiente as ca  # noqa: E402


def skill(pasta, nome, texto="x"):
    os.makedirs(os.path.join(pasta, nome), exist_ok=True)
    with open(os.path.join(pasta, nome, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(f"---\nname: {nome}\ndescription: {texto}\n---\n\n{texto}\n")
    return os.path.realpath(os.path.join(pasta, nome))


class Base(unittest.TestCase):
    def setUp(self):
        self.raiz = os.path.realpath(tempfile.mkdtemp(prefix="cxamb-"))
        self.addCleanup(self._limpar)

    def _limpar(self):
        # juncoes primeiro (rmtree nao deve seguir para a pasta de verdade)
        for r, ds, _ in os.walk(self.raiz):
            for d in ds:
                c = os.path.join(r, d)
                if ca._eh_juncao(c):
                    os.rmdir(c)
        shutil.rmtree(self.raiz, True)


class TestSkills(Base):
    def test_entradas_viram_skills_menos_as_de_plugin(self):
        local, terceiro = os.path.join(self.raiz, "local"), os.path.join(self.raiz, "terceiro")
        a = skill(local, "servidor")
        skill(local, "manual")                                   # escopo de projeto: fica de fora
        os.makedirs(os.path.join(local, "sem-skill"))              # pasta sem SKILL.md
        skill(terceiro, "servidor")                                 # nome repetido: a primeira fonte fica
        b = skill(terceiro, "security-audit")
        skill(terceiro, "outra")                                   # fora do include_only
        plugin = os.path.join(ca.CLAUDE, "plugins", "cache", "x", "p", "1", "skills")
        entradas = [{"path": local.replace("\\", "/"), "exclude": ["manual"]},
                    {"path": terceiro, "include_only": ["servidor", "security-audit"]},
                    {"path": plugin}]
        self.assertEqual(ca.skills_das_entradas(entradas), {"servidor": a, "security-audit": b})

    def test_plano_de_juncoes(self):
        pasta, nossa, fora = (os.path.join(self.raiz, n) for n in ("codex-skills", "nossa", "fora"))
        os.makedirs(pasta)
        a, b, c = skill(nossa, "a"), skill(nossa, "b"), skill(nossa, "c")
        f = skill(fora, "f")
        skill(pasta, "copia-antiga")                              # pasta de verdade: desativa
        skill(pasta, "a")                                          # pasta de verdade com nome desejado: desativa e cria
        os.makedirs(os.path.join(pasta, ".system"))                # do Codex: nunca
        ca._criar_juncao(c, os.path.join(pasta, "b"))              # nossa, apontando para outra skill: troca
        ca._criar_juncao(c, os.path.join(pasta, "velha"))          # nossa, nao desejada: tira
        ca._criar_juncao(f, os.path.join(pasta, "de-outro"))       # aponta para fora das nossas raizes: nao mexe
        quebrada = skill(nossa, "q")
        ca._criar_juncao(quebrada, os.path.join(pasta, "q"))
        shutil.rmtree(quebrada)                                    # juncao quebrada de skill desejada: troca
        q2 = skill(os.path.join(self.raiz, "nossa", "novo"), "q")
        acoes = ca.plano_skills(pasta, {"a": a, "b": b, "q": q2, "n": c}, [nossa])
        self.assertCountEqual(acoes, [("desativar", "a"), ("desativar", "copia-antiga"), ("trocar", "b", b),
                                      ("tirar", "velha"), ("trocar", "q", q2), ("criar", "a", a), ("criar", "n", c)])

    def test_aplicar_cria_desativa_e_tira_sem_tocar_na_pasta_de_verdade(self):
        pasta, nossa = os.path.join(self.raiz, "codex-skills"), os.path.join(self.raiz, "nossa")
        desativadas = os.path.join(self.raiz, "desativadas")
        a, b = skill(nossa, "a", "versao boa"), skill(nossa, "b")
        skill(pasta, "a", "copia velha")
        mudou = []
        ca.aplicar_skills(pasta, ca.plano_skills(pasta, {"a": a, "b": b}, [nossa]), desativadas, mudou, seco=False)
        self.assertTrue(ca._eh_juncao(os.path.join(pasta, "a")))
        with open(os.path.join(pasta, "a", "SKILL.md"), encoding="utf-8") as f:
            self.assertIn("versao boa", f.read())
        with open(os.path.join(desativadas, "a", "SKILL.md"), encoding="utf-8") as f:
            self.assertIn("copia velha", f.read())                # desativada, nao apagada
        self.assertEqual(ca.plano_skills(pasta, {"a": a, "b": b}, [nossa]), [])   # 2a rodada: nada
        # skill que saiu do Claude: a juncao sai, a pasta de verdade fica
        ca.aplicar_skills(pasta, ca.plano_skills(pasta, {"a": a}, [nossa]), desativadas, [], seco=False)
        self.assertFalse(os.path.lexists(os.path.join(pasta, "b")))
        self.assertTrue(os.path.isfile(os.path.join(b, "SKILL.md")))

    def test_seco_nao_mexe(self):
        pasta, nossa = os.path.join(self.raiz, "codex-skills"), os.path.join(self.raiz, "nossa")
        a = skill(nossa, "a")
        mudou = []
        ca.aplicar_skills(pasta, ca.plano_skills(pasta, {"a": a}, [nossa]), os.path.join(self.raiz, "d"), mudou, seco=True)
        self.assertEqual(mudou, ["codex skill criar: a"])
        self.assertFalse(os.path.lexists(os.path.join(pasta, "a")))

    def test_skills_de_projeto(self):
        ws = self.raiz
        fontes = os.path.join(ws, "scripts", "skills")
        m = skill(fontes, "manual")
        os.makedirs(os.path.join(ws, "repos", "manuais"))
        self.assertEqual(ca.skills_de_projeto(ws, {"repos/manuais": ["manual"], "repos/sumiu": ["manual"]}, fontes),
                         {os.path.join(ws, "repos/manuais"): {"manual": m}})


class TestMcpEPlugins(unittest.TestCase):
    def test_mcp_no_formato_do_codex(self):
        claude = {"projects": {"type": "http", "url": "https://x/mcp", "headers": {"Authorization": "Bearer t"}},
                  "jira": {"type": "stdio", "command": "uvx", "args": ["mcp-atlassian"], "env": {"JIRA_URL": "u"}},
                  "desligado": {"command": "x", "disabled": True},
                  "nome ruim": {"command": "x"}, "sem-nada": {"type": "sse"}}
        self.assertEqual(ca.mcp_para_codex(claude), {
            "projects": {"url": "https://x/mcp", "http_headers": {"Authorization": "Bearer t"}},
            "jira": {"command": "uvx", "args": ["mcp-atlassian"], "env": {"JIRA_URL": "u"}}})

    def test_plano_mcp_so_o_que_falta_ou_mudou(self):
        desejados = {"a": {"url": "u"}, "b": {"command": "c"}}
        self.assertEqual(ca.plano_mcp(desejados, {"a": {"url": "u"}, "b": {"command": "velho"}, "c": {"x": 1}}),
                         {"b": {"command": "c"}})
        self.assertEqual(ca.plano_mcp(desejados, None), desejados)

    def test_plano_de_plugins(self):
        cache = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, cache, True)
        os.makedirs(os.path.join(cache, "tob", "sa", "1.5.1"))
        os.makedirs(os.path.join(cache, "tob", "dr", "1.0.0"))
        claude = [("sa", "tob", "1.5.1", "C:/m/tob"), ("dr", "tob", "1.1.4", "C:/m/tob"),
                  ("abs", "pm", "0.1.113", "C:/m/pm"), ("sp", "spm", "7.0.2", "C:/m/spm"), ("fd", "off", "1.1.1", "C:/m/off")]
        mks = {"tob": {"source_type": "local"}, "pm": {"source_type": "git"}, "spm": {"source_type": "git"}}
        estados = {"sa@tob": {"installed": True, "enabled": True}, "dr@tob": {"installed": True, "enabled": True},
                   "abs@pm": {"installed": True, "enabled": True},       # git: versao diferente nao reinstala
                   "sp@spm": {"installed": True, "enabled": False}}
        caminhos = {"pm": "C:/codex/pm/marketplace.json"}
        acoes = ca.plano_plugins(claude, mks, estados, cache, caminhos)
        self.assertEqual(acoes, [
            ("instalar", "dr", "tob", os.path.join("C:/m/tob", ".claude-plugin", "marketplace.json")),  # versao nova
            ("ligar", "sp@spm"),
            ("marketplace", "off", "C:/m/off"),
            ("instalar", "fd", "off", os.path.join("C:/m/off", ".claude-plugin", "marketplace.json"))])


@unittest.skipUnless(codex.executavel(), "Codex nao instalado")
class TestCodexReal(Base):
    """aplicar() com o Codex de verdade num CODEX_HOME temporario (o plugin vem do marketplace local do Claude)."""

    def test_aplicar_duas_vezes(self):
        home = os.path.join(self.raiz, "h")
        os.makedirs(os.path.join(home, "skills"))
        skill(os.path.join(home, "skills"), "copia-de-junho")
        fontes = os.path.join(self.raiz, "fontes")
        skill(fontes, "minha-skill")
        plugins = [p for p in ca.plugins_do_claude() if p[0] == "static-analysis"][:1]
        claude_mcp = {"projects": {"type": "http", "url": "https://exemplo.invalid/mcp",
                                   "headers": {"Authorization": "Bearer teste"}}}
        with mock.patch.object(ca, "plugins_do_claude", return_value=plugins), \
                mock.patch.object(ca, "_ler_json", side_effect=lambda c, p: {"mcpServers": claude_mcp}
                                  if c.endswith(".claude.json") else p):
            with codex.Servidor(home=home) as srv:
                mudou = []
                ca.aplicar(srv, home, self.raiz, {"entradas": [{"path": fontes}]}, False, mudou)
                self.assertIn("codex skill criar: minha-skill", mudou)
                self.assertIn("codex skill desativar: copia-de-junho", mudou)
                self.assertIn("codex mcp: projects", mudou)
                nomes = {s["name"] for g in srv.chamar("skills/list", {"cwds": [self.raiz], "forceReload": True})["data"]
                         for s in g["skills"]}
                self.assertIn("minha-skill", nomes)
                self.assertNotIn("copia-de-junho", nomes)
                if plugins:
                    self.assertIn("static-analysis:semgrep", nomes)
                segunda = []
                ca.aplicar(srv, home, self.raiz, {"entradas": [{"path": fontes}]}, False, segunda)
                self.assertEqual(segunda, [])
        cfg = ca.ler_toml(os.path.join(home, "config.toml"))
        self.assertEqual(cfg["mcp_servers"]["projects"]["http_headers"], {"Authorization": "Bearer teste"})
        self.assertTrue(os.path.isfile(os.path.join(home, ca.DESATIVADAS, "copia-de-junho", "SKILL.md")))


if __name__ == "__main__":
    unittest.main()
