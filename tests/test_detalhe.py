"""Detalhe completo e expiração: cliente do Remotar, DetalheService e a tela.

Fixtures GRAVADAS DE VERDADE em 2026-10-12, com dados de contato (e-mail e usuário do `author`) trocados
por placeholder (`contato@exemplo.invalid`), de https://api.remotar.com.br/jobs/{id}:
  - remotar_job_164153.json: salário informado (900000 a 1300000, monthly, BRL; centavos);
  - remotar_job_166020.json: salário "uninformed", 8 requisitos obrigatórios e 6 desejáveis, sem benefícios;
  - remotar_job_165619.json: type "monthly" com from=null e to=0 (informado só na forma), com benefícios.
  - remotar_listagem_expiracao.json: GET /jobs?search=analista+de+dados&page=1, recortada para 2 itens de
    `data` (166020 sem prazo e 165963 com expiresAt futuro); `expired`/`expiresAt` vêm na listagem.
Também observado em 2026-10-12: GET /jobs/1 e /jobs/100000 devolvem HTTP 404 com corpo vazio.
Salário em CENTAVOS: conferido em 2026-10-12 na página pública https://remotar.com.br/job/164153, que mostra
"R$ 9,000.00 a R$ 13,000.00" para from=900000 e to=1300000.
Tudo o que está escrito à mão aqui (HTML de ataque, respostas malformadas) é dito no próprio teste.
"""

import copy
import json
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, quote, urlsplit

from tests.sessao import ClienteLogado

from src.client.remotar import ErroProvider, RemotarClient, VagaNaoEncontrada, get_json_detalhe
from src.controller.api import criar_app
from src.model.detalhe_vaga import DetalheVaga, Requisito, Salario
from src.model.vaga_externa import VagaExterna
from src.repository.base import CurriculoRepositoryMemoria, PerfilBuscaRepositoryMemoria, VagaEncontradaRepositoryMemoria
from src.service.busca_service import BuscaService
from src.service.detalhe_service import AVISO_SEM_DETALHE, DetalheService, id_remotar
from tests.test_tela_lista import Pagina

FIXTURES = Path(__file__).parent / "fixtures"
HOJE = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


def fixture(nome: str):
    return json.loads((FIXTURES / nome).read_text(encoding="utf-8"))


def cliente_remotar(job, relogio=lambda: HOJE, chamadas: list | None = None) -> RemotarClient:
    def http(url: str):
        if chamadas is not None:
            chamadas.append(url)
        if isinstance(job, Exception):
            raise job
        return job

    return RemotarClient(http_detalhe=http, relogio=relogio)


class ClienteDetalheTest(unittest.TestCase):
    def test_salario_informado_vem_em_centavos(self):
        chamadas = []
        detalhe = cliente_remotar(fixture("remotar_job_164153.json"), chamadas=chamadas).detalhar("164153")
        self.assertEqual(chamadas, ["https://api.remotar.com.br/jobs/164153"])
        self.assertEqual(detalhe.salario, Salario(900000, 1300000, "BRL", "monthly"))
        self.assertEqual(detalhe.salario.texto(), "R$ 9.000 a R$ 13.000 por mês")
        self.assertIsNone(detalhe.expirada_em)

    def test_salario_uninformed_e_none(self):
        detalhe = cliente_remotar(fixture("remotar_job_166020.json")).detalhar("166020")
        self.assertIsNone(detalhe.salario)

    def test_salario_monthly_com_to_zero_nao_e_informado(self):
        detalhe = cliente_remotar(fixture("remotar_job_165619.json")).detalhar("165619")
        self.assertIsNone(detalhe.salario)

    def test_requisitos_obrigatorios_e_desejaveis_e_beneficios(self):
        detalhe = cliente_remotar(fixture("remotar_job_166020.json")).detalhar("166020")
        self.assertEqual(sum(r.obrigatorio for r in detalhe.requisitos), 8)
        self.assertEqual(sum(not r.obrigatorio for r in detalhe.requisitos), 6)
        self.assertEqual(detalhe.requisitos[0], Requisito("Formação superior em Tecnologia da Informação, Ciência de Dados, Engenharia, Matemática, Estatística, Física, Química ou áreas correlatas.", True))
        self.assertEqual(detalhe.beneficios, [])
        com_beneficios = cliente_remotar(fixture("remotar_job_165619.json")).detalhar("165619")
        self.assertIn("Assistência médica", com_beneficios.beneficios)

    def test_descricao_e_subtitulo_em_texto_puro(self):
        detalhe = cliente_remotar(fixture("remotar_job_166020.json")).detalhar("166020")
        self.assertIn("• Modelar repositórios de dados", detalhe.descricao)
        self.assertNotIn("<", detalhe.descricao)
        self.assertTrue(detalhe.subtitulo.startswith("Modelagem e análise de dados"))

    def test_expirada_em_vem_do_prazo_ou_de_expired(self):
        base = fixture("remotar_job_166020.json")
        passado = datetime.fromisoformat("2026-10-01T10:00:00.000-03:00")
        futuro = datetime.fromisoformat("2026-10-24T18:33:25.000-03:00")
        # Escritos à mão: a vaga gravada de verdade não estava expirada.
        for campos, esperado in (
            ({"expired": True}, HOJE),
            ({"expired": True, "expiresAt": "2026-10-01T10:00:00.000-03:00"}, passado),
            ({"expired": True, "expiresAt": "2026-10-24T18:33:25.000-03:00"}, HOJE),
            ({"expired": False, "expiresAt": "2026-10-01T10:00:00.000-03:00"}, passado),
            # Prazo futuro é gravado: a vaga some sozinha quando o prazo passar.
            ({"expired": False, "expiresAt": "2026-10-24T18:33:25.000-03:00"}, futuro),
            ({"expired": "sim"}, None),
            ({"expiresAt": "ontem"}, None),
            ({"expiresAt": 12345}, None),
            ({}, None),
        ):
            with self.subTest(campos=campos):
                job = {**base, **campos}
                self.assertEqual(cliente_remotar(job).detalhar("166020").expirada_em, esperado)

    def test_tem_prazo_so_e_false_quando_o_remotar_diz_que_nao_ha_prazo(self):
        base = fixture("remotar_job_166020.json")  # gravada de verdade: expires false, expiresAt null
        self.assertIs(cliente_remotar(base).detalhar("166020").tem_prazo, False)
        # Escritos à mão a partir da gravada.
        for campos, esperado in (
            ({"expires": True, "expiresAt": "2026-10-24T18:33:25.000-03:00"}, True),
            ({"expires": False, "expired": True}, True),  # expirou: não é "sem prazo"
            ({"expires": False, "expiresAt": "2026-10-24T18:33:25.000-03:00"}, True),  # contraditório: a data vale
            ({"expires": False, "expiresAt": "ontem"}, None),  # data torta: mantém
            ({"expires": True, "expiresAt": None}, None),
            ({"expires": "false"}, None),
            ({"expires": None}, None),
        ):
            with self.subTest(campos=campos):
                self.assertIs(cliente_remotar({**base, **campos}).detalhar("166020").tem_prazo, esperado)
        sem_campo = {k: v for k, v in base.items() if k != "expires"}
        self.assertIsNone(cliente_remotar(sem_campo).detalhar("166020").tem_prazo)

    def test_id_so_digitos(self):
        for id in ("12a", "../1", "1/2", "", " 1", "1?x=2", "١٢٣", 123, None, "1" * 13):
            with self.subTest(id=id):
                chamadas = []
                with self.assertRaises(ValueError):
                    cliente_remotar(fixture("remotar_job_166020.json"), chamadas=chamadas).detalhar(id)
                self.assertEqual(chamadas, [])

    def test_resposta_que_nao_e_objeto_e_erro_do_provider(self):
        for job in ([], "x", None, 3):
            with self.subTest(job=job), self.assertRaises(ErroProvider):
                cliente_remotar(job).detalhar("1")

    def test_qualquer_erro_de_parsing_vira_erro_do_provider(self):
        # O parser é defensivo, mas nada que o provider mande pode virar 500: forço cada tipo de erro.
        for erro in (KeyError("x"), TypeError("x"), AttributeError("x"), IndexError("x"), ValueError("x"), RecursionError()):
            with self.subTest(erro=type(erro).__name__), mock.patch("src.client.remotar.html_para_texto", side_effect=erro):
                with self.assertRaises(ErroProvider):
                    cliente_remotar(fixture("remotar_job_166020.json")).detalhar("166020")

    def test_campo_malformado_descarta_so_o_campo(self):
        # Escrito à mão: cada campo com a forma errada, a vaga continua com o que presta.
        job = copy.deepcopy(fixture("remotar_job_164153.json"))
        job["jobRequirements"] = [
            "texto solto",
            5,
            None,
            {"description": None, "mandatory": True},
            {"description": "   ", "mandatory": True},
            {"description": "SQL <b>avançado</b>", "mandatory": "true"},
            {"description": "Excel", "mandatory": True},
        ]
        job["jobBenefits"] = [None, "x", {"description": 7}, {"description": "Vale-refeição"}]
        job["description"] = ["não é texto"]
        job["subtitle"] = {"a": 1}
        job["jobSalary"] = "uninformed"
        detalhe = cliente_remotar(job).detalhar("164153")
        self.assertEqual(detalhe.requisitos, [Requisito("SQL avançado", False), Requisito("Excel", True)])
        self.assertEqual(detalhe.beneficios, ["Vale-refeição"])
        self.assertIsNone(detalhe.descricao)
        self.assertIsNone(detalhe.subtitulo)
        self.assertIsNone(detalhe.salario)

    def test_listas_que_nao_sao_listas(self):
        job = {**fixture("remotar_job_166020.json"), "jobRequirements": {"a": 1}, "jobBenefits": "x", "jobSalary": [1]}
        detalhe = cliente_remotar(job).detalhar("166020")
        self.assertEqual((detalhe.requisitos, detalhe.beneficios, detalhe.salario), ([], [], None))

    def test_salario_estranho_vira_none(self):
        base = fixture("remotar_job_164153.json")
        for pago in (
            {"type": "monthly", "from": -5, "to": -1, "currency": "BRL"},
            {"type": "monthly", "from": True, "to": "alto", "currency": "BRL"},
            {"type": "monthly", "from": 10**15, "to": None},
            {"type": "monthly", "from": 1.5, "to": None},
        ):
            with self.subTest(pago=pago):
                self.assertIsNone(cliente_remotar({**base, "jobSalary": pago}).detalhar("164153").salario)

    def test_texto_de_salario(self):
        self.assertEqual(Salario(350000, 350000, "BRL", "monthly").texto(), "R$ 3.500 por mês")
        self.assertEqual(Salario(None, 362100, "BRL", "monthly").texto(), "até R$ 3.621 por mês")
        self.assertEqual(Salario(200000, None, "USD", None).texto(), "a partir de USD 2.000")
        self.assertEqual(Salario(12345, 50000, "BRL", "hourly").texto(), "R$ 123,45 a R$ 500 por hora")


class RequisicaoDetalheTest(unittest.TestCase):
    """`get_json_detalhe` sem rede: urlopen trocado. O 404 sem corpo é o comportamento observado de verdade."""

    def test_timeout_de_5_segundos(self):
        resposta = mock.MagicMock()
        resposta.__enter__.return_value.read.return_value = b"{}"
        with mock.patch("urllib.request.urlopen", return_value=resposta) as abrir:
            self.assertEqual(get_json_detalhe("https://api.remotar.com.br/jobs/1"), {})
        self.assertEqual(abrir.call_args.kwargs["timeout"], 5)

    def test_404_e_410_viram_vaga_nao_encontrada(self):
        for codigo in (404, 410):
            erro = urllib.error.HTTPError("u", codigo, "x", {}, None)
            with self.subTest(codigo=codigo), mock.patch("urllib.request.urlopen", side_effect=erro):
                with self.assertRaises(VagaNaoEncontrada):
                    get_json_detalhe("https://api.remotar.com.br/jobs/1")

    def test_outras_falhas_viram_erro_do_provider(self):
        casos = (
            urllib.error.HTTPError("u", 500, "x", {}, None),
            urllib.error.URLError("sem rede"),
            TimeoutError("demorou"),
            ConnectionResetError(),
        )
        for erro in casos:
            with self.subTest(erro=erro), mock.patch("urllib.request.urlopen", side_effect=erro):
                with self.assertRaises(ErroProvider):
                    get_json_detalhe("https://api.remotar.com.br/jobs/1")

    def test_json_malformado_e_corpo_gigante(self):
        for corpo in (b"<html>", b"", b"x" * 2_000_001, b"[" * 200_000):
            resposta = mock.MagicMock()
            resposta.__enter__.return_value.read.return_value = corpo
            with self.subTest(tamanho=len(corpo)), mock.patch("urllib.request.urlopen", return_value=resposta):
                with self.assertRaises(ErroProvider):
                    get_json_detalhe("https://api.remotar.com.br/jobs/1")


class ListagemExpiracaoTest(unittest.TestCase):
    """`expired`/`expiresAt` e o detalhe vêm na listagem (fixture gravada de verdade)."""

    def buscar(self, listagem, relogio):
        return RemotarClient(max_paginas=1, http=lambda url: listagem, relogio=relogio).buscar("analista de dados")

    def test_prazo_futuro_e_gravado_como_expirada_em(self):
        vagas = {v.url: v for v in self.buscar(fixture("remotar_listagem_expiracao.json"), lambda: HOJE)}
        self.assertEqual(len(vagas), 2)
        prazo = datetime.fromisoformat("2026-10-24T18:33:25.000-03:00")
        self.assertEqual(vagas["https://remotar.com.br/job/165963"].expirada_em, prazo)
        self.assertIsNone(vagas["https://remotar.com.br/job/166020"].expirada_em)
        self.assertIs(vagas["https://remotar.com.br/job/165963"].tem_prazo, True)
        self.assertIs(vagas["https://remotar.com.br/job/166020"].tem_prazo, False)

    def test_a_listagem_traz_o_detalhe_sanitizado(self):
        vagas = {v.url: v for v in self.buscar(fixture("remotar_listagem_expiracao.json"), lambda: HOJE)}
        detalhe = vagas["https://remotar.com.br/job/166020"].detalhe
        self.assertEqual(sum(r.obrigatorio for r in detalhe.requisitos), 8)
        self.assertEqual(len(detalhe.requisitos), 14)
        self.assertIn("• Modelar repositórios de dados", detalhe.descricao)
        self.assertNotIn("<", detalhe.descricao)
        self.assertIsNone(detalhe.salario)

    def test_expired_true_sem_data_vale_agora(self):
        listagem = fixture("remotar_listagem_expiracao.json")
        listagem["data"][0]["expired"] = True  # escrito à mão
        listagem["data"][1]["expiresAt"] = "não é data"  # escrito à mão: só ESTA data cai, o item fica
        vagas = self.buscar(listagem, lambda: HOJE)
        self.assertEqual([v.expirada_em for v in vagas], [HOJE, None])

    def test_job_estranho_fica_sem_detalhe_e_a_busca_segue(self):
        original = RemotarClient._detalhe_do_job
        chamadas = []

        def estranho(self, job, *resto):
            chamadas.append(job["id"])
            if len(chamadas) == 1:
                raise TypeError("forma inesperada")  # escrito à mão
            return original(self, job, *resto)

        with mock.patch.object(RemotarClient, "_detalhe_do_job", estranho):
            vagas = self.buscar(fixture("remotar_listagem_expiracao.json"), lambda: HOJE)
        self.assertEqual(len(vagas), 2)
        self.assertIsNone(vagas[0].detalhe)
        self.assertIsNotNone(vagas[1].detalhe)


def vaga_remotar(id: int, titulo: str = "Analista de Dados", plataforma: str = "Remotar", url: str | None = None, **kw):
    return VagaExterna(titulo, "Empresa Teste", plataforma, url or f"https://remotar.com.br/job/{id}", remoto=True, **kw)


class ProviderFalso:
    def __init__(self, resultado=None):
        self.resultado = resultado or DetalheVaga(descricao="Texto da vaga", requisitos=[Requisito("SQL", True)])
        self.chamadas: list[str] = []

    def detalhar(self, id: str):
        self.chamadas.append(id)
        if isinstance(self.resultado, Exception):
            raise self.resultado
        return self.resultado


class ProviderProibido:
    """Falha se for chamado: o GET /tela e o clique no card nunca esperam o provider."""

    def detalhar(self, id: str):
        raise AssertionError("o provider foi chamado")


class DetalheServiceTest(unittest.TestCase):
    def setUp(self):
        self.repo = VagaEncontradaRepositoryMemoria(1)
        self.repo.registrar([vaga_remotar(123)], "dados")  # sem detalhe: veio de antes da 004
        self.url = "https://remotar.com.br/job/123"
        self.provider = ProviderFalso()
        self.servico = DetalheService(self.repo, self.provider)

    def test_abrir_nunca_chama_o_provider_e_diz_que_falta_carregar(self):
        aberta = self.servico.abrir(self.url)
        self.assertEqual(self.provider.chamadas, [])
        self.assertTrue(aberta.carregar)
        self.assertIsNone(aberta.vaga.descricao)

    def test_carregar_busca_grava_e_devolve_o_detalhe(self):
        aberta = self.servico.carregar(self.url)
        self.assertEqual(self.provider.chamadas, ["123"])
        self.assertEqual(aberta.vaga.descricao, "Texto da vaga")
        self.assertIsNotNone(aberta.vaga.detalhada_em)
        self.assertIsNone(aberta.aviso)
        self.assertEqual(self.repo.obter(self.url).requisitos, [Requisito("SQL", True)])

    def test_detalhe_ja_gravado_nunca_busca_de_novo(self):
        self.servico.carregar(self.url)
        aberta = self.servico.carregar(self.url)
        self.assertFalse(aberta.carregar)
        self.assertEqual(self.provider.chamadas, ["123"])

    def test_detalhe_gravado_pela_coleta_nao_chama_o_provider(self):
        self.repo.registrar([vaga_remotar(124, detalhe=DetalheVaga(descricao="da listagem"))], "dados")
        aberta = self.servico.carregar("https://remotar.com.br/job/124")
        self.assertEqual((aberta.vaga.descricao, aberta.carregar), ("da listagem", False))
        self.assertEqual(self.provider.chamadas, [])

    def test_vaga_desconhecida_e_none_sem_chamar_o_provider(self):
        self.assertIsNone(self.servico.carregar("https://remotar.com.br/job/999"))
        self.assertIsNone(self.servico.abrir("https://remotar.com.br/job/999"))
        self.assertEqual(self.provider.chamadas, [])

    def test_404_marca_expirada_e_a_vaga_some_da_lista_mas_obter_acha(self):
        self.provider.resultado = VagaNaoEncontrada("x")
        aberta = self.servico.carregar(self.url)
        self.assertTrue(aberta.expirada)
        self.assertIsNone(aberta.aviso)
        self.assertEqual(self.repo.listar(False, 10), [])
        self.assertEqual(self.repo.contar(False), 0)
        self.assertEqual(self.repo.listar_recentes(False, 10), [])
        self.assertIsNotNone(self.repo.obter(self.url))

    def test_provider_diz_expired_marca_expirada_e_guarda_o_detalhe(self):
        self.provider.resultado = DetalheVaga(descricao="Encerrada", expirada_em=datetime.now(timezone.utc))
        aberta = self.servico.carregar(self.url)
        self.assertTrue(aberta.expirada)
        self.assertEqual(aberta.vaga.descricao, "Encerrada")
        self.assertEqual(self.repo.contar(False), 0)

    def test_expirada_nao_chama_o_provider(self):
        self.repo.marcar_expirada(self.url)
        aberta = self.servico.carregar(self.url)
        self.assertEqual(self.provider.chamadas, [])
        self.assertTrue(aberta.expirada)
        self.assertFalse(aberta.carregar)

    def test_prazo_futuro_nao_e_expirada_ainda(self):
        self.provider.resultado = DetalheVaga(descricao="x", expirada_em=datetime.now(timezone.utc) + timedelta(days=3))
        aberta = self.servico.carregar(self.url)
        self.assertFalse(aberta.expirada)
        self.assertEqual(self.repo.contar(False), 1)

    def test_falha_do_provider_vira_aviso_sem_gravar(self):
        self.provider.resultado = ErroProvider("HTTP 500")
        aberta = self.servico.carregar(self.url)
        self.assertEqual(aberta.aviso, AVISO_SEM_DETALHE)
        self.assertIsNone(aberta.vaga.descricao)
        self.assertIsNone(self.repo.obter(self.url).detalhada_em)
        # Sem cache de falha: a próxima tentativa busca de novo.
        self.servico.carregar(self.url)
        self.assertEqual(len(self.provider.chamadas), 2)

    def test_sem_provider_nada_sai_para_a_rede(self):
        servico = DetalheService(self.repo)
        for aberta in (servico.abrir(self.url), servico.carregar(self.url)):
            self.assertEqual(aberta.vaga.url, self.url)
            self.assertFalse(aberta.carregar)
            self.assertIsNone(aberta.aviso)

    def test_so_a_url_exata_do_remotar_chama_o_provider(self):
        urls = [
            "https://evil.test/job/123",
            "https://remotar.com.br.evil.test/job/123",
            "https://remotar.com.br@evil.test/job/123",
            "https://remotar.com.br/jobs/123",
            "https://remotar.com.br/job/123/",
            "https://remotar.com.br/job/123/../456",
            "https://remotar.com.br/job/123?x=1",
            "https://remotar.com.br/job/123#x",
            "https://remotar.com.br/job/12a",
            "https://remotar.com.br/job/",
            "http://remotar.com.br/job/123",
            "https://www.remotar.com.br/job/123",
            "https://remotar.com.br/job/1234567890123",
            "https://remotar.com.br/job/١٢٣",
            "https://remotar.com.br/job/123\n",
        ]
        self.repo.registrar([vaga_remotar(0, url=u) for u in urls], "dados")
        for url in urls:
            with self.subTest(url=url):
                self.assertFalse(self.servico.abrir(url).carregar)
                self.servico.carregar(url)
        self.assertEqual(self.provider.chamadas, [])

    def test_so_plataforma_remotar(self):
        self.repo.registrar([vaga_remotar(7, plataforma="Trampos")], "dados")
        self.servico.carregar("https://remotar.com.br/job/7")
        self.assertEqual(self.provider.chamadas, [])

    def test_id_remotar(self):
        self.assertEqual(id_remotar("https://remotar.com.br/job/166020"), "166020")
        for lixo in (None, 5, "", "https://remotar.com.br/job/x"):
            self.assertIsNone(id_remotar(lixo))


class ConexaoPorChamadaTest(unittest.TestCase):
    """Uma conexão do pool por chamada: com o `_PorChamada` de produção cada método abre e fecha a sua,
    e nenhuma fica aberta enquanto o serviço espera o provider."""

    def test_nenhuma_conexao_aberta_durante_a_espera_do_provider(self):
        from src.controller.api import _PorChamada

        abertas = {"n": 0, "durante": None}
        memoria = VagaEncontradaRepositoryMemoria(1)
        memoria.registrar([vaga_remotar(5)], "dados")

        class Pool:
            def connection(self_):
                class Ctx:
                    def __enter__(_):
                        abertas["n"] += 1
                        return None

                    def __exit__(_, *args):
                        abertas["n"] -= 1

                return Ctx()

        class Repo:
            def __init__(self, conn):
                pass

            def __getattr__(self, nome):
                return getattr(memoria, nome)

        provider = ProviderFalso()
        original = provider.detalhar
        provider.detalhar = lambda id: (abertas.update(durante=abertas["n"]), original(id))[1]
        encontradas = _PorChamada(lambda: Pool(), lambda: Repo)
        DetalheService(encontradas, provider).carregar("https://remotar.com.br/job/5")
        self.assertEqual(abertas["durante"], 0)
        self.assertEqual(abertas["n"], 0)


def montar_tela(provider=None, vagas=None):
    repo = VagaEncontradaRepositoryMemoria(1)
    repo.registrar(vagas or [vaga_remotar(123)], "dados")
    servico = DetalheService(repo, provider)
    app = criar_app(PerfilBuscaRepositoryMemoria(1), CurriculoRepositoryMemoria(1), BuscaService([]), encontradas=repo, detalhe=servico)
    return ClienteLogado(app), repo, app


URL_VAGA = "https://remotar.com.br/job/123"
URL_DETALHE = "/tela/vagas/detalhe?url=" + quote(URL_VAGA, safe="")
URL_SECOES = "/tela/vagas/secoes?url=" + quote(URL_VAGA, safe="")
HX = {"HX-Request": "true"}
DETALHE_COMPLETO = DetalheVaga(
    descricao="Linha 1\nLinha 2",
    requisitos=[Requisito("SQL", True), Requisito("Python", False), Requisito("Excel", True)],
    salario=Salario(350000, 450000, "BRL", "monthly"),
    beneficios=["Vale-refeição"],
    subtitulo="Resumo da vaga",
)


def carregador(html: str):
    """O elemento de carga das seções: (hx-get, hx-trigger, hx-swap) ou None."""
    for e in Pagina(html).tags("div"):
        if e.attrs.get("id") == "secoes-detalhe" and "hx-get" in e.attrs:
            return e.attrs["hx-get"], e.attrs.get("hx-trigger"), e.attrs.get("hx-swap")
    return None


class TelaNaoBloqueiaTest(unittest.TestCase):
    def test_pagina_inteira_e_clique_no_card_nunca_chamam_o_provider(self):
        cliente, _, _ = montar_tela(ProviderProibido())
        for rota, cabecalhos in (
            ("/tela", {}),
            ("/tela?url=" + quote(URL_VAGA, safe=""), {}),
            (URL_DETALHE, HX),
            (URL_DETALHE, {}),
            ("/tela/vagas", HX),
        ):
            with self.subTest(rota=rota, htmx=bool(cabecalhos)):
                resposta = cliente.get(rota, headers=cabecalhos)
                self.assertEqual(resposta.status_code, 200)
                self.assertIn("Analista de Dados", resposta.text)

    def test_sem_detalhe_gravado_o_detalhe_traz_o_carregador_com_url_codificada(self):
        cliente, _, _ = montar_tela(ProviderProibido())
        for rota, cabecalhos in (("/tela", {}), (URL_DETALHE, HX)):
            with self.subTest(rota=rota):
                hx_get, gatilho, troca = carregador(cliente.get(rota, headers=cabecalhos).text)
                self.assertEqual((gatilho, troca), ("load", "outerHTML"))
                # Ida e volta: a url do atributo, decodificada como o servidor faz, é a da vaga.
                self.assertEqual(parse_qs(urlsplit(hx_get).query)["url"], [URL_VAGA])
                self.assertEqual(urlsplit(hx_get).path, "/tela/vagas/secoes")
                self.assertIn("https%3A//", hx_get)  # o `|urlencode` do Jinja (como nos cards)
        self.assertIn("Carregando a descrição", cliente.get("/tela").text)

    def test_com_detalhe_gravado_as_secoes_vem_na_hora_sem_carregador(self):
        cliente, repo, _ = montar_tela(ProviderProibido(), [vaga_remotar(123, detalhe=DETALHE_COMPLETO)])
        html = cliente.get("/tela").text
        self.assertIsNone(carregador(html))
        self.assertIn("Linha 1\nLinha 2", html)
        self.assertIn("R$ 3.500 a R$ 4.500 por mês", html)

    def test_vaga_expirada_ou_de_outro_provider_nao_tem_carregador(self):
        cliente, repo, _ = montar_tela(ProviderProibido())
        repo.marcar_expirada(URL_VAGA)
        self.assertIsNone(carregador(cliente.get(URL_DETALHE, headers=HX).text))

    def test_sem_servico_de_detalhe_a_tela_nao_busca_nada(self):
        repo = VagaEncontradaRepositoryMemoria(1)
        repo.registrar([vaga_remotar(123)], "dados")
        app = criar_app(PerfilBuscaRepositoryMemoria(1), CurriculoRepositoryMemoria(1), BuscaService([]), encontradas=repo)
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("foi para a rede")):
            cliente = ClienteLogado(app)
            self.assertEqual(cliente.get(URL_DETALHE, headers=HX).status_code, 200)
            self.assertEqual(cliente.get(URL_SECOES, headers=HX).status_code, 200)


class TelaSecoesTest(unittest.TestCase):
    def test_secoes_com_requisitos_separados_e_salario_informado(self):
        cliente, _, _ = montar_tela(ProviderFalso(DETALHE_COMPLETO))

        resposta = cliente.get(URL_SECOES, headers=HX)

        self.assertEqual(resposta.status_code, 200)
        html = resposta.text
        self.assertIn("Resumo da vaga", html)
        self.assertIn("R$ 3.500 a R$ 4.500 por mês", html)
        self.assertIn("Linha 1\nLinha 2", html)
        pagina = Pagina(html)
        self.assertEqual([e.texto for e in pagina.tags("h3")], ["Salário", "Descrição", "Requisitos", "Benefícios"])
        self.assertEqual([e.texto for e in pagina.tags("h4")], ["Obrigatórios", "Desejáveis"])
        self.assertEqual([e.texto for e in pagina.tags("li")], ["SQL", "Excel", "Python", "Vale-refeição"])
        # É o fragmento que troca #secoes-detalhe: sem página inteira e sem carregador de novo.
        self.assertNotIn("<html", html)
        self.assertIsNone(carregador(html))

    def test_sem_salario_informado_nao_tem_secao_de_salario(self):
        cliente, _, _ = montar_tela(ProviderFalso(DetalheVaga(descricao="x")))
        html = cliente.get(URL_SECOES, headers=HX).text
        for ausente in ("Salário", "Requisitos", "Benefícios"):
            self.assertNotIn(ausente, html)

    def test_salario_com_zero_ou_nulo_nao_aparece_e_um_lado_vira_a_partir_de_ou_ate(self):
        for salario, esperado in (
            (Salario(None, 0, "BRL", "monthly"), None),
            (Salario(0, None, "BRL", "monthly"), None),
            (Salario(300000, None, "BRL", "monthly"), "a partir de R$ 3.000 por mês"),
            (Salario(0, 362100, "BRL", "monthly"), "até R$ 3.621 por mês"),
        ):
            with self.subTest(salario=salario):
                cliente, _, _ = montar_tela(ProviderFalso(DetalheVaga(descricao="x", salario=salario)))
                html = cliente.get(URL_SECOES, headers=HX).text
                if esperado is None:
                    self.assertNotIn("Salário", html)
                else:
                    self.assertIn(esperado, html)

    def test_falha_do_provider_nao_e_500_e_mostra_aviso_no_lugar_das_secoes(self):
        for erro in (ErroProvider("HTTP 500"), ErroProvider("tempo")):
            with self.subTest(erro=erro):
                cliente, _, _ = montar_tela(ProviderFalso(erro))
                resposta = cliente.get(URL_SECOES, headers=HX)
                self.assertEqual(resposta.status_code, 200)
                self.assertIn(AVISO_SEM_DETALHE, resposta.text)
                self.assertIsNone(carregador(resposta.text))

    def test_404_do_provider_grava_expirada_e_mostra_vaga_expirada(self):
        cliente, repo, _ = montar_tela(ProviderFalso(VagaNaoEncontrada("x")))
        resposta = cliente.get(URL_SECOES, headers=HX)
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Vaga expirada", resposta.text)
        self.assertEqual(repo.contar(False), 0)

    def test_vaga_desconhecida_e_404(self):
        cliente, _, _ = montar_tela(ProviderProibido())
        for url in ("https://remotar.com.br/job/999", "", "x" * 3000):
            resposta = cliente.get("/tela/vagas/secoes", params={"url": url}, headers=HX)
            self.assertEqual(resposta.status_code, 404)
        self.assertEqual(cliente.get("/tela/vagas/secoes", headers=HX).status_code, 404)

    def test_descricao_usa_pre_line_e_o_css_proprio_esta_no_base(self):
        cliente, _, _ = montar_tela(ProviderFalso())
        self.assertIn('href="/static/tela-detalhe.css?v=', cliente.get("/tela").text)
        css = cliente.get("/static/tela-detalhe.css")
        self.assertEqual(css.status_code, 200)
        self.assertIn("white-space: pre-line", css.text)


class TelaExpiradaTest(unittest.TestCase):
    def test_expirada_some_da_lista_e_do_contador_mas_o_link_direto_abre(self):
        cliente, repo, _ = montar_tela(ProviderProibido(), [vaga_remotar(123), vaga_remotar(124, "Outra Vaga")])
        repo.marcar_expirada(URL_VAGA)

        lista = Pagina(cliente.get("/tela").text)

        self.assertEqual(len(lista.cards()), 1)
        self.assertIn("1 encontrada", lista.por_id("vagas").texto)
        direto = cliente.get(URL_DETALHE, headers=HX)
        self.assertEqual(direto.status_code, 200)
        self.assertIn("Vaga expirada", direto.text)
        self.assertIn("Analista de Dados", direto.text)

    def test_prazo_futuro_aparece_hoje_e_some_depois_do_prazo_sem_nova_coleta(self):
        relogio = {"agora": HOJE}
        repo = VagaEncontradaRepositoryMemoria(1, relogio=lambda: relogio["agora"])
        prazo = HOJE + timedelta(days=2)
        repo.registrar([vaga_remotar(123, expirada_em=prazo, detalhe=DETALHE_COMPLETO)], "dados")
        servico = DetalheService(repo, ProviderProibido(), agora=lambda: relogio["agora"])
        app = criar_app(PerfilBuscaRepositoryMemoria(1), CurriculoRepositoryMemoria(1), BuscaService([]), encontradas=repo, detalhe=servico)
        cliente = ClienteLogado(app)

        hoje = cliente.get("/tela")
        self.assertEqual(len(Pagina(hoje.text).cards()), 1)
        self.assertNotIn("Vaga expirada", cliente.get(URL_DETALHE, headers=HX).text)

        relogio["agora"] = prazo + timedelta(seconds=1)
        depois = cliente.get("/tela")
        self.assertEqual(Pagina(depois.text).cards(), [])
        self.assertIn("Vaga expirada", cliente.get(URL_DETALHE, headers=HX).text)

    def test_aplicada_continua_nas_aplicadas_mesmo_expirada(self):
        cliente, repo, app = montar_tela(ProviderProibido())
        app.state.candidaturas.marcar_aplicada(URL_VAGA)
        repo.marcar_expirada(URL_VAGA)
        html = cliente.get("/tela").text
        aplicadas = Pagina(html).por_id("aplicadas").texto
        self.assertIn("Analista de Dados", aplicadas)
        self.assertIn("1 candidatura", html)


class TelaSegurancaTest(unittest.TestCase):
    ATAQUE = (  # escrito à mão
        "<p>Oi</p><script>alert('xss')</script>"
        '<img src=x onerror="alert(2)">'
        '<a href="javascript:alert(3)">clique</a>'
        "&lt;b&gt;negrito&lt;/b&gt; &amp; &copy;"
    )

    def job(self, descricao_html: str, requisito_html: str = "SQL") -> dict:
        return {
            "description": descricao_html,
            "jobRequirements": [{"description": requisito_html, "mandatory": True}],
            "jobBenefits": [{"description": f"<script>alert(4)</script>{requisito_html}"}],
            "subtitle": descricao_html,
            "jobSalary": {"type": "uninformed"},
        }

    def pelas_secoes(self, descricao_html: str, requisito_html: str = "SQL") -> str:
        cliente, _, _ = montar_tela(cliente_remotar(self.job(descricao_html, requisito_html)))
        return cliente.get(URL_SECOES, headers=HX).text

    def pela_coleta(self, descricao_html: str) -> str:
        """O caminho novo: o HTML do provider entra pela listagem (`buscar`) e `registrar`."""
        listagem = {"data": [{**self.job(descricao_html), "id": 123, "title": "Analista de Dados", "type": "remote"}], "meta": {"last_page": 1}}
        vagas = RemotarClient(max_paginas=1, http=lambda url: listagem).buscar("analista de dados")
        cliente, _, _ = montar_tela(ProviderProibido(), vagas)
        return cliente.get("/tela").text

    def confere(self, html: str, scripts_da_pagina: list[str]):
        # A página inteira tem os <script src="/static/..."> dela; nenhum outro, e nenhum inline.
        # Nenhum script inline e os da página continuam lá (sem fixar a lista inteira: outras frentes acrescentam).
        scripts = Pagina(html).tags("script")
        self.assertTrue(all(e.attrs.get("src") for e in scripts), "script inline na página")
        self.assertTrue(set(scripts_da_pagina) <= {e.attrs["src"].split("?")[0] for e in scripts})
        for perigo in ("onerror", "javascript:", "<img", "alert("):
            self.assertNotIn(perigo, html.lower(), perigo)
        self.assertIn("Oi", html)
        self.assertIn("clique", html)
        # A entidade vira texto "<b>negrito</b>" e a tela escapa: aparece como &lt;b&gt;, nunca como tag.
        self.assertIn("&lt;b&gt;negrito&lt;/b&gt;", html)
        self.assertNotIn("<b>negrito", html)
        self.assertIn("&amp; ©", html)

    def test_html_de_ataque_do_provider_nao_chega_cru_na_pagina_pelas_secoes(self):
        self.confere(self.pelas_secoes(self.ATAQUE, '<img src=x onerror="alert(5)">SQL'), [])

    def test_html_de_ataque_do_provider_nao_chega_cru_na_pagina_pela_coleta(self):
        self.confere(self.pela_coleta(self.ATAQUE), ["/static/htmx.min.js", "/static/tela.js"])

    def test_autoescape_segura_mesmo_se_o_banco_tiver_html(self):
        # Defesa em profundidade: texto "sujo" gravado direto no repositório, sem passar pelo sanitizador.
        cliente, repo, _ = montar_tela(None)
        sujo = '<script>alert(1)</script><img src=x onerror=alert(2)><a href="javascript:alert(3)">x</a>'
        repo.gravar_detalhe(
            URL_VAGA, DetalheVaga(descricao=sujo, requisitos=[Requisito(sujo, True)], beneficios=[sujo], subtitulo=sujo)
        )
        html = cliente.get(URL_DETALHE, headers=HX).text
        for perigo in ("<script", "<img", '<a href="javascript'):
            self.assertNotIn(perigo, html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertEqual(Pagina(html).tags("script"), [])

    def test_link_da_descricao_nunca_vira_link(self):
        html = self.pelas_secoes('<a href="javascript:alert(1)">Candidate-se</a> <a href="https://x.test/">ok</a>')
        self.assertEqual([e for e in Pagina(html).tags("a")], [])


if __name__ == "__main__":
    unittest.main()
