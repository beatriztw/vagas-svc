"""Botão "Marcar como aplicada" no detalhe: POST /tela/candidaturas e o OOB do detalhe.

Vagas e títulos escritos à mão (não são respostas gravadas de provider).
"""

import unittest
from dataclasses import replace
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from src.controller.api import criar_app
from src.model.vaga_externa import VagaExterna
from src.repository.base import CurriculoRepositoryMemoria, PerfilBuscaRepositoryMemoria, VagaEncontradaRepositoryMemoria
from src.service.busca_service import BuscaService
from tests.test_tela_lista import Pagina

HX = {"HX-Request": "true", "Origin": "http://testserver"}
URL = "https://exemplo.test/vaga/1"
URLS_DIFICEIS = [
    "https://exemplo.test/vaga com espaço/1",
    "https://exemplo.test/conciliação/analista",
    "https://x.com/vaga?a=1&b=2#c=d",
]


def montar(*vagas: VagaExterna):
    encontradas = VagaEncontradaRepositoryMemoria(1)
    encontradas.registrar(list(vagas) or [VagaExterna("Analista de Dados", "Empresa Teste", "Remotar", URL)], "dados")
    app = criar_app(PerfilBuscaRepositoryMemoria(1), CurriculoRepositoryMemoria(1), BuscaService([]), encontradas=encontradas)
    return TestClient(app, raise_server_exceptions=False), app.state.candidaturas


def formulario(pagina: Pagina):
    return next(e for e in pagina.tags("form") if e.attrs.get("hx-post") == "/tela/candidaturas")


def hidden_url(pagina: Pagina) -> str:
    return next(e for e in pagina.tags("input") if e.attrs.get("name") == "url" and e.attrs.get("type") == "hidden").attrs["value"]


class DetalheComBotaoTest(unittest.TestCase):
    def test_detalhe_sem_candidatura_tem_o_formulario_com_hidden_e_hx_disabled_elt(self):
        api, _ = montar()

        pagina = Pagina(api.get("/tela/vagas/detalhe", params={"url": URL}, headers=HX).text)

        form = formulario(pagina)
        self.assertEqual(form.attrs["hx-target"], "#aplicadas")
        self.assertEqual(form.attrs["hx-swap"], "outerHTML")
        self.assertIn("hx-disabled-elt", form.attrs)
        self.assertEqual(hidden_url(pagina), URL)
        self.assertNotIn("hx-vals", api.get("/tela").text)
        botao = next(b for b in pagina.tags("button") if b.texto.strip() == "Marcar como aplicada")
        self.assertEqual(botao.attrs["type"], "submit")
        self.assertNotIn("disabled", botao.attrs)
        self.assertNotIn("Aplicada em", pagina.por_id("detalhe").texto)

    def test_detalhe_de_vaga_ja_aplicada_mostra_a_data_no_fuso_de_sao_paulo(self):
        api, servico = montar()
        candidatura, _ = servico.marcar_aplicada(URL)
        # 01:00Z de 12/10 ainda é 11/10 às 22:00 em São Paulo.
        servico.candidaturas._linhas[candidatura.id] = replace(
            servico.candidaturas._linhas[candidatura.id], aplicada_em=datetime(2026, 10, 12, 1, 0, tzinfo=timezone.utc)
        )

        for resposta in (api.get("/tela"), api.get("/tela/vagas/detalhe", params={"url": URL}, headers=HX)):
            pagina = Pagina(resposta.text)
            detalhe = pagina.por_id("detalhe")
            self.assertIn("Aplicada em 11/10", " ".join(detalhe.texto.split()))
            self.assertEqual([f for f in pagina.tags("form") if f.attrs.get("hx-post") == "/tela/candidaturas"], [])
            self.assertNotIn("Marcar como aplicada", detalhe.texto)


class PostTest(unittest.TestCase):
    def test_post_cria_devolve_o_bloco_aplicadas_e_o_oob_do_detalhe(self):
        api, servico = montar()

        resposta = api.post("/tela/candidaturas", data={"url": URL}, headers=HX)

        self.assertEqual(resposta.status_code, 200)
        self.assertNotIn("<html", resposta.text)
        pagina = Pagina(resposta.text)
        self.assertIn("1 candidaturas", pagina.por_id("aplicadas").texto)
        self.assertIn("Analista de Dados", pagina.por_id("aplicadas").texto)
        oob = pagina.por_id("acao-aplicar")
        self.assertEqual(oob.attrs["hx-swap-oob"], "outerHTML")
        self.assertIn("Aplicada em", oob.texto)
        self.assertEqual(servico.contar(), 1)
        self.assertEqual(servico.obter_por_url(URL).vaga_url, URL)

    def test_post_repetido_nao_duplica(self):
        api, servico = montar()

        primeira = api.post("/tela/candidaturas", data={"url": URL}, headers=HX)
        segunda = api.post("/tela/candidaturas", data={"url": URL}, headers=HX)

        self.assertEqual(segunda.status_code, 200)
        self.assertEqual(servico.contar(), 1)
        self.assertIn("1 candidaturas", segunda.text)
        self.assertEqual(Pagina(primeira.text).por_id("acao-aplicar").texto.split(), Pagina(segunda.text).por_id("acao-aplicar").texto.split())

    def test_get_nao_cria_nada(self):
        api, servico = montar()

        for rota in ("/tela/candidaturas", "/tela"):
            api.get(rota, params={"url": URL}, headers=HX)
        self.assertEqual(api.get("/tela/candidaturas", params={"url": URL}, headers=HX).status_code, 200)

        self.assertEqual(servico.contar(), 0)

    def test_sem_hx_request_ou_com_origin_de_outro_host_e_403(self):
        api, servico = montar()
        casos = [
            {"Origin": "http://testserver"},
            {"HX-Request": "true", "Origin": "http://evil.test"},
            {"HX-Request": "true"},
            {"HX-Request": "true", "Origin": "http://evil.test", "Sec-Fetch-Site": "same-origin"},
        ]

        for cabecalhos in casos:
            with self.subTest(cabecalhos=cabecalhos):
                self.assertEqual(api.post("/tela/candidaturas", data={"url": URL}, headers=cabecalhos).status_code, 403)

        self.assertEqual(servico.contar(), 0)

    def test_vaga_inexistente_ou_url_vazia_e_422_no_aviso(self):
        api, servico = montar()

        for dados in ({"url": "https://exemplo.test/nao-existe"}, {"url": ""}, {}):
            with self.subTest(dados=dados):
                resposta = api.post("/tela/candidaturas", data=dados, headers=HX)

                self.assertEqual(resposta.status_code, 422)
                self.assertEqual(resposta.headers["hx-retarget"], "#aviso")
                self.assertEqual(resposta.headers["hx-reswap"], "outerHTML")
                self.assertNotIn("<html", resposta.text)
        self.assertEqual(servico.contar(), 0)

    def test_ida_e_volta_da_url_pelo_hidden(self):
        for url in URLS_DIFICEIS:
            with self.subTest(url=url):
                api, servico = montar(VagaExterna("Título", "Empresa", "Remotar", url), VagaExterna("Outra", "E", "R", URL))
                detalhe = api.get("/tela/vagas/detalhe", params={"url": url}, headers=HX)
                valor = hidden_url(Pagina(detalhe.text))
                self.assertEqual(valor, url)

                # O htmx manda o form como corpo urlencoded: o cliente de teste faz o mesmo.
                resposta = api.post("/tela/candidaturas", data={"url": valor}, headers=HX)

                self.assertEqual(resposta.status_code, 200)
                self.assertEqual([c.vaga_url for c in servico.listar()], [url])
                self.assertEqual(servico.obter_por_url(url).vaga_url, url)

    def test_pagina_inteira_do_detalhe_aplicado_traz_a_data_e_nao_o_formulario(self):
        api, _ = montar()
        api.post("/tela/candidaturas", data={"url": URL}, headers=HX)

        pagina = Pagina(api.get("/tela", params={"url": URL}).text)

        self.assertIn("Aplicada em", pagina.por_id("acao-aplicar").texto)
        self.assertEqual([f for f in pagina.tags("form") if f.attrs.get("hx-post") == "/tela/candidaturas"], [])


class DesfazerTest(unittest.TestCase):
    def aplicar(self, api):
        api.post("/tela/candidaturas", data={"url": URL}, headers=HX)
        return next(iter(api.app.state.candidaturas.listar())).id

    def test_botao_da_tabela_inclui_a_url_do_detalhe_aberto(self):
        api, _ = montar()
        self.aplicar(api)

        pagina = Pagina(api.get("/tela", params={"url": URL}).text)

        desfazer = next(b for b in pagina.tags("button") if b.texto.strip() == "Desfazer")
        self.assertEqual(desfazer.attrs["hx-include"], "#detalhe-url")
        campo = pagina.por_id("detalhe-url")
        self.assertEqual(campo.attrs["name"], "detalhe_url")
        self.assertEqual(campo.attrs["value"], URL)

    def test_desfazer_com_o_detalhe_da_mesma_vaga_devolve_o_botao_por_oob(self):
        api, servico = montar()
        id_ = self.aplicar(api)

        resposta = api.delete(f"/tela/candidaturas/{id_}", params={"detalhe_url": URL}, headers=HX)

        self.assertEqual(resposta.status_code, 200)
        pagina = Pagina(resposta.text)
        self.assertIn("0 candidaturas", pagina.por_id("aplicadas").texto)
        oob = pagina.por_id("acao-aplicar")
        self.assertEqual(oob.attrs["hx-swap-oob"], "outerHTML")
        self.assertEqual(hidden_url(pagina), URL)
        self.assertEqual(servico.contar(), 0)

    def test_desfazer_com_outro_detalhe_ou_sem_detalhe_nao_manda_oob(self):
        api, servico = montar(
            VagaExterna("Analista de Dados", "Empresa Teste", "Remotar", URL), VagaExterna("Outra", "E", "R", "https://exemplo.test/2")
        )
        for parametros in ({"detalhe_url": "https://exemplo.test/2"}, {}):
            with self.subTest(parametros=parametros):
                id_ = self.aplicar(api)

                resposta = api.delete(f"/tela/candidaturas/{id_}", params=parametros, headers=HX)

                self.assertEqual(resposta.status_code, 200)
                self.assertNotIn("acao-aplicar", resposta.text)
                self.assertNotIn("hx-swap-oob", resposta.text)
                self.assertEqual(servico.contar(), 0)

    def test_desfazer_candidatura_inexistente_continua_404_no_aviso(self):
        api, _ = montar()

        resposta = api.delete("/tela/candidaturas/99", params={"detalhe_url": URL}, headers=HX)

        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(resposta.headers["hx-retarget"], "#aviso")


class XssTest(unittest.TestCase):
    def test_titulo_e_url_com_html_sao_escapados_nos_pontos_novos(self):
        titulo = "<script>alert(1)</script> \"x\" 'y'"
        url = 'https://exemplo.test/v?a="onmouseover="alert(3)&b=\'><i>'
        api, servico = montar(VagaExterna(titulo, "\"><img src=x onerror=alert(1)>", "Remotar", url))

        respostas = [
            api.get("/tela/vagas/detalhe", params={"url": url}, headers=HX).text,
            api.post("/tela/candidaturas", data={"url": url}, headers=HX).text,
            api.get("/tela", params={"url": url}).text,
            api.delete(f"/tela/candidaturas/{servico.listar()[0].id}", params={"detalhe_url": url}, headers=HX).text,
        ]

        for texto in respostas:
            self.assertNotIn("<script>alert", texto)
            self.assertNotIn("<img src=x", texto)
            self.assertNotIn("<i>", texto)
            pagina = Pagina(texto)
            self.assertEqual(pagina.tags("img"), [])
            self.assertEqual(pagina.tags("i"), [])
            self.assertEqual([s for s in pagina.tags("script") if "src" not in s.attrs], [])
            for elemento in pagina.elementos:
                self.assertFalse(any(nome.startswith("on") for nome in elemento.attrs), (elemento.tag, elemento.attrs))
        self.assertEqual(hidden_url(Pagina(respostas[0])), url)
        self.assertEqual(Pagina(respostas[1]).por_id("aplicadas").texto.count(titulo), 1)


if __name__ == "__main__":
    unittest.main()
