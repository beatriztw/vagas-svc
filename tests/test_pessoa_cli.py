"""CLI `python -m src.controller.pessoa` (criar e senha). Nomes e senhas inventados."""

import getpass
import inspect
import io
import unittest

from src.controller import pessoa as cli
from src.repository.pessoa import PessoaRepositoryMemoria
from src.repository.sessao import SessaoRepositoryMemoria
from src.service import senha as senhas

SENHA = "senha-de-teste-123"


class Teclado:
    """Responde aos pedidos de senha na ordem; guarda os prompts (que nunca levam a senha)."""

    def __init__(self, *respostas: str):
        self.respostas = list(respostas)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.respostas.pop(0)


class CliTest(unittest.TestCase):
    def setUp(self):
        self.pessoas = PessoaRepositoryMemoria([1])
        self.sessoes = SessaoRepositoryMemoria(self.pessoas)
        self.saida, self.erro = io.StringIO(), io.StringIO()

    def rodar(self, argv, *respostas):
        teclado = Teclado(*respostas)
        codigo = cli.rodar(argv, self.pessoas, self.sessoes, self.saida, self.erro, teclado)
        return codigo, teclado

    def test_a_leitura_da_senha_e_o_getpass(self):
        self.assertIs(inspect.signature(cli.rodar).parameters["ler"].default, getpass.getpass)

    def test_criar_pede_a_senha_duas_vezes_e_so_imprime_o_id(self):
        codigo, teclado = self.rodar(["criar", "--nome", "Pessoa Teste Nova"], SENHA, SENHA)

        self.assertEqual(codigo, 0)
        self.assertEqual(len(teclado.prompts), 2)
        self.assertEqual(self.saida.getvalue().strip(), "pessoa 2 criada")
        credencial = self.pessoas.credencial_por_nome("Pessoa Teste Nova")
        self.assertEqual(credencial.id, 2)
        self.assertTrue(senhas.verificar(SENHA, credencial.senha_hash))

    def test_nunca_imprime_a_senha_nem_o_hash(self):
        self.rodar(["criar", "--nome", "Pessoa Teste Nova"], SENHA, SENHA)
        self.rodar(["senha", "--id", "1"], SENHA + "x", SENHA + "x")
        self.rodar(["senha", "--id", "1"], SENHA, "outra-diferente-1")
        tudo = self.saida.getvalue() + self.erro.getvalue()

        self.assertNotIn(SENHA, tudo)
        self.assertNotIn("scrypt", tudo)
        for hash_ in (self.pessoas.credencial_por_nome(n).senha_hash for n in ("Pessoa Teste Nova", "Pessoa Teste 1")):
            self.assertNotIn(hash_.split("$")[-1], tudo)

    def test_senhas_diferentes_nao_criam_ninguem(self):
        codigo, _ = self.rodar(["criar", "--nome", "Pessoa Teste Nova"], SENHA, SENHA + "x")

        self.assertEqual(codigo, 1)
        self.assertEqual(self.pessoas.listar(), [1])
        self.assertIn("não são iguais", self.erro.getvalue())

    def test_senha_curta_ou_longa_demais_e_recusada(self):
        for ruim in ("curta", "123456789", "x" * 129, ""):
            with self.subTest(len(ruim)):
                codigo, teclado = self.rodar(["criar", "--nome", "Pessoa Teste Nova"], ruim)
                self.assertEqual(codigo, 1)
                self.assertEqual(len(teclado.prompts), 1)
        self.assertEqual(self.pessoas.listar(), [1])

    def test_senha_de_10_caracteres_vale(self):
        codigo, _ = self.rodar(["criar", "--nome", "Pessoa Teste Nova"], "1234567890", "1234567890")

        self.assertEqual(codigo, 0)

    def test_nome_repetido_sem_diferenciar_caixa_e_recusado(self):
        self.rodar(["criar", "--nome", "Pessoa Teste Nova"], SENHA, SENHA)

        codigo, _ = self.rodar(["criar", "--nome", " pessoa TESTE nova "], SENHA, SENHA)

        self.assertEqual(codigo, 1)
        self.assertEqual(self.pessoas.listar(), [1, 2])
        self.assertIn("já existe", self.erro.getvalue())

    def test_nome_invalido_falha_antes_de_pedir_a_senha(self):
        for nome in ("", "   ", "a" * 61, "Pessoa\x00Teste", "Pessoa\nTeste"):
            with self.subTest(nome=nome[:10]):
                codigo, teclado = self.rodar(["criar", "--nome", nome], SENHA, SENHA)
                self.assertEqual((codigo, teclado.prompts), (1, []))

    def test_senha_define_a_da_pessoa_e_encerra_as_sessoes_dela(self):
        self.pessoas.adicionar(1, "Pessoa Um", senhas.hash_senha("senha-antiga-123"))
        self.sessoes.criar("hash-da-sessao", 1, self.relogio_futuro())

        codigo, _ = self.rodar(["senha", "--id", "1"], SENHA, SENHA)

        self.assertEqual(codigo, 0)
        self.assertEqual(self.sessoes.linhas, {})
        hash_ = self.pessoas.credencial_por_nome("Pessoa Um").senha_hash
        self.assertTrue(senhas.verificar(SENHA, hash_))
        self.assertFalse(senhas.verificar("senha-antiga-123", hash_))

    @staticmethod
    def relogio_futuro():
        from datetime import datetime, timedelta, timezone

        return datetime.now(timezone.utc) + timedelta(days=1)

    def test_senha_de_pessoa_inexistente_falha(self):
        codigo, _ = self.rodar(["senha", "--id", "99"], SENHA, SENHA)

        self.assertEqual(codigo, 1)
        self.assertIn("não existe", self.erro.getvalue())

    def test_senha_da_pessoa_1_tira_o_estado_sem_senha(self):
        # Depois do deploy a pessoa 1 está sem senha (não entra); o comando é o que a libera.
        self.assertIsNone(self.pessoas.credencial_por_nome("Pessoa Teste 1").senha_hash)

        self.rodar(["senha", "--id", "1"], SENHA, SENHA)

        self.assertIsNotNone(self.pessoas.credencial_por_nome("Pessoa Teste 1").senha_hash)

    def test_argumentos_errados_dao_1(self):
        for argv in ([], ["criar"], ["senha"], ["senha", "--id", "x"], ["apagar"]):
            with self.subTest(argv=argv):
                self.assertEqual(self.rodar(argv)[0], 1)

    def test_ctrl_d_cancela(self):
        def ler(_):
            raise EOFError

        codigo = cli.rodar(["senha", "--id", "1"], self.pessoas, self.sessoes, self.saida, self.erro, ler)

        self.assertEqual(codigo, 1)


if __name__ == "__main__":
    unittest.main()
