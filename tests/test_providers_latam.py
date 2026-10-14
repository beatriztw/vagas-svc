"""Himalayas e Remotive. `himalayas_bookkeeper_br.json` e `remotive.json` são respostas REAIS gravadas em
12/10/2026 (busca "bookkeeper", country=BR; listagem do Remotive), reduzidas: menos vagas e a descrição
cortada no primeiro bloco. Os itens tortos dos testes são escritos à mão, aqui no próprio teste."""

import io
import json
import os
import unittest
import urllib.error
from datetime import date
from pathlib import Path
from unittest import mock

from src.client.himalayas import HimalayasClient, aceita_brasil
from src.client.remotive import CACHE_SEGUNDOS, INTERVALO_MINIMO_SEGUNDOS, RemotiveClient, aceita_local
from src.client.sanitizar import MAX_CARACTERES_DESCRICAO
from src.service.busca_service import BuscaService

FIXTURES = Path(__file__).parent / "fixtures"


def _gravada(arquivo: str) -> dict:
    return json.loads((FIXTURES / arquivo).read_text(encoding="utf-8"))


def _http(dados, chamadas: list[str] | None = None):
    def http(url: str):
        if chamadas is not None:
            chamadas.append(url)
        return dados

    return http


def _http_erro(codigo: int, chamadas: list[str] | None = None):
    def http(url: str):
        if chamadas is not None:
            chamadas.append(url)
        raise urllib.error.HTTPError(url, codigo, "erro", {}, io.BytesIO())

    return http


class Relogio:
    def __init__(self):
        self.agora = 1000.0

    def __call__(self):
        return self.agora


# Item válido escrito à mão, para variar um campo por teste.
def _himalayas_item(**mudancas):
    item = {
        "title": "Bookkeeper",
        "companyName": "Empresa Exemplo",
        "guid": "https://himalayas.app/companies/exemplo/jobs/bookkeeper",
        "applicationLink": "https://himalayas.app/companies/exemplo/jobs/bookkeeper",
        "pubDate": 1790929856,
        "locationRestrictions": [],
        "description": "<p>Vaga inventada.</p>",
    }
    item.update(mudancas)
    return item


def _remotive_item(**mudancas):
    item = {
        "id": 1,
        "url": "https://remotive.com/remote-jobs/finance/bookkeeper-1",
        "title": "Bookkeeper",
        "company_name": "Empresa Exemplo",
        "publication_date": "2026-10-08T21:47:32",
        "candidate_required_location": "Worldwide",
        "description": "<p>Vaga inventada.</p>",
    }
    item.update(mudancas)
    return item


class HimalayasClientTest(unittest.TestCase):
    def test_envia_termo_e_pais_e_converte_a_resposta_gravada(self):
        chamadas = []
        vagas = HimalayasClient(http=_http(_gravada("himalayas_bookkeeper_br.json"), chamadas)).buscar("bookkeeper")

        self.assertEqual(len(chamadas), 1)
        self.assertIn("q=bookkeeper", chamadas[0])
        self.assertIn("country=BR", chamadas[0])
        self.assertIn("limit=20", chamadas[0])
        self.assertEqual([v.titulo for v in vagas], ["International Bookkeeping Associate", "Bookkeeper / CPA (US or AU Accounting)", "Bookkeeper"])
        vaga = vagas[0]
        self.assertEqual(vaga.empresa, "Solvency Now Bookkeeping")
        self.assertEqual(vaga.plataforma, "Himalayas")
        self.assertEqual(vaga.url, "https://himalayas.app/companies/solvency-now-bookkeeping/jobs/international-bookkeeping-associate")
        self.assertTrue(vaga.remoto)
        # pubDate 1790929856 (segundos) = 2026-10-02 UTC.
        self.assertEqual(vaga.publicada_em, date(2026, 10, 2))
        self.assertEqual(vaga.detalhe.descricao, "Overview")
        self.assertIn("WorkBetterNow", vagas[2].detalhe.descricao)
        self.assertNotIn("<", vagas[2].detalhe.descricao)

    def test_restricao_de_local_sem_o_brasil_sai(self):
        self.assertTrue(aceita_brasil([]))
        self.assertTrue(aceita_brasil(["Argentina", "Brazil", "Chile"]))
        self.assertTrue(aceita_brasil(["Brasil"]))
        self.assertFalse(aceita_brasil(["United States", "Canada"]))
        # Por palavra, não por pedaço: "Brazilian" não é "Brazil".
        self.assertFalse(aceita_brasil(["Brazilian Territory"]))
        vagas = HimalayasClient(
            http=_http({"jobs": [_himalayas_item(locationRestrictions=["United States"]), _himalayas_item(title="Ok")]})
        ).buscar("bookkeeper")
        self.assertEqual([v.titulo for v in vagas], ["Ok"])

    def test_item_torto_sai_sozinho(self):
        tortos = [
            "não é objeto",
            None,
            _himalayas_item(title=None),
            _himalayas_item(title="   "),
            _himalayas_item(guid=None, applicationLink=None),
            _himalayas_item(guid="javascript:alert(1)", applicationLink=None),
            _himalayas_item(pubDate="2026-10-02"),
            _himalayas_item(pubDate=10**15),
            _himalayas_item(pubDate=True),
        ]
        vagas = HimalayasClient(http=_http({"jobs": [*tortos, _himalayas_item(title="Válida")]})).buscar("x")
        self.assertEqual([v.titulo for v in vagas], ["Válida"])

    def test_sem_data_e_sem_descricao_a_vaga_fica(self):
        vagas = HimalayasClient(http=_http({"jobs": [_himalayas_item(pubDate=None, description=None)]})).buscar("x")
        self.assertEqual(len(vagas), 1)
        self.assertIsNone(vagas[0].publicada_em)
        self.assertIsNone(vagas[0].detalhe)

    def test_descricao_cortada_no_teto(self):
        vagas = HimalayasClient(http=_http({"jobs": [_himalayas_item(description="<p>" + "a" * 30_000 + "</p>")]})).buscar("x")
        self.assertEqual(len(vagas[0].detalhe.descricao), MAX_CARACTERES_DESCRICAO)

    def test_resposta_sem_lista_vira_erro_do_provider(self):
        for dados in ([], {"jobs": None}, "x"):
            with self.assertRaises(ValueError):
                HimalayasClient(http=_http(dados)).buscar("x")

    def test_erro_http_o_busca_service_registra_e_segue(self):
        resultado = BuscaService([HimalayasClient(http=_http_erro(503))]).buscar("bookkeeper")
        self.assertEqual(resultado.vagas, [])
        self.assertIn("HTTPError", resultado.erros["Himalayas"])


class RemotiveLocalTest(unittest.TestCase):
    def test_aceita_quem_esta_no_brasil_ou_na_america_latina(self):
        for local in (
            "Worldwide",
            "Anywhere",
            "LATAM",
            "Latin America",
            "Latin-America",
            "South America",
            "Americas, Europe, Israel",
            "Northern America, LATAM, Europe, APAC",
            "Brazil",
            "Brasil",
            "América Latina",
            "América do Sul",
        ):
            self.assertTrue(aceita_local(local), local)

    def test_recusa_o_resto(self):
        for local in (
            "USA only",
            "USA",
            "UK",
            "Europe",
            # North America = EUA + Canadá: "America" sozinha não basta, só "Americas".
            "North America",
            "Northern America",
            "Europe, USA, Canada, APAC",
            "USA, UK, India, Australia, Ireland, New Zealand, Philippines, Pakistan, Singapore, Mexico",
            "Anywhere in the US",
            # Por palavra, não por pedaço.
            "Brazilian timezone only",
            "",
            None,
            42,
        ):
            self.assertFalse(aceita_local(local), local)


class RemotiveClientTest(unittest.TestCase):
    def test_converte_a_resposta_gravada_so_com_local_aceito(self):
        chamadas = []
        cliente = RemotiveClient(http=_http(_gravada("remotive.json"), chamadas))

        vagas = cliente.buscar("engineer")

        self.assertEqual(len(chamadas), 1)
        # Sem `search`: a API o ignora; o termo é filtrado aqui.
        self.assertNotIn("search=", chamadas[0])
        self.assertIn("limit=100", chamadas[0])
        # "Senior AI Engineer" (LATAM) fica; "Senior back-end Engineer" não está na gravação reduzida;
        # "Software Engineer / AI Code Trainer" (USA, ..., Mexico) sai pelo local.
        self.assertEqual([v.titulo for v in vagas], ["Senior AI Engineer"])
        vaga = vagas[0]
        self.assertEqual(vaga.plataforma, "Remotive")
        self.assertEqual(vaga.empresa, "Lemon.io")
        self.assertEqual(vaga.url, "https://remotive.com/remote-jobs/artificial-intelligence/senior-ai-engineer-2091131")
        self.assertTrue(vaga.remoto)
        self.assertEqual(vaga.publicada_em, date(2026, 9, 18))
        self.assertTrue(vaga.detalhe.descricao.startswith("Are you a talented Senior AI Engineer"))
        self.assertNotIn("<", vaga.detalhe.descricao)

    def test_titulo_sem_palavra_do_termo_sai(self):
        cliente = RemotiveClient(http=_http(_gravada("remotive.json")))
        self.assertEqual([v.titulo for v in cliente.buscar("developer")], ["Senior Independent Software Developer"])
        self.assertEqual([v.titulo for v in cliente.buscar("sales")], ["Inside Sales Contractor"])
        self.assertEqual(cliente.buscar("bookkeeper"), [])

    def test_uma_chamada_serve_varios_termos_por_6_horas(self):
        chamadas, relogio = [], Relogio()
        cliente = RemotiveClient(http=_http(_gravada("remotive.json"), chamadas), relogio=relogio)

        cliente.buscar("engineer")
        relogio.agora += CACHE_SEGUNDOS - 1
        cliente.buscar("developer")
        self.assertEqual(len(chamadas), 1)
        relogio.agora += 1
        cliente.buscar("developer")
        self.assertEqual(len(chamadas), 2)

    def test_erro_http_vira_erro_e_a_proxima_espera_o_intervalo(self):
        chamadas, relogio = [], Relogio()
        cliente = RemotiveClient(http=_http_erro(429, chamadas), relogio=relogio)

        resultado = BuscaService([cliente]).buscar("engineer")
        self.assertIn("HTTPError", resultado.erros["Remotive"])
        # O termo seguinte da coleta não martela a API: erro sem nova chamada.
        resultado = BuscaService([cliente]).buscar("developer")
        self.assertIn("HTTP Error 429", resultado.erros["Remotive"])
        self.assertEqual(len(chamadas), 1)
        relogio.agora += INTERVALO_MINIMO_SEGUNDOS
        BuscaService([cliente]).buscar("developer")
        self.assertEqual(len(chamadas), 2)

    def test_item_torto_sai_sozinho(self):
        tortos = [
            "não é objeto",
            [],
            _remotive_item(title=None),
            _remotive_item(url=None),
            # Os termos de uso pedem o link do próprio Remotive.
            _remotive_item(url="https://outro-site.example/vaga"),
            _remotive_item(url="http://remotive.com/remote-jobs/x-1"),
            _remotive_item(candidate_required_location=None),
            _remotive_item(publication_date="ontem"),
            _remotive_item(publication_date=1790929856),
        ]
        vagas = RemotiveClient(http=_http({"jobs": [*tortos, _remotive_item(title="Bookkeeper válido")]})).buscar("bookkeeper")
        self.assertEqual([v.titulo for v in vagas], ["Bookkeeper válido"])

    def test_resposta_sem_lista_vira_erro_do_provider(self):
        for dados in ([], {"jobs": "x"}, None):
            with self.assertRaises(ValueError):
                RemotiveClient(http=_http(dados)).buscar("x")


class TimeoutCurtoTest(unittest.TestCase):
    def test_http_padrao_usa_timeout_curto_e_user_agent(self):
        for cliente in (HimalayasClient(), RemotiveClient()):
            resposta = mock.MagicMock()
            resposta.__enter__.return_value = io.BytesIO(b'{"jobs": []}')
            with mock.patch("urllib.request.urlopen", return_value=resposta) as urlopen:
                self.assertEqual(cliente.buscar("bookkeeper"), [])
            pedido = urlopen.call_args.args[0]
            self.assertEqual(urlopen.call_args.kwargs["timeout"], 10, cliente.nome)
            self.assertEqual(pedido.get_header("User-agent"), "vagas-svc/0.1")


class ProvidersLatamTest(unittest.TestCase):
    def _latam(self, env: dict) -> list[str]:
        from src.controller.api import providers_padrao

        with mock.patch.dict(os.environ, env, clear=True):
            return [p.nome for p in providers_padrao() if isinstance(p, (HimalayasClient, RemotiveClient))]

    def test_os_dois_por_padrao(self):
        for env in ({}, {"LATAM_PROVIDERS": ""}, {"LATAM_PROVIDERS": " , "}):
            self.assertEqual(self._latam(env), ["Himalayas", "Remotive"], env)

    def test_nenhum_desliga(self):
        for valor in ("nenhum", " Nenhum "):
            self.assertEqual(self._latam({"LATAM_PROVIDERS": valor}), [], valor)

    def test_lista_escolhe(self):
        self.assertEqual(self._latam({"LATAM_PROVIDERS": "remotive"}), ["Remotive"])
        self.assertEqual(self._latam({"LATAM_PROVIDERS": "Himalayas, himalayas"}), ["Himalayas"])
        self.assertEqual(self._latam({"LATAM_PROVIDERS": "REMOTIVE,himalayas"}), ["Remotive", "Himalayas"])

    def test_invalida_cai_no_padrao_sem_derrubar_a_app(self):
        for valor in ("linkedin", "himalayas,xpto", "nenhum,remotive"):
            with self.assertLogs("src.controller.api", "WARNING"):
                self.assertEqual(self._latam({"LATAM_PROVIDERS": valor}), ["Himalayas", "Remotive"], valor)


if __name__ == "__main__":
    unittest.main()
