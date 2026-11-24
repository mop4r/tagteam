import unittest

import pb


class TestPb(unittest.TestCase):
    def msg(self):
        interna = pb.gravar([[1, 2, b"oi"], [3, 0, 7]])
        return pb.gravar([[1, 0, 14], [19, 2, interna], [20, 5, b"\x01\x02\x03\x04"], [21, 1, b"12345678"]])

    def test_ida_e_volta_sem_perda(self):
        m = self.msg()
        self.assertEqual(pb.gravar(pb.ler(m)), m)

    def test_varint_grande_e_negativo(self):
        for n in (0, 1, 127, 128, 300, 2**35, 2**63):
            v, i = pb._varint(pb.enc_varint(n), 0)
            self.assertEqual(v, n)
        v, _ = pb._varint(pb.enc_varint(-1), 0)
        self.assertEqual(v, 2**64 - 1)

    def test_trocar_texto_aninhado_recalcula_tamanhos(self):
        m = pb.trocar(self.msg(), [19, 1], "olá, mundo com acento 🙂")
        self.assertEqual(pb.obter(m, [19, 1]).decode(), "olá, mundo com acento 🙂")
        self.assertEqual(pb.obter(m, [19, 3]), 7)
        self.assertEqual(pb.obter(m, [20]), b"\x01\x02\x03\x04")

    def test_apagar_campo(self):
        m = pb.trocar(self.msg(), [19, 3], None)
        self.assertIsNone(pb.obter(m, [19, 3]))
        self.assertEqual(pb.obter(m, [19, 1]), b"oi")

    def test_criar_caminho_que_falta(self):
        m = pb.trocar(self.msg(), [30, 2, 1], 99)
        self.assertEqual(pb.obter(m, [30, 2, 1]), 99)

    def test_texto_vazio_e_enorme(self):
        for t in ("", "x" * 200_000):
            m = pb.trocar(self.msg(), [19, 1], t)
            self.assertEqual(pb.obter(m, [19, 1]).decode(), t)

    def test_entrada_corrompida_falha_claro(self):
        for ruim in (b"\x0a\x05ab", b"\x80", b"\x0b", b"\x15\x01"):
            with self.assertRaises(ValueError):
                pb.ler(ruim)

    def test_troca_de_id_exige_mesmo_tamanho(self):
        self.assertEqual(pb.trocar_bytes_iguais(b"aXa", b"X", b"Y"), b"aYa")
        with self.assertRaises(ValueError):
            pb.trocar_bytes_iguais(b"a", b"a", b"bb")


if __name__ == "__main__":
    unittest.main()
