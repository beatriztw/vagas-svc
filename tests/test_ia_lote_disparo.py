"""Quem dispara o lote da IA: o fim de cada coleta (API, tela, CLI) e o botão da tela.

Vagas e currículos daqui são inventados e escritos à mão (não são respostas gravadas).
"""

import io
import unittest

from fastapi.testclient import TestClient

from src.controller.api import criar_app
from src.controller.coleta import enfileirar_lote, rodar
from src.model.curriculo import Curriculo
from src.model.perfil import Perfil
from src.repository.base import (
    CurriculoRepositoryMemoria,
    PerfilBuscaRepositoryMemoria,
    VagaEncontradaRepositoryMemoria,
)
from src.repository.candidatura import vaga_por_url_em
from src.repository.pedido_ia import PedidoIARepositoryMemoria
from src.service.busca_service import BuscaService
from src.service.coleta_service import ColetaService
from src.service.recomendacao_service import RecomendacaoService
from tests.test_busca_service import ClientQuebrado
from tests.test_coleta import ClientPorTermo, vaga
from tests.test_tela_lista import HX, Pagina

MUTACAO = {"HX-Request": "true", "Origin": "http://testserver"}


class Montagem:
    def __init__(self, com_base: bool = True):
        self.perfis = PerfilBuscaRepositoryMemoria()
        self.perfis.salvar(Perfil(termos=["python"]))
        self.encontradas = VagaEncontradaRepositoryMemoria()
        self.curriculos = CurriculoRepositoryMemoria()
        if com_base:
            self.curriculos.salvar(Curriculo(tipo="base", titulo="Base", conteudo_md="# Base"))
        self.pedidos = PedidoIARepositoryMemoria(vaga_por_url_em(self.encontradas))
        client = ClientPorTermo("A", {"python": [vaga("u1"), vaga("u2")]})
        self.app = criar_app(
            self.perfis,
            self.curriculos,
            BuscaService([client]),
            encontradas=self.encontradas,
            pedidos_ia=self.pedidos,
        )
        self.cliente = TestClient(self.app)

    def lote(self) -> set[str]:
        return {
            url
            for url in self.pedidos.urls_ativas("compatibilidade")
            if self.pedidos.ultimo(url, "compatibilidade").prioridade == 1
        }


class PosColetaTest(unittest.TestCase):
    def test_ao_terminar_roda_depois_da_coleta_com_o_resultado(self):
        perfis, vagas = PerfilBuscaRepositoryMemoria(), VagaEncontradaRepositoryMemoria()
        perfis.salvar(Perfil(termos=["python"]))
        vistos = []
        servico = ColetaService(
            RecomendacaoService(BuscaService([ClientPorTermo("A", {"python": [vaga("u1")]})])),
            perfis,
            vagas,
            ao_terminar=lambda r: vistos.append((r.novas, len(vagas.listar(somente_novas=False, limite=10)))),
        )
        servico.coletar()
        self.assertEqual(vistos, [(1, 1)])  # já com a coleta gravada

    def test_falha_no_pos_coleta_nao_derruba_a_coleta(self):
        perfis, vagas = PerfilBuscaRepositoryMemoria(), VagaEncontradaRepositoryMemoria()
        perfis.salvar(Perfil(termos=["python"]))

        def quebra(_):
            raise RuntimeError("fila fora do ar")

        servico = ColetaService(
            RecomendacaoService(BuscaService([ClientPorTermo("A", {"python": [vaga("u1")]})])),
            perfis,
            vagas,
            ao_terminar=quebra,
        )
        with self.assertLogs("src.service.coleta_service", "WARNING") as logs:
            resultado = servico.coletar()
        self.assertEqual(resultado.novas, 1)
        self.assertIn("RuntimeError", logs.output[0])
        self.assertNotIn("fila fora do ar", logs.output[0])

    def test_coleta_pela_api_enfileira_o_lote_so_com_base(self):
        m = Montagem()
        self.assertEqual(m.cliente.post("/coletas", json={}).status_code, 200)
        self.assertEqual(m.lote(), {"u1", "u2"})
        # Sem base a coleta segue igual e nada vai para a fila.
        sem_base = Montagem(com_base=False)
        self.assertEqual(sem_base.cliente.post("/coletas", json={}).status_code, 200)
        self.assertEqual(sem_base.lote(), set())

    def test_coleta_pela_tela_enfileira_o_lote(self):
        m = Montagem()
        self.assertEqual(m.cliente.post("/tela/coletas", headers=MUTACAO).status_code, 200)
        self.assertEqual(len(m.lote()), 2)

    def _servico(self, providers):
        perfis, vagas = PerfilBuscaRepositoryMemoria(), VagaEncontradaRepositoryMemoria()
        perfis.salvar(Perfil(termos=["python", "sql"]))
        return ColetaService(RecomendacaoService(BuscaService(providers)), perfis, vagas)

    def test_cli_imprime_a_fila_depois_das_linhas_dos_termos_no_stream_dado(self):
        class IA:
            def enfileirar_lote(self, candidatas):
                return len(list(candidatas))

        class Historico:
            def visiveis(self):
                return ["a", "b", "c"]

        saida, erro = io.StringIO(), io.StringIO()
        servico = self._servico([ClientPorTermo("A", {"python": [vaga("u1")], "sql": [vaga("u2")]})])

        codigo = rodar(lambda: servico, saida, erro, pos_coleta=lambda: enfileirar_lote(IA(), Historico()))

        self.assertEqual(codigo, 0)
        linhas = saida.getvalue().splitlines()
        self.assertEqual(linhas[-1], "ia: 3 vagas na fila")
        self.assertEqual(len(linhas), 3)  # dois termos e, por último, a fila
        self.assertEqual(erro.getvalue(), "")

    def test_cli_com_a_coleta_falhando_nao_roda_o_lote(self):
        chamadas = []
        saida, erro = io.StringIO(), io.StringIO()
        servico = self._servico([ClientQuebrado()])

        self.assertEqual(rodar(lambda: servico, saida, erro, pos_coleta=lambda: chamadas.append(1) or 1), 1)
        self.assertEqual(chamadas, [])
        self.assertNotIn("ia:", saida.getvalue())

    def test_lote_que_falha_vai_para_o_stderr_e_a_cli_sai_com_0(self):
        def quebra():
            raise RuntimeError("banco fora")

        saida, erro = io.StringIO(), io.StringIO()
        servico = self._servico([ClientPorTermo("A", {"python": [vaga("u1")]})])

        self.assertEqual(rodar(lambda: servico, saida, erro, pos_coleta=quebra), 0)
        self.assertEqual(erro.getvalue().strip(), "ia: lote falhou: RuntimeError")

    def test_servico_nao_chama_o_pos_coleta_quando_a_coleta_falhou(self):
        chamadas = []
        servico = self._servico([ClientQuebrado()])
        servico.ao_terminar = chamadas.append
        self.assertTrue(servico.coletar().falhou)
        self.assertEqual(chamadas, [])


class BotaoDaTelaTest(unittest.TestCase):
    def setUp(self):
        self.m = Montagem()
        self.m.encontradas.registrar([vaga("u1"), vaga("u2"), vaga("u3")], "python")

    def test_indicador_e_botao_na_barra_da_lista(self):
        pagina = Pagina(self.m.cliente.get("/tela").text)
        lote = pagina.por_id("lote-ia")
        self.assertIn("IA: 0 avaliadas · 0 na fila", lote.texto)
        botao = next(b for b in pagina.tags("button") if b.texto.strip() == "Avaliar vagas com IA")
        self.assertEqual(botao.attrs["hx-post"], "/tela/ia/lote")
        # O "carregar mais" não repete a barra.
        self.assertNotIn("lote-ia", self.m.cliente.get("/tela/vagas", params={"deslocamento": "1"}, headers=HX).text)

    def test_botao_enfileira_e_mostra_quantas(self):
        resposta = self.m.cliente.post("/tela/ia/lote", headers=MUTACAO)

        self.assertEqual(resposta.status_code, 200)
        texto = Pagina(resposta.text).por_id("lote-ia").texto
        self.assertIn("3 vagas na fila da IA", texto)
        self.assertIn("0 avaliadas · 3 na fila", texto)
        self.assertEqual(len(self.m.lote()), 3)
        # De novo: nada novo, sem duplicar.
        self.assertIn("0 vagas na fila da IA", self.m.cliente.post("/tela/ia/lote", headers=MUTACAO).text)

    def test_csrf_sem_hx_request_ou_de_outra_origem_e_403_e_nao_enfileira(self):
        for cabecalhos in ({}, {"HX-Request": "true", "Origin": "https://outro.example"}, {"Origin": "http://testserver"}):
            with self.subTest(cabecalhos=cabecalhos):
                self.assertEqual(self.m.cliente.post("/tela/ia/lote", headers=cabecalhos).status_code, 403)
        self.assertIn(self.m.cliente.get("/tela/ia/lote").status_code, (404, 405))  # GET nunca enfileira
        self.assertEqual(self.m.lote(), set())
        # A mesma rota com o CSRF certo existe e enfileira (senão o 403 acima não provaria nada).
        self.assertEqual(self.m.cliente.post("/tela/ia/lote", headers=MUTACAO).status_code, 200)

    def test_sem_base_mostra_o_aviso_e_nao_o_botao(self):
        m = Montagem(com_base=False)
        m.encontradas.registrar([vaga("u1")], "python")
        pagina = Pagina(m.cliente.get("/tela").text)
        self.assertIn("Salve um currículo base", pagina.por_id("lote-ia").texto)
        self.assertFalse([b for b in pagina.tags("button") if "Avaliar vagas" in b.texto])



if __name__ == "__main__":
    unittest.main()
