import json
import unittest
import unittest.mock
from datetime import date
from pathlib import Path

from src.client.apify_linkedin import ApifyLinkedInClient
from src.client.ashby import AshbyClient
from src.client.greenhouse import GreenhouseClient
from src.client.lever import LeverClient
from src.client.remotar import RemotarClient
from src.client.trampos import TramposClient
from src.service.busca_service import BuscaService

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



def fake_post(arquivo: str, chamadas: list[tuple] | None = None):
    dados = json.loads((FIXTURES / arquivo).read_text(encoding="utf-8"))

    def http_post(url: str, corpo: dict, token: str):
        if chamadas is not None:
            chamadas.append((url, corpo, token))
        return dados

    return http_post


class ApifyLinkedInClientTest(unittest.TestCase):
    """A fixture segue os campos documentados no README do actor; não é uma resposta gravada."""

    def test_envia_busca_ao_actor_com_token(self):
        chamadas: list[tuple] = []
        ApifyLinkedInClient(local="Rio de Janeiro", limite=20, token="tk", http_post=fake_post("apify_linkedin.json", chamadas)).buscar("power bi")

        url, corpo, token = chamadas[0]
        self.assertEqual(url, "https://api.apify.com/v2/acts/curious_coder~linkedin-jobs-scraper/run-sync-get-dataset-items?maxItems=20")
        self.assertEqual(corpo["keywords"], "power bi")
        self.assertEqual(corpo["location"], "Rio de Janeiro")
        self.assertEqual(corpo["limitPerSource"], 20)
        self.assertFalse(corpo["scrapeCompany"])
        self.assertEqual(token, "tk")

    def test_converte_vaga_com_url_estavel(self):
        vagas = ApifyLinkedInClient(token="tk", http_post=fake_post("apify_linkedin.json")).buscar("dados")

        vaga = vagas[0]
        self.assertEqual(vaga.titulo, "Analista de Dados Pleno - Power BI")
        self.assertEqual(vaga.empresa, "Empresa Exemplo")
        self.assertEqual(vaga.plataforma, "LinkedIn (Apify)")
        self.assertEqual(vaga.url, "https://www.linkedin.com/jobs/view/4310298811")
        self.assertEqual(vaga.local, "Rio de Janeiro, RJ")
        self.assertFalse(vaga.remoto)
        self.assertEqual(vaga.publicada_em, date(2026, 10, 8))

    def test_remoto_pelo_tipo_de_local_de_trabalho(self):
        vagas = ApifyLinkedInClient(token="tk", http_post=fake_post("apify_linkedin.json")).buscar("fiscal")

        self.assertTrue(vagas[1].remoto)

    def test_pula_vaga_sem_titulo_e_ignora_data_que_nao_e_iso(self):
        vagas = ApifyLinkedInClient(token="tk", http_post=fake_post("apify_linkedin.json")).buscar("dados")

        self.assertEqual([v.titulo for v in vagas], ["Analista de Dados Pleno - Power BI", "Analista Fiscal com SQL", "Assistente de Indicadores"])
        self.assertIsNone(vagas[2].publicada_em)
        self.assertIsNone(vagas[2].remoto)

    def test_remoto_com_rotulo_em_portugues(self):
        itens = [{"id": "1", "link": "https://br.linkedin.com/jobs/view/1", "title": "Analista", "workplaceTypes": ["Remoto"]}]
        vagas = ApifyLinkedInClient(token="tk", http_post=lambda url, corpo, token: itens).buscar("analista")

        self.assertTrue(vagas[0].remoto)

    def test_epoch_fora_do_intervalo_nao_derruba_a_busca(self):
        itens = [
            {"id": "1", "link": "https://br.linkedin.com/jobs/view/1", "title": "Analista", "postedAt": 10**20},
            {"id": "2", "link": "https://br.linkedin.com/jobs/view/2", "title": "Analista II", "postedAt": "2026-10-09"},
        ]
        vagas = ApifyLinkedInClient(token="tk", http_post=lambda url, corpo, token: itens).buscar("analista")

        self.assertEqual(len(vagas), 2)
        self.assertIsNone(vagas[0].publicada_em)
        self.assertEqual(vagas[1].publicada_em, date(2026, 10, 9))

    def test_epoch_em_segundos_e_em_milissegundos(self):
        itens = [
            {"id": "1", "link": "https://br.linkedin.com/jobs/view/1", "title": "A", "postedAt": 1791590400},
            {"id": "2", "link": "https://br.linkedin.com/jobs/view/2", "title": "B", "postedAt": 1791590400000},
        ]
        vagas = ApifyLinkedInClient(token="tk", http_post=lambda url, corpo, token: itens).buscar("a")

        self.assertEqual(vagas[0].publicada_em, vagas[1].publicada_em)
        self.assertEqual(vagas[0].publicada_em.year, 2026)

    def test_item_que_nao_e_objeto_nao_derruba_a_busca(self):
        itens = [None, "erro do actor", {"id": "1", "link": "https://br.linkedin.com/jobs/view/1", "title": "Analista"}]
        vagas = ApifyLinkedInClient(token="tk", http_post=lambda url, corpo, token: itens).buscar("analista")

        self.assertEqual([v.titulo for v in vagas], ["Analista"])

    def test_resposta_que_nao_e_lista_vira_erro(self):
        client = ApifyLinkedInClient(token="tk", http_post=lambda url, corpo, token: {"error": {"type": "run-failed"}})

        with self.assertRaises(ValueError):
            client.buscar("dados")

    def test_sem_token_o_busca_service_registra_erro_e_segue(self):
        sem_token = ApifyLinkedInClient(http_post=fake_post("apify_linkedin.json"))
        remotar = RemotarClient(http=fake_http("remotar.json"))

        with unittest.mock.patch.dict("os.environ", {}, clear=True):
            resultado = BuscaService([sem_token, remotar]).buscar("dados python")

        self.assertIn("LinkedIn (Apify)", resultado.erros)
        self.assertIn("APIFY_TOKEN", resultado.erros["LinkedIn (Apify)"])
        self.assertTrue(resultado.vagas)


if __name__ == "__main__":
    unittest.main()
