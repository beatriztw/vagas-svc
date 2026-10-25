"""O pedido de currículo busca antes a descrição que faltar na vaga.

O currículo é adaptado pelo texto da vaga, e no idioma dela (`curriculo-v3` do worker): sem a descrição,
a IA adaptaria só pelo título. As vagas e os detalhes daqui são escritos à mão; nenhum teste sai para a rede.
"""

import unittest

from tests.sessao import ClienteLogado

from src.client.remotar import ErroProvider
from src.controller.api import criar_app
from src.model.curriculo import Curriculo
from src.model.detalhe_vaga import DetalheVaga
from src.model.erros import DadoInvalido
from src.model.pedido_ia import TipoPedidoIA
from src.model.vaga_externa import VagaExterna
from src.repository.base import CurriculoRepositoryMemoria, PerfilBuscaRepositoryMemoria, VagaEncontradaRepositoryMemoria
from src.service.busca_service import BuscaService
from src.service.detalhe_service import DetalheService
from src.service.pedido_ia_service import MSG_SEM_DESCRICAO
from tests.test_detalhe import ProviderFalso, ProviderProibido
from tests.test_linkedin_detalhe import URL_LINKEDIN, vaga_linkedin
from tests.test_tela_ia import HX

DESCRICAO_EN = "We are looking for a Junior Data Analyst. Strong SQL and Excel skills required."
URL_TRAMPOS = "https://trampos.co/oportunidades/123"


class Montagem:
    def __init__(self, linkedin, com_detalhe: bool = True):
        self.encontradas = VagaEncontradaRepositoryMemoria(1)
        self.encontradas.registrar(
            [
                vaga_linkedin(),
                # Plataforma sem detalhe sob demanda: segue como antes, sem descrição.
                VagaExterna("Analista de Dados", "Empresa Fictícia", "Trampos", URL_TRAMPOS, remoto=True),
            ],
            "dados",
        )
        curriculos = CurriculoRepositoryMemoria(1)
        curriculos.salvar(Curriculo(tipo="base", titulo="Base", conteudo_md="# Base\nConsultas SQL"))
        detalhe = DetalheService(self.encontradas, ProviderProibido(), linkedin=linkedin) if com_detalhe else None
        self.app = criar_app(
            PerfilBuscaRepositoryMemoria(1), curriculos, BuscaService([]), encontradas=self.encontradas, detalhe=detalhe
        )
        self.servico = self.app.state.pedido_ia_service
        self.cliente = ClienteLogado(self.app)


class PedirCurriculoTest(unittest.TestCase):
    def test_vaga_sem_descricao_carrega_antes_e_o_worker_recebe_o_texto_como_veio(self):
        linkedin = ProviderFalso(DetalheVaga(descricao=DESCRICAO_EN))
        m = Montagem(linkedin)

        pedido, criado = m.servico.pedir(URL_LINKEDIN, TipoPedidoIA.CURRICULO)

        self.assertTrue(criado)
        self.assertEqual(linkedin.chamadas, ["4012345678"])
        self.assertEqual(m.encontradas.obter(URL_LINKEDIN).descricao, DESCRICAO_EN)
        # O pedido que o worker recebe já leva a descrição, sem tradução.
        self.assertEqual(m.servico.proximo()["vaga"]["descricao"], DESCRICAO_EN)

    def test_falha_do_provider_recusa_o_pedido_sem_criar_nada(self):
        m = Montagem(ProviderFalso(ErroProvider("HTTP 429")))

        with self.assertRaises(DadoInvalido) as erro:
            m.servico.pedir(URL_LINKEDIN, TipoPedidoIA.CURRICULO)

        self.assertEqual(str(erro.exception), MSG_SEM_DESCRICAO)
        self.assertIsNone(m.servico.proximo())
        # Falha do LinkedIn nunca expira a vaga: a próxima tentativa busca de novo.
        self.assertIsNone(m.encontradas.obter(URL_LINKEDIN).expirada_em)

    def test_vaga_ja_detalhada_nao_chama_o_provider(self):
        linkedin = ProviderFalso(DetalheVaga(descricao=DESCRICAO_EN))
        m = Montagem(linkedin)
        m.servico.pedir(URL_LINKEDIN, TipoPedidoIA.CURRICULO)

        m.servico.pedir(URL_LINKEDIN, TipoPedidoIA.CURRICULO)

        self.assertEqual(len(linkedin.chamadas), 1)

    def test_plataforma_sem_detalhe_sob_demanda_segue_como_antes(self):
        m = Montagem(ProviderProibido())

        _, criado = m.servico.pedir(URL_TRAMPOS, TipoPedidoIA.CURRICULO)

        self.assertTrue(criado)

    def test_compatibilidade_nao_busca_a_descricao(self):
        # O lote da compatibilidade passa por todas as vagas: buscar cada detalhe estouraria o limite do LinkedIn.
        m = Montagem(ProviderProibido())

        _, criado = m.servico.pedir(URL_LINKEDIN, TipoPedidoIA.COMPATIBILIDADE)

        self.assertTrue(criado)

    def test_sem_detalhe_service_nada_sai_para_a_rede(self):
        m = Montagem(ProviderProibido(), com_detalhe=False)

        _, criado = m.servico.pedir(URL_LINKEDIN, TipoPedidoIA.CURRICULO)

        self.assertTrue(criado)


class TelaPedirCurriculoTest(unittest.TestCase):
    def test_falha_vira_o_aviso_422_da_tela(self):
        m = Montagem(ProviderFalso(ErroProvider("HTTP 429")))

        resposta = m.cliente.post("/tela/vagas/ia", data={"vaga_url": URL_LINKEDIN, "tipo": "curriculo"}, headers=HX)

        self.assertEqual(resposta.status_code, 422)
        self.assertIn("Abra a vaga de novo mais tarde", resposta.text)

    def test_sucesso_enfileira(self):
        m = Montagem(ProviderFalso(DetalheVaga(descricao=DESCRICAO_EN)))

        resposta = m.cliente.post("/tela/vagas/ia", data={"vaga_url": URL_LINKEDIN, "tipo": "curriculo"}, headers=HX)

        self.assertEqual(resposta.status_code, 202)


if __name__ == "__main__":
    unittest.main()
