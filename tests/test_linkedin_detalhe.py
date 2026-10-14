"""Descrição sob demanda das vagas do LinkedIn: cliente da página pública, `id_linkedin`, DetalheService e tela.

A fixture `linkedin_job_posting_escrito_a_mao.html` é ESCRITA À MÃO (texto inventado), imitando a estrutura
de GET https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id} observada em 13/10/2026. Os outros HTMLs
daqui também são escritos à mão, no próprio teste. Nenhum teste sai para a rede.
"""

import unittest
import urllib.error
from pathlib import Path
from unittest import mock
from urllib.parse import quote

from fastapi.testclient import TestClient

from src.client.apify_linkedin import ApifyLinkedInClient
from src.client.linkedin_detalhe import LinkedInDetalheClient, extrair_descricao, get_html_detalhe
from src.client.remotar import ErroProvider, VagaNaoEncontrada
from src.controller.api import criar_app
from src.model.detalhe_vaga import DetalheVaga
from src.model.vaga_externa import VagaExterna
from src.repository.base import CurriculoRepositoryMemoria, PerfilBuscaRepositoryMemoria, VagaEncontradaRepositoryMemoria
from src.service.busca_service import BuscaService
from src.service.detalhe_service import AVISO_SEM_DETALHE, PLATAFORMA_LINKEDIN, DetalheService, id_linkedin
from tests.test_detalhe import HX, ProviderFalso, ProviderProibido, carregador, vaga_remotar

FIXTURE = Path(__file__).parent / "fixtures" / "linkedin_job_posting_escrito_a_mao.html"
URL_LINKEDIN = "https://www.linkedin.com/jobs/view/4012345678"


def vaga_linkedin(url: str = URL_LINKEDIN, **kw) -> VagaExterna:
    return VagaExterna("Analista de Dados", "Empresa Fictícia", PLATAFORMA_LINKEDIN, url, remoto=True, **kw)


def cliente(html_ou_erro, chamadas: list | None = None) -> LinkedInDetalheClient:
    def http(url: str) -> str:
        if chamadas is not None:
            chamadas.append(url)
        if isinstance(html_ou_erro, Exception):
            raise html_ou_erro
        return html_ou_erro

    return LinkedInDetalheClient(http=http)


def resposta_http(corpo: bytes):
    resposta = mock.MagicMock()
    resposta.__enter__.return_value.read.return_value = corpo
    resposta.__enter__.return_value.headers.get_content_charset.return_value = "utf-8"
    return resposta


class ParserTest(unittest.TestCase):
    def setUp(self):
        self.chamadas: list[str] = []
        self.detalhe = cliente(FIXTURE.read_text(encoding="utf-8"), self.chamadas).detalhar("4012345678")

    def test_chama_a_pagina_publica_da_vaga(self):
        self.assertEqual(self.chamadas, ["https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4012345678"])

    def test_descricao_inteira_ate_o_fim_do_bloco_mesmo_com_divs_aninhadas(self):
        descricao = self.detalhe.descricao
        self.assertTrue(descricao.startswith("Sobre a vaga\n"), descricao)
        self.assertIn("Parágrafo dentro de uma div aninhada.", descricao)
        self.assertIn("Mais um nível", descricao)
        # Depois de fechar as divs aninhadas o bloco continua até a div do markup fechar.
        self.assertTrue(descricao.endswith("Último parágrafo, depois das divs aninhadas."), descricao)
        self.assertIn("• SQL intermediário", descricao.splitlines())
        self.assertIn("• Python básico", descricao.splitlines())

    def test_nada_de_fora_do_bloco(self):
        for fora in ("Pessoa Analista", "não entra na descrição", "Exibir mais", "Nível de experiência", "Texto depois"):
            with self.subTest(fora=fora):
                self.assertNotIn(fora, self.detalhe.descricao)

    def test_script_some_e_entidade_e_decodificada_uma_vez_so(self):
        descricao = self.detalhe.descricao
        self.assertNotIn("isto não pode aparecer", descricao)
        self.assertNotIn("document.write", descricao)
        self.assertIn("dashboards & relatórios", descricao)
        self.assertIn("R$ 0,00", descricao)
        # `&lt;b&gt;` é texto: vira "<b>" literal (que a tela escapa), nunca uma tag que some.
        self.assertIn("o texto <b> não é tag", descricao)

    def test_so_a_descricao_e_texto_puro(self):
        self.assertEqual(self.detalhe.requisitos, [])
        self.assertIsNone(self.detalhe.salario)
        self.assertIsNone(self.detalhe.expirada_em)
        self.assertIsNone(self.detalhe.tem_prazo)
        self.assertNotIn("<strong>", self.detalhe.descricao)
        self.assertNotIn("<br", self.detalhe.descricao)

    def test_classe_tem_que_ser_a_palavra_inteira(self):
        # Escrito à mão: `...__markup-x` não é o bloco.
        with self.assertRaises(ErroProvider):
            extrair_descricao('<div class="show-more-less-html__markup-x">texto</div>')

    def test_so_o_primeiro_bloco(self):
        html = (
            '<div class="show-more-less-html__markup">primeiro</div>'
            '<div class="show-more-less-html__markup">segundo</div>'
        )
        self.assertEqual(cliente(html).detalhar("1").descricao, "primeiro")


class ErrosTest(unittest.TestCase):
    def test_id_invalido_e_value_error_sem_chamar_a_rede(self):
        chamadas: list[str] = []
        c = cliente(FIXTURE.read_text(encoding="utf-8"), chamadas)
        for lixo in ("", "12a", "1" * 21, "١٢٣", "123\n", " 123", "../1", None, 123):
            with self.subTest(id=lixo), self.assertRaises(ValueError):
                c.detalhar(lixo)
        self.assertEqual(chamadas, [])
        self.assertEqual(c.detalhar("1" * 20).descricao[:12], "Sobre a vaga")

    def test_pagina_sem_bloco_ou_com_descricao_vazia_e_erro_do_provider(self):
        # Escritos à mão: página sem o bloco, bloco vazio, bloco só com script/espaço/br.
        for html in (
            "<html><body>Faça login para ver a vaga</body></html>",
            "",
            '<div class="show-more-less-html__markup"></div>',
            '<div class="show-more-less-html__markup">  <br> <script>x()</script> </div>',
        ):
            with self.subTest(html=html), self.assertRaises(ErroProvider) as erro:
                cliente(html).detalhar("1")
            self.assertNotIsInstance(erro.exception, VagaNaoEncontrada)

    def test_status_http_viram_erro_do_provider_e_nunca_vaga_nao_encontrada(self):
        for codigo in (404, 410, 429, 500, 503):
            erro = urllib.error.HTTPError("u", codigo, "x", {}, None)
            with self.subTest(codigo=codigo), mock.patch("urllib.request.urlopen", side_effect=erro):
                with self.assertRaises(ErroProvider) as pego:
                    LinkedInDetalheClient().detalhar("1")
                self.assertNotIsInstance(pego.exception, VagaNaoEncontrada)
                self.assertIn(str(codigo), str(pego.exception))

    def test_rede_e_tempo_viram_erro_do_provider(self):
        for erro in (urllib.error.URLError("sem rede"), TimeoutError("demorou"), ConnectionResetError()):
            with self.subTest(erro=erro), mock.patch("urllib.request.urlopen", side_effect=erro):
                with self.assertRaises(ErroProvider):
                    LinkedInDetalheClient().detalhar("1")

    def test_corpo_gigante_e_erro_do_provider(self):
        with mock.patch("urllib.request.urlopen", return_value=resposta_http(b"x" * 2_000_001)):
            with self.assertRaises(ErroProvider):
                get_html_detalhe("https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/1")

    def test_requisicao_com_timeout_de_5_s_e_cabecalhos_de_navegador(self):
        html = FIXTURE.read_text(encoding="utf-8").encode()
        with mock.patch("urllib.request.urlopen", return_value=resposta_http(html)) as abrir:
            detalhe = LinkedInDetalheClient().detalhar("4012345678")
        self.assertIn("Sobre a vaga", detalhe.descricao)
        req = abrir.call_args.args[0]
        self.assertEqual(abrir.call_args.kwargs["timeout"], 5)
        self.assertEqual(req.full_url, "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4012345678")
        self.assertEqual(req.get_header("Accept-language"), "pt-BR")
        self.assertTrue(req.get_header("User-agent").startswith("Mozilla/5.0"))


class IdLinkedinTest(unittest.TestCase):
    def test_aceita_so_a_url_exata(self):
        self.assertEqual(id_linkedin("https://www.linkedin.com/jobs/view/4012345678"), "4012345678")
        for lixo in (
            None,
            5,
            "",
            "http://www.linkedin.com/jobs/view/4012345678",
            "https://linkedin.com/jobs/view/4012345678",
            "https://br.linkedin.com/jobs/view/4012345678",
            "https://www.linkedin.com.evil.test/jobs/view/4012345678",
            "https://www.linkedin.com@evil.test/jobs/view/4012345678",
            "https://evil.test/https://www.linkedin.com/jobs/view/4012345678",
            "https://www.linkedin.com/jobs/view/4012345678?trk=x",
            "https://www.linkedin.com/jobs/view/4012345678#x",
            "https://www.linkedin.com/jobs/view/4012345678/",
            "https://www.linkedin.com/jobs/view/40123a5678",
            "https://www.linkedin.com/jobs/view/analista-4012345678",
            "https://www.linkedin.com/jobs/view/",
            "https://www.linkedin.com/jobs/view/" + "1" * 21,
            "https://www.linkedin.com/jobs/view/١٢٣",
            "https://www.linkedin.com/jobs/view/4012345678\n",
        ):
            with self.subTest(url=lixo):
                self.assertIsNone(id_linkedin(lixo))

    def test_plataforma_e_a_que_a_coleta_grava(self):
        self.assertEqual(PLATAFORMA_LINKEDIN, ApifyLinkedInClient.nome)


class DetalheServiceLinkedinTest(unittest.TestCase):
    def setUp(self):
        self.repo = VagaEncontradaRepositoryMemoria()
        self.repo.registrar([vaga_linkedin(), vaga_remotar(123)], "dados")
        self.linkedin = ProviderFalso(DetalheVaga(descricao="Descrição do LinkedIn"))
        self.remotar = ProviderFalso()
        self.servico = DetalheService(self.repo, self.remotar, linkedin=self.linkedin)

    def test_abrir_diz_que_falta_carregar_sem_chamar_o_provider(self):
        aberta = self.servico.abrir(URL_LINKEDIN)
        self.assertTrue(aberta.carregar)
        self.assertEqual((self.linkedin.chamadas, self.remotar.chamadas), ([], []))

    def test_carregar_busca_no_linkedin_e_grava(self):
        aberta = self.servico.carregar(URL_LINKEDIN)
        self.assertEqual(self.linkedin.chamadas, ["4012345678"])
        self.assertEqual(self.remotar.chamadas, [])
        self.assertEqual(aberta.vaga.descricao, "Descrição do LinkedIn")
        self.assertIsNone(aberta.aviso)
        self.assertFalse(aberta.carregar)
        self.assertIsNotNone(self.repo.obter(URL_LINKEDIN).detalhada_em)
        # Gravado: abrir de novo não busca mais.
        self.servico.carregar(URL_LINKEDIN)
        self.assertEqual(self.linkedin.chamadas, ["4012345678"])

    def test_falha_vira_aviso_sem_marcar_expirada_e_tenta_de_novo_depois(self):
        self.linkedin.resultado = ErroProvider("HTTP 429")
        aberta = self.servico.carregar(URL_LINKEDIN)
        self.assertEqual(aberta.aviso, AVISO_SEM_DETALHE)
        self.assertFalse(aberta.expirada)
        lida = self.repo.obter(URL_LINKEDIN)
        self.assertIsNone(lida.expirada_em)
        self.assertIsNone(lida.detalhada_em)
        self.assertEqual(self.repo.contar(False), 2)
        self.servico.carregar(URL_LINKEDIN)
        self.assertEqual(len(self.linkedin.chamadas), 2)

    def test_remotar_continua_indo_so_para_o_remotar(self):
        self.servico.carregar("https://remotar.com.br/job/123")
        self.assertEqual(self.remotar.chamadas, ["123"])
        self.assertEqual(self.linkedin.chamadas, [])

    def test_sem_provider_do_linkedin_nada_sai_para_a_rede(self):
        servico = DetalheService(self.repo, self.remotar)
        self.assertFalse(servico.abrir(URL_LINKEDIN).carregar)
        self.assertIsNone(servico.carregar(URL_LINKEDIN).aviso)
        self.assertEqual((self.linkedin.chamadas, self.remotar.chamadas), ([], []))

    def test_url_do_linkedin_em_outra_plataforma_ou_url_torta_nao_chama_ninguem(self):
        tortas = [
            "https://www.linkedin.com/jobs/view/4012345678?trk=x",
            "https://br.linkedin.com/jobs/view/4012345678",
            "http://www.linkedin.com/jobs/view/4012345678",
        ]
        self.repo.registrar([vaga_linkedin(u) for u in tortas], "dados")
        self.repo.registrar([vaga_remotar(0, plataforma="Remotar", url="https://www.linkedin.com/jobs/view/1")], "dados")
        self.repo.registrar([vaga_remotar(0, plataforma=PLATAFORMA_LINKEDIN)], "dados")
        for url in tortas + ["https://www.linkedin.com/jobs/view/1", "https://remotar.com.br/job/0"]:
            with self.subTest(url=url):
                self.assertFalse(self.servico.abrir(url).carregar)
                self.servico.carregar(url)
        self.assertEqual((self.linkedin.chamadas, self.remotar.chamadas), ([], []))

    def test_404_do_linkedin_de_verdade_vira_aviso_e_nao_expira(self):
        # Pilha inteira com o cliente real: o 404 (ou 429) do LinkedIn não prova que a vaga fechou.
        servico = DetalheService(self.repo, linkedin=LinkedInDetalheClient())
        for codigo in (404, 429):
            erro = urllib.error.HTTPError("u", codigo, "x", {}, None)
            with self.subTest(codigo=codigo), mock.patch("urllib.request.urlopen", side_effect=erro) as abrir:
                aberta = servico.carregar(URL_LINKEDIN)
                self.assertEqual(abrir.call_count, 1)
            self.assertEqual(aberta.aviso, AVISO_SEM_DETALHE)
            self.assertFalse(aberta.expirada)
            self.assertIsNone(self.repo.obter(URL_LINKEDIN).expirada_em)


class TelaLinkedinTest(unittest.TestCase):
    def montar(self, provider):
        repo = VagaEncontradaRepositoryMemoria()
        repo.registrar([vaga_linkedin()], "dados")
        servico = DetalheService(repo, ProviderProibido(), linkedin=provider)
        app = criar_app(PerfilBuscaRepositoryMemoria(), CurriculoRepositoryMemoria(), BuscaService([]), encontradas=repo, detalhe=servico)
        return TestClient(app), repo

    def test_vaga_do_linkedin_sem_detalhe_traz_o_carregador_sem_chamar_o_provider(self):
        cliente_tela, _ = self.montar(ProviderProibido())
        rota = "/tela/vagas/detalhe?url=" + quote(URL_LINKEDIN, safe="")
        achado = carregador(cliente_tela.get(rota, headers=HX).text)
        self.assertIsNotNone(achado)
        hx_get, gatilho, _ = achado
        self.assertEqual(gatilho, "load")
        self.assertIn(quote(URL_LINKEDIN, safe="/"), hx_get)

    def test_secoes_carregam_a_descricao(self):
        cliente_tela, repo = self.montar(ProviderFalso(DetalheVaga(descricao="Descrição do LinkedIn")))
        resposta = cliente_tela.get("/tela/vagas/secoes", params={"url": URL_LINKEDIN}, headers=HX)
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Descrição do LinkedIn", resposta.text)
        self.assertIsNone(carregador(resposta.text))

    def test_falha_mostra_o_aviso_e_nao_expira(self):
        cliente_tela, repo = self.montar(ProviderFalso(ErroProvider("HTTP 429")))
        resposta = cliente_tela.get("/tela/vagas/secoes", params={"url": URL_LINKEDIN}, headers=HX)
        self.assertEqual(resposta.status_code, 200)
        self.assertIn(AVISO_SEM_DETALHE, resposta.text)
        self.assertNotIn("Vaga expirada", resposta.text)
        self.assertEqual(repo.contar(False), 1)


class ProducaoTest(unittest.TestCase):
    def test_app_de_producao_passa_o_cliente_do_linkedin(self):
        from src.controller import api

        with mock.patch.object(api, "criar_app") as criar:
            api.criar_app_producao()
        servico = criar.call_args.kwargs["detalhe"]
        self.assertIsInstance(servico.linkedin, LinkedInDetalheClient)


if __name__ == "__main__":
    unittest.main()
