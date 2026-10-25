"""Migrations automáticas contra um Postgres de verdade. Só rodam com PGHOST definido.

Cada teste usa um schema descartável (search_path da conexão), então `schema_migrations` e as
tabelas das migrations não tocam o schema padrão. O advisory lock é do banco inteiro, não do schema.
Migrations de teste são escritas à mão em pastas temporárias.
"""

import os
import tempfile
import threading
import unittest
import uuid
from pathlib import Path

import psycopg

from src.repository.migracoes import MigracaoFalhou, PASTA_MIGRATIONS, aplicar_com_retry, aplicar_pendentes


@unittest.skipUnless(os.environ.get("PGHOST"), "PGHOST não definido: integração com Postgres pulada")
class MigracoesTest(unittest.TestCase):
    def setUp(self):
        from psycopg import sql

        from src.repository.conexao import conectar

        self.sql = sql
        self.admin = conectar()
        self.addCleanup(self.admin.close)
        self.schema = f"teste_{uuid.uuid4().hex[:12]}"
        self.admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.admin.execute, sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))
        self.admin.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
        self.pasta = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def conectar(self):
        from src.repository.conexao import conectar

        conn = conectar()
        conn.execute(self.sql.SQL("SET search_path TO {}").format(self.sql.Identifier(self.schema)))
        return conn

    def aplicar(self, pasta=None):
        return aplicar_pendentes(pasta or self.pasta, self.conectar)

    def migration(self, nome: str, conteudo: str):
        (self.pasta / nome).write_text(conteudo, encoding="utf-8")

    def registradas(self) -> list[str]:
        return [r[0] for r in self.admin.execute("SELECT nome FROM schema_migrations ORDER BY nome")]

    def existe(self, tabela: str) -> bool:
        return self.admin.execute("SELECT to_regclass(%s)", (tabela,)).fetchone()[0] is not None

    def test_banco_vazio_aplica_todas_e_registra(self):
        self.migration("001_a.sql", "CREATE TABLE a (id int);")
        self.migration("002_b.sql", "CREATE TABLE b (id int);")
        self.assertEqual(self.aplicar(), ["001_a.sql", "002_b.sql"])
        self.assertEqual(self.registradas(), ["001_a.sql", "002_b.sql"])
        self.assertTrue(self.existe("a") and self.existe("b"))
        aplicada_em = self.admin.execute("SELECT aplicada_em FROM schema_migrations LIMIT 1").fetchone()[0]
        self.assertIsNotNone(aplicada_em)

    def test_segunda_execucao_nao_reaplica(self):
        # Sem IF NOT EXISTS: reaplicar daria "relation already exists".
        self.migration("001_a.sql", "CREATE TABLE a (id int);")
        self.aplicar()
        self.assertEqual(self.aplicar(), [])
        self.assertEqual(self.registradas(), ["001_a.sql"])

    def test_migration_nova_aplica_so_ela(self):
        self.migration("001_a.sql", "CREATE TABLE a (id int);")
        self.aplicar()
        self.migration("002_b.sql", "CREATE TABLE b (id int);")
        self.assertEqual(self.aplicar(), ["002_b.sql"])
        self.assertEqual(self.registradas(), ["001_a.sql", "002_b.sql"])

    def test_erro_faz_rollback_da_migration_e_levanta(self):
        self.migration("001_a.sql", "CREATE TABLE a (id int);")
        # A primeira instrução cria a tabela; a segunda falha: a tabela tem que sumir junto.
        self.migration("002_ruim.sql", "CREATE TABLE ruim (id int); SELECT * FROM nao_existe;")
        self.migration("003_c.sql", "CREATE TABLE c (id int);")
        with self.assertLogs("src.repository.migracoes", level="ERROR") as logs:
            with self.assertRaises(MigracaoFalhou) as ctx:
                self.aplicar()
        self.assertIn("002_ruim.sql", str(ctx.exception))
        self.assertIn("002_ruim.sql", logs.output[0])
        self.assertTrue(self.existe("a"))  # a anterior já tinha sido confirmada
        self.assertFalse(self.existe("ruim"))
        self.assertFalse(self.existe("c"))  # as seguintes não rodam
        self.assertEqual(self.registradas(), ["001_a.sql"])

    def test_falha_ao_registrar_desfaz_tambem_o_sql_da_migration(self):
        # Atomicidade SQL + registro: o SQL da migration roda sem erro, mas o INSERT em
        # schema_migrations é barrado por um trigger; a tabela criada pelo SQL tem que sumir junto.
        # (Sem `with conn.transaction()`, o SQL já teria sido confirmado em autocommit.)
        self.admin.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "nome TEXT PRIMARY KEY, aplicada_em TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        self.admin.execute(
            "CREATE FUNCTION barra_registro() RETURNS trigger LANGUAGE plpgsql AS "
            "$$ BEGIN RAISE EXCEPTION 'registro barrado de propósito'; END $$"
        )
        self.admin.execute(
            "CREATE TRIGGER barra BEFORE INSERT ON schema_migrations FOR EACH ROW EXECUTE FUNCTION barra_registro()"
        )
        self.migration("001_x.sql", "CREATE TABLE x (id int);")
        with self.assertLogs("src.repository.migracoes", level="ERROR"), self.assertRaises(MigracaoFalhou):
            self.aplicar()
        self.assertFalse(self.existe("x"))
        self.assertEqual(self.registradas(), [])

    def test_corrigida_a_migration_a_proxima_subida_aplica(self):
        self.migration("001_ruim.sql", "SELECT * FROM nao_existe;")
        with self.assertLogs("src.repository.migracoes", level="ERROR"), self.assertRaises(MigracaoFalhou):
            self.aplicar()
        self.migration("001_ruim.sql", "CREATE TABLE ok (id int);")
        self.assertEqual(self.aplicar(), ["001_ruim.sql"])

    def test_ordem_lexicografica_do_nome(self):
        # 002 depende da 001 e 010 da 002; criadas fora de ordem no disco.
        self.migration("010_c.sql", "INSERT INTO ordem VALUES ('010');")
        self.migration("001_a.sql", "CREATE TABLE ordem (n text);")
        self.migration("002_b.sql", "INSERT INTO ordem VALUES ('002');")
        self.assertEqual(self.aplicar(), ["001_a.sql", "002_b.sql", "010_c.sql"])
        # Ordem de aplicação = ordem de inserção (ctid).
        linhas = [r[0] for r in self.admin.execute("SELECT n FROM ordem ORDER BY ctid")]
        self.assertEqual(linhas, ["002", "010"])

    def test_arquivo_que_nao_e_sql_e_ignorado(self):
        self.migration("001_a.sql", "CREATE TABLE a (id int);")
        self.migration("LEIAME.md", "isto não é SQL")
        self.assertEqual(self.aplicar(), ["001_a.sql"])

    def test_pasta_inexistente_levanta(self):
        with self.assertRaises(MigracaoFalhou):
            self.aplicar(self.pasta / "nao_existe")

    def test_execucoes_concorrentes_aplicam_uma_vez_so(self):
        self.migration("001_a.sql", "CREATE TABLE corrida (id int);")
        # pg_sleep alarga a janela: sem o lock, a segunda execução leria "pendente" e inseriria de novo.
        self.migration("002_b.sql", "SELECT pg_sleep(0.5); INSERT INTO corrida VALUES (1);")
        resultados, erros = [], []

        def rodar():
            try:
                resultados.append(self.aplicar())
            except Exception as e:  # noqa: BLE001 - o teste só quer ver se alguma falhou
                erros.append(e)

        threads = [threading.Thread(target=rodar) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(erros, [])
        self.assertEqual(sorted(len(r) for r in resultados), [0, 0, 0, 2])
        self.assertEqual(self.admin.execute("SELECT count(*) FROM corrida").fetchone()[0], 1)
        self.assertEqual(self.registradas(), ["001_a.sql", "002_b.sql"])

    def test_migrations_reais_aplicam_em_banco_vazio_e_de_novo(self):
        nomes = sorted(p.name for p in PASTA_MIGRATIONS.glob("*.sql"))
        self.assertGreaterEqual(len(nomes), 3)
        self.assertEqual(self.aplicar(PASTA_MIGRATIONS), nomes)
        for tabela in ("curriculo", "perfil_busca", "vaga_encontrada", "candidatura"):
            self.assertTrue(self.existe(tabela), tabela)
        self.assertEqual(self.registradas(), nomes)
        self.assertEqual(self.aplicar(PASTA_MIGRATIONS), [])

    def test_banco_ja_migrado_a_mao_passa_a_constar_sem_erro(self):
        # O Pi aplicou 001–003 à mão (sem schema_migrations): no primeiro boot elas rodam de novo.
        for arquivo in sorted(PASTA_MIGRATIONS.glob("*.sql")):
            self.admin.execute(arquivo.read_text(encoding="utf-8"))
        self.assertFalse(self.existe("schema_migrations"))
        nomes = sorted(p.name for p in PASTA_MIGRATIONS.glob("*.sql"))
        self.assertEqual(self.aplicar(PASTA_MIGRATIONS), nomes)
        self.assertEqual(self.registradas(), nomes)

    def test_pasta_padrao_nao_depende_do_cwd(self):
        antes = os.getcwd()
        self.addCleanup(os.chdir, antes)
        os.chdir(tempfile.gettempdir())
        self.assertTrue(PASTA_MIGRATIONS.is_absolute())
        self.assertEqual(aplicar_pendentes(conectar=self.conectar), sorted(p.name for p in PASTA_MIGRATIONS.glob("*.sql")))

    # --- 013_pessoa.sql: dados de antes da 013 ficam na pessoa 1; a 013 é idempotente; a FK composta isola ---

    def aplicar_ate_012(self):
        """Só as 001–012 numa pasta própria, aplicadas pelo runner de verdade (com schema_migrations)."""
        for arquivo in sorted(PASTA_MIGRATIONS.glob("*.sql")):
            if arquivo.name < "013":
                (self.pasta / arquivo.name).write_text(arquivo.read_text(encoding="utf-8"), encoding="utf-8")
        self.assertEqual(len(self.aplicar()), 11)  # a 005 nunca existiu

    def aplicar_013(self):
        (self.pasta / "013_pessoa.sql").write_text(
            (PASTA_MIGRATIONS / "013_pessoa.sql").read_text(encoding="utf-8"), encoding="utf-8"
        )
        return self.aplicar()

    def popular_como_antes_da_013(self):
        """Uma linha de cada tabela, como o banco de produção tinha: vaga, candidatura, pedido, rótulo,
        perfil, currículo e pausa. Dados inventados."""
        for sql in (
            "INSERT INTO vaga_encontrada (url, titulo, empresa, plataforma) VALUES "
            "('https://exemplo.com/v1', 'Dev', 'Acme', 'Teste'), ('https://exemplo.com/v2', 'Dados', 'Acme', 'Teste')",
            "INSERT INTO candidatura (vaga_url) VALUES ('https://exemplo.com/v1')",
            "INSERT INTO curriculo (tipo, titulo, conteudo_md) VALUES ('base', 'Base', '# base')",
            "INSERT INTO curriculo (tipo, titulo, conteudo_md, base_id, vaga_url) "
            "VALUES ('adaptado', 'Adaptado', '# adaptado', 1, 'https://exemplo.com/v1')",
            "INSERT INTO pedido_ia (vaga_url, tipo, curriculo_base_id) VALUES ('https://exemplo.com/v1', 'compatibilidade', 1)",
            "INSERT INTO pedido_ia (vaga_url, tipo) VALUES ('https://exemplo.com/v2', 'curriculo')",
            "INSERT INTO rotulo_ia (vaga_url, faixa) VALUES ('https://exemplo.com/v1', 'alta')",
            "INSERT INTO perfil_busca (id, termos) VALUES (1, ARRAY['python'])",
            "INSERT INTO ia_lote_pausa (id, pausado) VALUES (1, true)",
        ):
            self.admin.execute(sql)

    TABELAS_DA_PESSOA = (
        "vaga_encontrada", "candidatura", "pedido_ia", "rotulo_ia", "curriculo", "perfil_busca", "ia_lote_pausa",
    )

    def test_013_dados_de_antes_continuam_e_ficam_na_pessoa_1(self):
        from src.repository.candidatura_postgres import CandidaturaRepositoryPostgres
        from src.repository.pedido_ia_postgres import PedidoIARepositoryPostgres
        from src.repository.postgres import (
            CurriculoRepositoryPostgres,
            PerfilBuscaRepositoryPostgres,
            VagaEncontradaRepositoryPostgres,
        )

        self.aplicar_ate_012()
        self.popular_como_antes_da_013()
        antes = {t: self.admin.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in self.TABELAS_DA_PESSOA}
        self.assertEqual(antes, dict(zip(self.TABELAS_DA_PESSOA, (2, 1, 2, 1, 2, 1, 1))))

        self.assertEqual(self.aplicar_013(), ["013_pessoa.sql"])

        for tabela in self.TABELAS_DA_PESSOA:
            with self.subTest(tabela=tabela):
                self.assertEqual(self.admin.execute(f"SELECT count(*) FROM {tabela}").fetchone()[0], antes[tabela])
                self.assertEqual(
                    self.admin.execute(f"SELECT DISTINCT pessoa_id FROM {tabela}").fetchall(), [(1,)]
                )
        # A pessoa 1 é a dona, com nome neutro (o real, se houver, fica só no banco).
        self.assertEqual(self.admin.execute("SELECT id, nome_exibicao FROM pessoa").fetchall(), [(1, "Pessoa 1")])

        # Os repositórios da pessoa 1 leem tudo o que existia.
        conn = self.conectar()
        self.addCleanup(conn.close)
        self.assertEqual(PerfilBuscaRepositoryPostgres(conn, 1).obter().termos, ["python"])
        self.assertEqual(CurriculoRepositoryPostgres(conn, 1).obter_base().titulo, "Base")
        self.assertEqual([c.titulo for c in CurriculoRepositoryPostgres(conn, 1).listar_adaptados()], ["Adaptado"])
        vagas = VagaEncontradaRepositoryPostgres(conn, 1)
        self.assertEqual(vagas.contar(False), 2)
        self.assertEqual(vagas.obter("https://exemplo.com/v1").titulo, "Dev")
        self.assertEqual(CandidaturaRepositoryPostgres(conn, 1).contar(), 1)
        pedidos = PedidoIARepositoryPostgres(conn, 1)
        self.assertEqual(pedidos.urls_ativas("compatibilidade"), {"https://exemplo.com/v1"})
        self.assertEqual(pedidos.listar_rotulos(), {"https://exemplo.com/v1": "alta"})
        self.assertTrue(pedidos.lote_pausado())
        # E a pessoa 2 não herda nada.
        self.admin.execute("INSERT INTO pessoa (id, nome_exibicao) VALUES (2, 'Pessoa 2')")
        self.assertIsNone(PerfilBuscaRepositoryPostgres(conn, 2).obter())
        self.assertEqual(VagaEncontradaRepositoryPostgres(conn, 2).contar(False), 0)
        self.assertFalse(PedidoIARepositoryPostgres(conn, 2).lote_pausado())

    def test_013_roda_duas_vezes_sem_erro_e_sem_mexer_nos_dados(self):
        self.aplicar_ate_012()
        self.popular_como_antes_da_013()
        self.aplicar_013()
        texto = (PASTA_MIGRATIONS / "013_pessoa.sql").read_text(encoding="utf-8")

        def retrato():
            return (
                {t: self.admin.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in self.TABELAS_DA_PESSOA},
                self.admin.execute(
                    "SELECT conname, contype FROM pg_constraint WHERE connamespace = current_schema()::regnamespace "
                    "ORDER BY conname"
                ).fetchall(),
                self.admin.execute(
                    "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = current_schema() ORDER BY indexname"
                ).fetchall(),
            )

        depois_da_primeira = retrato()
        self.admin.execute(texto)
        self.admin.execute(texto)
        self.assertEqual(retrato(), depois_da_primeira)
        # E uma 013 aplicada a mão por cima do runner também é aceita pelo runner (já consta).
        self.assertEqual(self.aplicar(), [])

    def test_013_estado_final_do_schema(self):
        self.aplicar_ate_012()
        self.aplicar_013()
        constraints = dict(
            self.admin.execute(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE connamespace = current_schema()::regnamespace"
            ).fetchall()
        )
        self.assertEqual(constraints["vaga_encontrada_pkey"], "PRIMARY KEY (pessoa_id, url)")
        self.assertEqual(constraints["rotulo_ia_pkey"], "PRIMARY KEY (pessoa_id, vaga_url)")
        self.assertEqual(constraints["perfil_busca_pkey"], "PRIMARY KEY (pessoa_id)")
        self.assertEqual(constraints["ia_lote_pausa_pkey"], "PRIMARY KEY (pessoa_id)")
        self.assertEqual(constraints["candidatura_pessoa_vaga_key"], "UNIQUE (pessoa_id, vaga_url)")
        for nome in ("candidatura_vaga_fkey", "pedido_ia_vaga_fkey", "rotulo_ia_vaga_fkey"):
            self.assertEqual(
                constraints[nome],
                "FOREIGN KEY (pessoa_id, vaga_url) REFERENCES vaga_encontrada(pessoa_id, url)",
            )
        # As antigas saíram, com o CHECK id = 1.
        for velha in (
            "candidatura_vaga_url_fkey", "pedido_ia_vaga_url_fkey", "rotulo_ia_vaga_url_fkey",
            "candidatura_vaga_url_key", "perfil_busca_id_check", "ia_lote_pausa_id_check",
        ):
            self.assertNotIn(velha, constraints)
        indices = dict(
            self.admin.execute(
                "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = current_schema()"
            ).fetchall()
        )
        for nome in (
            "vaga_encontrada_novas_idx", "vaga_encontrada_primeira_vez_idx", "vaga_encontrada_salva_em_idx",
            "candidatura_aplicada_idx", "pedido_ia_ativo_uq", "pedido_ia_vaga_idx", "pedido_ia_lote_idx",
            "curriculo_pessoa_tipo_idx",
        ):
            self.assertIn("(pessoa_id, ", indices[nome], nome)
        self.assertIn("(pessoa_id, vaga_url, tipo) WHERE", indices["pedido_ia_ativo_uq"])
        self.assertIn("UNIQUE", indices["pedido_ia_ativo_uq"])
        # A fila do worker segue global.
        self.assertNotIn("pessoa_id", indices["pedido_ia_fila_prioridade_idx"])
        # Sem DEFAULT: quem esquecer a pessoa falha alto.
        com_default = self.admin.execute(
            "SELECT table_name FROM information_schema.columns WHERE table_schema = current_schema() "
            "AND column_name = 'pessoa_id' AND column_default IS NOT NULL"
        ).fetchall()
        self.assertEqual(com_default, [])
        obrigatorias = self.admin.execute(
            "SELECT table_name FROM information_schema.columns WHERE table_schema = current_schema() "
            "AND column_name = 'pessoa_id' AND is_nullable = 'NO'"
        ).fetchall()
        self.assertEqual(sorted(c[0] for c in obrigatorias), sorted(self.TABELAS_DA_PESSOA))
        self.assertEqual(
            self.admin.execute(
                "SELECT count(*) FROM information_schema.columns WHERE table_schema = current_schema() "
                "AND column_name = 'id' AND table_name IN ('perfil_busca', 'ia_lote_pausa')"
            ).fetchone()[0],
            0,
        )

    def test_013_sem_pessoa_o_insert_falha_e_pessoa_inexistente_tambem(self):
        self.aplicar_ate_012()
        self.aplicar_013()
        with self.assertRaises(psycopg.errors.NotNullViolation):
            self.admin.execute("INSERT INTO vaga_encontrada (url, titulo, empresa, plataforma) VALUES ('u', 't', 'e', 'p')")
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            self.admin.execute(
                "INSERT INTO vaga_encontrada (pessoa_id, url, titulo, empresa, plataforma) VALUES (99, 'u', 't', 'e', 'p')"
            )

    def test_013_fk_composta_barra_pessoa_2_em_vaga_que_so_existe_na_pessoa_1(self):
        self.aplicar_ate_012()
        self.popular_como_antes_da_013()
        self.aplicar_013()
        self.admin.execute("INSERT INTO pessoa (id, nome_exibicao) VALUES (2, 'Pessoa 2')")
        url = "https://exemplo.com/v1"  # só existe para a pessoa 1
        for tabela, colunas, valores in (
            ("candidatura", "pessoa_id, vaga_url", "2, %s"),
            ("pedido_ia", "pessoa_id, vaga_url, tipo", "2, %s, 'curriculo'"),
            ("rotulo_ia", "pessoa_id, vaga_url, faixa", "2, %s, 'alta'"),
        ):
            with self.subTest(tabela=tabela), self.assertRaises(psycopg.errors.ForeignKeyViolation):
                self.admin.execute(f"INSERT INTO {tabela} ({colunas}) VALUES ({valores})", (url,))

        # Controle: com a vaga na pessoa 2, os mesmos INSERTs entram (a falha acima era a FK, e só ela).
        self.admin.execute(
            "INSERT INTO vaga_encontrada (pessoa_id, url, titulo, empresa, plataforma) VALUES (2, %s, 'Dev', 'Acme', 'Teste')",
            (url,),
        )
        self.admin.execute("INSERT INTO candidatura (pessoa_id, vaga_url) VALUES (2, %s)", (url,))
        self.admin.execute("INSERT INTO pedido_ia (pessoa_id, vaga_url, tipo) VALUES (2, %s, 'curriculo')", (url,))
        self.admin.execute("INSERT INTO rotulo_ia (pessoa_id, vaga_url, faixa) VALUES (2, %s, 'baixa')", (url,))

    def test_013_mesma_url_em_duas_pessoas_mas_nao_duas_vezes_na_mesma(self):
        self.aplicar_ate_012()
        self.popular_como_antes_da_013()
        self.aplicar_013()
        self.admin.execute("INSERT INTO pessoa (id, nome_exibicao) VALUES (2, 'Pessoa 2')")
        url = "https://exemplo.com/v1"
        self.admin.execute(
            "INSERT INTO vaga_encontrada (pessoa_id, url, titulo, empresa, plataforma) VALUES (2, %s, 'Dev', 'Acme', 'Teste')",
            (url,),
        )
        self.assertEqual(
            self.admin.execute("SELECT pessoa_id FROM vaga_encontrada WHERE url = %s ORDER BY 1", (url,)).fetchall(),
            [(1,), (2,)],
        )
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.admin.execute(
                "INSERT INTO vaga_encontrada (pessoa_id, url, titulo, empresa, plataforma) VALUES (2, %s, 'x', 'x', 'x')",
                (url,),
            )
        # Uma candidatura e um pedido ativo por vaga de cada pessoa, não por URL no banco todo.
        self.admin.execute("INSERT INTO candidatura (pessoa_id, vaga_url) VALUES (2, %s)", (url,))
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.admin.execute("INSERT INTO candidatura (pessoa_id, vaga_url) VALUES (2, %s)", (url,))
        # A pessoa 1 já tinha o pedido ativo de compatibilidade nesta URL: a 2 pede o dela sem conflito.
        self.admin.execute("INSERT INTO pedido_ia (pessoa_id, vaga_url, tipo) VALUES (2, %s, 'compatibilidade')", (url,))
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.admin.execute(
                "INSERT INTO pedido_ia (pessoa_id, vaga_url, tipo) VALUES (2, %s, 'compatibilidade')", (url,)
            )
        self.admin.execute("INSERT INTO rotulo_ia (pessoa_id, vaga_url, faixa) VALUES (2, %s, 'baixa')", (url,))
        # Perfil e pausa: uma linha por pessoa.
        self.admin.execute("INSERT INTO perfil_busca (pessoa_id, termos) VALUES (2, ARRAY['go'])")
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.admin.execute("INSERT INTO perfil_busca (pessoa_id) VALUES (2)")
        self.admin.execute("INSERT INTO ia_lote_pausa (pessoa_id, pausado) VALUES (2, false)")
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.admin.execute("INSERT INTO ia_lote_pausa (pessoa_id, pausado) VALUES (2, true)")


class RetryTest(unittest.TestCase):
    """Sem Postgres: `conectar` falso e relógio/sleep injetados (nada espera de verdade)."""

    def setUp(self):
        self.pasta = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.agora = 0.0
        self.pausas = []

    def dormir(self, s):
        self.pausas.append(s)
        self.agora += s

    def relogio(self):
        return self.agora

    def test_tenta_de_novo_ate_o_banco_voltar(self):
        chamadas = []

        def aplicar(pasta, conectar_):
            if len(chamadas) < 2:
                chamadas.append(1)
                raise psycopg.OperationalError("connection refused")
            return ["001_a.sql"]

        from unittest import mock

        with mock.patch("src.repository.migracoes.aplicar_pendentes", aplicar), self.assertLogs(
            "src.repository.migracoes", level="WARNING"
        ):
            r = aplicar_com_retry(self.pasta, dormir=self.dormir, agora=self.relogio)
        self.assertEqual(r, ["001_a.sql"])
        self.assertEqual(self.pausas, [1.0, 2.0])  # backoff dobrando

    def test_estoura_o_teto_e_levanta_sem_esperar_de_verdade(self):
        from unittest import mock

        def aplicar(pasta, conectar_):
            raise psycopg.OperationalError("connection refused")

        with mock.patch("src.repository.migracoes.aplicar_pendentes", aplicar), self.assertLogs(
            "src.repository.migracoes", level="WARNING"
        ):
            with self.assertRaises(MigracaoFalhou) as ctx:
                aplicar_com_retry(self.pasta, teto_s=60, dormir=self.dormir, agora=self.relogio)
        self.assertIn("inacessível", str(ctx.exception))
        self.assertAlmostEqual(sum(self.pausas), 60.0)
        self.assertLessEqual(max(self.pausas), 8.0)

    def test_erro_de_migration_nao_tem_retry(self):
        from unittest import mock

        chamadas = []

        def aplicar(pasta, conectar_):
            chamadas.append(1)
            raise MigracaoFalhou("001_x.sql falhou")

        with mock.patch("src.repository.migracoes.aplicar_pendentes", aplicar):
            with self.assertRaises(MigracaoFalhou):
                aplicar_com_retry(self.pasta, dormir=self.dormir, agora=self.relogio)
        self.assertEqual((len(chamadas), self.pausas), (1, []))

    def test_conectar_real_sem_banco_vira_operational_error(self):
        # Garante que o erro de conexão do psycopg é mesmo o que o retry captura.
        def conectar():
            return psycopg.connect(host="127.0.0.1", port=1, connect_timeout=1)

        with self.assertRaises(psycopg.OperationalError):
            aplicar_pendentes(self.pasta, conectar)


class LifespanTest(unittest.TestCase):
    """Sem Postgres: o startup da app de produção chama as migrations (fake no lugar do banco)."""

    def criar(self, ao_subir):
        from src.controller.api import criar_app
        from src.repository.base import CurriculoRepositoryMemoria, PerfilBuscaRepositoryMemoria
        from src.service.busca_service import BuscaService

        return criar_app(PerfilBuscaRepositoryMemoria(1), CurriculoRepositoryMemoria(1), BuscaService([]), ao_subir=ao_subir)

    def test_startup_chama_ao_subir_uma_vez(self):
        from fastapi.testclient import TestClient

        chamadas = []
        with TestClient(self.criar(lambda: chamadas.append(1))) as c:
            self.assertEqual(c.get("/saude").status_code, 200)
        self.assertEqual(chamadas, [1])

    def test_ao_subir_que_falha_impede_a_app_de_subir(self):
        from fastapi.testclient import TestClient

        def falha():
            raise MigracaoFalhou("002_x.sql falhou")

        with self.assertRaises(MigracaoFalhou), TestClient(self.criar(falha)):
            pass

    def test_app_de_producao_liga_o_startup_em_aplicar_com_retry(self):
        from unittest import mock

        from fastapi.testclient import TestClient

        from src.controller.api import criar_app_producao

        with mock.patch("src.repository.migracoes.aplicar_com_retry", return_value=[]) as m:
            with TestClient(criar_app_producao()):
                pass
        m.assert_called_once_with()
