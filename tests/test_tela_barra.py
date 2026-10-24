"""Barra da lista: hora da última coleta ao lado de "Buscar vagas novas".

Repositório na memória e no Postgres de verdade (PGHOST), com o mesmo contrato; a tela na memória.
As vagas daqui são escritas à mão (não são respostas gravadas de provider).
"""

import os
import time
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from src.controller.tela import quando
from src.model.vaga_externa import VagaExterna
from src.repository.base import VagaEncontradaRepositoryMemoria
from tests.test_coleta import _schema_descartavel
from tests.test_tela_lista import HX, Pagina, criar

SAO_PAULO = ZoneInfo("America/Sao_Paulo")


def vaga(i: int) -> VagaExterna:
    return VagaExterna(f"Analista de Dados {i}", "Empresa Teste", "Remotar", f"https://exemplo.test/vaga/{i}")


class ContratoUltimaColeta:
    repo = None

    def passar_tempo(self) -> None:
        raise NotImplementedError

    def test_sem_historico_e_none_e_cada_coleta_empurra_para_frente(self):
        self.assertIsNone(self.repo.ultima_coleta())

        self.repo.registrar([vaga(1)], "dados")
        primeira = self.repo.ultima_coleta()
        self.assertIsNotNone(primeira)

        self.passar_tempo()
        # Vaga que já existia também conta: a coleta a achou de novo.
        self.repo.registrar([vaga(1)], "dados")
        self.assertGreater(self.repo.ultima_coleta(), primeira)


class UltimaColetaMemoriaTest(ContratoUltimaColeta, unittest.TestCase):
    def setUp(self):
        self.agora = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
        self.repo = VagaEncontradaRepositoryMemoria(1, relogio=lambda: self.agora)

    def passar_tempo(self) -> None:
        self.agora += timedelta(seconds=1)


@unittest.skipUnless(os.environ.get("PGHOST"), "PGHOST não definido: integração com Postgres pulada")
class UltimaColetaPostgresTest(ContratoUltimaColeta, unittest.TestCase):
    def setUp(self):
        from src.repository.postgres import VagaEncontradaRepositoryPostgres

        self.conn, _ = _schema_descartavel(self, "teste_ultima_coleta")
        self.repo = VagaEncontradaRepositoryPostgres(self.conn, 1)

    def passar_tempo(self) -> None:
        time.sleep(0.01)  # o now() de cada transação avança


class QuandoTest(unittest.TestCase):
    AGORA = datetime(2026, 10, 14, 2, 0, tzinfo=timezone.utc)  # 13/10 23:00 em São Paulo

    def test_hoje_ontem_e_data_no_fuso_de_sao_paulo(self):
        # 01:30Z do dia 14 ainda é 13/10 em São Paulo: "hoje", não "amanhã" nem 14/10.
        self.assertEqual(quando(datetime(2026, 10, 14, 1, 30, tzinfo=timezone.utc), self.AGORA), "hoje 22:30")
        self.assertEqual(quando(datetime(2026, 10, 12, 21, 0, tzinfo=timezone.utc), self.AGORA), "ontem 18:00")
        self.assertEqual(quando(datetime(2026, 10, 10, 11, 1, tzinfo=timezone.utc), self.AGORA), "10/10 08:01")

    def test_sem_fuso_e_utc_e_sem_data_e_none(self):
        self.assertEqual(quando(datetime(2026, 10, 13, 21, 0), self.AGORA), "hoje 18:00")
        self.assertIsNone(quando(None, self.AGORA))


class TelaTest(unittest.TestCase):
    def test_sem_historico_nao_mostra_a_hora(self):
        cliente, _ = criar([])
        self.assertNotIn("última:", Pagina(cliente.get("/tela").text).por_id("vagas").texto)

    def test_mostra_a_hora_da_ultima_coleta_so_na_barra(self):
        # Três dias atrás: nem "hoje" nem "ontem", então a data sai inteira.
        em = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=3)
        cliente, repo = criar([])
        repo._agora = lambda: em
        repo.registrar([vaga(i) for i in range(60)], "dados")

        texto = Pagina(cliente.get("/tela").text).por_id("vagas").texto

        self.assertIn(f"última: {em.astimezone(SAO_PAULO):%d/%m %H:%M}", texto)
        # O "carregar mais" não repete a barra.
        self.assertNotIn("última:", cliente.get("/tela/vagas", params={"deslocamento": "50"}, headers=HX).text)


if __name__ == "__main__":
    unittest.main()
