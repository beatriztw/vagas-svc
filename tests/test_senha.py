"""Hash e conferência da senha (scrypt da stdlib). Senhas daqui são inventadas."""

import base64
import unittest
from unittest import mock

from src.service import senha

SENHA = "senha-de-teste-123"


class HashTest(unittest.TestCase):
    def test_formato_autodescritivo_com_os_parametros_documentados(self):
        partes = senha.hash_senha(SENHA).split("$")

        self.assertEqual(partes[:4], ["scrypt", str(2**14), "8", "1"])
        self.assertEqual(len(base64.b64decode(partes[4])), 16)
        self.assertEqual(len(base64.b64decode(partes[5])), 32)

    def test_sal_aleatorio_por_senha(self):
        a, b = senha.hash_senha(SENHA), senha.hash_senha(SENHA)

        self.assertNotEqual(a, b)
        self.assertTrue(senha.verificar(SENHA, a) and senha.verificar(SENHA, b))

    def test_o_hash_nao_contem_a_senha(self):
        self.assertNotIn(SENHA, senha.hash_senha(SENHA))


class VerificarTest(unittest.TestCase):
    def setUp(self):
        self.guardado = senha.hash_senha(SENHA)

    def test_senha_certa(self):
        self.assertTrue(senha.verificar(SENHA, self.guardado))

    def test_senha_errada(self):
        self.assertFalse(senha.verificar(SENHA + "x", self.guardado))
        self.assertFalse(senha.verificar("", self.guardado))

    def test_acentos_compostos_e_decompostos_valem_igual(self):
        guardado = senha.hash_senha("açaí-de-teste-1")

        self.assertTrue(senha.verificar("açaí-de-teste-1", guardado))

    def test_hash_nulo_nunca_confere(self):
        self.assertFalse(senha.verificar(SENHA, None))
        self.assertFalse(senha.verificar("", None))

    def test_hash_torto_nunca_confere_e_nunca_levanta(self):
        _, n, r, p, sal, h = self.guardado.split("$")
        tortos = [
            "",
            "texto qualquer",
            "scrypt$$$$$",
            f"bcrypt${n}${r}${p}${sal}${h}",
            f"scrypt${n}${r}${p}${sal}",  # falta o hash
            f"scrypt${n}${r}${p}${sal}${h}$extra",
            f"scrypt$abc${r}${p}${sal}${h}",
            f"scrypt$-16384${r}${p}${sal}${h}",
            f"scrypt${n}${r}${p}$@@@${h}",  # base64 inválido
            f"scrypt$16385${r}${p}${sal}${h}",  # n não é potência de 2
            f"scrypt${2**30}${r}${p}${sal}${h}",  # pediria gigabytes
            f"scrypt${n}$999${p}${sal}${h}",
            f"scrypt${n}${r}$99${sal}${h}",
            f"scrypt${n}${r}${p}${base64.b64encode(b'ab').decode()}${h}",  # sal curto demais
            f"scrypt${n}${r}${p}${sal}${base64.b64encode(b'ab').decode()}",
        ]
        for torto in tortos:
            with self.subTest(torto=torto[:40]):
                self.assertFalse(senha.verificar(SENHA, torto))

    def test_valor_que_nao_e_texto_nao_levanta(self):
        self.assertFalse(senha.verificar(SENHA, 123))  # type: ignore[arg-type]
        self.assertFalse(senha.verificar(None, self.guardado))  # type: ignore[arg-type]

    def test_senha_enorme_nao_confere(self):
        self.assertFalse(senha.verificar("a" * (senha.MAX_LOGIN + 1), self.guardado))

    def test_senha_com_lixo_de_encoding_nao_levanta(self):
        self.assertFalse(senha.verificar("\udcff\udcfe", self.guardado))

    def test_hash_antigo_com_outro_custo_continua_valendo(self):
        sal = b"0123456789abcdef"
        import hashlib

        derivado = hashlib.scrypt(SENHA.encode(), salt=sal, n=2**10, r=8, p=1, dklen=32)
        antigo = "$".join(["scrypt", str(2**10), "8", "1", base64.b64encode(sal).decode(), base64.b64encode(derivado).decode()])

        self.assertTrue(senha.verificar(SENHA, antigo))
        self.assertFalse(senha.verificar("outra", antigo))

    def test_sem_hash_utilizavel_gasta_o_mesmo_scrypt(self):
        # Nome inexistente, senha NULL e hash torto não podem responder mais rápido que a senha errada.
        real = senha._scrypt
        for guardado in (None, "torto", self.guardado):
            with self.subTest(guardado=str(guardado)[:10]):
                with mock.patch.object(senha, "_scrypt", wraps=real) as scrypt:
                    senha.verificar("errada", guardado)
                self.assertEqual(scrypt.call_count, 1)

    def test_a_comparacao_e_em_tempo_constante(self):
        with mock.patch.object(senha.hmac, "compare_digest", wraps=senha.hmac.compare_digest) as comparar:
            senha.verificar(SENHA, self.guardado)
        self.assertEqual(comparar.call_count, 1)


if __name__ == "__main__":
    unittest.main()
