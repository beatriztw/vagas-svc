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

from src.repository.migracoes import MigracaoFalhou, PASTA_MIGRATIONS, aplicar_pendentes


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
