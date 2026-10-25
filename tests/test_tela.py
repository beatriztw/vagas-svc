import hashlib
import os
import re
import tempfile
import unittest
from pathlib import Path

from fastapi import Depends, Request
from fastapi.testclient import TestClient

from src.controller.api import criar_app
from src.controller.tela_seguranca import estatico, exigir_mutacao_da_tela, renderizar, templates, url_segura
from src.model.erros import DadoInvalido
from src.model.vaga_externa import VagaExterna
from src.repository.base import CurriculoRepositoryMemoria, PerfilBuscaRepositoryMemoria, VagaEncontradaRepositoryMemoria
from src.service.busca_service import BuscaService

RAIZ = Path(__file__).resolve().parent.parent
HTMX = RAIZ / "src" / "static" / "htmx.min.js"
README_STATIC = RAIZ / "src" / "static" / "README.md"

# Fixture escrita à mão (não é resposta gravada de provider).
VAGA = VagaExterna("Analista de Dados", "Empresa Teste", "Remotar", "https://exemplo.test/vaga/1", remoto=True)


def criar_cliente(encontradas=None, **kwargs) -> tuple[TestClient, object]:
    app = criar_app(
        PerfilBuscaRepositoryMemoria(1),
        CurriculoRepositoryMemoria(1),
        BuscaService([]),
        encontradas=encontradas,
    )
    return TestClient(app, **kwargs), app


class PaginaTest(unittest.TestCase):
    def test_pagina_vazia_mostra_o_vazio_e_o_botao_de_coleta(self):
        cliente, _ = criar_cliente()

        resposta = cliente.get("/tela")

        self.assertEqual(resposta.status_code, 200)
        html = resposta.text
        self.assertIn("<html", html)
        self.assertIn("Nenhuma vaga ainda; rode uma coleta", html)
        # O botão de coleta do estado vazio está habilitado e chama POST /tela/coletas (PR4).
        self.assertRegex(html, r'<button[^>]*hx-post="/tela/coletas"[^>]*>\s*Buscar vagas novas\s*</button>')
        self.assertNotRegex(html, r"<button[^>]*(?<![-\w])disabled(?![-\w])[^>]*>\s*Buscar vagas novas")
        self.assertRegex(html, r'<a[^>]*href="/tela/perfil"[^>]*>\s*Meu perfil\s*</a>')
        self.assertIn("Selecione uma vaga", html)
        self.assertIn("Nenhuma candidatura ainda", html)
        self.assertIn('id="lista-vagas"', html)
        self.assertIn('id="detalhe"', html)
        self.assertIn('id="aplicadas"', html)

    def test_com_vaga_no_historico_nao_mostra_o_vazio(self):
        encontradas = VagaEncontradaRepositoryMemoria(1)
        encontradas.registrar([VAGA], "dados")
        cliente, _ = criar_cliente(encontradas)

        html = cliente.get("/tela").text

        self.assertNotIn("Nenhuma vaga ainda", html)

    def test_get_tela_com_hx_request_continua_sendo_a_pagina_inteira(self):
        cliente, _ = criar_cliente()

        self.assertIn("<html", cliente.get("/tela", headers={"HX-Request": "true"}).text)

    def test_raiz_redireciona_para_a_tela(self):
        cliente, _ = criar_cliente()

        resposta = cliente.get("/", follow_redirects=False)

        self.assertEqual(resposta.status_code, 307)
        self.assertEqual(resposta.headers["location"], "/tela")

    def test_cabecalhos_de_seguranca_na_tela(self):
        cliente, _ = criar_cliente()

        resposta = cliente.get("/tela")

        self.assertEqual(
            resposta.headers["content-security-policy"],
            "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        self.assertEqual(resposta.headers["cache-control"], "no-store")
        self.assertEqual(resposta.headers["x-content-type-options"], "nosniff")
        self.assertEqual(resposta.headers["referrer-policy"], "same-origin")

    def test_rotas_json_nao_levam_os_cabecalhos_da_tela(self):
        cliente, _ = criar_cliente()

        resposta = cliente.get("/saude")

        self.assertNotIn("content-security-policy", resposta.headers)
        self.assertNotIn("cache-control", resposta.headers)

    def test_prefixo_parecido_nao_e_tela(self):
        cliente, _ = criar_cliente()

        self.assertNotIn("cache-control", cliente.get("/telaxyz").headers)

    def test_pagina_nao_tem_js_nem_css_inline_e_usa_so_o_static(self):
        cliente, _ = criar_cliente()

        html = cliente.get("/tela").text

        self.assertNotRegex(html, r"<script(?![^>]*\bsrc=)")
        self.assertNotRegex(html, r"<style")
        self.assertNotRegex(html, r"\sstyle=")
        self.assertNotRegex(html, r"\son\w+=")
        self.assertIn('src="/static/htmx.min.js?v=', html)
        self.assertIn('href="/static/tela.css?v=', html)
        self.assertIn('"includeIndicatorStyles":false', html)


class StaticTest(unittest.TestCase):
    def test_url_do_estatico_leva_a_versao_do_conteudo(self):
        # Sem a versão o navegador seguia com o CSS do deploy anterior (o servidor não manda cache-control).
        css = RAIZ / "src" / "static" / "tela.css"
        versao = hashlib.sha256(css.read_bytes()).hexdigest()[:10]
        self.assertEqual(estatico("tela.css"), f"/static/tela.css?v={versao}")
        self.assertIn(f'href="/static/tela.css?v={versao}"', criar_cliente()[0].get("/tela").text)
        # A url com a versão é servida como o arquivo.
        self.assertEqual(criar_cliente()[0].get(estatico("tela.css")).text, css.read_text())

    def test_estatico_que_nao_existe_sai_sem_versao_e_a_pagina_abre(self):
        self.assertEqual(estatico("nao-existe.css"), "/static/nao-existe.css")

    def test_htmx_e_css_servidos_mesmo_com_outro_cwd(self):
        cliente, _ = criar_cliente()
        antes = os.getcwd()
        # O Jinja guarda os templates já compilados: sem limpar, o teste passaria pelo cache.
        templates.env.cache.clear()
        with tempfile.TemporaryDirectory() as vazio:
            os.chdir(vazio)
            try:
                htmx = cliente.get("/static/htmx.min.js")
                css = cliente.get("/static/tela.css")
                pagina = cliente.get("/tela")
            finally:
                os.chdir(antes)

        self.assertEqual(htmx.status_code, 200)
        self.assertEqual(css.status_code, 200)
        self.assertEqual(pagina.status_code, 200)
        self.assertEqual(htmx.headers["x-content-type-options"], "nosniff")
        self.assertIn("javascript", htmx.headers["content-type"])

    def test_sha256_do_htmx_bate_com_o_registrado(self):
        registrado = re.search(r"sha256:\s*([0-9a-f]{64})", README_STATIC.read_text())
        self.assertIsNotNone(registrado)

        self.assertEqual(hashlib.sha256(HTMX.read_bytes()).hexdigest(), registrado.group(1))

    def test_css_tem_os_tokens_do_plano(self):
        css = (RAIZ / "src" / "static" / "tela.css").read_text()

        for token in ("--accent: #0F766E", "--ai: #9D1F8F", "--fundo: #F4F6F5", "--borda: #DCE3E1", "--texto: #16201F", "--sec: #4D5C59"):
            self.assertIn(token, css)


class UrlSeguraTest(unittest.TestCase):
    def test_http_e_https_com_host_viram_link(self):
        for url in ("http://exemplo.test/vaga", "https://exemplo.test/vaga?a=1&b=2#c=d", "HTTPS://Exemplo.test/x"):
            self.assertEqual(url_segura(url), url)

    def test_esquemas_perigosos_relativos_e_malformados_nao_viram_link(self):
        for url in (
            "javascript:alert(1)",
            "JaVaScRiPt:alert(1)",
            " javascript:alert(1)",
            "\tjavascript:alert(1)",
            " https://exemplo.test/vaga",
            "https://exemplo.test/vaga\n",
            "https://exemplo.test/va\tga",
            "java\tscript:alert(1)",
            "java\nscript:alert(1)",
            "javascript:alert(1) ",
            "data:text/html,<script>alert(1)</script>",
            "vbscript:msgbox(1)",
            "/vaga/1",
            "//exemplo.test/vaga",
            "vaga/1",
            "https:exemplo.test",
            "https:///sem-host",
            "http://",
            "ftp://exemplo.test/arquivo",
            "https://[::1",
            "",
            None,
            42,
        ):
            with self.subTest(url=url):
                self.assertIsNone(url_segura(url))

    def test_filtro_registrado_no_jinja_nao_gera_href_para_javascript(self):
        modelo = templates.env.from_string('{% if u|url_segura %}<a href="{{ u|url_segura }}">x</a>{% else %}sem link{% endif %}')

        self.assertEqual(modelo.render(u="javascript:alert(1)"), "sem link")
        self.assertEqual(modelo.render(u="https://exemplo.test/v?a=1&b=2"), '<a href="https://exemplo.test/v?a=1&amp;b=2">x</a>')


class RotasDeTesteTest(unittest.TestCase):
    """Rotas montadas só aqui: o PR1 não tem rota mutável nem rota de partial."""

    def setUp(self):
        self.cliente, app = criar_cliente()

        @app.post("/tela/_teste/mutacao", dependencies=[Depends(exigir_mutacao_da_tela)])
        def mutacao():
            return {"ok": True}

        @app.get("/tela/_teste/partial")
        def partial(request: Request):
            return renderizar(request, "tela/index.html", "tela/_aplicadas.html")

        @app.get("/tela/_teste/dado-invalido")
        def dado_invalido():
            raise DadoInvalido("<script>alert(1)</script> inválido")

        @app.get("/tela/_teste/valor")
        def valor():
            raise ValueError("config quebrada")

        @app.get("/_teste/dado-invalido-json")
        def dado_invalido_json():
            raise DadoInvalido("campo inválido")

        self.cliente = TestClient(app, raise_server_exceptions=False)

    # Partial x página inteira

    def test_com_hx_request_devolve_so_o_partial(self):
        resposta = self.cliente.get("/tela/_teste/partial", headers={"HX-Request": "true"})

        self.assertEqual(resposta.status_code, 200)
        self.assertNotIn("<html", resposta.text)
        self.assertNotIn("<body", resposta.text)
        self.assertIn('id="aplicadas"', resposta.text)

    def test_sem_hx_request_devolve_a_pagina_inteira(self):
        resposta = self.cliente.get("/tela/_teste/partial")

        self.assertIn("<html", resposta.text)

    # CSRF

    def test_mutacao_sem_hx_request_e_403(self):
        resposta = self.cliente.post("/tela/_teste/mutacao", headers={"Origin": "http://testserver"})

        self.assertEqual(resposta.status_code, 403)

    def test_mutacao_com_origin_de_outro_host_e_403(self):
        for origem in ("http://evil.test", "null", "http://testserver.evil.test", "http://testserver:9999"):
            with self.subTest(origem=origem):
                resposta = self.cliente.post("/tela/_teste/mutacao", headers={"HX-Request": "true", "Origin": origem})

                self.assertEqual(resposta.status_code, 403)

    def test_mutacao_com_origin_igual_ao_host_passa(self):
        resposta = self.cliente.post("/tela/_teste/mutacao", headers={"HX-Request": "true", "Origin": "http://testserver"})

        self.assertEqual(resposta.status_code, 200)

    def test_mutacao_sem_origin_exige_sec_fetch_site_same_origin(self):
        cabecalhos = {"HX-Request": "true"}

        self.assertEqual(self.cliente.post("/tela/_teste/mutacao", headers=cabecalhos).status_code, 403)
        self.assertEqual(
            self.cliente.post("/tela/_teste/mutacao", headers={**cabecalhos, "Sec-Fetch-Site": "cross-site"}).status_code, 403
        )
        self.assertEqual(
            self.cliente.post("/tela/_teste/mutacao", headers={**cabecalhos, "Sec-Fetch-Site": "same-origin"}).status_code, 200
        )

    def test_origin_de_outro_host_vence_sec_fetch_site_same_origin(self):
        resposta = self.cliente.post(
            "/tela/_teste/mutacao",
            headers={"HX-Request": "true", "Origin": "http://evil.test", "Sec-Fetch-Site": "same-origin"},
        )

        self.assertEqual(resposta.status_code, 403)

    # Erros

    def test_dado_invalido_com_htmx_e_422_que_troca_o_aviso_da_pagina(self):
        resposta = self.cliente.get("/tela/_teste/dado-invalido", headers={"HX-Request": "true"})

        self.assertEqual(resposta.status_code, 422)
        self.assertIn("text/html", resposta.headers["content-type"])
        # O alvo do chamador (uma linha de tabela, por exemplo) não é trocado pelo erro.
        self.assertEqual(resposta.headers["hx-retarget"], "#aviso")
        self.assertEqual(resposta.headers["hx-reswap"], "outerHTML")
        self.assertNotIn("<html", resposta.text)
        self.assertRegex(resposta.text, r'<div id="aviso" class="erro" role="alert">')
        self.assertNotIn("hidden", resposta.text)
        self.assertNotIn("<script>", resposta.text)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt; inválido", resposta.text)
        self.assertEqual(resposta.headers["cache-control"], "no-store")

    def test_dado_invalido_sem_htmx_e_a_pagina_de_erro_inteira(self):
        resposta = self.cliente.get("/tela/_teste/dado-invalido")

        self.assertEqual(resposta.status_code, 422)
        self.assertIn("<html", resposta.text)
        self.assertNotIn("hx-retarget", resposta.headers)
        self.assertEqual(resposta.text.count('role="alert"'), 1)
        self.assertIn('<div id="aviso" class="erro" role="alert">', resposta.text)
        self.assertIn('href="/tela"', resposta.text)
        self.assertIn("Voltar para /tela", resposta.text)
        self.assertNotIn("<script>alert", resposta.text)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt; inválido", resposta.text)

    def test_dado_invalido_em_rota_json_continua_json(self):
        resposta = self.cliente.get("/_teste/dado-invalido-json")

        self.assertEqual(resposta.status_code, 422)
        self.assertEqual(resposta.json(), {"detail": "campo inválido"})

    def test_value_error_em_rota_da_tela_e_500(self):
        self.assertEqual(self.cliente.get("/tela/_teste/valor").status_code, 500)

    # Autoescape

    def test_titulo_com_script_e_escapado_no_jinja(self):
        modelo = templates.env.from_string("<h3>{{ titulo }}</h3>")

        html = modelo.render(titulo="<script>alert(1)</script>")

        self.assertEqual(html, "<h3>&lt;script&gt;alert(1)&lt;/script&gt;</h3>")


if __name__ == "__main__":
    unittest.main()
