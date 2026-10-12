"""CSRF das rotas JSON que mudam estado: Content-Type JSON, Sec-Fetch-Site e Origin."""

import unittest

from fastapi.testclient import TestClient

from src.controller.api import criar_app
from src.repository.base import CurriculoRepositoryMemoria, PerfilBuscaRepositoryMemoria
from src.service.busca_service import BuscaService

JSON = {"Content-Type": "application/json"}
PERFIL = {"termos": ["python"]}


class CsrfJsonTest(unittest.TestCase):
    def setUp(self):
        self.perfis = PerfilBuscaRepositoryMemoria()
        self.app = criar_app(self.perfis, CurriculoRepositoryMemoria(), BuscaService([]))
        self.api = TestClient(self.app)

    def salvar(self, headers):
        return self.api.put("/perfil", content='{"termos": ["python"]}', headers=headers)

    def test_sem_content_type_e_415_e_nao_grava(self):
        self.assertEqual(self.salvar({}).status_code, 415)
        self.assertIsNone(self.perfis.obter())

    def test_text_plain_do_formulario_cross_site_e_415(self):
        # É o que um <form enctype="text/plain"> de outro site consegue mandar.
        resposta = self.salvar({"Content-Type": "text/plain", "Origin": "https://malicioso.example"})
        self.assertEqual(resposta.status_code, 415)
        self.assertIsNone(self.perfis.obter())

    def test_outros_tipos_de_formulario_sao_415(self):
        for tipo in ("application/x-www-form-urlencoded", "multipart/form-data; boundary=x", "application/jsonx"):
            with self.subTest(tipo=tipo):
                self.assertEqual(self.salvar({"Content-Type": tipo}).status_code, 415)

    def test_application_json_com_charset_passa(self):
        resposta = self.salvar({"Content-Type": "application/json; charset=utf-8"})
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(self.perfis.obter().termos, ["python"])

    def test_sec_fetch_site_cross_site_e_403(self):
        for valor in ("cross-site", "same-site"):
            with self.subTest(valor=valor):
                self.assertEqual(self.salvar({**JSON, "Sec-Fetch-Site": valor}).status_code, 403)
        self.assertIsNone(self.perfis.obter())

    def test_sec_fetch_site_same_origin_e_none_passam(self):
        for valor in ("same-origin", "none"):
            with self.subTest(valor=valor):
                self.assertEqual(self.salvar({**JSON, "Sec-Fetch-Site": valor}).status_code, 200)

    def test_origin_diferente_do_host_e_403(self):
        resposta = self.salvar({**JSON, "Origin": "https://malicioso.example"})
        self.assertEqual(resposta.status_code, 403)
        self.assertIsNone(self.perfis.obter())

    def test_origin_null_e_403(self):
        self.assertEqual(self.salvar({**JSON, "Origin": "null"}).status_code, 403)
        self.assertIsNone(self.perfis.obter())

    def test_sem_origin_e_sem_sec_fetch_site_passa(self):
        # curl e o timer do Pi.
        self.assertEqual(self.salvar(JSON).status_code, 200)

    def test_origin_com_a_mesma_porta_do_host_passa(self):
        resposta = self.salvar({**JSON, "Host": "localhost:8000", "Origin": "http://localhost:8000"})
        self.assertEqual(resposta.status_code, 200)

    def test_origin_com_porta_diferente_da_do_host_e_403(self):
        resposta = self.salvar({**JSON, "Host": "localhost:8000", "Origin": "http://localhost:9999"})
        self.assertEqual(resposta.status_code, 403)

    def test_todas_as_rotas_json_mutaveis_exigem_json(self):
        # (método, rota) de cada rota JSON que muda estado, com o corpo mais simples.
        chamadas = [
            ("PUT", "/perfil"),
            ("PUT", "/curriculos/base"),
            ("POST", "/curriculos/adaptados"),
            ("POST", "/coletas"),
            ("POST", "/vagas/vistas"),
            ("POST", "/candidaturas"),
            ("PATCH", "/candidaturas/1"),
            ("DELETE", "/candidaturas/1"),
        ]
        for metodo, rota in chamadas:
            with self.subTest(metodo=metodo, rota=rota):
                # Sem corpo e sem Content-Type: 415 (DELETE incluído).
                self.assertEqual(self.api.request(metodo, rota).status_code, 415)
                resposta = self.api.request(metodo, rota, headers={**JSON, "Origin": "null"})
                self.assertEqual(resposta.status_code, 403)

    def test_get_nao_exige_content_type(self):
        for rota in ("/saude", "/perfil", "/vagas/encontradas", "/candidaturas"):
            with self.subTest(rota=rota):
                self.assertNotIn(self.api.get(rota, headers={"Origin": "https://malicioso.example"}).status_code, (403, 415))

    def test_toda_rota_mutavel_do_openapi_exige_json(self):
        # Varre o app pelo OpenAPI (enxerga também as rotas de routers incluídos): rota mutável nova,
        # fora de /tela e /interno, que escape da proteção faz este teste falhar.
        varridas = []
        for caminho, operacoes in self.app.openapi()["paths"].items():
            if caminho.startswith(("/tela", "/interno")):
                continue
            for metodo in operacoes:
                if metodo.upper() in ("GET", "HEAD", "OPTIONS"):
                    continue
                url = caminho.replace("{id}", "1")
                with self.subTest(metodo=metodo, rota=caminho):
                    self.assertEqual(self.api.request(metodo.upper(), url).status_code, 415)
                    varridas.append((metodo, caminho))
        self.assertGreaterEqual(len(varridas), 8)

    def test_rota_mutavel_acrescentada_depois_ja_nasce_protegida(self):
        @self.app.post("/futura")
        def futura() -> dict[str, bool]:
            return {"ok": True}

        self.assertEqual(self.api.post("/futura").status_code, 415)
        self.assertEqual(self.api.post("/futura", headers=JSON).status_code, 200)

    def test_tela_e_interno_ficam_fora(self):
        # /tela tem a própria proteção (403 do HTMX, não 415); /interno é isento (aqui, 404: ainda não existe).
        self.assertEqual(self.api.post("/tela/coletas", headers={"Origin": "http://testserver"}).status_code, 403)
        self.assertEqual(self.api.post("/interno/x").status_code, 404)


if __name__ == "__main__":
    unittest.main()
