import os
import unittest
import uuid
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from src.controller.api import criar_app
from src.model.erros import DadoInvalido
from src.model.vaga_externa import VagaExterna
from src.repository.base import CurriculoRepositoryMemoria, PerfilBuscaRepositoryMemoria
from src.service.busca_service import BuscaService
from tests.test_busca_service import ClientFixo, ClientQuebrado

MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "001_curriculo_perfil.sql"

PERFIL = {
    "termos": ["python"],
    "excluir": ["senior"],
    "modalidades": ["Remoto"],
    "localidades": ["Brasil"],
    "plataformas": ["A"],
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

    def test_put_perfil_tira_valores_repetidos(self):
        corpo = {**PERFIL, "modalidades": ["Remoto", "Remoto"], "termos": ["python", "python", "dados"]}

        salvo = self.api.put("/perfil", json=corpo).json()

        self.assertEqual(salvo["modalidades"], ["Remoto"])
        self.assertEqual(salvo["termos"], ["python", "dados"])

    def test_put_perfil_com_enum_invalido_e_422(self):
        for campo, valor in (("modalidades", ["Presencial"]), ("niveis", ["junior"])):
            with self.subTest(campo=campo):
                resposta = self.api.put("/perfil", json={**PERFIL, campo: valor})

                self.assertEqual(resposta.status_code, 422)
        self.assertIsNone(self.perfis.obter())

    def test_put_perfil_com_dias_nao_positivo_ou_campo_desconhecido_e_422(self):
        for corpo in (
            {**PERFIL, "publicada_desde_dias": 0},
            {**PERFIL, "publicada_desde_dias": 1_000_000},
            {**PERFIL, "nivel": ["Pleno"]},
        ):
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


class ValueErrorDoRepositorio(PerfilBuscaRepositoryMemoria):
    def salvar(self, perfil):
        raise DadoInvalido("recusado pelo repositório")


class FalhaDeConfiguracao(PerfilBuscaRepositoryMemoria):
    def obter(self):
        # Como o int(PGPORT) de conexao.parametros com PGPORT="5432/tcp".
        return int("5432/tcp")


class ApiErrosDoServidorTest(unittest.TestCase):
    def test_value_error_que_nao_e_validacao_vira_500(self):
        api = TestClient(
            criar_app(FalhaDeConfiguracao(), CurriculoRepositoryMemoria(), BuscaService([])),
            raise_server_exceptions=False,
        )

        self.assertEqual(api.get("/perfil").status_code, 500)


class ApiErrosDoRepositorioTest(unittest.TestCase):
    def test_value_error_do_repositorio_vira_422(self):
        api = TestClient(criar_app(ValueErrorDoRepositorio(), CurriculoRepositoryMemoria(), BuscaService([])))

        resposta = api.put("/perfil", json=PERFIL)

        self.assertEqual(resposta.status_code, 422)
        self.assertEqual(resposta.json(), {"detail": "recusado pelo repositório"})


@unittest.skipUnless(os.environ.get("PGHOST"), "PGHOST não definido: API com Postgres pulada")
class ApiPostgresTest(unittest.TestCase):
    """A API com os repositórios Postgres, num schema descartável."""

    def setUp(self):
        from psycopg import sql

        from src.repository.conexao import conectar
        from src.repository.postgres import CurriculoRepositoryPostgres, PerfilBuscaRepositoryPostgres

        conn = conectar()
        self.addCleanup(conn.close)
        schema = f"teste_api_{uuid.uuid4().hex[:12]}"
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        self.addCleanup(conn.execute, sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        conn.execute(MIGRATION.read_text(encoding="utf-8"))
        self.api = TestClient(
            criar_app(PerfilBuscaRepositoryPostgres(conn), CurriculoRepositoryPostgres(conn), BuscaService([]))
        )

    def test_perfil_e_curriculos_de_ponta_a_ponta(self):
        self.assertEqual(self.api.get("/perfil").status_code, 404)
        self.assertEqual(self.api.put("/perfil", json=PERFIL).json(), PERFIL)
        self.assertEqual(self.api.get("/perfil").json(), PERFIL)

        sem_base = self.api.post("/curriculos/adaptados", json={"titulo": "a", "conteudo_md": "# a", "vaga_url": "u1"})
        self.assertEqual(sem_base.status_code, 409)
        base = self.api.put("/curriculos/base", json={"titulo": "v1", "conteudo_md": "# v1"}).json()
        adaptado = self.api.post("/curriculos/adaptados", json={"titulo": "a", "conteudo_md": "# a", "vaga_url": "u1"})

        self.assertEqual(adaptado.status_code, 201)
        self.assertEqual(adaptado.json()["base_id"], base["id"])
        self.assertEqual(self.api.get("/curriculos/base").json(), base)
        self.assertEqual(self.api.get("/curriculos/adaptados").json(), [adaptado.json()])


@unittest.skipUnless(os.environ.get("PGHOST"), "PGHOST não definido: pool com Postgres pulado")
class PoolPostgresConcorrenciaTest(unittest.TestCase):
    """Gravações simultâneas pelo pool da produção: o rollback de uma não desfaz a outra."""

    def test_falha_simultanea_nao_desfaz_gravacao_que_respondeu_ok(self):
        from concurrent.futures import ThreadPoolExecutor

        from psycopg import sql
        from psycopg_pool import ConnectionPool

        from src.controller.api import _PorChamada
        from src.model.curriculo import Curriculo
        from src.repository.conexao import conectar, parametros
        from src.repository.postgres import CurriculoRepositoryPostgres

        admin = conectar()
        self.addCleanup(admin.close)
        schema = f"teste_pool_{uuid.uuid4().hex[:12]}"
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        self.addCleanup(admin.execute, sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        admin.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        admin.execute(MIGRATION.read_text(encoding="utf-8"))

        pool = ConnectionPool(
            kwargs={**parametros(), "autocommit": True, "options": f"-c search_path={schema}"},
            min_size=4,
            max_size=4,
            open=True,
        )
        self.addCleanup(pool.close)
        repo = _PorChamada(lambda: pool, lambda: CurriculoRepositoryPostgres)
        base = repo.salvar(Curriculo("base", "Base", "# b"))
        adaptado = repo.salvar(Curriculo("adaptado", "A0", "# a0", base_id=base.id))

        def gravar(i):
            # Metade falha (adaptado de adaptado, rollback), metade grava.
            alvo = adaptado.id if i % 2 else base.id
            try:
                return repo.salvar(Curriculo("adaptado", f"A{i}", "# a", base_id=alvo)).id
            except ValueError:
                return None

        with ThreadPoolExecutor(max_workers=8) as executor:
            ids = [i for i in executor.map(gravar, range(40)) if i is not None]

        self.assertEqual(len(ids), 20)
        no_banco = {c.id for c in repo.listar_adaptados()}
        self.assertLessEqual(set(ids), no_banco)
        self.assertEqual(len(no_banco), 21)


class PerfilSemValidacao(PerfilBuscaRepositoryMemoria):
    """Como o repositório Postgres: grava sem chamar validar_perfil."""

    def salvar(self, perfil):
        self._perfil = perfil


class ApiValidaPerfilTest(unittest.TestCase):
    def test_api_valida_o_perfil_mesmo_com_repositorio_que_nao_valida(self):
        from unittest import mock

        perfis = PerfilSemValidacao()
        api = TestClient(criar_app(perfis, CurriculoRepositoryMemoria(), BuscaService([])))

        with mock.patch("src.controller.api.validar_perfil", side_effect=DadoInvalido("inválido")):
            resposta = api.put("/perfil", json=PERFIL)

        self.assertEqual(resposta.status_code, 422)
        self.assertIsNone(perfis._perfil)


class PorChamadaTest(unittest.TestCase):
    """Cada chamada pega e devolve a própria conexão: requisições simultâneas não dividem transação."""

    def test_cada_chamada_usa_uma_conexao_propria_e_devolve(self):
        from contextlib import contextmanager

        from src.controller.api import _PorChamada

        entregues, devolvidas = [], []

        class Pool:
            @contextmanager
            def connection(self):
                conn = object()
                entregues.append(conn)
                try:
                    yield conn
                finally:
                    devolvidas.append(conn)

        class Repo:
            def __init__(self, conn):
                self.conn = conn

            def qual(self):
                return self.conn

        pool = Pool()
        repo = _PorChamada(lambda: pool, lambda: Repo)

        primeira, segunda = repo.qual(), repo.qual()

        self.assertIsNot(primeira, segunda)
        self.assertEqual(entregues, [primeira, segunda])
        self.assertEqual(devolvidas, [primeira, segunda])


class ProvidersPadraoTest(unittest.TestCase):
    def test_linkedin_pelo_apify_so_com_token(self):
        from unittest import mock

        from src.client.apify_linkedin import ApifyLinkedInClient
        from src.controller.api import providers_padrao

        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(any(isinstance(p, ApifyLinkedInClient) for p in providers_padrao()))

        env = {"APIFY_TOKEN": "tk", "APIFY_LOCAL": "Rio de Janeiro", "APIFY_LIMITE": "20"}
        with mock.patch.dict(os.environ, env, clear=True):
            apify = [p for p in providers_padrao() if isinstance(p, ApifyLinkedInClient)]
        self.assertEqual(len(apify), 1)
        self.assertEqual((apify[0].local, apify[0].limite), ("Rio de Janeiro", 20))

    def test_apify_local_vazio_cai_no_padrao(self):
        from unittest import mock

        from src.client.apify_linkedin import ApifyLinkedInClient
        from src.controller.api import providers_padrao

        for valor in ("", "   "):
            with mock.patch.dict(os.environ, {"APIFY_TOKEN": "tk", "APIFY_LOCAL": valor}, clear=True):
                apify = [p for p in providers_padrao() if isinstance(p, ApifyLinkedInClient)]
            self.assertEqual(apify[0].local, "Brasil", repr(valor))

    def test_apify_limite_invalido_cai_no_padrao_sem_derrubar_a_app(self):
        from unittest import mock

        from src.client.apify_linkedin import ApifyLinkedInClient
        from src.controller.api import providers_padrao

        for valor in ("", "50.0", "cinquenta", "0", "-5"):
            with mock.patch.dict(os.environ, {"APIFY_TOKEN": "tk", "APIFY_LIMITE": valor}, clear=True):
                apify = [p for p in providers_padrao() if isinstance(p, ApifyLinkedInClient)]
            self.assertEqual(apify[0].limite, 50, valor)

