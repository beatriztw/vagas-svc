"""Integração contra um Postgres de verdade. Só roda com PGHOST definido (o CI sobe um postgres:16-alpine).

Cada classe cria um schema descartável, aplica a migration nele e o apaga no fim:
nada é escrito nas tabelas do schema padrão do banco apontado por PG*.
"""

import os
import unittest
import uuid
from pathlib import Path

from src.model.curriculo import Curriculo
from src.model.perfil import Perfil
from src.model.vaga import Modalidade, Nivel

MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "001_curriculo_perfil.sql"


@unittest.skipUnless(os.environ.get("PGHOST"), "PGHOST não definido: integração com Postgres pulada")
class PostgresTest(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql

        from src.repository.conexao import conectar
        from src.repository.postgres import CurriculoRepositoryPostgres, PerfilBuscaRepositoryPostgres

        self.errors = psycopg.errors
        self.conn = conectar()
        self.addCleanup(self.conn.close)
        schema = f"teste_{uuid.uuid4().hex[:12]}"
        self.conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        self.addCleanup(
            self.conn.execute, sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
        )
        self.conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        migration = MIGRATION.read_text(encoding="utf-8")
        self.conn.execute(migration)
        self.conn.execute(migration)  # idempotente: segunda aplicação não falha
        self.perfis = PerfilBuscaRepositoryPostgres(self.conn)
        self.curriculos = CurriculoRepositoryPostgres(self.conn)

    def contar(self, tabela: str) -> int:
        return self.conn.execute(f"SELECT count(*) FROM {tabela}").fetchone()[0]

    def test_upsert_do_perfil_duas_gravacoes_uma_linha(self):
        self.assertIsNone(self.perfis.obter())
        self.perfis.salvar(Perfil(termos=["python"], modalidades=[Modalidade.REMOTO], niveis=[Nivel.PLENO]))
        segundo = Perfil(
            termos=["dados"],
            excluir=["estágio"],
            modalidades=[Modalidade.HIBRIDO],
            niveis=[Nivel.JUNIOR, Nivel.SENIOR],
            publicada_desde_dias=14,
            ocultar_candidatadas=False,
        )
        self.perfis.salvar(segundo)
        self.assertEqual(self.contar("perfil_busca"), 1)
        lido = self.perfis.obter()
        self.assertEqual(lido, segundo)
        self.assertIsInstance(lido.modalidades[0], Modalidade)
        self.assertIsInstance(lido.niveis[0], Nivel)

    def test_enum_invalido_recusado_pelo_check(self):
        with self.assertRaises(self.errors.CheckViolation):
            self.conn.execute("INSERT INTO perfil_busca (modalidades) VALUES (ARRAY['Presencial'])")
        with self.assertRaises(self.errors.CheckViolation):
            self.conn.execute("INSERT INTO perfil_busca (niveis) VALUES (ARRAY['Estagiário'])")
        with self.assertRaises(self.errors.CheckViolation):
            self.conn.execute("INSERT INTO perfil_busca (id) VALUES (2)")
        with self.assertRaises(self.errors.CheckViolation):
            self.perfis.salvar(Perfil(publicada_desde_dias=0))
        self.assertEqual(self.contar("perfil_busca"), 0)

    def test_base_vigente_e_a_mais_recente(self):
        self.assertIsNone(self.curriculos.obter_base())
        primeira = self.curriculos.salvar(Curriculo("base", "v1", "# v1", arquivo_s3="originais/v1.pdf"))
        self.assertIsNotNone(primeira.id)
        self.assertIsNotNone(primeira.criado_em)
        segunda = self.curriculos.salvar(Curriculo("base", "v2", "# v2"))
        self.assertEqual(self.curriculos.obter_base().id, segunda.id)
        self.assertEqual(self.curriculos.obter(primeira.id), primeira)

    def test_adaptado_exige_base_id(self):
        with self.assertRaises(self.errors.CheckViolation):
            self.conn.execute(
                "INSERT INTO curriculo (tipo, titulo, conteudo_md) VALUES ('adaptado', 'A', '# a')"
            )
        with self.assertRaises(ValueError):
            self.curriculos.salvar(Curriculo("adaptado", "A", "# a"))
        with self.assertRaises(self.errors.ForeignKeyViolation):
            self.curriculos.salvar(Curriculo("adaptado", "A", "# a", base_id=999999))
        base = self.curriculos.salvar(Curriculo("base", "Base", "# b"))
        with self.assertRaises(self.errors.CheckViolation):
            self.conn.execute(
                "INSERT INTO curriculo (tipo, titulo, conteudo_md, base_id) VALUES ('base', 'B', '# b', %s)",
                (base.id,),
            )
        adaptado = self.curriculos.salvar(
            Curriculo("adaptado", "Vaga X", "# x", base_id=base.id, vaga_url="https://exemplo.com/vaga/1")
        )
        self.assertEqual(self.curriculos.listar_adaptados(), [adaptado])
        self.assertEqual(self.curriculos.obter_base().id, base.id)


if __name__ == "__main__":
    unittest.main()
