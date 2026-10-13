"""Vagas salvas para ver depois: a bandeirinha, a aba "Salvas" e o "Marcar como aplicada" que tira dos salvos.

Repositório na memória e no Postgres de verdade (PGHOST), com o mesmo contrato; tela e serviço na memória.
As vagas daqui são escritas à mão (não são respostas gravadas de provider).
"""

import os
import time
import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from src.controller.api import criar_app
from src.controller.tela import id_salvar
from src.model.perfil import Perfil
from src.model.vaga_externa import VagaExterna
from src.repository.base import CurriculoRepositoryMemoria, PerfilBuscaRepositoryMemoria, VagaEncontradaRepositoryMemoria
from src.service.busca_service import BuscaService
from tests.test_coleta import _schema_descartavel
from tests.test_tela_lista import Pagina

HX = {"HX-Request": "true", "Origin": "http://testserver"}


def url(i: int) -> str:
    return f"https://exemplo.test/vaga/{i}"


def vaga(i: int, titulo: str | None = None) -> VagaExterna:
    return VagaExterna(titulo or f"Analista de Dados {i}", "Empresa Teste", "Remotar", url(i), local="Rio de Janeiro", remoto=True)


class ContratoSalvas:
    repo = None

    def passar_tempo(self) -> None:
        raise NotImplementedError

    def test_salvar_tirar_e_url_inexistente(self):
        self.repo.registrar([vaga(1), vaga(2)], "dados")
        self.assertTrue(self.repo.marcar_salva(url(1), True))
        self.assertIsNotNone(self.repo.obter(url(1)).salva_em)
        self.assertIsNone(self.repo.obter(url(2)).salva_em)
        self.assertEqual(self.repo.contar_salvas(), 1)

        self.assertTrue(self.repo.marcar_salva(url(1), False))
        self.assertIsNone(self.repo.obter(url(1)).salva_em)
        self.assertEqual(self.repo.contar_salvas(), 0)
        self.assertFalse(self.repo.marcar_salva("https://exemplo.test/nao-existe", True))

    def test_salvar_de_novo_guarda_a_primeira_data(self):
        self.repo.registrar([vaga(1)], "dados")
        self.repo.marcar_salva(url(1), True)
        primeira = self.repo.obter(url(1)).salva_em
        self.passar_tempo()
        self.repo.marcar_salva(url(1), True)
        self.assertEqual(self.repo.obter(url(1)).salva_em, primeira)

    def test_listar_salvas_da_mais_recente_inclui_expirada_e_filtra_busca(self):
        self.repo.registrar([vaga(1), vaga(2, "Bookkeeper"), vaga(3), vaga(4)], "dados")
        for i in (1, 2, 3):
            self.repo.marcar_salva(url(i), True)
            self.passar_tempo()
        self.repo.marcar_expirada(url(2))

        self.assertEqual([v.url for v in self.repo.listar_salvas(teto=100)], [url(3), url(2), url(1)])
        self.assertEqual(self.repo.contar_salvas(), 3)
        self.assertEqual([v.url for v in self.repo.listar_salvas(teto=100, q="bookkeeper")], [url(2)])
        self.assertEqual([v.url for v in self.repo.listar_salvas(teto=2)], [url(3), url(2)])
        # A coleta não mexe no salvo.
        self.repo.registrar([vaga(1)], "dados")
        self.assertIsNotNone(self.repo.obter(url(1)).salva_em)


class SalvasMemoriaTest(ContratoSalvas, unittest.TestCase):
    def setUp(self):
        self.agora = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
        self.repo = VagaEncontradaRepositoryMemoria(relogio=lambda: self.agora)

    def passar_tempo(self) -> None:
        self.agora += timedelta(seconds=1)


@unittest.skipUnless(os.environ.get("PGHOST"), "PGHOST não definido: integração com Postgres pulada")
class SalvasPostgresTest(ContratoSalvas, unittest.TestCase):
    def setUp(self):
        from src.repository.postgres import VagaEncontradaRepositoryPostgres

        self.conn, _ = _schema_descartavel(self, "teste_salvas")
        self.repo = VagaEncontradaRepositoryPostgres(self.conn)

    def passar_tempo(self) -> None:
        time.sleep(0.01)  # o now() de cada transação avança

    def test_migration_009_acrescenta_a_coluna(self):
        colunas = {
            linha[0]
            for linha in self.conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'vaga_encontrada' "
                "AND table_schema = current_schema()"
            ).fetchall()
        }
        self.assertIn("salva_em", colunas)


class Tela:
    def __init__(self, vagas: list[VagaExterna], perfil: Perfil | None = None):
        self.repo = VagaEncontradaRepositoryMemoria()
        self.repo.registrar(vagas, "dados")
        perfis = PerfilBuscaRepositoryMemoria()
        if perfil is not None:
            perfis.salvar(perfil)
        self.cliente = TestClient(criar_app(perfis, CurriculoRepositoryMemoria(), BuscaService([]), encontradas=self.repo))

    def salvar(self, i: int, salvar: str = "1"):
        return self.cliente.post("/tela/vagas/salva", data={"url": url(i), "salvar": salvar}, headers=HX)

    def lista(self, query: str = "") -> Pagina:
        return Pagina(self.cliente.get(f"/tela/vagas{query}", headers=HX).text)


def titulos(pagina: Pagina) -> list[str]:
    # A 1ª linha com texto do card é o título (e o selo "nova", que sai).
    return [next(linha.strip() for linha in c.texto.splitlines() if linha.strip()).removesuffix(" nova").strip() for c in pagina.cards()]


class BandeirinhaTest(unittest.TestCase):
    def test_salvar_responde_so_com_as_trocas_oob(self):
        t = Tela([vaga(1)])

        resposta = t.salvar(1)

        self.assertEqual(resposta.status_code, 200)
        pagina = Pagina(resposta.text)
        sufixo = id_salvar(url(1))
        for variante in ("c", "d"):
            form = pagina.por_id(f"salvar-{variante}-{sufixo}")
            self.assertEqual(form.attrs["hx-swap-oob"], "outerHTML")
        self.assertEqual(pagina.por_id("contador-salvas").texto.strip(), "1")
        self.assertIn('aria-pressed="true"', resposta.text)
        self.assertIn('name="salvar" value="0"', resposta.text)  # o próximo clique tira dos salvos

        resposta = t.salvar(1, "0")
        self.assertEqual(Pagina(resposta.text).por_id("contador-salvas").texto.strip(), "0")
        self.assertIsNone(t.repo.obter(url(1)).salva_em)

    def test_vaga_inexistente_404_e_valor_torto_422(self):
        t = Tela([vaga(1)])
        self.assertEqual(t.cliente.post("/tela/vagas/salva", data={"url": url(9), "salvar": "1"}, headers=HX).status_code, 404)
        self.assertEqual(t.salvar(1, "talvez").status_code, 422)
        self.assertEqual(t.cliente.post("/tela/vagas/salva", data={"url": "", "salvar": "1"}, headers=HX).status_code, 422)

    def test_sem_htmx_e_origem_da_tela_e_recusado(self):
        t = Tela([vaga(1)])
        resposta = t.cliente.post("/tela/vagas/salva", data={"url": url(1), "salvar": "1"})
        self.assertIn(resposta.status_code, (403, 415))
        self.assertIsNone(t.repo.obter(url(1)).salva_em)

    def test_card_e_detalhe_tem_a_bandeirinha_fora_do_link(self):
        t = Tela([vaga(1)])
        t.repo.marcar_salva(url(1), True)

        html = t.cliente.get("/tela").text
        pagina = Pagina(html)

        sufixo = id_salvar(url(1))
        self.assertEqual(pagina.por_id(f"salvar-c-{sufixo}").tag, "form")
        self.assertEqual(pagina.por_id(f"salvar-d-{sufixo}").tag, "form")
        # O form do card vem depois do </a>: botão dentro de link não é HTML válido e abriria a vaga.
        inicio_card = html.index('class="card"')
        self.assertLess(html.index("</a>", inicio_card), html.index(f'id="salvar-c-{sufixo}"'))


class AbaSalvasTest(unittest.TestCase):
    def test_aba_mostra_so_as_salvas_na_ordem_em_que_foram_salvas(self):
        t = Tela([vaga(i) for i in range(5)])
        for i in (3, 1):
            t.salvar(i)

        pagina = t.lista("?salvas=1")

        self.assertEqual(titulos(pagina), ["Analista de Dados 1", "Analista de Dados 3"])
        self.assertIn("2 salvas", pagina.por_id("vagas").texto)
        self.assertEqual(pagina.por_id("contador-salvas").texto.strip(), "2")

    def test_aba_ignora_o_perfil_e_mostra_a_encerrada(self):
        perfil = Perfil(termos=["dados"], excluir=["bookkeeper"])
        t = Tela([vaga(1), vaga(2, "Bookkeeper")], perfil)
        t.salvar(2)
        t.repo.marcar_expirada(url(2))

        self.assertNotIn("Bookkeeper", titulos(t.lista()))
        salvas = t.lista("?salvas=1")
        self.assertEqual(titulos(salvas), ["Bookkeeper"])
        self.assertIn("encerrada", salvas.por_id("vagas").texto)

    def test_aba_sem_barra_da_ia_e_com_o_filtro_nos_links(self):
        t = Tela([vaga(1)])
        t.salvar(1)

        resposta = t.cliente.get("/tela/vagas?salvas=1", headers=HX)

        self.assertNotIn("lista-ia", resposta.text)
        self.assertIn('name="salvas" value="1"', resposta.text)  # a coleta e a busca continuam na aba
        card = Pagina(resposta.text).cards()[0]
        self.assertIn("salvas=1", card.attrs["hx-get"])
        self.assertIn("salvas=1", resposta.headers["HX-Push-Url"])

    def test_carregar_mais_na_aba(self):
        t = Tela([vaga(i) for i in range(60)])
        for i in range(60):
            t.repo.marcar_salva(url(i), True)

        primeira = t.lista("?salvas=1")
        self.assertEqual(len(primeira.cards()), 50)
        sentinela = next(e for e in primeira.tags("li") if "mais" in (e.attrs.get("class") or ""))
        self.assertIn("salvas=1", sentinela.attrs["hx-get"])
        segunda = Pagina(t.cliente.get(sentinela.attrs["hx-get"], headers=HX).text)
        self.assertEqual(len(segunda.cards()), 10)

    def test_tirar_dos_salvos_com_a_pagina_aberta_nao_pula_vaga_no_carregar_mais(self):
        # O bug do review: com deslocamento, tirar uma vaga da 1ª página fazia a 51ª nunca aparecer.
        t = Tela([vaga(i) for i in range(60)])
        for i in range(60):
            t.salvar(i)

        primeira = t.lista("?salvas=1")
        vistas = [c.attrs["data-url"] for c in primeira.cards()]
        t.salvar(int(vistas[10].rsplit("/", 1)[1]), "0")  # o card continua na tela
        sentinela = next(e for e in primeira.tags("li") if "mais" in (e.attrs.get("class") or ""))
        segunda = Pagina(t.cliente.get(sentinela.attrs["hx-get"], headers=HX).text)
        vistas += [c.attrs["data-url"] for c in segunda.cards()]

        self.assertEqual(sorted(vistas), sorted(url(i) for i in range(60)))
        self.assertEqual(len(vistas), len(set(vistas)))
        # A última página não deixa outro sentinela.
        self.assertFalse([e for e in segunda.tags("li") if "mais" in (e.attrs.get("class") or "")])

    def test_cursor_torto_e_422(self):
        t = Tela([vaga(1)])
        for query in ("?salvas=1&apos_em=ontem&apos_url=x", "?salvas=1&apos_em=2026-10-13T10:00:00&apos_url=x", "?salvas=1&apos_url=x"):
            with self.subTest(query=query):
                self.assertEqual(t.cliente.get(f"/tela/vagas{query}", headers=HX).status_code, 422)

    def test_aba_vazia_e_busca_sem_resultado(self):
        t = Tela([vaga(1)])
        self.assertIn("Nenhuma vaga salva", t.lista("?salvas=1").por_id("vagas").texto)
        t.salvar(1)
        self.assertIn("Nenhuma vaga salva encontrada para a busca", t.lista("?salvas=1&q=python").por_id("vagas").texto)

    def test_pagina_inteira_abre_na_aba(self):
        t = Tela([vaga(1), vaga(2)])
        t.salvar(2)

        pagina = Pagina(t.cliente.get("/tela?salvas=1").text)

        self.assertEqual(titulos(pagina), ["Analista de Dados 2"])
        ativa = next(a for a in pagina.tags("a") if a.attrs.get("aria-current") == "page")
        self.assertIn("Salvas", ativa.texto)


class AplicadaSaiDasSalvasTest(unittest.TestCase):
    def test_marcar_como_aplicada_tira_dos_salvos_e_atualiza_a_tela(self):
        t = Tela([vaga(1), vaga(2)])
        t.salvar(1)
        t.salvar(2)

        resposta = t.cliente.post("/tela/candidaturas", data={"url": url(1)}, headers=HX)

        self.assertEqual(resposta.status_code, 200)
        self.assertIsNone(t.repo.obter(url(1)).salva_em)
        self.assertIsNotNone(t.repo.obter(url(2)).salva_em)
        pagina = Pagina(resposta.text)
        self.assertEqual(pagina.por_id("contador-salvas").texto.strip(), "1")
        self.assertEqual(pagina.por_id(f"salvar-d-{id_salvar(url(1))}").attrs["hx-swap-oob"], "outerHTML")

    def test_api_json_tambem_tira_dos_salvos(self):
        t = Tela([vaga(1)])
        t.salvar(1)

        resposta = t.cliente.post("/candidaturas", json={"vaga_url": url(1)})

        self.assertEqual(resposta.status_code, 201)
        self.assertIsNone(t.repo.obter(url(1)).salva_em)


if __name__ == "__main__":
    unittest.main()
