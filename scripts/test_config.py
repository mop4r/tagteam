"""config.json: padroes, validacao, variaveis antigas por cima, e quem obedece (sincronizar, hooks, passagem, instalar)."""
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import config
import gancho
import instalar
import passagem


class ComEstado(unittest.TestCase):
    """Pasta de estado descartavel: o teste nunca le o config.json de quem roda os testes."""

    def setUp(self):
        self.pasta = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.pasta, True)
        env = {k: v for k, v in os.environ.items() if k not in ("TAGTEAM_WORKSPACE", config.ENV_LIMIAR)}
        env["TAGTEAM_STATE_DIR"] = self.pasta
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def gravar_cfg(self, dado):
        with open(config.caminho(), "w", encoding="utf-8") as f:
            f.write(dado if isinstance(dado, str) else json.dumps(dado))


class TestConfig(ComEstado):
    def test_sem_arquivo_vale_o_comportamento_antigo(self):
        cfg = config.carregar()
        self.assertTrue(all(cfg["conversas"].values()) and all(cfg["ambiente"].values()))
        self.assertTrue(cfg["atalhos_admin"] and cfg["atalhos_sem_aprovacao"])
        self.assertEqual(cfg["passagem_pct"], 95)
        self.assertFalse(config.existe())

    def test_instalacao_nova_propoe_atalhos_sem_admin_e_com_aprovacao(self):
        self.assertFalse(config.PADRAO_NOVO["atalhos_admin"])
        self.assertFalse(config.PADRAO_NOVO["atalhos_sem_aprovacao"])

    def test_valor_errado_volta_ao_padrao_e_o_certo_fica(self):
        self.gravar_cfg({"conversas": {"grok": False, "codex": "nao"}, "passagem_pct": 500, "pasta": "relativa",
                         "pastas_excluidas": ["C:\\trabalho", 3, "rel"], "idioma": "xx", "atalhos": "sim"})
        cfg = config.carregar()
        self.assertFalse(cfg["conversas"]["grok"])
        self.assertTrue(cfg["conversas"]["codex"])
        self.assertEqual(cfg["passagem_pct"], 95)
        self.assertEqual(cfg["pasta"], "")
        self.assertEqual(cfg["pastas_excluidas"], ["C:\\trabalho"])
        self.assertEqual(cfg["idioma"], "auto")
        self.assertTrue(cfg["atalhos"])

    def test_arquivo_quebrado_ou_grande_nao_derruba(self):
        self.gravar_cfg("{nao e json")
        self.assertEqual(config.ler_bruto()[1], "invalida")
        self.assertTrue(config.carregar()["conversas"]["grok"])
        self.gravar_cfg("[1, 2]")
        self.assertEqual(config.ler_bruto()[1], "invalida")
        self.gravar_cfg(" " * (config.MAX_CONFIG + 1))
        self.assertEqual(config.ler_bruto()[1], "grande")

    def test_variaveis_antigas_valem_por_cima(self):
        self.gravar_cfg({"passagem_pct": 80, "pasta": self.pasta})
        outra = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outra, True)
        with mock.patch.dict(os.environ, {config.ENV_LIMIAR: "10", "TAGTEAM_WORKSPACE": outra}):
            cfg = config.carregar()
        self.assertEqual(cfg["passagem_pct"], 10)
        self.assertEqual(cfg["pasta"], outra)
        with mock.patch.dict(os.environ, {config.ENV_LIMIAR: "abc"}):
            self.assertEqual(config.carregar()["passagem_pct"], 80)

    def test_salvar_e_ler_de_volta_sem_sobra_de_temporario(self):
        cfg = config.normalizar({}, config.PADRAO_NOVO)
        cfg["conversas"]["cursor"] = False
        config.salvar(cfg)
        self.assertFalse(config.carregar()["conversas"]["cursor"])
        self.assertFalse(config.carregar()["atalhos_admin"])
        self.assertEqual([n for n in os.listdir(self.pasta) if n.endswith(".tmp")], [])

    def test_pasta_inexistente_cai_na_home(self):
        self.assertEqual(config.pasta_trabalho({"pasta": "C:\\nao\\existe\\mesmo"}), config.PASTA_PADRAO)
        self.assertEqual(config.pasta_trabalho({"pasta": self.pasta}), self.pasta)

    def test_projeto_excluido_pela_pasta_e_subpastas(self):
        cfg = {"pastas_excluidas": ["C:\\Users\\ana\\trabalho\\"]}
        self.assertTrue(config.projeto_excluido(cfg, "C--Users-ana-trabalho"))
        self.assertTrue(config.projeto_excluido(cfg, "c--users-ana-trabalho-cliente"))
        self.assertFalse(config.projeto_excluido(cfg, "C--Users-ana-trabalhos"))
        self.assertFalse(config.projeto_excluido(cfg, "C--Users-ana"))
        self.assertFalse(config.projeto_excluido({"pastas_excluidas": []}, "C--Users-ana-trabalho"))

    def test_chave_do_projeto_igual_a_do_claude(self):
        self.assertEqual(config.chave_do_projeto("C:\\Users\\Ana Lú\\x_y"), "C--Users-Ana-L--x-y")


class TestQuemObedece(ComEstado):
    def test_sessoes_de_pasta_excluida_nao_saem_do_claude(self):
        import sincronizar
        projetos = os.path.join(self.pasta, "projects")
        for proj in ("C--trabalho", "C--trabalho-cliente", "C--casa"):
            os.makedirs(os.path.join(projetos, proj))
            open(os.path.join(projetos, proj, "a.jsonl"), "w").close()
        self.gravar_cfg({"pastas_excluidas": ["C:\\trabalho"]})
        with mock.patch.object(sincronizar, "PROJETOS", projetos):
            sobra = sincronizar._sessoes_do_claude()
        self.assertEqual([os.path.basename(os.path.dirname(c)) for c in sobra], ["C--casa"])

    def test_sincronizar_so_roda_as_ias_marcadas(self):
        import sincronizar
        self.gravar_cfg({"conversas": {"antigravity": False, "codex": False, "cursor": False}})
        chamadas = []
        nomes = ("para_antigravity", "para_grok", "para_cursor", "para_claude_", "grok_para_claude", "cursor_para_claude")
        ps = [mock.patch.object(sincronizar, n, lambda *a, _n=n, **k: chamadas.append(_n)) for n in nomes]
        ps += [mock.patch.object(sincronizar, "_codex", lambda *a, **k: chamadas.append("_codex")),
               mock.patch.object(sincronizar.antigravity, "bases", lambda: ["x"]),
               mock.patch.object(sincronizar.grok, "instalado", lambda: True),
               mock.patch.object(sincronizar.codex, "instalado", lambda: True),
               mock.patch.object(sincronizar.cursor, "instalado", lambda: True),
               mock.patch.object(sincronizar, "PASTA_ESTADO", self.pasta),
               mock.patch.object(sincronizar, "TRAVA", os.path.join(self.pasta, ".trava")),
               mock.patch.object(sincronizar, "ESTADO", os.path.join(self.pasta, "estado.json")),
               mock.patch("sys.argv", ["sincronizar.py"])]
        for p in ps:
            p.start()
            self.addCleanup(p.stop)
        with mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(sincronizar.main(), 0)
        self.assertEqual(sorted(chamadas), ["grok_para_claude", "para_grok"])

    def test_nenhuma_ia_marcada_nao_faz_nada(self):
        import sincronizar
        self.gravar_cfg({"conversas": {ia: False for ia in config.IAS}})
        with mock.patch("sys.argv", ["sincronizar.py"]), mock.patch("sys.stdout", io.StringIO()) as saida, \
                mock.patch.object(sincronizar, "pegar_trava") as trava:
            self.assertEqual(sincronizar.main(), 0)
        trava.assert_not_called()
        self.assertIn("nada a fazer", saida.getvalue())

    def test_hook_de_ia_desmarcada_nao_dispara(self):
        import gancho_codex
        self.gravar_cfg({"conversas": {"codex": False}})
        entrada = json.dumps({"hook_event_name": "Stop", "session_id": "0" * 8 + "-0000-0000-0000-" + "0" * 12})
        with mock.patch.object(gancho, "PASTA", self.pasta), mock.patch.object(gancho, "disparar") as disparar, \
                mock.patch("sys.stdin", io.TextIOWrapper(io.BytesIO(entrada.encode()))):
            gancho_codex.main()
        disparar.assert_not_called()
        with open(os.path.join(self.pasta, "gancho-codex.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "Stop")       # o carimbo vale mesmo assim: o monitor ve o hook chegando

    def test_ha_ia_para_sincronizar_respeita_o_config(self):
        casa = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, casa, True)
        os.makedirs(os.path.join(casa, ".grok", "sessions"))
        with mock.patch("os.path.expanduser", lambda c: casa if c == "~" else c):
            self.assertTrue(gancho.ha_ia_para_sincronizar(config.carregar()))
            self.gravar_cfg({"conversas": {"grok": False}})
            self.assertFalse(gancho.ha_ia_para_sincronizar(config.carregar()))

    def test_passagem_desligada_nunca_pede(self):
        self.gravar_cfg({"passagem": False})
        self.assertIsNone(passagem.limiar())
        self.assertIsNone(passagem.decidir_gemini("abc", False, "", lambda: self.fail("nao consulta a cota")))
        self.gravar_cfg({"passagem_pct": 70})
        self.assertEqual(passagem.limiar(), 70.0)


class TestInstalarObedece(ComEstado):
    def setUp(self):
        super().setUp()
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        self.claude = os.path.join(self.home, ".claude")
        os.makedirs(self.claude)
        p = mock.patch.object(instalar, "CLAUDE", self.claude)
        p.start()
        self.addCleanup(p.stop)

    def com_cfg(self, **mudancas):
        cfg = config.normalizar({}, config.PADRAO_NOVO)
        cfg.update(mudancas)
        p = mock.patch.object(instalar, "CFG", cfg)
        p.start()
        self.addCleanup(p.stop)
        return cfg

    def settings(self, dado):
        with open(os.path.join(self.claude, "settings.json"), "w", encoding="utf-8") as f:
            json.dump(dado, f)

    def test_desinstalar_tira_so_os_hooks_e_o_status_line_do_tagteam(self):
        gancho_py = instalar.p(os.path.join(instalar.AQUI, "gancho.py"))
        linha = instalar.p(os.path.join(instalar.AQUI, "statusline.py"))
        self.settings({"hooks": {"Stop": [{"hooks": [{"command": f'"py" -I "{gancho_py}"'}]},
                                          {"hooks": [{"command": "outro.exe"}]}],
                                 "SessionStart": [{"hooks": [{"command": f'"py" -I "{gancho_py}"'}]}]},
                       "statusLine": {"command": f'"py" -I "{linha}"'}, "model": "x"})
        self.com_cfg()
        instalar.CFG.update(instalar.desligar_tudo(instalar.CFG))
        s = instalar.montar_settings_claude()
        self.assertEqual(s["hooks"], {"Stop": [{"hooks": [{"command": "outro.exe"}]}]})
        self.assertNotIn("statusLine", s)
        self.assertEqual(s["model"], "x")

    def test_status_line_de_outra_pessoa_nunca_sai(self):
        self.settings({"statusLine": {"command": "meu-status.exe"}})
        cfg = self.com_cfg(statusline=False)
        s = instalar.montar_settings_claude()
        self.assertEqual(s["statusLine"], {"command": "meu-status.exe"})
        self.assertTrue(all(len(s["hooks"][e]) == 1 for e in instalar.EVENTOS_CLAUDE))
        self.assertTrue(cfg["conversas"]["grok"])

    def test_hooks_do_cursor_e_do_codex_saem_e_os_dos_outros_ficam(self):
        cursor = {"version": 1, "hooks": {"stop": [{"command": "outro"}, {"command": "x gancho_cursor.py"}],
                                          "sessionEnd": [{"command": "y gancho_cursor.py"}]}}
        self.assertEqual(instalar.montar_hooks_cursor(cursor, ligado=False),
                         {"version": 1, "hooks": {"stop": [{"command": "outro"}]}})
        self.assertIsNone(instalar.montar_hooks_cursor(None, ligado=False))      # nada a tirar: nao cria arquivo
        codex = {"hooks": {"Stop": [{"hooks": [{"command": "& py gancho_codex.py"}]}]}}
        self.assertEqual(instalar.montar_hooks_codex(codex, ligado=False), {"hooks": {}})

    def test_bloco_do_codex_sai_e_o_texto_da_pessoa_fica(self):
        texto = f"{instalar.INICIO_AGENTS}\nregras\n{instalar.FIM_AGENTS}\n\nMinhas notas\n"
        self.assertEqual(instalar.montar_agents_codex(texto, ligado=False), "Minhas notas\n")

    def test_grok_desmarcado_tira_o_bloco_inteiro(self):
        grok = os.path.join(self.home, ".grok")
        os.makedirs(grok)
        with open(os.path.join(grok, "config.toml"), "w", encoding="utf-8") as f:
            f.write(f'[cli]\ntheme = "dark"\n\n{instalar.INICIO_BLOCO}\n[plugins]\nenabled = ["a"]\n{instalar.FIM_BLOCO}\n')
        cfg = self.com_cfg()
        cfg["conversas"]["grok"] = cfg["ambiente"]["grok"] = False
        with mock.patch.object(instalar, "GROK", grok), mock.patch.object(instalar, "HOME", self.home):
            novo = instalar.montar_config_grok()
        self.assertNotIn(instalar.INICIO_BLOCO, novo)
        self.assertIn('theme = "dark"', novo)

    def test_grok_so_conversas_leva_o_hook_sem_plugins(self):
        cfg = self.com_cfg()
        cfg["ambiente"]["grok"] = False
        with mock.patch.object(instalar, "plugins_do_claude", lambda: [("superpowers", "x")]):
            bloco = instalar.montar_bloco_grok()
        self.assertIn("[[hooks.Stop]]", bloco)
        self.assertNotIn("[plugins]", bloco)

    def test_apagar_se_gerado_so_apaga_o_arquivo_do_tagteam(self):
        nosso, dele = os.path.join(self.home, "a.md"), os.path.join(self.home, "b.md")
        with open(nosso, "w", encoding="utf-8") as f:
            f.write(instalar.MARCA_GERADO + "\n\ntexto\n")
        with open(dele, "w", encoding="utf-8") as f:
            f.write("# Minhas regras\n")
        mudou = []
        instalar.apagar_se_gerado(nosso, False, mudou)
        instalar.apagar_se_gerado(dele, False, mudou)
        self.assertFalse(os.path.exists(nosso))
        self.assertTrue(os.path.exists(dele))

    def test_atalhos_sem_aprovacao_so_quando_pedido(self):
        self.com_cfg(atalhos_sem_aprovacao=False)
        self.assertEqual(instalar._livre("--always-approve"), "")
        instalar.CFG["atalhos_sem_aprovacao"] = True
        self.assertEqual(instalar._livre("--always-approve"), " --always-approve")

    def test_atalhos_de_ia_desmarcada_saem(self):
        menu = os.path.join(self.home, "menu")
        os.makedirs(menu)
        for nome in ("Grok.lnk", "Grok --resume.lnk", "Outro.lnk"):
            open(os.path.join(menu, nome), "w").close()
        cfg = self.com_cfg()
        cfg["conversas"]["grok"] = cfg["ambiente"]["grok"] = False
        with mock.patch.object(instalar, "MENU", menu):
            instalar.gravar_atalhos({"Grok.lnk": "x"}, "i", False, [], "grok")
        self.assertEqual(os.listdir(menu), ["Outro.lnk"])

    def test_passo_que_falha_nao_impede_os_outros(self):
        feitos = []

        def quebra(_seco, _mudou):
            raise RuntimeError("fora do ar")
        passos = (("a", "um", quebra), ("b", "dois", lambda s, m: (feitos.append("dois"), m.append("x"))))
        avisos = []
        with mock.patch.object(instalar, "PASSOS", passos), mock.patch("sys.stderr", io.StringIO()):
            mudou, falhou = instalar.rodar_passos(True, lambda *a: avisos.append(a[4]))
        self.assertTrue(falhou)
        self.assertEqual(feitos, ["dois"])
        self.assertEqual(mudou, ["x"])
        self.assertEqual(avisos, ["rodando", "erro: RuntimeError: fora do ar", "rodando", "ok"])

    def test_desligar_tudo_nao_muda_a_config_original(self):
        cfg = self.com_cfg()
        off = instalar.desligar_tudo(cfg)
        self.assertFalse(any(off["conversas"].values()) or any(off["ambiente"].values()) or off["atalhos"])
        self.assertTrue(cfg["conversas"]["grok"])


    def test_todo_passo_tem_titulo_em_ingles(self):
        self.assertEqual([t for _ia, t, _f in instalar.PASSOS if t not in instalar.TITULOS_EN], [])



class TestAtualizarClis(ComEstado):
    def setUp(self):
        super().setUp()
        self.cfg = config.normalizar({"atualizar_clis": True}, config.PADRAO_NOVO)
        ps = [mock.patch.object(instalar, "CFG", self.cfg),
              mock.patch.object(instalar, "comandos_de_atualizacao", lambda: {"grok": ["grok", "update"],
                                                                                "codex": ["codex", "update"]})]
        for p in ps:
            p.start()
            self.addCleanup(p.stop)
        self.rodados = []

    def rodar(self, codigos=None):
        codigos = codigos or {}

        def falso(cmd):
            self.rodados.append(cmd[0])
            return codigos.get(cmd[0], 0)
        return falso

    def test_roda_o_update_de_cada_cli_uma_vez_por_dia(self):
        mudou = []
        instalar.passo_atualizar_clis(False, mudou, agora=1000.0, rodar=self.rodar())
        self.assertEqual(sorted(self.rodados), ["codex", "grok"])
        self.assertEqual(len(mudou), 2)
        carimbo = instalar._carimbo_atualizar()
        os.utime(carimbo, (1000.0, 1000.0))
        instalar.passo_atualizar_clis(False, [], agora=1000.0 + 3600, rodar=self.rodar())       # no mesmo dia
        self.assertEqual(len(self.rodados), 2)
        instalar.passo_atualizar_clis(False, [], agora=1000.0 + instalar.INTERVALO_ATUALIZAR_S, rodar=self.rodar())
        self.assertEqual(len(self.rodados), 4)
        self.assertFalse(os.path.exists(carimbo + ".trava"))

    def test_desligado_ou_ensaio_nao_roda_nada(self):
        self.cfg["atualizar_clis"] = False
        instalar.passo_atualizar_clis(False, [], agora=1000.0, rodar=self.rodar())
        self.cfg["atualizar_clis"] = True
        mudou = []
        instalar.passo_atualizar_clis(True, mudou, agora=1000.0, rodar=self.rodar())
        self.assertEqual(self.rodados, [])
        self.assertEqual(mudou, ["atualizaria: codex, grok"])

    def test_falha_nao_derruba_a_instalacao_e_o_monitor_mostra(self):
        import saude
        mudou = []
        instalar.passo_atualizar_clis(False, mudou, agora=1000.0, rodar=self.rodar({"grok": 1}))   # nao levanta
        self.assertIn("grok: nao atualizou (1); tenta de novo amanha", mudou)
        self.assertEqual(sorted(self.rodados), ["codex", "grok"])            # um falhar nao impede o outro
        self.assertFalse(os.path.exists(instalar._carimbo_atualizar() + ".trava"))
        self.assertEqual(saude.falhas_de_atualizacao(), ["grok"])
        os.utime(instalar._carimbo_atualizar(), (1000.0, 1000.0))             # o carimbo no relogio do teste
        instalar.passo_atualizar_clis(False, [], agora=1000.0 + instalar.INTERVALO_ATUALIZAR_S, rodar=self.rodar())
        self.assertEqual(saude.falhas_de_atualizacao(), [])                    # deu certo depois: o aviso some

    def test_outra_sessao_atualizando_nao_roda_de_novo(self):
        open(instalar._carimbo_atualizar() + ".trava", "w").close()
        instalar.passo_atualizar_clis(False, [], rodar=self.rodar())
        self.assertEqual(self.rodados, [])

    def test_quem_ja_usava_nao_atualiza_sem_pedir(self):
        self.assertFalse(config.PADRAO_LEGADO["atualizar_clis"])
        self.assertTrue(config.PADRAO_NOVO["atualizar_clis"])

    def test_trava_removida_no_meio_nao_falha_o_passo(self):
        trava = instalar._carimbo_atualizar() + ".trava"
        base = self.rodar()

        def rodar_apagando_trava(cmd):
            if os.path.exists(trava):
                os.remove(trava)
            return base(cmd)

        instalar.passo_atualizar_clis(False, [], agora=1000.0, rodar=rodar_apagando_trava)
        self.assertEqual(sorted(self.rodados), ["codex", "grok"])
        self.assertTrue(os.path.isfile(instalar._carimbo_atualizar()))


class TestComandosAtualizar(ComEstado):
    def test_so_os_clis_marcados_e_instalados(self):
        cfg = config.normalizar({"conversas": {"grok": False}, "ambiente": {"grok": False}}, config.PADRAO_NOVO)
        existe = {"agy.exe", "grok.exe", "x it's.cmd"}
        with mock.patch.object(instalar, "CFG", cfg), mock.patch.object(instalar, "AGY", "C:/l/agy.exe"), \
                mock.patch.object(instalar, "GROK_EXE", "C:/g/grok.exe"), \
                mock.patch.object(instalar, "CURSOR_AGENT", "C:/A B/x it's.cmd"), \
                mock.patch.object(instalar, "_codex_exe", lambda: "C:/c/codex.exe"), \
                mock.patch("os.path.isfile", lambda c: os.path.basename(c) in existe):
            cmds = instalar.comandos_de_atualizacao()
        self.assertEqual(sorted(cmds), ["antigravity", "codex", "cursor"])      # o Grok esta desmarcado
        self.assertEqual(cmds["antigravity"], ["C:/l/agy.exe", "update"])
        self.assertEqual(cmds["cursor"][-1], "& 'C:/A B/x it''s.cmd' update")   # aspas simples dobradas

    def test_cursor_app_acha_em_program_files_e_usa_no_icone(self):
        pf = r"C:\Program Files\cursor\Cursor.exe"
        gravados = []
        with mock.patch.object(instalar, "CURSOR_APP", r"C:\nao\existe\Cursor.exe"), \
                mock.patch.object(instalar, "CURSOR_APPS", (pf,)), \
                mock.patch.object(instalar, "CURSOR_AGENT", r"C:\ca\cursor-agent.cmd"), \
                mock.patch("os.path.isfile", lambda c: c in (pf, r"C:\ca\cursor-agent.cmd")), \
                mock.patch("os.path.isdir", lambda _c: True), \
                mock.patch.object(instalar, "gravar_atalhos",
                                  lambda cmds, icone, *_a: gravados.append((cmds, icone))):
            instalar.criar_atalhos_cursor(False, [])
        self.assertEqual(len(gravados), 1)
        self.assertEqual(gravados[0][1], pf + ",0")


if __name__ == "__main__":
    unittest.main()
