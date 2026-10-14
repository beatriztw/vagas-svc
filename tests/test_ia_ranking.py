"""Ranking da lista pela nota da IA: ordem padrão com nota, corte por faixa, chip no card e as urls da tela.

Vagas, currículos e notas daqui são inventados e escritos à mão (não são respostas gravadas).
"""

import os
import unittest
from datetime import date
from urllib.parse import parse_qs, quote, urlsplit

from fastapi.testclient import TestClient

from src.controller.api import criar_app
from src.model.curriculo import Curriculo
from src.model.erros import DadoInvalido
from src.model.pedido_ia import Faixa, NotaIA
from src.model.perfil import Perfil
from src.model.vaga import Modalidade
from src.model.vaga_externa import VagaExterna
from src.repository.base import (
    CurriculoRepositoryMemoria,
    PerfilBuscaRepositoryMemoria,
    VagaEncontradaRepositoryMemoria,
)
from src.repository.candidatura import vaga_por_url_em
from src.repository.pedido_ia import PedidoIARepositoryMemoria
from src.service.busca_service import BuscaService
from src.service.historico_service import (
    FaixaFiltro,
    HistoricoService,
    Ordem,
    ordenar_por_nota,
    validar_faixa,
    validar_ordem,
)
from tests.test_tela_lista import HX, URLS_DIFICEIS, Pagina

HOJE = date(2026, 10, 12)
PERFIL = Perfil(termos=["analista de dados"], skills=["sql"], modalidades=[Modalidade.REMOTO])


def v(url: str, titulo: str, remoto: bool = True) -> VagaExterna:
    return VagaExterna(titulo, "Empresa Teste", "Remotar", url, remoto=remoto, local="Remoto")


# Ordem por match com PERFIL: título com o termo e com a skill primeiro.
VAGAS = [
    v("m1", "Analista de Dados SQL"),
    v("m2", "Analista de Dados"),
    v("m3", "Analista Financeiro"),
    v("m4", "Assistente Administrativo"),
    v("m5", "Recepcionista"),
]
ORDEM_MATCH = ["m1", "m2", "m3", "m4", "m5"]


def nota(n: int) -> NotaIA:
    return NotaIA(n, Faixa.ALTA if n >= 70 else Faixa.MEDIA if n >= 40 else Faixa.BAIXA)


NOTAS = {"m4": nota(90), "m2": nota(55), "m5": nota(55), "m3": nota(20)}
# m4 (90), depois m2 e m5 (55, empate na ordem do match), m3 (20), e a sem nota (m1) no fim.
ORDEM_IA = ["m4", "m2", "m5", "m3", "m1"]


def urls(vagas) -> list[str]:
    return [x.url for x in vagas]


def chips_por_card(pagina: Pagina) -> dict[str, list]:
    """Os chips de IA de cada card (o Pagina é plano: o chip vem depois do <a> do card, antes do próximo <li>)."""
    chips, atual = {}, None
    for e in pagina.elementos:
        if e.tag == "li":
            atual = None
        elif e.tag == "a" and "card" in (e.attrs.get("class") or "").split():
            atual = e.attrs["data-url"]
            chips[atual] = []
        elif atual and e.tag == "span" and "chip-ia" in (e.attrs.get("class") or "").split():
            chips[atual].append(e)
    return chips


class OrdenarTest(unittest.TestCase):
    def setUp(self):
        self.repo = VagaEncontradaRepositoryMemoria()
        self.repo.registrar(VAGAS, "dados")
        self.perfis = PerfilBuscaRepositoryMemoria()
        self.perfis.salvar(PERFIL)
        self.servico = HistoricoService(self.repo, self.perfis)

    def pagina(self, **kwargs):
        kwargs.setdefault("limite", 500)
        return self.servico.pagina(False, hoje=HOJE, **kwargs)

    def test_ordenar_por_nota_mantem_a_ordem_que_veio_no_empate_e_nas_sem_nota(self):
        vagas = [self.repo.obter(u) for u in ORDEM_MATCH]
        self.assertEqual(urls(ordenar_por_nota(vagas, NOTAS)), ORDEM_IA)
        self.assertEqual(urls(ordenar_por_nota(vagas, {})), ORDEM_MATCH)

    def test_ia_e_a_ordem_padrao_quando_ha_nota_e_perfil_quando_nao_ha(self):
        com = self.pagina(notas=NOTAS)
        self.assertEqual((urls(com.vagas), com.ordem), (ORDEM_IA, Ordem.IA))
        sem = self.pagina(notas={})
        self.assertEqual((urls(sem.vagas), sem.ordem), (ORDEM_MATCH, Ordem.PERFIL))
        forcada = self.pagina(notas=NOTAS, ordem=Ordem.PERFIL)
        self.assertEqual((urls(forcada.vagas), forcada.ordem), (ORDEM_MATCH, Ordem.PERFIL))

    def test_faixa_mostra_so_aquela_faixa_sem_as_sem_nota_e_o_total_acompanha(self):
        alta = self.pagina(notas=NOTAS, faixa=FaixaFiltro.ALTA)
        self.assertEqual((urls(alta.vagas), alta.total), (["m4"], 1))
        # Só as médias: a alta (m4) não entra, senão "Média" abriria com as altas no topo.
        media = self.pagina(notas=NOTAS, faixa=FaixaFiltro.MEDIA)
        self.assertEqual((urls(media.vagas), media.total), (["m2", "m5"], 2))
        baixa = self.pagina(notas=NOTAS, faixa=FaixaFiltro.BAIXA)
        self.assertEqual((urls(baixa.vagas), baixa.total), (["m3"], 1))
        # Com a ordem do perfil, o filtro vale igual.
        self.assertEqual(urls(self.pagina(notas=NOTAS, ordem=Ordem.PERFIL, faixa=FaixaFiltro.MEDIA).vagas), ["m2", "m5"])

    def test_paginacao_pela_nota_sem_repetir_nem_pular(self):
        lidas = []
        for inicio in range(0, 5, 2):
            lidas += urls(self.servico.pagina(False, 2, deslocamento=inicio, hoje=HOJE, notas=NOTAS).vagas)
        self.assertEqual(lidas, ORDEM_IA)

    def test_sem_perfil_a_ordem_pela_nota_usa_as_mais_recentes_como_desempate(self):
        servico = HistoricoService(self.repo, PerfilBuscaRepositoryMemoria())
        recentes = urls(servico.pagina(False, 500, hoje=HOJE).vagas)
        # Sem perfil o padrão não muda por causa das notas: só a ordem pedida de propósito ranqueia.
        self.assertEqual(urls(servico.pagina(False, 500, hoje=HOJE, notas=NOTAS).vagas), recentes)
        ranqueadas = urls(servico.pagina(False, 500, hoje=HOJE, notas=NOTAS, ordem=Ordem.IA).vagas)
        self.assertEqual(ranqueadas[:4], ["m4"] + [u for u in recentes if u in ("m2", "m5")] + ["m3"])
        self.assertEqual(ranqueadas[4:], ["m1"])
        self.assertEqual(urls(servico.pagina(False, 500, hoje=HOJE, notas=NOTAS, faixa=FaixaFiltro.ALTA).vagas), ["m4"])

    def test_sem_perfil_uma_nota_nao_corta_o_historico_na_janela_de_varredura(self):
        from unittest import mock

        servico = HistoricoService(self.repo, PerfilBuscaRepositoryMemoria())
        total = servico.pagina(False, 500, hoje=HOJE).total
        with mock.patch("src.service.historico_service.TETO_VARREDURA_HISTORICO", 2):
            pagina = servico.pagina(False, 500, hoje=HOJE, notas=NOTAS)
        self.assertEqual((pagina.total, len(pagina.vagas)), (total, total))
        self.assertGreater(total, 2)

    def test_validar_ordem_e_faixa(self):
        self.assertEqual((validar_ordem(None), validar_ordem(""), validar_ordem("IA")), (None, None, Ordem.IA))
        self.assertEqual(validar_ordem("perfil"), Ordem.PERFIL)
        self.assertEqual((validar_faixa("todas"), validar_faixa("media")), (None, FaixaFiltro.MEDIA))
        self.assertEqual(validar_faixa("Baixa"), FaixaFiltro.BAIXA)
        for ruim in ("nota", "1"):
            with self.subTest(ruim=ruim), self.assertRaises(DadoInvalido):
                validar_ordem(ruim)
        for ruim in ("média", "x"):
            with self.subTest(ruim=ruim), self.assertRaises(DadoInvalido):
                validar_faixa(ruim)


@unittest.skipUnless(os.environ.get("PGHOST"), "PGHOST não definido: integração com Postgres pulada")
class OrdenarPostgresTest(OrdenarTest):
    def setUp(self):
        from src.repository.postgres import VagaEncontradaRepositoryPostgres
        from tests.test_coleta import _schema_descartavel

        self.repo = VagaEncontradaRepositoryPostgres(_schema_descartavel(self, "rank")[0])
        self.repo.registrar(VAGAS, "dados")
        self.perfis = PerfilBuscaRepositoryMemoria()
        self.perfis.salvar(PERFIL)
        self.servico = HistoricoService(self.repo, self.perfis)


def compat(n: int, faixa: str) -> dict:
    return {"nota": n, "faixa": faixa, "atende": [], "falta": [], "eliminatorios": [], "resumo": "Resumo."}


class TelaTest(unittest.TestCase):
    def setUp(self):
        self.encontradas = VagaEncontradaRepositoryMemoria()
        self.encontradas.registrar(VAGAS, "dados")
        self.perfis = PerfilBuscaRepositoryMemoria()
        self.perfis.salvar(PERFIL)
        self.curriculos = CurriculoRepositoryMemoria()
        self.curriculos.salvar(Curriculo(tipo="base", titulo="Base", conteudo_md="# Base"))
        self.pedidos = PedidoIARepositoryMemoria(vaga_por_url_em(self.encontradas))
        self.app = criar_app(
            self.perfis, self.curriculos, BuscaService([]), encontradas=self.encontradas, pedidos_ia=self.pedidos
        )
        self.servico = self.app.state.pedido_ia_service
        self.cliente = TestClient(self.app)

    def avaliar(self, notas: dict):
        for url, n in notas.items():
            self.servico.pedir(url, "compatibilidade")
            p = self.pedidos.proximo()
            self.pedidos.concluir(p.id, p.tentativas, compat(n.nota, n.faixa.value), "compatibilidade-v1", "m", n.nota, n.faixa)

    def cards(self, caminho="/tela", **kwargs):
        return [c.attrs["data-url"] for c in Pagina(self.cliente.get(caminho, **kwargs).text).cards()]

    def test_sem_nota_a_lista_segue_o_perfil_e_nao_ha_chip(self):
        pagina = self.cliente.get("/tela").text
        self.assertEqual([c.attrs["data-url"] for c in Pagina(pagina).cards()], ORDEM_MATCH)
        self.assertNotIn("chip-ia", pagina)
        ativa = next(a for a in Pagina(pagina).tags("a") if a.texto.strip() == "Meus termos")
        self.assertEqual(ativa.attrs.get("aria-current"), "page")

    def test_com_nota_a_lista_vem_pela_ia_com_chip_e_na_fila(self):
        self.avaliar(NOTAS)
        self.servico.pedir("m1", "compatibilidade")  # m1 na fila

        pagina = Pagina(self.cliente.get("/tela").text)
        self.assertEqual([c.attrs["data-url"] for c in pagina.cards()], ORDEM_IA)
        chips = chips_por_card(pagina)
        self.assertEqual(chips["m5"][0].texto.strip(), "55 · média")
        self.assertEqual(chips["m4"][0].texto.strip(), "90 · alta")
        self.assertIn("chip-ia-alta", chips["m4"][0].attrs["class"])
        self.assertIn("chip-ia-media", chips["m2"][0].attrs["class"])
        self.assertIn("chip-ia-baixa", chips["m3"][0].attrs["class"])
        self.assertEqual(chips["m1"][0].texto.strip(), "IA na fila")
        ativa = next(a for a in pagina.tags("a") if a.texto.strip() == "Nota da IA")
        self.assertEqual(ativa.attrs.get("aria-current"), "page")

    def test_filtro_de_nota_uma_faixa_por_botao_com_intervalo_e_cor(self):
        self.avaliar(NOTAS)
        pagina = Pagina(self.cliente.get("/tela?faixa=media").text)
        self.assertTrue([e for e in pagina.tags("nav") if e.attrs.get("aria-label") == "Faixa da nota da IA"])
        # O texto do botão é "Média" e, na linha de baixo, só o intervalo "40–69" (sem quantas há).
        links = {" ".join(a.texto.split()): a for a in pagina.tags("a")}
        for rotulo, faixa in (("Alta 70–100", "alta"), ("Média 40–69", "media"), ("Baixa 0–39", "baixa")):
            with self.subTest(rotulo=rotulo):
                self.assertEqual(parse_qs(urlsplit(links[rotulo].attrs["href"]).query)["faixa"], [faixa])
                self.assertIn(f"nota-{faixa}", links[rotulo].attrs["class"].split())
        self.assertIn("Todas", links)
        self.assertEqual(links["Média 40–69"].attrs.get("aria-current"), "page")
        self.assertNotIn("aria-current", links["Alta 70–100"].attrs)

    def test_ordem_perfil_e_faixa_pela_url_e_pelo_partial(self):
        self.avaliar(NOTAS)
        self.assertEqual(self.cards("/tela?ordem=perfil"), ORDEM_MATCH)
        self.assertEqual(self.cards("/tela?faixa=alta"), ["m4"])
        self.assertEqual(self.cards("/tela/vagas?faixa=media&ordem=perfil", headers=HX), ["m2", "m5"])
        self.assertEqual(self.cards("/tela?faixa=baixa"), ["m3"])
        vazio = Pagina(self.cliente.get("/tela?faixa=alta&q=recepcionista").text)
        self.assertIn("Nenhuma vaga com essa nota da IA", vazio.por_id("vagas").texto)

    def test_valor_invalido_e_422(self):
        for caminho in ("/tela?ordem=nota", "/tela?faixa=média", "/tela/vagas?faixa=x", "/tela/vagas/detalhe?url=m1&ordem=x"):
            with self.subTest(caminho=caminho):
                self.assertEqual(self.cliente.get(caminho, headers=HX).status_code, 422)

    def test_escolha_viaja_no_card_no_push_url_no_carregar_mais_e_na_busca(self):
        self.avaliar(NOTAS)
        resposta = self.cliente.get("/tela/vagas", params={"ordem": "perfil", "faixa": "media", "q": "a&b #1"}, headers=HX)
        self.assertEqual(
            parse_qs(urlsplit(resposta.headers["HX-Push-Url"]).query),
            {"ordem": ["perfil"], "faixa": ["media"], "q": ["a&b #1"]},
        )
        pagina = Pagina(self.cliente.get("/tela?ordem=perfil&faixa=media").text)
        card = pagina.cards()[0]
        self.assertEqual(parse_qs(urlsplit(card.attrs["hx-get"]).query)["ordem"], ["perfil"])
        self.assertEqual(parse_qs(urlsplit(card.attrs["href"]).query)["faixa"], ["media"])
        estado = {
            e.attrs["name"]: e.attrs["value"]
            for e in pagina.tags("input")
            if e.attrs.get("type") == "hidden" and e.attrs.get("name") in ("novas", "ordem", "faixa")
        }
        self.assertEqual(estado, {"ordem": "perfil", "faixa": "media"})

    def test_carregar_mais_pela_ia_percorre_tudo_sem_repetir(self):
        extras = [v(f"x{i:02}", f"Analista de Dados {i}") for i in range(60)]
        self.encontradas.registrar(extras, "dados")
        self.avaliar({"x59": nota(99), "x00": nota(10), "m3": nota(80)})
        esperado = urls(
            HistoricoService(self.encontradas, self.perfis).pagina(False, 500, notas=self.servico.notas_validas()).vagas
        )
        self.assertEqual(esperado[:3], ["x59", "m3", "x00"])

        pagina = Pagina(self.cliente.get("/tela?faixa=").text)
        lidas = [c.attrs["data-url"] for c in pagina.cards()]
        proximo = next(e for e in pagina.tags("li") if "mais" in (e.attrs.get("class") or "")).attrs["hx-get"]
        while proximo:
            parte = Pagina(self.cliente.get(proximo, headers=HX).text)
            lidas += [c.attrs["data-url"] for c in parte.cards()]
            sentinelas = [e for e in parte.tags("li") if "mais" in (e.attrs.get("class") or "")]
            proximo = sentinelas[0].attrs["hx-get"] if sentinelas else None
        self.assertEqual(lidas, esperado)

    def test_urls_dificeis_ida_e_volta_com_a_ordem_pela_ia(self):
        self.encontradas.registrar([v(u, "Analista de Dados SQL") for u in URLS_DIFICEIS], "dados")
        self.avaliar({URLS_DIFICEIS[0]: nota(95)})
        cards = {c.attrs["data-url"]: c for c in Pagina(self.cliente.get("/tela?faixa=alta").text).cards()}
        self.assertEqual(list(cards), [URLS_DIFICEIS[0]])
        card = cards[URLS_DIFICEIS[0]]
        self.assertTrue(card.attrs["hx-get"].startswith("/tela/vagas/detalhe?url=" + quote(URLS_DIFICEIS[0], safe="/")))
        parcial = Pagina(self.cliente.get(card.attrs["hx-get"], headers=HX).text)
        self.assertEqual(parcial.por_id("detalhe").attrs["data-url"], URLS_DIFICEIS[0])

    def test_uma_leitura_de_notas_por_pagina_qualquer_que_seja_o_numero_de_cards(self):
        self.encontradas.registrar([v(f"y{i:02}", f"Analista de Dados {i}") for i in range(40)], "dados")
        self.avaliar(NOTAS)
        chamadas = []
        original = self.pedidos.notas_compatibilidade
        self.pedidos.notas_compatibilidade = lambda *a, **k: chamadas.append(1) or original(*a, **k)
        pagina = Pagina(self.cliente.get("/tela").text)
        self.assertEqual(len(chamadas), 1)
        self.assertEqual(sum(len(c) for c in chips_por_card(pagina).values()), len(NOTAS))


if __name__ == "__main__":
    unittest.main()
