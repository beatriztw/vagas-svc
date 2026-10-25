"""Tela: selo "nova", alternância novas/todas, vista pelo POST e "Buscar vagas novas" (PR4).

As vagas e os providers daqui são escritos à mão (não são respostas gravadas de provider).
"""

import re
import threading
import unittest
from urllib.parse import parse_qs, quote, urlsplit

from tests.sessao import ClienteLogado

from src.client.base import ProviderClient
from src.controller.api import criar_app
from src.model.perfil import Perfil
from src.model.vaga_externa import VagaExterna
from src.repository.base import CurriculoRepositoryMemoria, PerfilBuscaRepositoryMemoria, VagaEncontradaRepositoryMemoria
from src.service.busca_service import BuscaService
from tests.test_tela_lista import HX, URLS_DIFICEIS, Pagina, criar, vaga

MUTACAO = {"HX-Request": "true", "Origin": "http://testserver"}
ESPERA = 10


def selos(texto: str) -> list[str]:
    """Urls dos cards que mostram o selo "nova"."""
    pagina = Pagina(texto)
    # O selo é um <span class="selo-nova"> dentro do card: o texto do card o inclui.
    return [c.attrs["data-url"] for c in pagina.cards() if re.search(r"\bnova\b", c.texto)]


def mutacao(cliente: ClienteLogado, caminho: str, **dados):
    return cliente.post(caminho, data=dados, headers=MUTACAO)


class SeloENovasTest(unittest.TestCase):
    def test_selo_so_nas_nao_vistas(self):
        cliente, repo = criar([vaga(1), vaga(2), vaga(3)])
        repo.marcar_vistas(["https://exemplo.test/vaga/2"])

        urls = selos(cliente.get("/tela").text)

        self.assertEqual(sorted(urls), ["https://exemplo.test/vaga/1", "https://exemplo.test/vaga/3"])

    def test_alternancia_padrao_todas_e_novas_filtra_e_conta_com_contar(self):
        cliente, repo = criar([vaga(i) for i in range(60)])
        repo.marcar_vistas([f"https://exemplo.test/vaga/{i}" for i in range(10)])

        todas = Pagina(cliente.get("/tela").text)
        novas = Pagina(cliente.get("/tela", params={"novas": "1"}).text)

        self.assertIn("60 encontradas", todas.por_id("vagas").texto)
        self.assertIn("50 não abertas", novas.por_id("vagas").texto)
        self.assertEqual(len(novas.cards()), 50)
        vistas = {f"https://exemplo.test/vaga/{i}" for i in range(10)}
        self.assertFalse(vistas & {c.attrs["data-url"] for c in novas.cards()})

    def test_partial_com_novas_e_o_mesmo_bloco(self):
        cliente, repo = criar([vaga(1), vaga(2)])
        repo.marcar_vistas(["https://exemplo.test/vaga/1"])

        resposta = cliente.get("/tela/vagas", params={"novas": "1"}, headers=HX)

        self.assertNotIn("<html", resposta.text)
        self.assertEqual([c.attrs["data-url"] for c in Pagina(resposta.text).cards()], ["https://exemplo.test/vaga/2"])

    def test_novas_invalido_e_422_html(self):
        cliente, _ = criar([vaga(1)])

        for caminho in ("/tela", "/tela/vagas", "/tela/vagas/detalhe"):
            with self.subTest(caminho=caminho):
                resposta = cliente.get(caminho, params={"novas": "talvez"}, headers=HX)
                self.assertEqual(resposta.status_code, 422)
                self.assertIn("novas tem que ser", resposta.text)
                self.assertNotIn('"detail"', resposta.text)

    def test_alternancia_marca_a_ativa_e_carrega_o_q(self):
        cliente, _ = criar([vaga(1, "Analista de conciliação & contas #1")])
        q = "conciliação & contas #1"

        pagina = Pagina(cliente.get("/tela", params={"q": q, "novas": "1"}).text)

        # O 1º "Todas" é o da lista; o do filtro "Nota" da IA vem depois e não entra.
        links = {}
        for e in pagina.tags("a"):
            if e.texto.strip() in ("Todas", "Não abertas"):
                links.setdefault(e.texto.strip(), e)
        self.assertEqual(links["Não abertas"].attrs.get("aria-current"), "page")
        self.assertNotIn("aria-current", links["Todas"].attrs)
        todas = urlsplit(links["Todas"].attrs["hx-get"])
        self.assertEqual(todas.path, "/tela/vagas")
        self.assertEqual(parse_qs(todas.query), {"q": [q]})  # ida e volta do q codificado
        self.assertNotIn("hx-push-url", links["Todas"].attrs)  # a barra vem do HX-Push-Url do servidor
        self.assertEqual(links["Todas"].attrs["hx-include"], "#detalhe-url")
        self.assertEqual(links["Todas"].attrs["href"], "/tela?" + todas.query)
        novas = urlsplit(links["Não abertas"].attrs["hx-get"])
        self.assertEqual(parse_qs(novas.query), {"q": [q], "novas": ["1"]})
        for e in links.values():
            for atributo in ("hx-get", "href"):
                self.assertNotRegex(e.attrs[atributo].split("?", 1)[-1], r"[ #]")

    def test_novas_e_q_vao_em_sentinela_card_e_push_url_com_ida_e_volta(self):
        url = "https://x.com/vaga?a=1&b=2#c=d"
        vagas = [VagaExterna(f"Analista de conciliação {i}", "Empresa", "Remotar", f"https://exemplo.test/c/{i}") for i in range(60)]
        vagas.append(VagaExterna("Analista de conciliação especial", "Empresa", "Remotar", url))
        cliente, _ = criar(vagas)
        parametros = {"q": "conciliação analista", "novas": "1"}

        pagina = Pagina(cliente.get("/tela", params=parametros).text)

        sentinela = next(e for e in pagina.tags("li") if "mais" in (e.attrs.get("class") or ""))
        consulta = parse_qs(urlsplit(sentinela.attrs["hx-get"]).query)
        self.assertEqual(consulta, {"deslocamento": ["50"], "q": ["conciliação analista"], "novas": ["1"]})
        resposta = cliente.get(sentinela.attrs["hx-get"], headers=HX)
        self.assertEqual(len(Pagina(resposta.text).cards()), 11)
        for card in pagina.cards():
            for atributo in ("href", "hx-get", "hx-push-url"):
                query = parse_qs(urlsplit(card.attrs[atributo]).query)
                self.assertEqual(query["novas"], ["1"], atributo)
                self.assertEqual(query["q"], ["conciliação analista"], atributo)
                self.assertEqual(query["url"], [card.attrs["data-url"]], atributo)

    def test_card_com_url_dificil_mantem_a_url_e_o_filtro_ida_e_volta(self):
        for url in URLS_DIFICEIS:
            with self.subTest(url=url):
                cliente, _ = criar([VagaExterna("Analista de conciliação", "Empresa", "Remotar", url)])
                card = Pagina(cliente.get("/tela", params={"q": "conciliação", "novas": "1"}).text).cards()[0]

                for atributo in ("href", "hx-push-url"):
                    query = parse_qs(urlsplit(card.attrs[atributo]).query)
                    self.assertEqual(query, {"url": [url], "q": ["conciliação"], "novas": ["1"]})
                query = parse_qs(urlsplit(card.attrs["hx-get"]).query)
                self.assertEqual(query["url"], [url])
                # A página de fallback (href) abre a vaga e mantém o filtro.
                inteira = Pagina(cliente.get(card.attrs["href"]).text)
                self.assertEqual(inteira.por_id("detalhe").attrs["data-url"], url)

    def test_sem_filtro_as_urls_continuam_sem_q_nem_novas(self):
        cliente, _ = criar([vaga(1)])

        card = Pagina(cliente.get("/tela").text).cards()[0]

        self.assertEqual(card.attrs["href"], "/tela?url=" + quote("https://exemplo.test/vaga/1", safe="/"))

    def test_vazio_de_novas_nao_oferece_coleta_como_se_nao_houvesse_historico(self):
        cliente, repo = criar([vaga(1)])
        repo.marcar_vistas(["https://exemplo.test/vaga/1"])

        texto = cliente.get("/tela", params={"novas": "1"}).text

        self.assertIn("Você já abriu todas as vagas", texto)
        self.assertNotIn("Nenhuma vaga ainda", texto)


class BuscaComNovasTest(unittest.TestCase):
    """O campo de busca (fora de #vagas) tem que levar o filtro "Novas" e não duplicar o q."""

    Q = "conciliação & contas #1"

    def montar(self):
        vagas = [VagaExterna(f"Analista de conciliação & contas #1 n{i}", "Empresa", "Remotar", f"https://exemplo.test/c/{i}") for i in range(60)]
        vagas.append(VagaExterna("Analista de conciliação & contas #1 vista", "Empresa", "Remotar", "https://exemplo.test/c/vista"))
        cliente, repo = criar(vagas)
        repo.marcar_vistas(["https://exemplo.test/c/vista"])
        return cliente

    def requisicao_do_campo(self, texto: str, q: str) -> str:
        """Simula o htmx: o GET do campo (hx-get + hx-include) com o valor digitado e os inputs incluídos."""
        pagina = Pagina(texto)
        campo = pagina.por_id("busca")
        self.assertEqual(campo.attrs["hx-include"], "#estado-lista, #detalhe-url")
        estado = [i for i in pagina.tags("input") if i.attrs.get("type") == "hidden" and i.attrs.get("name") in ("q", "novas")]
        nomes = [i.attrs["name"] for i in estado]
        self.assertEqual(nomes.count("q"), 0, "o q é só o do campo de busca")
        from urllib.parse import urlencode

        params = [("q", q)] + [(i.attrs["name"], i.attrs["value"]) for i in estado if i.attrs["name"] != "q"]
        return campo.attrs["hx-get"] + "?" + urlencode(params, quote_via=quote)

    def test_buscar_com_novas_ligado_mantem_novas_ida_e_volta(self):
        cliente = self.montar()
        pagina_novas = cliente.get("/tela", params={"novas": "1"}).text

        pedido = self.requisicao_do_campo(pagina_novas, self.Q)

        self.assertEqual(parse_qs(urlsplit(pedido).query), {"q": [self.Q], "novas": ["1"]})
        resposta = cliente.get(pedido, headers=HX)
        pagina = Pagina(resposta.text)
        self.assertIn("60 não abertas", pagina.por_id("vagas").texto)  # a vista não entra
        self.assertNotIn("https://exemplo.test/c/vista", [c.attrs["data-url"] for c in pagina.cards()])
        # O novo bloco continua em Novas e o sentinela leva os dois.
        sentinela = next(e for e in pagina.tags("li") if "mais" in (e.attrs.get("class") or ""))
        self.assertEqual(
            parse_qs(urlsplit(sentinela.attrs["hx-get"]).query),
            {"deslocamento": ["50"], "q": [self.Q], "novas": ["1"]},
        )
        # A barra recebe `/tela?...` (HX-Push-Url): abri-la (F5) mostra a mesma lista.
        inteira = Pagina(cliente.get(pedido).text)
        self.assertIn("60 não abertas", inteira.por_id("vagas").texto)
        self.assertEqual(inteira.por_id("busca").attrs["value"], self.Q)
        self.assertEqual([c.attrs["data-url"] for c in inteira.cards()], [c.attrs["data-url"] for c in pagina.cards()])
        # O estado do novo bloco ainda tem novas, para a próxima digitação.
        self.assertEqual(self.requisicao_do_campo(inteira and cliente.get(pedido).text, "outra"), "/tela/vagas?q=outra&novas=1")

    def test_buscar_com_todas_nao_manda_novas_vazio(self):
        cliente = self.montar()

        pedido = self.requisicao_do_campo(cliente.get("/tela").text, self.Q)

        self.assertEqual(parse_qs(urlsplit(pedido).query), {"q": [self.Q]})
        self.assertNotIn("novas", pedido)
        self.assertIn("61 encontradas", Pagina(cliente.get(pedido, headers=HX).text).por_id("vagas").texto)

    def test_coleta_leva_o_q_do_campo_e_o_novas(self):
        cliente = self.montar()
        botao = next(b for b in Pagina(cliente.get("/tela", params={"novas": "1"}).text).tags("button") if "Buscar vagas novas" in b.texto)

        self.assertEqual(botao.attrs["hx-include"], "#busca, #estado-lista")


class VistaTest(unittest.TestCase):
    URL = "https://exemplo.test/vaga/1"

    def vista_em(self, repo):
        return repo.obter(self.URL).vista_em

    def test_get_do_detalhe_nao_marca_vista(self):
        cliente, repo = criar([vaga(1)])

        cliente.get("/tela/vagas/detalhe", params={"url": self.URL}, headers=HX)
        cliente.get("/tela", params={"url": self.URL})

        self.assertIsNone(self.vista_em(repo))

    def test_detalhe_traz_o_post_de_vista_no_load_com_a_url_em_campo_escondido(self):
        for url in URLS_DIFICEIS:
            with self.subTest(url=url):
                cliente, _ = criar([VagaExterna("T", "E", "Remotar", url)])

                pagina = Pagina(cliente.get("/tela/vagas/detalhe", params={"url": url}, headers=HX).text)

                [form] = [f for f in pagina.tags("form") if f.attrs.get("hx-post") == "/tela/vagas/vista"]
                self.assertEqual(form.attrs["hx-trigger"], "load")
                self.assertNotIn("hx-vals", form.attrs)
                campo = next(i for i in pagina.tags("input") if i.attrs.get("name") == "url")
                self.assertEqual(campo.attrs["value"], url)

    def test_detalhe_vazio_ou_nao_encontrado_nao_tem_post_de_vista(self):
        cliente, _ = criar([vaga(1)])

        for texto in (
            cliente.get("/tela/vagas/detalhe", params={"url": "nao-existe"}, headers=HX).text,
            cliente.get("/tela/vagas/detalhe", headers=HX).text,
        ):
            self.assertNotIn("/tela/vagas/vista", texto)

    def test_post_marca_vista_e_responde_204_sem_corpo(self):
        cliente, repo = criar([vaga(1), vaga(2)])

        resposta = mutacao(cliente, "/tela/vagas/vista", url=self.URL)

        self.assertEqual(resposta.status_code, 204)
        self.assertEqual(resposta.content, b"")
        self.assertIsNotNone(self.vista_em(repo))
        self.assertIsNone(repo.obter("https://exemplo.test/vaga/2").vista_em)
        self.assertEqual(selos(cliente.get("/tela").text), ["https://exemplo.test/vaga/2"])

    def test_post_com_url_dificil_marca_so_aquela(self):
        for url in URLS_DIFICEIS:
            with self.subTest(url=url):
                cliente, repo = criar([VagaExterna("T", "E", "Remotar", url), vaga(9)])

                self.assertEqual(mutacao(cliente, "/tela/vagas/vista", url=url).status_code, 204)

                self.assertIsNotNone(repo.obter(url).vista_em)
                self.assertIsNone(repo.obter("https://exemplo.test/vaga/9").vista_em)

    def test_post_repetido_e_url_inexistente_nao_falham(self):
        cliente, _ = criar([vaga(1)])

        self.assertEqual(mutacao(cliente, "/tela/vagas/vista", url=self.URL).status_code, 204)
        self.assertEqual(mutacao(cliente, "/tela/vagas/vista", url=self.URL).status_code, 204)
        self.assertEqual(mutacao(cliente, "/tela/vagas/vista", url="nao-existe").status_code, 204)

    def test_url_ausente_ou_gigante_e_422_html(self):
        cliente, repo = criar([vaga(1)])

        for dados in ({}, {"url": ""}, {"url": "x" * 3000}):
            with self.subTest(dados=str(dados)[:20]):
                resposta = mutacao(cliente, "/tela/vagas/vista", **dados)
                self.assertEqual(resposta.status_code, 422)
                self.assertNotIn('"detail"', resposta.text)
                self.assertEqual(resposta.headers["HX-Retarget"], "#aviso")
        self.assertIsNone(self.vista_em(repo))

    def test_csrf_sem_hx_request_ou_origem_errada_e_403_e_nao_marca(self):
        cliente, repo = criar([vaga(1)])
        casos = [
            {"Origin": "http://testserver"},
            {"HX-Request": "true", "Origin": "http://evil.test"},
            {"HX-Request": "true"},
            {"HX-Request": "true", "Sec-Fetch-Site": "cross-site"},
        ]

        for cabecalhos in casos:
            with self.subTest(cabecalhos=cabecalhos):
                resposta = cliente.post("/tela/vagas/vista", data={"url": self.URL}, headers=cabecalhos)
                self.assertEqual(resposta.status_code, 403)
        self.assertIsNone(self.vista_em(repo))

    def test_get_em_vista_e_405(self):
        cliente, repo = criar([vaga(1)])

        resposta = cliente.get("/tela/vagas/vista", params={"url": self.URL}, headers=HX)

        self.assertEqual(resposta.status_code, 405)
        self.assertIsNone(self.vista_em(repo))

    def test_tela_js_tira_o_selo_depois_do_post_de_vista(self):
        cliente, _ = criar([vaga(1)])

        js = cliente.get("/static/tela.js").text

        self.assertIn("/tela/vagas/vista", js)
        self.assertIn("selo-nova", js)


class ProviderBloqueado(ProviderClient):
    nome = "Bloqueado"

    def __init__(self, vagas=None):
        self.comecou = threading.Event()
        self.soltar = threading.Event()
        self.chamadas = 0
        self.vagas = vagas if vagas is not None else [VagaExterna("Analista de dados", "Acme", "Remotar", "https://exemplo.test/c/1")]

    def buscar(self, termo):
        self.chamadas += 1
        self.comecou.set()
        if not self.soltar.wait(ESPERA):
            raise TimeoutError("o teste não soltou o provider")
        return self.vagas


class ProviderQuebrado(ProviderClient):
    nome = "Quebrado"

    def buscar(self, termo):
        raise TimeoutError("sem resposta")


def app_de_coleta(provider, termos=("dados",), encontradas=None):
    perfis = PerfilBuscaRepositoryMemoria(1)
    if termos is not None:
        perfis.salvar(Perfil(termos=list(termos)))
    repo = encontradas if encontradas is not None else VagaEncontradaRepositoryMemoria(1)
    app = criar_app(perfis, CurriculoRepositoryMemoria(1), BuscaService([provider]), encontradas=repo)
    return app, repo


class ColetaPelaTelaTest(unittest.TestCase):
    def test_botoes_de_coleta_postam_com_indicador_e_desabilitam_o_proprio(self):
        cliente, _ = criar([])

        botoes = [b for b in Pagina(cliente.get("/tela").text).tags("button") if "Buscar vagas novas" in b.texto]

        self.assertEqual(len(botoes), 2)  # barra da lista e estado vazio
        for botao in botoes:
            self.assertEqual(botao.attrs["hx-post"], "/tela/coletas")
            self.assertEqual(botao.attrs["hx-disabled-elt"], "this")
            self.assertEqual(botao.attrs["hx-target"], "#vagas")
            self.assertEqual(botao.attrs["hx-include"], "#busca, #estado-lista")

    def test_sucesso_devolve_a_lista_atualizada_com_o_resumo(self):
        provider = ProviderBloqueado()
        provider.soltar.set()
        app, repo = app_de_coleta(provider)
        cliente = ClienteLogado(app, raise_server_exceptions=False)

        resposta = mutacao(cliente, "/tela/coletas")

        self.assertEqual(resposta.status_code, 200)
        self.assertNotIn("<html", resposta.text)
        pagina = Pagina(resposta.text)
        self.assertIn("Coleta concluída: 1 novas.", pagina.por_id("vagas").texto)
        self.assertIn("1 encontradas", pagina.por_id("vagas").texto)
        self.assertEqual([c.attrs["data-url"] for c in pagina.cards()], ["https://exemplo.test/c/1"])
        self.assertEqual(repo.contar(somente_novas=False), 1)

    def test_segunda_coleta_diz_zero_novas(self):
        provider = ProviderBloqueado()
        provider.soltar.set()
        app, _ = app_de_coleta(provider)
        cliente = ClienteLogado(app, raise_server_exceptions=False)
        mutacao(cliente, "/tela/coletas")

        resposta = mutacao(cliente, "/tela/coletas")

        self.assertIn("Coleta concluída: 0 novas.", resposta.text)

    def test_coleta_mantem_o_filtro_da_tela(self):
        provider = ProviderBloqueado(
            [
                VagaExterna("Analista de dados", "Acme", "Remotar", "https://exemplo.test/c/1"),
                VagaExterna("Dev Java", "Acme", "Remotar", "https://exemplo.test/c/2"),
            ]
        )
        provider.soltar.set()
        app, _ = app_de_coleta(provider)
        cliente = ClienteLogado(app, raise_server_exceptions=False)

        resposta = mutacao(cliente, "/tela/coletas", q="java", novas="1")

        self.assertEqual([c.attrs["data-url"] for c in Pagina(resposta.text).cards()], ["https://exemplo.test/c/2"])
        self.assertIn("1 não abertas", Pagina(resposta.text).por_id("vagas").texto)

    def test_todos_os_providers_com_erro_aparece_no_resumo(self):
        app, _ = app_de_coleta(ProviderQuebrado())
        cliente = ClienteLogado(app, raise_server_exceptions=False)

        resposta = mutacao(cliente, "/tela/coletas")

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("todas as fontes deram erro", resposta.text)
        self.assertIn("dados: Quebrado", resposta.text)
        self.assertNotIn("sem resposta", resposta.text)  # o texto da exceção do provider não vai para a tela

    def test_sem_perfil_ou_sem_termos_vira_aviso_html_422(self):
        for termos in (None, []):
            with self.subTest(termos=termos):
                provider = ProviderBloqueado()
                provider.soltar.set()
                app, _ = app_de_coleta(provider, termos=termos)
                cliente = ClienteLogado(app, raise_server_exceptions=False)

                resposta = mutacao(cliente, "/tela/coletas")

                self.assertEqual(resposta.status_code, 422)
                self.assertTrue(resposta.headers["content-type"].startswith("text/html"))
                self.assertEqual(resposta.headers["HX-Retarget"], "#aviso")
                self.assertIn('id="aviso"', resposta.text)
                self.assertNotIn('"detail"', resposta.text)
                self.assertEqual(provider.chamadas, 0)

    def test_filtro_invalido_na_coleta_e_422_e_nao_coleta(self):
        provider = ProviderBloqueado()
        provider.soltar.set()
        app, _ = app_de_coleta(provider)
        cliente = ClienteLogado(app, raise_server_exceptions=False)

        self.assertEqual(mutacao(cliente, "/tela/coletas", novas="talvez").status_code, 422)
        self.assertEqual(mutacao(cliente, "/tela/coletas", q="x" * 201).status_code, 422)
        self.assertEqual(provider.chamadas, 0)

    def test_duas_coletas_ao_mesmo_tempo_a_segunda_e_409_html_no_aviso(self):
        provider = ProviderBloqueado()
        app, repo = app_de_coleta(provider)
        resposta = {}
        primeira = threading.Thread(
            target=lambda: resposta.update(r=mutacao(ClienteLogado(app, raise_server_exceptions=False), "/tela/coletas"))
        )
        primeira.start()
        self.addCleanup(primeira.join, ESPERA)
        self.addCleanup(provider.soltar.set)
        self.assertTrue(provider.comecou.wait(ESPERA))

        segunda = mutacao(ClienteLogado(app, raise_server_exceptions=False), "/tela/coletas")

        self.assertEqual(segunda.status_code, 409)
        self.assertTrue(segunda.headers["content-type"].startswith("text/html"))
        self.assertEqual(segunda.headers["HX-Retarget"], "#aviso")
        self.assertEqual(segunda.headers["HX-Reswap"], "outerHTML")
        self.assertIn("coleta em andamento", Pagina(segunda.text).por_id("aviso").texto)
        provider.soltar.set()
        primeira.join(ESPERA)
        self.assertEqual(resposta["r"].status_code, 200)
        self.assertEqual(provider.chamadas, 1, "só a primeira coleta chamou o provider")
        self.assertEqual(repo.contar(somente_novas=False), 1)

    def test_tela_e_api_dividem_a_mesma_trava(self):
        provider = ProviderBloqueado()
        app, _ = app_de_coleta(provider)
        resposta = {}
        primeira = threading.Thread(target=lambda: resposta.update(r=ClienteLogado(app).post("/coletas", json={})))
        primeira.start()
        self.addCleanup(primeira.join, ESPERA)
        self.addCleanup(provider.soltar.set)
        self.assertTrue(provider.comecou.wait(ESPERA))

        segunda = mutacao(ClienteLogado(app, raise_server_exceptions=False), "/tela/coletas")

        self.assertEqual(segunda.status_code, 409)
        provider.soltar.set()
        primeira.join(ESPERA)
        self.assertEqual(provider.chamadas, 1)

    def test_409_sem_htmx_e_a_pagina_de_erro_e_na_api_continua_json(self):
        provider = ProviderBloqueado()
        app, _ = app_de_coleta(provider)
        primeira = threading.Thread(target=lambda: ClienteLogado(app).post("/coletas", json={}))
        primeira.start()
        self.addCleanup(primeira.join, ESPERA)
        self.addCleanup(provider.soltar.set)
        self.assertTrue(provider.comecou.wait(ESPERA))
        cliente = ClienteLogado(app, raise_server_exceptions=False)

        api = cliente.post("/coletas", json={})
        self.assertEqual((api.status_code, api.json()), (409, {"detail": "coleta em andamento"}))
        sem_htmx = cliente.post("/tela/coletas", headers={"Origin": "http://testserver"})
        self.assertEqual(sem_htmx.status_code, 403)  # mutação sem HX-Request nem chega à coleta
        self.assertEqual(provider.chamadas, 1)

    def test_erro_do_servidor_e_500_e_a_trava_solta(self):
        class FalhaDoBanco(VagaEncontradaRepositoryMemoria):
            def registrar(self, vagas, termo):
                raise ValueError("banco caiu")

        provider = ProviderBloqueado()
        provider.soltar.set()
        app, _ = app_de_coleta(provider, encontradas=FalhaDoBanco(1))
        cliente = ClienteLogado(app, raise_server_exceptions=False)

        self.assertEqual(mutacao(cliente, "/tela/coletas").status_code, 500)
        self.assertEqual(mutacao(cliente, "/tela/coletas").status_code, 500)  # nunca 409

    def test_csrf_coleta_sem_hx_request_ou_origem_errada_e_403_e_nao_coleta(self):
        provider = ProviderBloqueado()
        provider.soltar.set()
        app, repo = app_de_coleta(provider)
        cliente = ClienteLogado(app, raise_server_exceptions=False)

        for cabecalhos in ({"Origin": "http://testserver"}, {"HX-Request": "true", "Origin": "http://evil.test"}, {}):
            with self.subTest(cabecalhos=cabecalhos):
                self.assertEqual(cliente.post("/tela/coletas", headers=cabecalhos).status_code, 403)
        self.assertEqual(provider.chamadas, 0)
        self.assertEqual(cliente.get("/tela/coletas").status_code, 405)

    def test_409_esta_no_response_handling_do_htmx(self):
        import json

        cliente, _ = criar([])
        meta = next(m for m in Pagina(cliente.get("/tela").text).tags("meta") if m.attrs.get("name") == "htmx-config")
        regras = json.loads(meta.attrs["content"])["responseHandling"]

        regra = next(r for r in regras if re.fullmatch(r["code"], "409"))

        self.assertTrue(regra["swap"])
        self.assertIs(regra["error"], False)


if __name__ == "__main__":
    unittest.main()
