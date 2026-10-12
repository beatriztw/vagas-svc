"""Os mesmos contratos de tests/test_historico_b2.py contra um Postgres de verdade (só com PGHOST)."""

import os
import unittest

from tests.test_coleta import _schema_descartavel
from tests.test_historico_b2 import ContratoAgrupamento


@unittest.skipUnless(os.environ.get("PGHOST"), "PGHOST não definido: integração com Postgres pulada")
class AgrupamentoPostgresTest(ContratoAgrupamento, unittest.TestCase):
    def criar(self):
        from src.repository.postgres import PerfilBuscaRepositoryPostgres, VagaEncontradaRepositoryPostgres

        self.conn, _ = _schema_descartavel(self, "teste_b2")
        self.repo = VagaEncontradaRepositoryPostgres(self.conn)
        self.perfis = PerfilBuscaRepositoryPostgres(self.conn)


if __name__ == "__main__":
    unittest.main()
