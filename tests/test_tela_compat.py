"""Compatibilidade na tela: detalhe e coluna "Compat." das aplicadas. Dados inventados à mão."""

import unittest
from dataclasses import dataclass
from datetime import date

from fastapi.testclient import TestClient

from src.controller.api import criar_app
from src.model.detalhe_vaga import DetalheVaga, Requisito
from src.model.perfil import Perfil
from src.model.vaga import Nivel
from src.model.vaga_externa import VagaExterna
from src.repository.base import CurriculoRepositoryMemoria, PerfilBuscaRepositoryMemoria, VagaEncontradaRepositoryMemoria
from src.service.busca_service import BuscaService

HX = {"HX-Request": "true", "Origin": "http://testserver"}
URL = "https://exemplo.test/vaga/a"


def cliente(perfil=None):
    encontradas = VagaEncontradaRepositoryMemoria()
    encontradas.registrar([VagaExterna("Analista de Dados SQL Pleno", "Empresa", "Remotar", URL)], "dados")
    perfis = PerfilBuscaRepositoryMemoria()
    if perfil is not None:
        perfis.salvar(perfil)
    app = criar_app(perfis, CurriculoRepositoryMemoria(), BuscaService([]), encontradas=encontradas)
    return TestClient(app), app


PERFIL = Perfil(termos=["analista de dados"], skills=["SQL", "Power BI"], niveis=[Nivel.PLENO])


class DetalheTest(unittest.TestCase):
    def test_sem_perfil_nao_mostra_compatibilidade(self):
        api, _ = cliente()
        for html in (api.get("/tela").text, api.get("/tela/vagas/detalhe", params={"url": URL}, headers=HX).text):
            self.assertNotIn('class="compat"', html)
            self.assertNotIn("Compatibilidade", html)

    def test_com_perfil_mostra_percentual_parcial_e_listas(self):
        api, _ = cliente(PERFIL)
        html = api.get("/tela/vagas/detalhe", params={"url": URL}, headers=HX).text
        self.assertIn('class="compat"', html)
        self.assertIn("100%", html)
        self.assertIn("parcial (sem requisitos da vaga)", html)
        self.assertIn("<li>SQL</li>", html)
        self.assertIn("<li>Nível Pleno</li>", html)
        self.assertEqual(html.count('class="compat"'), 1)
        # Logo depois dos chips.
        self.assertLess(html.index("chips"), html.index('class="compat"'))

    def test_pagina_inteira_tambem_mostra(self):
        api, _ = cliente(PERFIL)
        self.assertIn('class="compat"', api.get("/tela").text)

    def test_nivel_fora_do_perfil_aparece_como_lacuna(self):
        api, _ = cliente(Perfil(termos=["analista de dados"], niveis=[Nivel.SENIOR]))
        html = api.get("/tela/vagas/detalhe", params={"url": URL}, headers=HX).text
        self.assertIn("fora do perfil (Sênior)", html)


    def test_texto_do_requisito_e_escapado_no_template_de_verdade(self):
        # Pela rota do detalhe, que inclui `tela/_compatibilidade.html`: um `|safe` no template quebra este teste.
        encontradas = VagaEncontradaRepositoryMemoria()
        maligno = '<script>alert(1)</script> & "x"'
        detalhe = DetalheVaga(requisitos=[Requisito(maligno, True)])
        encontradas.registrar([VagaExterna("Analista de Dados", "Empresa", "Remotar", URL, detalhe=detalhe)], "dados")
        perfis = PerfilBuscaRepositoryMemoria()
        perfis.salvar(PERFIL)
        api = TestClient(criar_app(perfis, CurriculoRepositoryMemoria(), BuscaService([]), encontradas=encontradas))

        html = api.get("/tela/vagas/detalhe", params={"url": URL}, headers=HX).text
        inicio = html.index('<section class="compat"')
        secao = html[inicio : html.index("</section>", inicio)]

        self.assertIn('class="compat-lista compat-lacunas"', secao)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt; &amp; &#34;x&#34; (obrigatório)", secao)
        self.assertNotIn("<script>", secao)


class AplicadasTest(unittest.TestCase):
    def test_coluna_compat_mostra_o_percentual(self):
        api, app = cliente(PERFIL)
        app.state.candidaturas.marcar_aplicada(URL)
        html = api.get("/tela").text
        linha = html[html.index('<tr id="candidatura-') :]
        self.assertIn("100%", linha[: linha.index("</tr>")])
        self.assertIn("parcial", linha[: linha.index("</tr>")])

    def test_sem_perfil_coluna_fica_vazia(self):
        api, app = cliente()
        app.state.candidaturas.marcar_aplicada(URL)
        html = api.get("/tela").text
        linha = html[html.index('<tr id="candidatura-') :]
        self.assertNotIn("%", linha[: linha.index("</tr>")])

    def test_linha_devolvida_pelo_patch_tambem_tem_compat(self):
        api, app = cliente(PERFIL)
        c, _ = app.state.candidaturas.marcar_aplicada(URL)
        r = api.patch(f"/tela/candidaturas/{c.id}", data={"status": "entrevista"}, headers=HX)
        self.assertEqual(r.status_code, 200)
        self.assertIn("100%", r.text)

    def test_perfil_alterado_muda_o_resultado_na_proxima_requisicao(self):
        api, app = cliente(PERFIL)
        app.state.candidaturas.marcar_aplicada(URL)
        app.state.perfis.salvar(Perfil(termos=["motorista"], niveis=[Nivel.SENIOR]))
        html = api.get("/tela").text
        linha = html[html.index('<tr id="candidatura-') :]
        linha = linha[: linha.index("</tr>")]
        # "0%" é pedaço de "100%": a célula tem de ser exatamente 0% e o 100% do perfil antigo tem de sumir.
        self.assertIn(">0%<", linha)
        self.assertNotIn("100%", linha)


class CssTest(unittest.TestCase):
    def test_link_do_css_e_arquivo_servido(self):
        api, _ = cliente(PERFIL)
        self.assertEqual(api.get("/tela").text.count("/static/tela-compat.css"), 1)
        self.assertEqual(api.get("/static/tela-compat.css").status_code, 200)


if __name__ == "__main__":
    unittest.main()
