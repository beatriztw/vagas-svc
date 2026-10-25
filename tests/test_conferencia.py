"""Conferência diária na fonte: a situação de cada vaga no Remotar e no LinkedIn, e o serviço que marca as fechadas.

Respostas e páginas daqui são escritas à mão (não são respostas gravadas de provider). A página fechada do
LinkedIn imita a marcação vista em vagas fechadas de verdade em 24/10/2026 (`closed-job__flavor--closed`).
"""

import threading
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from unittest import mock

from fastapi.testclient import TestClient

from src.client.linkedin_detalhe import LimiteDeRequisicoes, LinkedInDetalheClient, get_html_detalhe
from src.client.remotar import ErroProvider, RemotarClient, VagaNaoEncontrada
from src.controller.api import criar_app
from src.model.situacao_vaga import SituacaoNaFonte
from src.model.vaga_externa import VagaExterna
from src.repository.base import CurriculoRepositoryMemoria, PerfilBuscaRepositoryMemoria, VagaEncontradaRepositoryMemoria
from src.service.busca_service import BuscaService
from src.service.conferencia_service import ConferenciaDasPessoas, ConferenciaService

AGORA = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
ABERTA_NO_REMOTAR = {"id": 1, "title": "Analista", "active": True, "expired": False, "expires": False}

PAGINA_FECHADA = (
    '<section><h2 class="top-card-layout__title">Analista</h2>'
    '<figure class="closed-job closed-job__flavor topcard__flavor-row">'
    '<figcaption class="closed-job__flavor--closed">Não aceita mais candidaturas</figcaption></figure>'
    '<div class="show-more-less-html__markup">Descrição antiga da vaga.</div></section>'
)
PAGINA_ABERTA = '<section><div class="show-more-less-html__markup">Conciliar notas fiscais.</div></section>'


def remotar(resposta=None, erro=None, gupy=None):
    def http_detalhe(url):
        if erro is not None:
            raise erro
        return resposta

    return RemotarClient(http_detalhe=http_detalhe, relogio=lambda: AGORA, gupy=gupy)


class SituacaoNoRemotarTest(unittest.TestCase):
    def test_encerrada_inativa_ou_404_e_fechada(self):
        for resposta, erro in (
            ({**ABERTA_NO_REMOTAR, "expired": True}, None),
            ({**ABERTA_NO_REMOTAR, "active": False}, None),
            (None, VagaNaoEncontrada("x")),
        ):
            with self.subTest(resposta=resposta, erro=erro):
                self.assertEqual(remotar(resposta, erro).situacao("158648"), SituacaoNaFonte.FECHADA)

    def test_ativa_e_nao_expirada_e_aberta(self):
        self.assertEqual(remotar(ABERTA_NO_REMOTAR).situacao("1"), SituacaoNaFonte.ABERTA)

    def test_falha_ou_resposta_torta_nunca_fecha(self):
        for resposta, erro in ((None, ErroProvider("HTTP 500")), ([1, 2], None), ({"title": "sem os campos"}, None)):
            with self.subTest(resposta=resposta, erro=erro):
                self.assertEqual(remotar(resposta, erro).situacao("1"), SituacaoNaFonte.DESCONHECIDA)

    def test_encerrada_no_gupy_e_fechada_mesmo_ativa_no_remotar(self):
        job = {**ABERTA_NO_REMOTAR, "externalLink": "https://empresa.gupy.io/jobs/123"}
        fechou = remotar(job, gupy=lambda link: AGORA - timedelta(days=1))
        self.assertEqual(fechou.situacao("1"), SituacaoNaFonte.FECHADA)
        ainda_vai_fechar = remotar(job, gupy=lambda link: AGORA + timedelta(days=3))
        self.assertEqual(ainda_vai_fechar.situacao("1"), SituacaoNaFonte.ABERTA)

    def test_id_que_nao_e_so_digitos_e_recusado(self):
        with self.assertRaises(ValueError):
            remotar(ABERTA_NO_REMOTAR).situacao("1/../2")


class SituacaoNoLinkedInTest(unittest.TestCase):
    def cliente(self, pagina=None, erro=None):
        def http(url):
            if erro is not None:
                raise erro
            return pagina

        return LinkedInDetalheClient(http=http)

    def test_pagina_que_diz_que_nao_aceita_mais_e_fechada(self):
        self.assertEqual(self.cliente(PAGINA_FECHADA).situacao("4479566047"), SituacaoNaFonte.FECHADA)

    def test_pagina_normal_com_descricao_e_aberta(self):
        self.assertEqual(self.cliente(PAGINA_ABERTA).situacao("1"), SituacaoNaFonte.ABERTA)

    def test_429_e_bloqueada_e_404_nao_prova_que_fechou(self):
        self.assertEqual(self.cliente(erro=LimiteDeRequisicoes("HTTP 429")).situacao("1"), SituacaoNaFonte.BLOQUEADA)
        self.assertEqual(self.cliente(erro=ErroProvider("HTTP 404")).situacao("1"), SituacaoNaFonte.DESCONHECIDA)
        # Página sem descrição e sem o aviso (login, captcha): não dá para saber.
        self.assertEqual(self.cliente("<html>entre para continuar</html>").situacao("1"), SituacaoNaFonte.DESCONHECIDA)

    def test_get_html_detalhe_separa_o_429_dos_outros_erros(self):
        for codigo, esperado in ((429, LimiteDeRequisicoes), (404, ErroProvider)):
            erro = urllib.error.HTTPError("https://x", codigo, "erro", {}, None)
            with self.subTest(codigo=codigo), mock.patch("urllib.request.urlopen", side_effect=erro):
                with self.assertRaises(esperado) as pego:
                    get_html_detalhe("https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/1")
                self.assertIs(type(pego.exception), esperado)


def vaga(plataforma: str, url: str) -> VagaExterna:
    return VagaExterna("Analista", "Empresa Teste", plataforma, url)


class FonteFalsa:
    def __init__(self, situacoes: dict[str, SituacaoNaFonte] | None = None, padrao=SituacaoNaFonte.ABERTA, erro_em=()):
        self.situacoes, self.padrao, self.erro_em, self.chamadas = situacoes or {}, padrao, set(erro_em), []

    def situacao(self, id: str) -> SituacaoNaFonte:
        self.chamadas.append(id)
        if id in self.erro_em:
            raise RuntimeError("falha inesperada")
        return self.situacoes.get(id, self.padrao)


class ConferenciaServiceTest(unittest.TestCase):
    def setUp(self):
        self.repo = VagaEncontradaRepositoryMemoria(1, relogio=lambda: AGORA)
        self.repo.registrar(
            [
                vaga("Remotar", "https://remotar.com.br/job/1"),
                vaga("Remotar", "https://remotar.com.br/job/2"),
                vaga("Remotar", "https://remotar.com.br/job/3"),
                vaga("LinkedIn (Apify)", "https://www.linkedin.com/jobs/view/10"),
                vaga("LinkedIn (Apify)", "https://www.linkedin.com/jobs/view/11"),
                vaga("Himalayas", "https://himalayas.app/companies/x/jobs/y"),
            ],
            "dados",
        )
        self.pausas = []

    def servico(self, remotar, linkedin):
        return ConferenciaService(self.repo, remotar, linkedin, dormir=self.pausas.append, pausa_s={"Remotar": 0.2, "LinkedIn (Apify)": 2.0})

    def no_ar(self) -> set[str]:
        return {v.url for v in self.repo.listar_recentes(somente_novas=False, teto=100)}

    def test_marca_so_as_fechadas_e_deixa_o_resto_como_esta(self):
        remotar = FonteFalsa({"1": SituacaoNaFonte.FECHADA, "3": SituacaoNaFonte.DESCONHECIDA})
        linkedin = FonteFalsa({"11": SituacaoNaFonte.FECHADA})

        resultado = self.servico(remotar, linkedin).conferir()

        self.assertEqual(
            self.no_ar(),
            {"https://remotar.com.br/job/2", "https://remotar.com.br/job/3", "https://www.linkedin.com/jobs/view/10",
             "https://himalayas.app/companies/x/jobs/y"},
        )
        self.assertEqual(resultado.fechadas, 2)
        self.assertEqual(resultado.situacoes, {"fechada": 2, "aberta": 2, "desconhecida": 1})
        # Himalayas não tem consulta: não entra na conta nem na pausa.
        self.assertEqual(sorted(self.pausas), [0.2, 0.2, 0.2, 2.0, 2.0])

    def test_vaga_ja_encerrada_nao_e_consultada_de_novo(self):
        self.repo.marcar_expirada("https://remotar.com.br/job/1")
        remotar = FonteFalsa()
        self.servico(remotar, FonteFalsa()).conferir()
        self.assertEqual(sorted(remotar.chamadas), ["2", "3"])

    def test_429_para_a_fonte_e_as_outras_seguem(self):
        linkedin = FonteFalsa(padrao=SituacaoNaFonte.BLOQUEADA)
        remotar = FonteFalsa({"2": SituacaoNaFonte.FECHADA})

        resultado = self.servico(remotar, linkedin).conferir()

        self.assertEqual(len(linkedin.chamadas), 1)  # a segunda vaga do LinkedIn fica para a próxima
        self.assertEqual(resultado.bloqueadas, {"LinkedIn (Apify)"})
        self.assertEqual(len(remotar.chamadas), 3)
        self.assertNotIn("https://remotar.com.br/job/2", self.no_ar())

    def test_falha_numa_vaga_nao_para_a_conferencia(self):
        remotar = FonteFalsa({"3": SituacaoNaFonte.FECHADA}, erro_em={"1"})
        resultado = self.servico(remotar, FonteFalsa()).conferir()
        self.assertEqual(resultado.situacoes["desconhecida"], 1)
        self.assertNotIn("https://remotar.com.br/job/3", self.no_ar())

    def test_uma_conferencia_por_vez(self):
        dentro, solta = threading.Event(), threading.Event()

        class Lenta(FonteFalsa):
            def situacao(self, id):
                dentro.set()
                solta.wait(5)
                return super().situacao(id)

        servico = self.servico(Lenta(), FonteFalsa())
        primeira = servico.conferir_em_segundo_plano()
        self.assertTrue(dentro.wait(5))
        self.assertIsNone(servico.conferir())  # a segunda não entra enquanto a primeira roda
        solta.set()
        primeira.join(5)
        self.assertIsNotNone(servico.conferir())


class LigacaoNaAppTest(unittest.TestCase):
    """A conferência roda quando a app sobe e depois de cada coleta agendada (mesmo se a coleta falhar)."""

    def test_roda_na_subida_e_depois_da_coleta_agendada(self):
        chamadas, recebido = threading.Semaphore(0), {}

        class Conferencia:
            def conferir(self):
                chamadas.release()

        historicos = []
        app = criar_app(
            PerfilBuscaRepositoryMemoria(1), CurriculoRepositoryMemoria(1), BuscaService([]),
            # Guarda a coleta agendada sem criar o agendador (nenhuma thread de coleta nos testes).
            agendador=lambda coletar: recebido.update(coletar=coletar),
            conferencia=lambda historico: historicos.append(historico) or Conferencia(),
        )
        with TestClient(app):
            self.assertTrue(chamadas.acquire(timeout=5))
        self.assertEqual(len(historicos), 1)  # o histórico da única pessoa (o mesmo da coleta)
        # Sem perfil a coleta levanta (DadoInvalido); a conferência roda assim mesmo.
        with self.assertRaises(Exception):
            recebido["coletar"]()
        self.assertTrue(chamadas.acquire(timeout=5))
        self.assertEqual(len(historicos), 1)  # a conferência da pessoa é reaproveitada

    def test_sem_conferencia_nada_e_reconsultado(self):
        app = criar_app(PerfilBuscaRepositoryMemoria(1), CurriculoRepositoryMemoria(1), BuscaService([]))
        with TestClient(app) as cliente:
            self.assertEqual(cliente.get("/saude").status_code, 200)
        self.assertIsNone(app.state.conferencia)

    def test_vaga_fechada_na_fonte_avisa_na_candidatura_sem_mudar_o_status(self):
        encontradas = VagaEncontradaRepositoryMemoria(1)
        url = "https://remotar.com.br/job/158648"
        encontradas.registrar([vaga("Remotar", url)], "dados")
        app = criar_app(PerfilBuscaRepositoryMemoria(1), CurriculoRepositoryMemoria(1), BuscaService([]), encontradas=encontradas)
        candidatura, _ = app.state.candidaturas.marcar_aplicada(url)

        encerrada = remotar({**ABERTA_NO_REMOTAR, "expired": True})
        ConferenciaService(encontradas, encerrada, None, dormir=lambda s: None).conferir()

        html = TestClient(app).get("/tela/candidaturas", headers={"HX-Request": "true"}).text
        self.assertIn("A vaga saiu do ar.", html)
        self.assertEqual(app.state.candidaturas.obter(candidatura.id).status.value, "enviada")


class ConferenciaDasPessoasTest(unittest.TestCase):
    """Cada pessoa tem as próprias vagas: a mesma URL fechada na fonte sai do histórico de cada uma."""

    def setUp(self):
        self.url = "https://remotar.com.br/job/7"
        self.vagas = {p: VagaEncontradaRepositoryMemoria(p, relogio=lambda: AGORA) for p in (1, 2)}
        self.vagas[2].registrar([vaga("Remotar", self.url)], "dados")
        self.vagas[2].registrar([vaga("Remotar", "https://remotar.com.br/job/8")], "dados")
        self.fonte = FonteFalsa({"7": SituacaoNaFonte.FECHADA})

    def conferencia(self, quebrada=()):
        def da_pessoa(pessoa_id):
            if pessoa_id in quebrada:
                raise RuntimeError("banco fora")
            return ConferenciaService(self.vagas[pessoa_id], self.fonte, None, dormir=lambda s: None)

        return ConferenciaDasPessoas(lambda: [1, 2], da_pessoa)

    def no_ar(self, pessoa_id):
        return {v.url for v in self.vagas[pessoa_id].listar_recentes(somente_novas=False, teto=100)}

    def test_confere_o_historico_de_cada_pessoa(self):
        resultados = self.conferencia().conferir()

        self.assertEqual(self.no_ar(2), {"https://remotar.com.br/job/8"})  # a fechada saiu do histórico da pessoa 2
        self.assertEqual(resultados[2].fechadas, 1)

    def test_falha_numa_pessoa_nao_para_as_outras(self):
        resultados = self.conferencia(quebrada={1}).conferir()

        self.assertNotIn(1, resultados)
        self.assertEqual(self.no_ar(2), {"https://remotar.com.br/job/8"})


if __name__ == "__main__":
    unittest.main()
