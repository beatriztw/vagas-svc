import unittest
from datetime import date

from fastapi.testclient import TestClient

from src.controller.api import criar_app
from src.model.vaga_externa import VagaExterna
from src.repository.base import CurriculoRepositoryMemoria, PerfilBuscaRepositoryMemoria
from src.service.busca_service import BuscaService
from tests.test_busca_service import ClientFixo, ClientQuebrado

PERFIL = {
    "termos": ["python"],
    "excluir": ["senior"],
    "modalidades": ["Remoto"],
    "localidades": ["Brasil"],
    "plataformas": ["Greenhouse"],
    "niveis": ["Júnior", "Pleno"],
    "skills": ["django"],
    "publicada_desde_dias": 30,
    "ocultar_candidatadas": False,
}


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.perfis = PerfilBuscaRepositoryMemoria()
        self.curriculos = CurriculoRepositoryMemoria()
        self.vagas = [
            VagaExterna("Dev Python Sênior", "X", "A", "u1", remoto=True, publicada_em=date.today()),
            VagaExterna("Dev Python", "X", "A", "u2", remoto=True, publicada_em=date.today()),
            VagaExterna("Dev Python presencial", "X", "A", "u3", remoto=False),
        ]
        busca = BuscaService([ClientQuebrado(), ClientFixo("A", self.vagas)])
        self.api = TestClient(criar_app(self.perfis, self.curriculos, busca))

    def test_saude(self):
        resposta = self.api.get("/saude")

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {"ok": True})

    # Perfil

    def test_get_perfil_sem_perfil_salvo_e_404(self):
        self.assertEqual(self.api.get("/perfil").status_code, 404)

    def test_put_perfil_salva_e_devolve_valores_dos_enums(self):
        resposta = self.api.put("/perfil", json=PERFIL)

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), PERFIL)
        self.assertEqual(self.api.get("/perfil").json(), PERFIL)
        self.assertEqual(self.perfis.obter().niveis[0].name, "JUNIOR")

    def test_put_perfil_com_enum_invalido_e_422(self):
        for campo, valor in (("modalidades", ["Presencial"]), ("niveis", ["junior"])):
            with self.subTest(campo=campo):
                resposta = self.api.put("/perfil", json={**PERFIL, campo: valor})

                self.assertEqual(resposta.status_code, 422)
        self.assertIsNone(self.perfis.obter())

    def test_put_perfil_com_dias_nao_positivo_ou_campo_desconhecido_e_422(self):
        for corpo in ({**PERFIL, "publicada_desde_dias": 0}, {**PERFIL, "nivel": ["Pleno"]}):
            with self.subTest(corpo=corpo):
                self.assertEqual(self.api.put("/perfil", json=corpo).status_code, 422)

    # Currículos

    def test_get_base_sem_base_e_404(self):
        self.assertEqual(self.api.get("/curriculos/base").status_code, 404)

    def test_put_base_cria_nova_versao_e_get_devolve_a_mais_recente(self):
        primeira = self.api.put("/curriculos/base", json={"titulo": "v1", "conteudo_md": "# v1"}).json()
        segunda = self.api.put(
            "/curriculos/base", json={"titulo": "v2", "conteudo_md": "# v2", "arquivo_s3": "originais/cv.pdf"}
        ).json()

        self.assertNotEqual(primeira["id"], segunda["id"])
        self.assertEqual(segunda["tipo"], "base")
        self.assertIsNone(segunda["base_id"])
        self.assertIsNotNone(segunda["criado_em"])
        self.assertEqual(self.api.get("/curriculos/base").json(), segunda)
        self.assertEqual(self.curriculos.obter(primeira["id"]).titulo, "v1")

    def test_put_base_sem_conteudo_e_422(self):
        self.assertEqual(self.api.put("/curriculos/base", json={"titulo": "v1"}).status_code, 422)
        self.assertEqual(self.api.put("/curriculos/base", json={"titulo": "v1", "conteudo_md": ""}).status_code, 422)

    def test_post_adaptado_sem_base_e_409(self):
        resposta = self.api.post("/curriculos/adaptados", json={"titulo": "a", "conteudo_md": "# a", "vaga_url": "u1"})

        self.assertEqual(resposta.status_code, 409)
        self.assertEqual(self.curriculos.listar_adaptados(), [])

    def test_post_adaptado_usa_a_base_vigente(self):
        self.api.put("/curriculos/base", json={"titulo": "v1", "conteudo_md": "# v1"})
        vigente = self.api.put("/curriculos/base", json={"titulo": "v2", "conteudo_md": "# v2"}).json()

        resposta = self.api.post("/curriculos/adaptados", json={"titulo": "a", "conteudo_md": "# a", "vaga_url": "u1"})

        self.assertEqual(resposta.status_code, 201)
        self.assertEqual(resposta.json()["base_id"], vigente["id"])
        self.assertEqual(resposta.json()["tipo"], "adaptado")
        self.assertEqual(self.api.get("/curriculos/adaptados").json(), [resposta.json()])

    def test_post_adaptado_com_base_id_explicito(self):
        antiga = self.api.put("/curriculos/base", json={"titulo": "v1", "conteudo_md": "# v1"}).json()
        self.api.put("/curriculos/base", json={"titulo": "v2", "conteudo_md": "# v2"})

        resposta = self.api.post(
            "/curriculos/adaptados", json={"titulo": "a", "conteudo_md": "# a", "vaga_url": "u1", "base_id": antiga["id"]}
        )

        self.assertEqual(resposta.json()["base_id"], antiga["id"])

    def test_post_adaptado_com_base_id_inexistente_ou_que_nao_e_base_e_404(self):
        self.api.put("/curriculos/base", json={"titulo": "v1", "conteudo_md": "# v1"})
        adaptado = self.api.post("/curriculos/adaptados", json={"titulo": "a", "conteudo_md": "# a", "vaga_url": "u1"}).json()

        for base_id in (999, adaptado["id"]):
            with self.subTest(base_id=base_id):
                resposta = self.api.post(
                    "/curriculos/adaptados", json={"titulo": "b", "conteudo_md": "# b", "vaga_url": "u2", "base_id": base_id}
                )

                self.assertEqual(resposta.status_code, 404)

    def test_post_adaptado_sem_vaga_url_e_422(self):
        self.api.put("/curriculos/base", json={"titulo": "v1", "conteudo_md": "# v1"})

        resposta = self.api.post("/curriculos/adaptados", json={"titulo": "a", "conteudo_md": "# a"})

        self.assertEqual(resposta.status_code, 422)

    def test_listar_adaptados_vazio(self):
        self.assertEqual(self.api.get("/curriculos/adaptados").json(), [])

    # Vagas

    def test_vagas_sem_perfil_devolve_tudo_e_os_erros_dos_providers(self):
        resposta = self.api.get("/vagas", params={"termo": "python"})

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual([v["url"] for v in resposta.json()["vagas"]], ["u1", "u2", "u3"])
        self.assertEqual(resposta.json()["erros"], {"Quebrado": "TimeoutError: sem resposta"})

    def test_vagas_aplica_os_filtros_do_perfil(self):
        self.api.put("/perfil", json=PERFIL)

        resposta = self.api.get("/vagas", params={"termo": "python"}).json()

        self.assertEqual([v["url"] for v in resposta["vagas"]], ["u2"])
        self.assertEqual(resposta["vagas"][0]["publicada_em"], date.today().isoformat())
        self.assertIn("Quebrado", resposta["erros"])

    def test_vagas_sem_termo_e_422(self):
        self.assertEqual(self.api.get("/vagas").status_code, 422)
