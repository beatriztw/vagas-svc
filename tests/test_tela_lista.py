import html
import json
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote

from fastapi.testclient import TestClient

from src.controller.api import criar_app
from src.model.vaga_externa import VagaExterna
from src.repository.base import CurriculoRepositoryMemoria, PerfilBuscaRepositoryMemoria, VagaEncontradaRepositoryMemoria
from src.service.busca_service import BuscaService

RAIZ = Path(__file__).resolve().parent.parent
HX = {"HX-Request": "true"}
VOID = {"meta", "link", "img", "input", "br", "hr"}

# Urls de teste escritas à mão: espaço, acento, e ?, &, #, = dentro da url da vaga.
URLS_DIFICEIS = [
    "https://exemplo.test/vaga com espaço/1",
    "https://exemplo.test/conciliação/analista",
    "https://x.com/vaga?a=1&b=2#c=d",
    "https://exemplo.test/p?q=a+b&r=%C3%A7%20x",
]


class Elemento:
    def __init__(self, tag: str, attrs: dict):
        self.tag, self.attrs, self.texto = tag, attrs, ""


class Pagina(HTMLParser):
    """Lê o HTML como um navegador leria: atributos já desescapados, texto sem tags."""

    def __init__(self, texto: str):
        super().__init__()
        self.elementos: list[Elemento] = []
        self._abertos: list[Elemento] = []
        self.feed(texto)

    def handle_starttag(self, tag, attrs):
        elemento = Elemento(tag, dict(attrs))
        self.elementos.append(elemento)
        if tag not in VOID:
            self._abertos.append(elemento)

    def handle_endtag(self, tag):
        while self._abertos:
            if self._abertos.pop().tag == tag:
                break

    def handle_data(self, dado):
        for elemento in self._abertos:
            elemento.texto += dado

    def tags(self, tag: str) -> list[Elemento]:
        return [e for e in self.elementos if e.tag == tag]

    def cards(self) -> list[Elemento]:
        return [e for e in self.elementos if e.tag == "a" and "card" in (e.attrs.get("class") or "").split()]

    def por_id(self, id: str) -> Elemento:
        return next(e for e in self.elementos if e.attrs.get("id") == id)


def vaga(i: int | str, titulo: str | None = None, **campos) -> VagaExterna:
    # Fixtures escritas à mão: não são respostas gravadas de provider.
    campos.setdefault("local", "Rio de Janeiro")
    return VagaExterna(titulo or f"Analista {i}", "Empresa Teste", "Remotar", f"https://exemplo.test/vaga/{i}", **campos)


def criar(vagas: list[VagaExterna]) -> tuple[TestClient, VagaEncontradaRepositoryMemoria]:
    repo = VagaEncontradaRepositoryMemoria()
    if vagas:
        repo.registrar(vagas, "teste")
    app = criar_app(PerfilBuscaRepositoryMemoria(), CurriculoRepositoryMemoria(), BuscaService([]), encontradas=repo)
    return TestClient(app, raise_server_exceptions=False), repo


def sem_selecao(texto: str) -> str:
    return texto.replace(' aria-current="true"', "")


class ListaTest(unittest.TestCase):
    def test_primeira_pagina_tem_50_cards_e_o_contador_vem_de_contar(self):
        cliente, _ = criar([vaga(i) for i in range(120)])

        pagina = Pagina(cliente.get("/tela").text)

        self.assertEqual(len(pagina.cards()), 50)
        self.assertIn("120 encontradas", pagina.por_id("vagas").texto)
        self.assertNotIn("50 encontradas", pagina.por_id("vagas").texto)

    def test_primeira_vaga_vem_selecionada_com_o_detalhe(self):
        cliente, _ = criar([vaga(i) for i in range(3)])

        pagina = Pagina(cliente.get("/tela").text)

        selecionados = [c for c in pagina.cards() if "aria-current" in c.attrs]
        self.assertEqual(len(selecionados), 1)
        self.assertIs(selecionados[0], pagina.cards()[0])
        detalhe = pagina.por_id("detalhe")
        self.assertEqual(detalhe.attrs["data-url"], selecionados[0].attrs["data-url"])
        self.assertIn(pagina.por_id("t-detalhe").texto, selecionados[0].texto)

    def test_carregar_mais_percorre_todas_sem_repetir(self):
        cliente, _ = criar([vaga(i) for i in range(120)])
        pagina = Pagina(cliente.get("/tela").text)
        urls = [c.attrs["data-url"] for c in pagina.cards()]
        sentinelas = [e for e in pagina.tags("li") if "mais" in (e.attrs.get("class") or "")]
        self.assertEqual(len(sentinelas), 1)
        self.assertEqual(sentinelas[0].attrs["hx-trigger"], "revealed")
        proximo = sentinelas[0].attrs["hx-get"]
        self.assertIn("deslocamento=50", proximo)

        paginas = 1
        while proximo:
            resposta = cliente.get(proximo, headers=HX)
            self.assertEqual(resposta.status_code, 200)
            self.assertNotIn("<html", resposta.text)
            self.assertNotIn('id="lista-vagas"', resposta.text)
            self.assertNotIn("encontradas", resposta.text)
            parcial = Pagina(resposta.text)
            urls += [c.attrs["data-url"] for c in parcial.cards()]
            sentinelas = [e for e in parcial.tags("li") if "mais" in (e.attrs.get("class") or "")]
            proximo = sentinelas[0].attrs["hx-get"] if sentinelas else None
            paginas += 1

        self.assertEqual(paginas, 3)
        self.assertEqual(len(urls), 120)
        self.assertEqual(len(set(urls)), 120)

    def test_sem_vagas_mostra_o_vazio_sem_sentinela(self):
        cliente, _ = criar([])

        html_ = cliente.get("/tela").text
        pagina = Pagina(html_)

        self.assertIn("Nenhuma vaga ainda; rode uma coleta", html_)
        self.assertIn("0 encontradas", pagina.por_id("vagas").texto)
        self.assertIn("Selecione uma vaga", pagina.por_id("detalhe").texto)
        self.assertEqual(pagina.cards(), [])
        self.assertNotIn('class="mais"', html_)

    def test_q_passa_adiante_no_carregar_mais_ja_codificado(self):
        vagas = [vaga(i, f"Analista de conciliação contábil {i}") for i in range(60)] + [vaga("x", "Dev Java")]
        cliente, _ = criar(vagas)

        pagina = Pagina(cliente.get("/tela", params={"q": "conciliação contábil"}).text)

        self.assertEqual(len(pagina.cards()), 50)
        self.assertIn("60 encontradas", pagina.por_id("vagas").texto)
        proximo = next(e for e in pagina.tags("li") if "mais" in (e.attrs.get("class") or "")).attrs["hx-get"]
        self.assertEqual(proximo, "/tela/vagas?deslocamento=50&q=" + quote("conciliação contábil"))
        resposta = cliente.get(proximo, headers=HX)
        self.assertEqual(len(Pagina(resposta.text).cards()), 10)
        self.assertNotIn("Java", resposta.text)

    def test_partial_da_lista_e_um_pedaco_da_pagina_inteira(self):
        cliente, _ = criar([vaga(i) for i in range(5)])

        parcial = cliente.get("/tela/vagas", headers=HX).text
        inteira = cliente.get("/tela/vagas").text

        self.assertNotIn("<html", parcial)
        self.assertIn("<html", inteira)
        self.assertIn(parcial.strip(), sem_selecao(inteira))

    def test_partial_do_detalhe_e_um_pedaco_da_pagina_inteira(self):
        cliente, _ = criar([vaga(i) for i in range(3)])
        url = "/tela/vagas/detalhe?url=" + quote("https://exemplo.test/vaga/1", safe="/")

        parcial = cliente.get(url, headers=HX).text
        inteira = cliente.get(url).text

        self.assertNotIn("<html", parcial)
        self.assertIn("<html", inteira)
        self.assertIn(parcial.strip(), inteira)

    def test_restauro_de_historico_do_htmx_recebe_a_pagina_inteira(self):
        cliente, _ = criar([vaga(1)])

        resposta = cliente.get("/tela/vagas", headers={**HX, "HX-History-Restore-Request": "true"})

        self.assertIn("<html", resposta.text)

    def test_cards_mostram_empresa_local_e_modalidade(self):
        cliente, _ = criar(
            [
                vaga(1, local=None, remoto=None),
                vaga(2, remoto=True),
                vaga(3, remoto=False),
            ]
        )

        textos = {c.attrs["data-url"]: re.sub(r"\s+", " ", c.texto).strip() for c in Pagina(cliente.get("/tela").text).cards()}

        self.assertEqual(textos["https://exemplo.test/vaga/1"], "Analista 1 nova Empresa Teste - — não informado")
        self.assertEqual(textos["https://exemplo.test/vaga/2"], "Analista 2 nova Empresa Teste - Rio de Janeiro remoto")
        self.assertEqual(textos["https://exemplo.test/vaga/3"], "Analista 3 nova Empresa Teste - Rio de Janeiro presencial")

    def test_deslocamento_invalido_e_422_html(self):
        cliente, _ = criar([vaga(1)])

        for valor in ("abc", "-1", "999999999", ""):
            for cabecalhos in (HX, {}):
                with self.subTest(valor=valor, cabecalhos=cabecalhos):
                    resposta = cliente.get("/tela/vagas", params={"deslocamento": valor}, headers=cabecalhos)

                    self.assertEqual(resposta.status_code, 422)
                    self.assertIn("text/html", resposta.headers["content-type"])
                    self.assertIn('role="alert"', resposta.text)

    def test_busca_grande_demais_e_422_html(self):
        cliente, _ = criar([vaga(1)])

        resposta = cliente.get("/tela", params={"q": "a" * 500})

        self.assertEqual(resposta.status_code, 422)
        self.assertIn('role="alert"', resposta.text)


class DetalheTest(unittest.TestCase):
    def test_url_pedida_seleciona_a_vaga_e_o_card(self):
        cliente, _ = criar([vaga(i) for i in range(5)])

        pagina = Pagina(cliente.get("/tela", params={"url": "https://exemplo.test/vaga/3"}).text)

        self.assertEqual(pagina.por_id("detalhe").attrs["data-url"], "https://exemplo.test/vaga/3")
        selecionados = [c.attrs["data-url"] for c in pagina.cards() if "aria-current" in c.attrs]
        self.assertEqual(selecionados, ["https://exemplo.test/vaga/3"])
        self.assertEqual(pagina.por_id("t-detalhe").texto, "Analista 3")

    def test_url_inexistente_na_pagina_e_404_com_aviso_e_lista(self):
        cliente, _ = criar([vaga(1), vaga(2)])

        resposta = cliente.get("/tela", params={"url": "https://exemplo.test/nao-existe"})
        pagina = Pagina(resposta.text)

        self.assertEqual(resposta.status_code, 404)
        self.assertIn("<html", resposta.text)
        self.assertIn("Vaga não encontrada", pagina.por_id("detalhe").texto)
        self.assertEqual(len(pagina.cards()), 2)
        self.assertFalse(any("aria-current" in c.attrs for c in pagina.cards()))

    def test_url_pedida_vazia_ou_gigante_tambem_nao_e_encontrada(self):
        cliente, _ = criar([vaga(1)])

        self.assertEqual(cliente.get("/tela", params={"url": "x" * 5000}).status_code, 404)
        self.assertEqual(cliente.get("/tela/vagas/detalhe", headers=HX).status_code, 404)

    def test_detalhe_inexistente_com_htmx_e_partial_404(self):
        cliente, _ = criar([vaga(1)])

        resposta = cliente.get("/tela/vagas/detalhe", params={"url": "https://exemplo.test/nao-existe"}, headers=HX)

        self.assertEqual(resposta.status_code, 404)
        self.assertNotIn("<html", resposta.text)
        self.assertIn('id="detalhe"', resposta.text)
        self.assertIn("Vaga não encontrada", resposta.text)

    def test_detalhe_inexistente_sem_htmx_e_a_pagina_404(self):
        cliente, _ = criar([vaga(1)])

        resposta = cliente.get("/tela/vagas/detalhe", params={"url": "https://exemplo.test/nao-existe"})

        self.assertEqual(resposta.status_code, 404)
        self.assertIn("<html", resposta.text)

    def test_detalhe_sem_htmx_e_a_pagina_com_a_vaga_selecionada(self):
        cliente, _ = criar([vaga(i) for i in range(3)])

        pagina = Pagina(cliente.get("/tela/vagas/detalhe", params={"url": "https://exemplo.test/vaga/2"}).text)

        self.assertEqual(len(pagina.tags("html")), 1)
        self.assertEqual(pagina.por_id("detalhe").attrs["data-url"], "https://exemplo.test/vaga/2")
        self.assertEqual([c.attrs["data-url"] for c in pagina.cards() if "aria-current" in c.attrs], ["https://exemplo.test/vaga/2"])

    def test_abrir_o_detalhe_nao_marca_a_vaga_como_vista(self):
        cliente, repo = criar([vaga(1)])

        cliente.get("/tela/vagas/detalhe", params={"url": "https://exemplo.test/vaga/1"}, headers=HX)
        cliente.get("/tela")

        self.assertIsNone(repo.obter("https://exemplo.test/vaga/1").vista_em)

    def test_detalhe_tem_chips_link_seguro_e_painel_de_ia(self):
        cliente, _ = criar([vaga(1, remoto=True)])

        pagina = Pagina(cliente.get("/tela/vagas/detalhe", params={"url": "https://exemplo.test/vaga/1"}, headers=HX).text)

        detalhe = pagina.por_id("detalhe")
        self.assertIn("Empresa Teste", detalhe.texto)
        self.assertIn("Rio de Janeiro", detalhe.texto)
        link = next(a for a in pagina.tags("a") if a.texto.strip() == "Ver vaga original")
        self.assertEqual(link.attrs["href"], "https://exemplo.test/vaga/1")
        self.assertEqual(link.attrs["target"], "_blank")
        self.assertEqual(link.attrs["rel"], "noopener noreferrer")
        botoes = {b.texto.strip(): b.attrs for b in pagina.tags("button")}
        # O "Gerar" desabilitado virou o painel de IA (carrega por hx-get); "Marcar como aplicada" é o formulário de candidatura.
        self.assertNotIn("Gerar", botoes)
        self.assertEqual(pagina.por_id("ia").attrs["hx-trigger"], "load")
        self.assertNotIn("disabled", botoes["Marcar como aplicada"])
        self.assertNotIn("Descrição", detalhe.texto)
        self.assertNotIn("Requisitos", detalhe.texto)

    def test_url_javascript_mostra_o_texto_sem_link(self):
        cliente, _ = criar([VagaExterna("Vaga X", "Empresa", "Remotar", "javascript:alert(1)")])

        for resposta in (
            cliente.get("/tela"),
            cliente.get("/tela/vagas/detalhe", params={"url": "javascript:alert(1)"}, headers=HX),
        ):
            pagina = Pagina(resposta.text)
            self.assertIn("Ver vaga original", resposta.text)
            for a in pagina.tags("a"):
                self.assertFalse(a.attrs.get("href", "").lower().startswith("javascript"), a.attrs)
            self.assertFalse(any(a.texto.strip() == "Ver vaga original" for a in pagina.tags("a")))


class CodificacaoDeUrlTest(unittest.TestCase):
    def test_ida_e_volta_com_espaco_acento_e_caracteres_de_query(self):
        for url in URLS_DIFICEIS:
            with self.subTest(url=url):
                cliente, _ = criar([VagaExterna(f"Título de {url[-6:]}", "Empresa", "Remotar", url), vaga(9)])
                card = next(c for c in Pagina(cliente.get("/tela").text).cards() if c.attrs["data-url"] == url)
                esperado = quote(url, safe="/")

                self.assertEqual(card.attrs["hx-get"], "/tela/vagas/detalhe?url=" + esperado)
                self.assertEqual(card.attrs["href"], "/tela?url=" + esperado)
                self.assertEqual(card.attrs["hx-push-url"], card.attrs["href"])
                # Nada de caractere de query solto dentro do valor.
                valor = card.attrs["hx-get"].split("?url=", 1)[1]
                self.assertNotRegex(valor, r"[ &#=?+]")

                parcial = Pagina(cliente.get(card.attrs["hx-get"], headers=HX).text)
                self.assertEqual(parcial.por_id("detalhe").attrs["data-url"], url)
                inteira = Pagina(cliente.get(card.attrs["href"]).text)
                self.assertEqual(inteira.por_id("detalhe").attrs["data-url"], url)
                self.assertEqual([c.attrs["data-url"] for c in inteira.cards() if "aria-current" in c.attrs], [url])


class XssTest(unittest.TestCase):
    def test_titulo_empresa_e_local_com_html_e_aspas_sao_escapados(self):
        titulo = "<script>alert(1)</script> \"x\" 'y'"
        empresa = "\"><img src=x onerror=alert(1)>"
        local = "'><b onmouseover=alert(2)>"
        url = 'https://exemplo.test/v?a="onmouseover="alert(3)&b=\'><i>'
        cliente, _ = criar([VagaExterna(titulo, empresa, "Remotar", url, local=local)])

        for texto in (
            cliente.get("/tela").text,
            cliente.get("/tela/vagas", headers=HX).text,
            cliente.get("/tela/vagas/detalhe", params={"url": url}, headers=HX).text,
        ):
            self.assertNotIn("<script>alert", texto)
            self.assertNotIn("<img src=x", texto)
            self.assertNotIn("<b onmouseover", texto)
            self.assertNotIn("<i>", texto)
            pagina = Pagina(texto)
            self.assertEqual(pagina.tags("img"), [])
            self.assertEqual(pagina.tags("i"), [])
            self.assertEqual(pagina.tags("b"), [])
            self.assertEqual([s for s in pagina.tags("script") if "src" not in s.attrs], [])
            for elemento in pagina.elementos:
                for nome in elemento.attrs:
                    self.assertFalse(nome.startswith("on"), (elemento.tag, nome))
                self.assertNotIn("onmouseover", elemento.attrs)

        detalhe = Pagina(cliente.get("/tela/vagas/detalhe", params={"url": url}, headers=HX).text)
        self.assertEqual(detalhe.por_id("t-detalhe").texto, titulo)
        self.assertEqual(detalhe.por_id("detalhe").attrs["data-url"], url)
        self.assertIn(empresa, detalhe.por_id("detalhe").texto)

    def test_nenhuma_url_de_vaga_entra_em_atributo_de_navegacao_sem_codificar(self):
        url = "https://exemplo.test/v?a=1&b=2#c=d"
        cliente, _ = criar([VagaExterna("T", "E", "Remotar", url)])

        texto = cliente.get("/tela").text

        self.assertNotIn('/tela/vagas/detalhe?url=https:', texto)
        self.assertNotIn('"/tela?url=https:', texto)
        self.assertNotIn('url=https://exemplo.test/v?', texto)


class ConfiguracaoDoHtmxTest(unittest.TestCase):
    def setUp(self):
        cliente, _ = criar([vaga(1)])
        self.pagina = Pagina(cliente.get("/tela").text)
        meta = next(m for m in self.pagina.tags("meta") if m.attrs.get("name") == "htmx-config")
        self.config = json.loads(meta.attrs["content"])

    def tratamento(self, codigo: int) -> dict:
        """Mesma regra do htmx 2: vale a primeira entrada cujo `code` (regex) casa com o status."""
        for regra in self.config["responseHandling"]:
            if re.fullmatch(regra["code"], str(codigo)):
                return regra
        return {}

    def test_sem_eval_e_sem_estilos_do_htmx(self):
        self.assertIs(self.config["allowEval"], False)
        self.assertIs(self.config["includeIndicatorStyles"], False)

    def test_404_e_422_trocam_sem_erro_e_5xx_nao_troca(self):
        for codigo in (200, 201, 301):
            self.assertTrue(self.tratamento(codigo)["swap"], codigo)
        self.assertFalse(self.tratamento(204)["swap"])
        for codigo in (404, 422):
            regra = self.tratamento(codigo)
            self.assertTrue(regra["swap"], codigo)
            self.assertFalse(regra.get("error", False), codigo)
        for codigo in (400, 403, 500, 502, 503):
            regra = self.tratamento(codigo)
            self.assertFalse(regra["swap"], codigo)
            self.assertTrue(regra["error"], codigo)

    def test_pagina_e_scripts_nao_usam_hx_on_nem_eval(self):
        for elemento in self.pagina.elementos:
            for nome in elemento.attrs:
                self.assertFalse(nome.startswith("hx-on"), (elemento.tag, nome))
        for arquivo in [*(RAIZ / "src" / "templates").rglob("*.html"), RAIZ / "src" / "static" / "tela.js"]:
            conteudo = arquivo.read_text()
            self.assertNotRegex(conteudo, r"hx-on", arquivo.name)
        js = (RAIZ / "src" / "static" / "tela.js").read_text()
        self.assertNotRegex(js, r"\beval\s*\(|new\s+Function|setTimeout\s*\(\s*[\"']")

    def test_tela_js_e_servido_e_escuta_os_eventos_de_erro(self):
        cliente, _ = criar([])

        resposta = cliente.get("/static/tela.js")

        self.assertEqual(resposta.status_code, 200)
        for evento in ("htmx:responseError", "htmx:sendError"):
            self.assertIn(evento, resposta.text)
        self.assertIn("/static/tela.js", cliente.get("/tela").text)


if __name__ == "__main__":
    unittest.main()
