"""Compatibilidade na tela: detalhe e coluna "Compat." das aplicadas. Dados inventados à mão."""

import unittest
from dataclasses import dataclass
from datetime import date

from fastapi.testclient import TestClient

from src.controller.api import criar_app
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


    def test_texto_do_requisito_e_escapado(self):
        from src.controller import tela_compat
        from src.service.compatibilidade_service import compatibilidade

        r = compatibilidade("x", [("<script>alert(1)</script>", True)], PERFIL)
        self.assertIn("<script>", r.lacunas[0])  # o service não escapa; o template sim
        api, _ = cliente(PERFIL)
        env = tela_compat.templates.env
        saida = env.from_string("{% for i in l %}<li>{{ i }}</li>{% endfor %}").render(l=r.lacunas)
        self.assertNotIn("<script>", saida)


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
        self.assertIn("0%", html)


class CssTest(unittest.TestCase):
    def test_link_do_css_e_arquivo_servido(self):
        api, _ = cliente(PERFIL)
        self.assertEqual(api.get("/tela").text.count("/static/tela-compat.css"), 1)
        self.assertEqual(api.get("/static/tela-compat.css").status_code, 200)


if __name__ == "__main__":
    unittest.main()
