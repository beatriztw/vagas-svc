"""Pausar e retomar o lote da IA ("Pausar" e "Retomar" na linha da IA da tela).

Repositório na memória e no Postgres de verdade (PGHOST), com o mesmo contrato; serviço e tela na memória.
Vagas, currículos e resultados daqui são inventados e escritos à mão (não são respostas gravadas).
"""

import os
import unittest

from src.model.pedido_ia import PRIORIDADE_LOTE, EstadoPedidoIA, TipoPedidoIA
from src.model.vaga_externa import VagaExterna
from src.repository.base import VagaEncontradaRepositoryMemoria
from src.repository.candidatura import vaga_por_url_em
from src.repository.pedido_ia import PedidoIARepositoryMemoria
from tests.test_coleta import _schema_descartavel, vaga
from tests.test_ia_lote_disparo import MUTACAO, Montagem as MontagemTela
from tests.test_pedido_ia_lote import URLS, Montagem as MontagemServico, compat
from tests.test_tela_lista import Pagina

U = [f"https://exemplo.com/pausa-{i}" for i in range(6)]


class ContratoPausa:
    """`self.repo` (PedidoIARepository) com as vagas `U` no histórico; `self.novo_repo()` relê do mesmo banco."""

    def novo_repo(self):
        return self.repo

    def test_comeca_sem_pausa_e_pausa_e_retoma(self):
        self.assertFalse(self.repo.lote_pausado())
        self.assertEqual(self.repo.pausar_lote(), 0)
        self.assertTrue(self.repo.lote_pausado())
        self.assertTrue(self.novo_repo().lote_pausado())  # a pausa fica gravada
        self.repo.pausar_lote()  # pausar de novo não quebra
        self.repo.retomar_lote()
        self.assertFalse(self.novo_repo().lote_pausado())

    def test_pausar_apaga_so_o_lote_pendente(self):
        lote = TipoPedidoIA.COMPATIBILIDADE
        da_pessoa, _ = self.repo.criar(U[3], lote)
        curriculo, _ = self.repo.criar(U[4], TipoPedidoIA.CURRICULO)
        l0, _ = self.repo.criar(U[0], lote, prioridade=PRIORIDADE_LOTE)
        l1, _ = self.repo.criar(U[1], lote, prioridade=PRIORIDADE_LOTE)
        l2, _ = self.repo.criar(U[2], lote, prioridade=PRIORIDADE_LOTE)
        # O worker pega os da pessoa (prioridade 0) e o lote mais antigo: ficam rodando.
        rodando = [self.repo.proximo() for _ in range(3)]
        self.assertEqual({p.id for p in rodando}, {da_pessoa.id, curriculo.id, l0.id})
        # O seguinte do lote termina.
        pronto = self.repo.proximo()
        self.assertEqual(pronto.id, l1.id)
        self.repo.concluir(pronto.id, pronto.tentativas, compat(70, "alta"), "compatibilidade-v1", "m", 70, "alta")
        # Pendentes que não são do lote ficam.
        pessoa_pendente, _ = self.repo.criar(U[5], lote)
        curriculo_pendente, _ = self.repo.criar(U[0], TipoPedidoIA.CURRICULO)

        self.assertEqual(self.repo.pausar_lote(), 1)

        self.assertIsNone(self.repo.obter(l2.id))
        for p in rodando:
            self.assertEqual(self.repo.obter(p.id).estado, EstadoPedidoIA.RODANDO)
        self.assertEqual(self.repo.obter(l1.id).estado, EstadoPedidoIA.PRONTO)
        self.assertEqual(self.repo.obter(pessoa_pendente.id).estado, EstadoPedidoIA.PENDENTE)
        self.assertEqual(self.repo.obter(curriculo_pendente.id).estado, EstadoPedidoIA.PENDENTE)


class PausaMemoriaTest(ContratoPausa, unittest.TestCase):
    def setUp(self):
        vagas = VagaEncontradaRepositoryMemoria()
        vagas.registrar([vaga(u) for u in U], "dados")
        self.repo = PedidoIARepositoryMemoria(vaga_por_url_em(vagas))


@unittest.skipUnless(os.environ.get("PGHOST"), "PGHOST não definido: integração com Postgres pulada")
class PausaPostgresTest(ContratoPausa, unittest.TestCase):
    def setUp(self):
        from src.repository.pedido_ia_postgres import PedidoIARepositoryPostgres
        from src.repository.postgres import VagaEncontradaRepositoryPostgres

        self.conn, _ = _schema_descartavel(self, "teste_pausa")
        VagaEncontradaRepositoryPostgres(self.conn).registrar(
            [VagaExterna(titulo="Analista", empresa="Acme", plataforma="Remotar", url=u) for u in U], "dados"
        )
        self.repo = PedidoIARepositoryPostgres(self.conn)

    def novo_repo(self):
        from src.repository.pedido_ia_postgres import PedidoIARepositoryPostgres

        return PedidoIARepositoryPostgres(self.conn)


class PausaServicoTest(unittest.TestCase):
    def setUp(self):
        self.m = MontagemServico()

    def test_pausado_o_lote_nao_enfileira_mas_o_clique_da_pessoa_sim(self):
        self.m.servico.enfileirar_lote(self.m.vagas(0, 1, 2))
        self.assertEqual(self.m.servico.pausar_lote(), 3)
        self.assertTrue(self.m.servico.lote_pausado())

        self.assertEqual(self.m.servico.enfileirar_lote(self.m.vagas(0, 1, 2, 3)), 0)
        self.assertEqual(self.m.pedidos.urls_ativas(TipoPedidoIA.COMPATIBILIDADE), set())
        _, criado = self.m.servico.pedir(URLS[4], TipoPedidoIA.COMPATIBILIDADE)
        self.assertTrue(criado)
        _, criado = self.m.servico.pedir(URLS[5], TipoPedidoIA.CURRICULO)
        self.assertTrue(criado)

    def test_retomar_tira_a_pausa_e_enfileira_as_sem_nota(self):
        self.m.servico.enfileirar_lote(self.m.vagas(0))
        self.m.responder()  # a vaga 0 já tem nota
        self.m.servico.pausar_lote()

        criados = self.m.servico.retomar_lote(self.m.vagas(0, 1, 2))

        self.assertEqual(criados, 2)
        self.assertFalse(self.m.servico.lote_pausado())
        self.assertEqual(self.m.pedidos.urls_ativas(TipoPedidoIA.COMPATIBILIDADE), {URLS[1], URLS[2]})


class PausaTelaTest(unittest.TestCase):
    def setUp(self):
        self.m = MontagemTela()
        self.m.encontradas.registrar([vaga("u1"), vaga("u2"), vaga("u3")], "python")

    def lote_ia(self, resposta) -> str:
        self.assertEqual(resposta.status_code, 200)
        return Pagina(resposta.text).por_id("lote-ia").texto

    def test_botao_pausar_ao_lado_do_avaliar(self):
        pagina = Pagina(self.m.cliente.get("/tela").text)
        botoes = {b.texto.strip(): b.attrs.get("hx-post") for b in pagina.tags("button")}
        self.assertEqual(botoes["Pausar"], "/tela/ia/lote/pausar")
        self.assertEqual(botoes["Avaliar 3 sem nota"], "/tela/ia/lote")
        self.assertNotIn("Retomar", botoes)

    def test_pausar_esvazia_a_fila_e_a_coleta_nao_enche_de_novo(self):
        self.m.cliente.post("/tela/ia/lote", headers=MUTACAO)
        self.assertEqual(len(self.m.lote()), 3)

        texto = self.lote_ia(self.m.cliente.post("/tela/ia/lote/pausar", headers=MUTACAO))

        self.assertIn("IA pausada · 0 avaliadas · 0 na fila", texto)
        self.assertIn("3 vagas saíram da fila", texto)
        self.assertIn("Retomar", texto)
        self.assertNotIn("Avaliar", texto)
        self.assertNotIn("Pausar", texto)
        self.assertEqual(self.m.lote(), set())
        # A coleta (pela tela e pela API) não manda vaga para a IA enquanto pausada.
        self.assertEqual(self.m.cliente.post("/tela/coletas", headers=MUTACAO).status_code, 200)
        self.assertEqual(self.m.cliente.post("/coletas", json={}).status_code, 200)
        self.assertEqual(self.m.lote(), set())
        # A página inteira mostra a pausa.
        self.assertIn("pausada", Pagina(self.m.cliente.get("/tela").text).por_id("lote-ia").texto)

    def test_retomar_volta_o_botao_e_enfileira(self):
        self.m.cliente.post("/tela/ia/lote/pausar", headers=MUTACAO)

        texto = self.lote_ia(self.m.cliente.post("/tela/ia/lote/retomar", headers=MUTACAO))

        self.assertNotIn("pausada", texto)
        self.assertIn("IA ligada · 0 avaliadas · 3 na fila", texto)
        self.assertIn("Pausar", texto)
        self.assertEqual(len(self.m.lote()), 3)

    def test_csrf(self):
        for rota in ("/tela/ia/lote/pausar", "/tela/ia/lote/retomar"):
            for cabecalhos in ({}, {"HX-Request": "true", "Origin": "https://outro.example"}):
                with self.subTest(rota=rota, cabecalhos=cabecalhos):
                    self.assertEqual(self.m.cliente.post(rota, headers=cabecalhos).status_code, 403)
        self.assertFalse(self.m.pedidos.lote_pausado())


if __name__ == "__main__":
    unittest.main()
