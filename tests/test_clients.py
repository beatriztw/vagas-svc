import json
import unittest
from datetime import date
from pathlib import Path

from src.client.ashby import AshbyClient
from src.client.greenhouse import GreenhouseClient
from src.client.lever import LeverClient
from src.client.remotar import RemotarClient
from src.client.trampos import TramposClient

FIXTURES = Path(__file__).parent / "fixtures"


def fake_http(arquivo: str, chamadas: list[str] | None = None):
    dados = json.loads((FIXTURES / arquivo).read_text(encoding="utf-8"))

    def http(url: str):
        if chamadas is not None:
            chamadas.append(url)
        return dados

    return http


class GreenhouseClientTest(unittest.TestCase):
    def test_filtra_por_termo_ignorando_acento(self):
        vagas = GreenhouseClient(["stone"], http=fake_http("greenhouse.json")).buscar("junior")

        self.assertEqual(len(vagas), 1)
        vaga = vagas[0]
        self.assertTrue(vaga.titulo.startswith("Agente Fit"))
        self.assertEqual(vaga.empresa, "stone")
        self.assertEqual(vaga.plataforma, "Greenhouse")
        self.assertEqual(vaga.url, "https://job-boards.greenhouse.io/stone/jobs/7778799003")
        self.assertEqual(vaga.local, "Pocos De Caldas, Minas Gerais, Brasil")
        self.assertEqual(vaga.publicada_em, date(2026, 6, 19))

    def test_remove_espacos_do_titulo(self):
        vagas = GreenhouseClient(["stone"], http=fake_http("greenhouse.json")).buscar("account")

        self.assertEqual(vagas[0].titulo, "Account Executive - Hunter (Hibrido/SP)")


class LeverClientTest(unittest.TestCase):
    def test_converte_vaga(self):
        vagas = LeverClient(["leverdemo"], http=fake_http("lever.json")).buscar("customer")

        self.assertEqual(len(vagas), 1)
        vaga = vagas[0]
        self.assertEqual(vaga.titulo, "Customer Success Manager")
        self.assertEqual(vaga.local, "Atlanta, Georgia")
        self.assertFalse(vaga.remoto)
        self.assertIsNotNone(vaga.publicada_em)


class AshbyClientTest(unittest.TestCase):
    def test_converte_vaga(self):
        vagas = AshbyClient(["ashby"], http=fake_http("ashby.json")).buscar("engineering")

        self.assertEqual(len(vagas), 1)
        self.assertEqual(vagas[0].titulo, "Engineering Manager - EU")
        self.assertTrue(vagas[0].remoto)
        self.assertEqual(vagas[0].publicada_em, date(2024, 3, 4))


class RemotarClientTest(unittest.TestCase):
    def test_envia_termo_para_api_e_converte(self):
        chamadas: list[str] = []
        vagas = RemotarClient(http=fake_http("remotar.json", chamadas)).buscar("dados python")

        self.assertEqual(chamadas, ["https://api.remotar.com.br/jobs?search=dados+python&page=1"])
        self.assertEqual(len(vagas), 2)
        self.assertEqual(vagas[0].empresa, "Compass.UOL")
        self.assertEqual(vagas[0].url, "https://remotar.com.br/job/166022")
        self.assertTrue(vagas[0].remoto)
        self.assertIsNone(vagas[0].local)


class TramposClientTest(unittest.TestCase):
    def test_filtra_localmente_por_titulo_ou_categoria(self):
        chamadas: list[str] = []
        vagas = TramposClient(http=fake_http("trampos.json", chamadas)).buscar("comercial")

        self.assertEqual(chamadas, ["https://trampos.co/api/v2/opportunities?page=1"])
        self.assertEqual({v.empresa for v in vagas}, {"Pestshop", "YAAYOO"})
        self.assertEqual(vagas[0].url, "https://trampos.co/oportunidades/774721")
        self.assertEqual(vagas[0].local, "São Paulo, SP")
        self.assertEqual(vagas[0].publicada_em, date(2026, 10, 10))

    def test_respeita_limite_de_paginas(self):
        chamadas: list[str] = []

        def http(url):
            chamadas.append(url)
            return {"opportunities": [], "pagination": {"total_pages": 50}}

        TramposClient(max_paginas=3, http=http).buscar("python")

        self.assertEqual(len(chamadas), 3)


if __name__ == "__main__":
    unittest.main()
