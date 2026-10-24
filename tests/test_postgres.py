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

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


def aplicar_todas_duas_vezes(conn) -> None:
    """Todas as migrations em ordem, duas vezes (idempotência). A 013 (pessoa_id) depende das anteriores
    e os repositórios gravam e leem por `pessoa_id`, então não dá para aplicar só a 001."""
    for _ in range(2):
        for arquivo in sorted(MIGRATIONS.glob("*.sql")):
            conn.execute(arquivo.read_text(encoding="utf-8"))


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
        aplicar_todas_duas_vezes(self.conn)
        self.perfis = PerfilBuscaRepositoryPostgres(self.conn, 1)
        self.curriculos = CurriculoRepositoryPostgres(self.conn, 1)

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
            self.conn.execute("INSERT INTO perfil_busca (pessoa_id, modalidades) VALUES (1, ARRAY['Presencial'])")
        with self.assertRaises(self.errors.CheckViolation):
            self.conn.execute("INSERT INTO perfil_busca (pessoa_id, niveis) VALUES (1, ARRAY['Estagiário'])")
        with self.assertRaises(self.errors.CheckViolation):
            self.conn.execute("INSERT INTO perfil_busca (pessoa_id, publicada_desde_dias) VALUES (1, 0)")
        with self.assertRaises(self.errors.CheckViolation):
            self.conn.execute("INSERT INTO perfil_busca (pessoa_id, publicada_desde_dias) VALUES (1, 3651)")
        self.assertEqual(self.contar("perfil_busca"), 0)

    def test_repository_recusa_perfil_invalido_com_value_error(self):
        # Mesmo contrato da memória e do SQLite: a API transforma ValueError em 422.
        for perfil in (Perfil(publicada_desde_dias=0), Perfil(publicada_desde_dias=10**6), Perfil(modalidades=["Presencial"])):
            with self.assertRaises(ValueError):
                self.perfis.salvar(perfil)
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
                "INSERT INTO curriculo (pessoa_id, tipo, titulo, conteudo_md) VALUES (1, 'adaptado', 'A', '# a')"
            )
        with self.assertRaises(ValueError):
            self.curriculos.salvar(Curriculo("adaptado", "A", "# a"))
        with self.assertRaises(self.errors.ForeignKeyViolation):
            self.curriculos.salvar(Curriculo("adaptado", "A", "# a", base_id=999999))
        base = self.curriculos.salvar(Curriculo("base", "Base", "# b"))
        with self.assertRaises(self.errors.CheckViolation):
            self.conn.execute(
                "INSERT INTO curriculo (pessoa_id, tipo, titulo, conteudo_md, base_id) VALUES (1, 'base', 'B', '# b', %s)",
                (base.id,),
            )
        adaptado = self.curriculos.salvar(
            Curriculo("adaptado", "Vaga X", "# x", base_id=base.id, vaga_url="https://exemplo.com/vaga/1")
        )
        self.assertEqual(self.curriculos.listar_adaptados(), [adaptado])
        self.assertEqual(self.curriculos.obter_base().id, base.id)

    def test_adaptado_nao_pode_derivar_de_outro_adaptado(self):
        base = self.curriculos.salvar(Curriculo("base", "Base", "# b"))
        adaptado = self.curriculos.salvar(Curriculo("adaptado", "Vaga X", "# x", base_id=base.id))

        with self.assertRaisesRegex(ValueError, "adaptado"):
            self.curriculos.salvar(Curriculo("adaptado", "Vaga Y", "# y", base_id=adaptado.id))
        self.assertEqual(self.curriculos.listar_adaptados(), [adaptado])


if __name__ == "__main__":
    unittest.main()
