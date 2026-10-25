"""Migration 016, repositórios de pessoa e sessão e o login ponta a ponta contra um Postgres de verdade
(só com PGHOST; cada teste usa um schema descartável). Nomes e senhas inventados."""

import os
import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from src.model.erros import DadoInvalido
from src.service import senha as senhas
from src.service.autenticacao import hash_do_token
from tests.test_candidaturas import _pool, _schema_descartavel

AGORA = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
SENHA = "senha-de-teste-123"


@unittest.skipUnless(os.environ.get("PGHOST"), "PGHOST não definido: integração com Postgres pulada")
class PostgresLoginTest(unittest.TestCase):
    def setUp(self):
        from src.repository.pessoa_postgres import PessoaRepositoryPostgres
        from src.repository.sessao_postgres import SessaoRepositoryPostgres

        self.conn, self.schema = _schema_descartavel(self, "teste_login")
        self.pessoas = PessoaRepositoryPostgres(self.conn)
        self.sessoes = SessaoRepositoryPostgres(self.conn)

    def contar(self, tabela: str) -> int:
        return self.conn.execute(f"SELECT count(*) FROM {tabela}").fetchone()[0]

    # --- migration 016 -----------------------------------------------------------------------------------------

    def test_a_016_cria_senha_hash_nula_e_a_tabela_sessao(self):
        colunas = {
            l[0]: (l[1], l[2])
            for l in self.conn.execute(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'sessao'"
            )
        }
        self.assertEqual(colunas["id"][0], "text")
        self.assertEqual(colunas["pessoa_id"], ("bigint", "NO"))
        self.assertEqual(colunas["expira_em"], ("timestamp with time zone", "NO"))
        self.assertEqual(colunas["criada_em"][1], "NO")
        senha_hash = self.conn.execute(
            "SELECT is_nullable FROM information_schema.columns WHERE table_schema = current_schema() "
            "AND table_name = 'pessoa' AND column_name = 'senha_hash'"
        ).fetchone()
        self.assertEqual(senha_hash[0], "YES")
        indices = {l[0] for l in self.conn.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'sessao'")}
        self.assertIn("sessao_expira_em_idx", indices)

    def test_a_pessoa_1_do_seed_nasce_sem_senha_e_portanto_nao_entra(self):
        credencial = self.pessoas.credencial_por_nome("Pessoa 1")

        self.assertEqual(credencial.id, 1)
        self.assertIsNone(credencial.senha_hash)

    def test_a_016_nao_apaga_a_senha_ao_rodar_de_novo(self):
        from pathlib import Path

        self.pessoas.definir_senha(1, senhas.hash_senha(SENHA))
        texto = (Path(__file__).resolve().parent.parent / "migrations" / "016_sessao.sql").read_text(encoding="utf-8")

        self.conn.execute(texto)

        self.assertTrue(senhas.verificar(SENHA, self.pessoas.credencial_por_nome("Pessoa 1").senha_hash))

    def test_a_016_nao_tem_begin_commit_nem_concurrently(self):
        from pathlib import Path

        texto = (Path(__file__).resolve().parent.parent / "migrations" / "016_sessao.sql").read_text(encoding="utf-8").upper()
        # Só o SQL conta (os comentários podem falar nas palavras).
        sql = "\n".join(l for l in texto.splitlines() if not l.strip().startswith("--"))
        for proibida in ("BEGIN", "COMMIT", "ROLLBACK", "CONCURRENTLY"):
            self.assertNotIn(proibida, sql)

    # --- pessoa --------------------------------------------------------------------------------------------------

    def test_criar_e_achar_pela_caixa_e_pelas_pontas(self):
        pessoa_id = self.pessoas.criar("Pessoa Teste Nova", senhas.hash_senha(SENHA))

        achada = self.pessoas.credencial_por_nome("  PESSOA teste NOVA ")

        self.assertEqual((achada.id, achada.nome_exibicao), (pessoa_id, "Pessoa Teste Nova"))
        self.assertTrue(senhas.verificar(SENHA, achada.senha_hash))
        self.assertIsNone(self.pessoas.credencial_por_nome("Outra Pessoa"))

    def test_nome_repetido_sem_diferenciar_caixa_e_recusado_pelo_banco(self):
        self.pessoas.criar("Pessoa Teste Nova", None)

        with self.assertRaises(DadoInvalido):
            self.pessoas.criar("PESSOA TESTE NOVA", None)
        self.assertEqual(self.pessoas.listar(), [1, 2])

    def test_nome_com_nul_e_recusado_antes_do_banco(self):
        with self.assertRaises(DadoInvalido):
            self.pessoas.criar("Pessoa\x00Teste", None)

    def test_definir_senha(self):
        self.assertFalse(self.pessoas.definir_senha(99, "x"))
        self.assertTrue(self.pessoas.definir_senha(1, senhas.hash_senha(SENHA)))

        self.assertTrue(senhas.verificar(SENHA, self.pessoas.credencial_por_nome("Pessoa 1").senha_hash))

    # --- sessão --------------------------------------------------------------------------------------------------

    def test_sessao_valida_expirada_e_removida(self):
        self.sessoes.criar("h-valida", 1, AGORA + timedelta(days=1))
        self.sessoes.criar("h-vencida", 1, AGORA - timedelta(seconds=1))

        valida = self.sessoes.obter("h-valida", AGORA)
        self.assertEqual((valida.id, valida.nome_exibicao), (1, "Pessoa 1"))
        self.assertIsNone(self.sessoes.obter("h-vencida", AGORA))
        self.assertIsNone(self.sessoes.obter("h-valida", AGORA + timedelta(days=1)))  # no instante exato já venceu
        self.assertIsNone(self.sessoes.obter("nao-existe", AGORA))

        self.assertEqual(self.sessoes.limpar_expiradas(AGORA), 1)
        self.sessoes.remover("h-valida")
        self.assertEqual(self.contar("sessao"), 0)

    def test_remover_da_pessoa_so_apaga_as_dela(self):
        dois = self.pessoas.criar("Pessoa Teste Dois", None)
        self.sessoes.criar("a", 1, AGORA + timedelta(days=1))
        self.sessoes.criar("b", 1, AGORA + timedelta(days=1))
        self.sessoes.criar("c", dois, AGORA + timedelta(days=1))

        self.assertEqual(self.sessoes.remover_da_pessoa(1), 2)
        self.assertIsNotNone(self.sessoes.obter("c", AGORA))

    def test_apagar_a_pessoa_apaga_as_sessoes_e_a_fk_impede_sessao_de_pessoa_inexistente(self):
        import psycopg

        dois = self.pessoas.criar("Pessoa Teste Dois", None)
        self.sessoes.criar("c", dois, AGORA + timedelta(days=1))
        self.conn.execute("DELETE FROM pessoa WHERE id = %s", (dois,))
        self.assertEqual(self.contar("sessao"), 0)

        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            self.sessoes.criar("d", 99, AGORA + timedelta(days=1))

    # --- ponta a ponta ---------------------------------------------------------------------------------------------

    def app(self):
        from src.controller.api import _PorChamada, criar_app
        from src.repository.candidatura_postgres import CandidaturaRepositoryPostgres
        from src.repository.pedido_ia_postgres import PedidoIARepositoryPostgres
        from src.repository.pessoa_postgres import PessoaRepositoryPostgres
        from src.repository.pessoa_provider_postgres import PessoaProviderRepositoryPostgres
        from src.repository.postgres import (
            CurriculoRepositoryPostgres,
            PerfilBuscaRepositoryPostgres,
            VagaEncontradaRepositoryPostgres,
        )
        from src.repository.sessao_postgres import SessaoRepositoryPostgres
        from src.service.busca_service import BuscaService
        from src.service.coleta_service import RepositoriosDaPessoa
        from src.service.pedido_ia_service import FabricasPorPessoa

        pool = _pool(self, self.schema, tamanho=4)

        def pc(classe, pessoa=None):
            return _PorChamada(lambda: pool, lambda: classe, pessoa)

        fabricas = FabricasPorPessoa(
            perfis=lambda p: pc(PerfilBuscaRepositoryPostgres, p),
            curriculos=lambda p: pc(CurriculoRepositoryPostgres, p),
            encontradas=lambda p: pc(VagaEncontradaRepositoryPostgres, p),
        )
        return criar_app(
            pc(PerfilBuscaRepositoryPostgres, 1),
            pc(CurriculoRepositoryPostgres, 1),
            BuscaService([]),
            encontradas=pc(VagaEncontradaRepositoryPostgres, 1),
            candidaturas=pc(CandidaturaRepositoryPostgres, 1),
            pedidos_ia=pc(PedidoIARepositoryPostgres, 1),
            pessoas=pc(PessoaRepositoryPostgres),
            sessoes=pc(SessaoRepositoryPostgres),
            repositorios_da_pessoa=lambda p: RepositoriosDaPessoa(
                pc(PerfilBuscaRepositoryPostgres, p),
                pc(VagaEncontradaRepositoryPostgres, p),
                pc(CurriculoRepositoryPostgres, p),
                pc(PedidoIARepositoryPostgres, p),
                pc(CandidaturaRepositoryPostgres, p),
            ),
            providers_da_pessoa=lambda p: pc(PessoaProviderRepositoryPostgres, p),
            fabricas=fabricas,
        )

    def test_login_logout_e_duas_pessoas_com_uma_conexao_do_pool_por_chamada(self):
        origem = {"Origin": "http://testserver"}
        self.pessoas.definir_senha(1, senhas.hash_senha(SENHA))
        self.pessoas.criar("Pessoa Teste Dois", senhas.hash_senha("outra-senha-de-teste"))
        api = self.app()
        um, dois, anonimo = (TestClient(api, follow_redirects=False) for _ in range(3))

        self.assertEqual(anonimo.get("/perfil").status_code, 401)
        # A pessoa 1 não entra com a senha da 2, nem a 2 sem senha.
        self.assertEqual(um.post("/login", data={"nome": "Pessoa 1", "senha": "outra-senha-de-teste"}, headers=origem).status_code, 401)
        self.assertEqual(um.post("/login", data={"nome": "Pessoa 1", "senha": SENHA}, headers=origem).status_code, 303)
        self.assertEqual(
            dois.post("/login", data={"nome": "pessoa teste dois", "senha": "outra-senha-de-teste"}, headers=origem).status_code, 303
        )
        token = um.cookies.get("vagas_sessao")
        ids = {l[0] for l in self.conn.execute("SELECT id FROM sessao")}
        self.assertIn(hash_do_token(token), ids)
        self.assertNotIn(token, ids)  # o banco só guarda o hash

        self.assertEqual(um.put("/perfil", json={"termos": ["python"]}).status_code, 200)
        self.assertEqual(dois.put("/perfil", json={"termos": ["golang"]}).status_code, 200)
        self.assertEqual(um.get("/perfil").json()["termos"], ["python"])
        self.assertEqual(dois.get("/perfil").json()["termos"], ["golang"])
        self.assertIn("Pessoa 1", um.get("/tela").text)
        self.assertIn("Pessoa Teste Dois", dois.get("/tela").text)

        self.assertEqual(um.post("/logout", headers=origem).status_code, 303)
        self.assertEqual(um.get("/perfil").status_code, 401)
        self.assertEqual(dois.get("/perfil").status_code, 200)
        self.assertEqual(self.contar("sessao"), 1)


if __name__ == "__main__":
    unittest.main()
