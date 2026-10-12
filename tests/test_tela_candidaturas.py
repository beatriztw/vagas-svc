"""Seção "Vagas aplicadas" da tela: GET/PATCH/DELETE em /tela/candidaturas.

Vagas e títulos escritos à mão (não são respostas gravadas de provider).
"""

import re
import unittest
from dataclasses import replace
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from src.controller.api import criar_app
from src.controller.tela_candidaturas import data_curta
from src.model.candidatura import StatusCandidatura
from src.model.vaga_externa import VagaExterna
from src.repository.base import CurriculoRepositoryMemoria, PerfilBuscaRepositoryMemoria, VagaEncontradaRepositoryMemoria
from src.service.busca_service import BuscaService

HX = {"HX-Request": "true", "Origin": "http://testserver"}
URL_A = "https://exemplo.test/vaga/a"
URL_B = "https://exemplo.test/vaga/b?x=1&y=2#z"


def vaga(url, titulo="Analista de Dados", empresa="Empresa Teste"):
    return VagaExterna(titulo, empresa, "Remotar", url)


def cliente(*vagas):
    encontradas = VagaEncontradaRepositoryMemoria()
    encontradas.registrar(list(vagas) or [vaga(URL_A), vaga(URL_B, "Engenheira", "Beta")], "dados")
    app = criar_app(PerfilBuscaRepositoryMemoria(), CurriculoRepositoryMemoria(), BuscaService([]), encontradas=encontradas)
    return TestClient(app), app.state.candidaturas


class ListagemTest(unittest.TestCase):
    def test_pagina_mostra_as_candidaturas_e_o_contador(self):
        api, servico = cliente()
        servico.marcar_aplicada(URL_A)
        servico.marcar_aplicada(URL_B)

        html = api.get("/tela").text

        self.assertIn("<html", html)
        self.assertIn("2 candidaturas", html)
        self.assertEqual(html.count("<tr id=\"candidatura-"), 2)
        self.assertIn("Analista de Dados", html)
        self.assertIn("Beta", html)
        self.assertNotIn("Nenhuma candidatura ainda", html)

    def test_vazio_mostra_zero_e_o_aviso(self):
        api, _ = cliente()

        html = api.get("/tela").text

        self.assertIn("0 candidaturas", html)
        self.assertIn("Nenhuma candidatura ainda", html)

    def test_partial_sem_html_e_sem_pagina(self):
        api, servico = cliente()
        servico.marcar_aplicada(URL_A)

        resposta = api.get("/tela/candidaturas", headers={"HX-Request": "true"})

        self.assertEqual(resposta.status_code, 200)
        self.assertNotIn("<html", resposta.text)
        self.assertIn('id="aplicadas"', resposta.text)
        self.assertIn("1 candidaturas", resposta.text)

    def test_sem_hx_request_volta_para_a_pagina_inteira(self):
        api, _ = cliente()

        resposta = api.get("/tela/candidaturas", follow_redirects=False)

        self.assertEqual((resposta.status_code, resposta.headers["location"]), (303, "/tela"))

    def test_select_com_as_cinco_opcoes_e_a_atual_marcada(self):
        api, servico = cliente()
        c, _ = servico.marcar_aplicada(URL_A)
        servico.mudar_status(c.id, "entrevista")

        html = api.get("/tela/candidaturas", headers={"HX-Request": "true"}).text

        self.assertRegex(
            html,
            rf'<select name="status"[^>]*hx-patch="/tela/candidaturas/{c.id}"[^>]*hx-trigger="change"'
            r'[^>]*hx-target="closest tr"[^>]*hx-swap="outerHTML"',
        )
        opcoes = re.findall(r'<option value="([^"]+)"( selected)?>([^<]+)</option>', html)
        self.assertEqual([(v, r) for v, _, r in opcoes], [(s.value, s.rotulo) for s in StatusCandidatura])
        self.assertEqual([v for v, sel, _ in opcoes if sel], ["entrevista"])
        self.assertIn('class="chip st-entrevista"', html)
        self.assertIn(f'hx-delete="/tela/candidaturas/{c.id}"', html)

    def test_get_nao_muda_nada(self):
        api, servico = cliente()
        c, _ = servico.marcar_aplicada(URL_A)

        api.get("/tela", headers=HX)
        api.get("/tela/candidaturas", headers=HX)

        self.assertEqual((servico.contar(), servico.obter(c.id).status), (1, StatusCandidatura.ENVIADA))

    def test_data_em_dd_mm_no_fuso_de_sao_paulo(self):
        # 01:00Z de 12/10 ainda é 11/10 às 22:00 em São Paulo.
        instante = datetime(2026, 10, 12, 1, 0, tzinfo=timezone.utc)
        self.assertEqual(data_curta(instante), "11/10")
        self.assertEqual(data_curta(datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)), "12/10")
        self.assertEqual(data_curta(None), "—")
        api, servico = cliente()
        c, _ = servico.marcar_aplicada(URL_A)
        repo = servico.candidaturas
        repo._linhas[c.id] = replace(repo._linhas[c.id], aplicada_em=instante)

        html = api.get("/tela/candidaturas", headers={"HX-Request": "true"}).text

        self.assertIn(">11/10</time>", html)
        self.assertNotIn(">12/10</time>", html)

    def test_titulo_e_empresa_escapados(self):
        perigoso = '<script>alert(1)</script> "x" onfocus="y'
        api, servico = cliente(vaga(URL_A, perigoso, '<img src=x onerror=alert(2)>'))
        servico.marcar_aplicada(URL_A)

        html = api.get("/tela").text

        self.assertNotIn("<script>alert(1)", html)
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        # Aspas do título dentro do aria-label (atributo) não fecham o atributo.
        self.assertNotIn('onfocus="y"', html)
        self.assertIn("&#34;x&#34; onfocus=&#34;y", html)


class MudarStatusTest(unittest.TestCase):
    def setUp(self):
        self.api, self.servico = cliente()
        self.c, _ = self.servico.marcar_aplicada(URL_A)

    def test_patch_muda_e_devolve_a_linha(self):
        resposta = self.api.patch(f"/tela/candidaturas/{self.c.id}", data={"status": "em_analise"}, headers=HX)

        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(resposta.text.lstrip().startswith(f'<tr id="candidatura-{self.c.id}">'))
        self.assertNotIn("<html", resposta.text)
        self.assertIn("st-analise", resposta.text)
        self.assertRegex(resposta.text, r'<option value="em_analise" selected>')
        self.assertEqual(self.servico.obter(self.c.id).status, StatusCandidatura.EM_ANALISE)

    def test_status_invalido_e_422_html_e_nao_muda(self):
        for corpo in ({"status": "aprovada"}, {"status": ""}, {}):
            with self.subTest(corpo=corpo):
                resposta = self.api.patch(f"/tela/candidaturas/{self.c.id}", data=corpo, headers=HX)
                self.assertEqual(resposta.status_code, 422)
                self.assertIn("text/html", resposta.headers["content-type"])
                self.assertIn('class="erro"', resposta.text)
        self.assertEqual(self.servico.obter(self.c.id).status, StatusCandidatura.ENVIADA)

    def test_patch_de_inexistente_e_404_com_aviso(self):
        resposta = self.api.patch("/tela/candidaturas/999", data={"status": "oferta"}, headers=HX)

        self.assertEqual(resposta.status_code, 404)
        self.assertIn("Candidatura não encontrada", resposta.text)

    def test_mutacao_sem_hx_request_ou_com_origem_de_outro_host_e_403(self):
        casos = {
            "sem HX-Request": {"Origin": "http://testserver"},
            "origem de outro host": {"HX-Request": "true", "Origin": "http://outro.test"},
            "sem Origin nem Sec-Fetch-Site": {"HX-Request": "true"},
        }
        for nome, cabecalhos in casos.items():
            with self.subTest(nome):
                self.assertEqual(
                    self.api.patch(f"/tela/candidaturas/{self.c.id}", data={"status": "oferta"}, headers=cabecalhos).status_code, 403
                )
                self.assertEqual(self.api.delete(f"/tela/candidaturas/{self.c.id}", headers=cabecalhos).status_code, 403)
        self.assertEqual((self.servico.contar(), self.servico.obter(self.c.id).status), (1, StatusCandidatura.ENVIADA))


class DesfazerTest(unittest.TestCase):
    def test_delete_some_com_a_linha_e_atualiza_o_contador(self):
        api, servico = cliente()
        a, _ = servico.marcar_aplicada(URL_A)
        b, _ = servico.marcar_aplicada(URL_B)

        resposta = api.delete(f"/tela/candidaturas/{a.id}", headers=HX)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("1 candidaturas", resposta.text)
        self.assertNotIn(f'id="candidatura-{a.id}"', resposta.text)
        self.assertIn(f'id="candidatura-{b.id}"', resposta.text)
        self.assertIsNone(servico.obter(a.id))

    def test_delete_da_ultima_mostra_o_vazio(self):
        api, servico = cliente()
        a, _ = servico.marcar_aplicada(URL_A)

        resposta = api.delete(f"/tela/candidaturas/{a.id}", headers=HX)

        self.assertIn("0 candidaturas", resposta.text)
        self.assertIn("Nenhuma candidatura ainda", resposta.text)

    def test_delete_de_inexistente_e_404(self):
        api, _ = cliente()

        self.assertEqual(api.delete("/tela/candidaturas/999", headers=HX).status_code, 404)


class FiacaoTest(unittest.TestCase):
    def test_json_e_tela_usam_o_mesmo_servico(self):
        api, servico = cliente()
        api.post("/candidaturas", json={"vaga_url": URL_A})

        self.assertEqual(servico.contar(), 1)
        self.assertIn("1 candidaturas", api.get("/tela").text)


if __name__ == "__main__":
    unittest.main()
