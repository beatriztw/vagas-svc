"""Coleta e histórico de vagas: repositório (memória e Postgres), serviço, API e CLI.

As vagas daqui são escritas à mão (não são respostas gravadas de provider).
"""

import io
import os
import unittest
import uuid
from datetime import date, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from src.controller.api import criar_app
from src.controller.coleta import rodar
from src.model.erros import DadoInvalido
from src.model.perfil import Perfil
from src.model.vaga_externa import VagaExterna
from src.repository.base import (
    CurriculoRepositoryMemoria,
    PerfilBuscaRepositoryMemoria,
    VagaEncontradaRepositoryMemoria,
)
from src.service.busca_service import BuscaService
from src.service.coleta_service import ColetaService, termos_da_coleta
from src.service.recomendacao_service import RecomendacaoService
from tests.test_busca_service import ClientFixo, ClientQuebrado

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


def vaga(url: str, titulo: str = "Dev Python", **campos) -> VagaExterna:
    return VagaExterna(titulo=titulo, empresa=campos.pop("empresa", "X"), plataforma="A", url=url, **campos)


class ClientPorTermo(ClientFixo):
    """Devolve vagas diferentes para cada termo."""

    def __init__(self, nome: str, por_termo: dict[str, list[VagaExterna]]):
        super().__init__(nome, [])
        self.por_termo = por_termo
        self.termos_buscados: list[str] = []

    def buscar(self, termo):
        self.termos_buscados.append(termo)
        return self.por_termo.get(termo, [])


class ContratoVagaEncontrada:
    """Mesmos casos para a memória e o Postgres; a subclasse define `self.repo`."""

    def test_vaga_nova_conta_e_aparece_com_o_termo(self):
        novas = self.repo.registrar([vaga("u1", local="Rio de Janeiro", remoto=False, publicada_em=date(2026, 10, 1))], "python")

        self.assertEqual(novas, 1)
        [salva] = self.repo.listar(somente_novas=True, limite=10)
        self.assertEqual(
            (salva.url, salva.titulo, salva.empresa, salva.plataforma, salva.local, salva.remoto, salva.publicada_em),
            ("u1", "Dev Python", "X", "A", "Rio de Janeiro", False, date(2026, 10, 1)),
        )
        self.assertEqual(salva.termos, ["python"])
        self.assertIsNone(salva.vista_em)
        self.assertIsInstance(salva.primeira_vez_em, datetime)
        self.assertEqual(salva.primeira_vez_em, salva.ultima_vez_em)

    def test_coleta_do_linkedin_grava_a_descricao_e_sem_descricao_nao_apaga(self):
        from src.client.apify_linkedin import ApifyLinkedInClient
        from tests.test_clients import fake_post

        perfis = PerfilBuscaRepositoryMemoria(1)
        perfis.salvar(Perfil(termos=["dados"]))
        linkedin = ApifyLinkedInClient(token="tk", http_post=fake_post("apify_linkedin.json"))
        ColetaService(RecomendacaoService(BuscaService([linkedin])), perfis, self.repo).coletar()

        url = "https://www.linkedin.com/jobs/view/4310298811"
        salva = self.repo.obter(url)
        self.assertEqual(salva.descricao, "Vaga inventada para teste.\n\n• Power BI\n• SQL")
        self.assertIsNotNone(salva.detalhada_em)
        # A mesma vaga vista de novo sem descrição (o actor às vezes não traz) mantém a gravada.
        sem = [{"id": "4310298811", "link": url, "title": "Analista de Dados Pleno - Power BI"}]
        self.repo.registrar(ApifyLinkedInClient(token="tk", http_post=lambda u, c, t: sem).buscar("dados"), "dados")
        self.assertEqual(self.repo.obter(url).descricao, salva.descricao)

    def test_mesma_vaga_por_dois_termos_vira_uma_linha_com_os_dois(self):
        self.assertEqual(self.repo.registrar([vaga("u1")], "python"), 1)
        self.assertEqual(self.repo.registrar([vaga("u1")], "django"), 0)
        self.assertEqual(self.repo.registrar([vaga("u1")], "python"), 0)

        linhas = self.repo.listar(somente_novas=False, limite=10)
        self.assertEqual([v.url for v in linhas], ["u1"])
        self.assertEqual(linhas[0].termos, ["python", "django"])

    def test_segundo_registro_nao_conta_como_nova_e_nao_apaga_vista_em_nem_primeira_vez(self):
        self.repo.registrar([vaga("u1")], "python")
        self.assertEqual(self.repo.marcar_vistas(["u1"]), 1)
        [antes] = self.repo.listar(somente_novas=False, limite=10)

        novas = self.repo.registrar([vaga("u1", titulo="Dev Python Pleno")], "python")

        self.assertEqual(novas, 0)
        [depois] = self.repo.listar(somente_novas=False, limite=10)
        self.assertEqual(depois.vista_em, antes.vista_em)
        self.assertIsNotNone(depois.vista_em)
        self.assertEqual(depois.primeira_vez_em, antes.primeira_vez_em)
        self.assertGreater(depois.ultima_vez_em, antes.ultima_vez_em)
        self.assertEqual(depois.titulo, "Dev Python Pleno")
        self.assertEqual(self.repo.listar(somente_novas=True, limite=10), [])

    def test_campo_que_nao_veio_nao_apaga_o_que_ja_estava(self):
        self.repo.registrar([vaga("u1", local="Recife", remoto=True, publicada_em=date(2026, 9, 1))], "python")

        self.repo.registrar([vaga("u1", titulo="", empresa="")], "python")

        [salva] = self.repo.listar(somente_novas=False, limite=10)
        self.assertEqual(
            (salva.titulo, salva.empresa, salva.local, salva.remoto, salva.publicada_em),
            ("Dev Python", "X", "Recife", True, date(2026, 9, 1)),
        )

    def test_remoto_false_que_veio_substitui_true(self):
        # False é "veio preenchido": não pode ser tratado como ausente.
        self.repo.registrar([vaga("u1", remoto=True)], "python")
        self.repo.registrar([vaga("u1", remoto=False)], "python")

        [salva] = self.repo.listar(somente_novas=False, limite=10)
        self.assertIs(salva.remoto, False)

    def test_url_repetida_na_mesma_chamada_conta_uma_vez(self):
        novas = self.repo.registrar([vaga("u1"), vaga("u1", titulo="Outro")], "python")

        self.assertEqual(novas, 1)
        [salva] = self.repo.listar(somente_novas=False, limite=10)
        self.assertEqual(salva.titulo, "Dev Python")  # fica a primeira, como no BuscaService

    def test_item_invalido_do_provider_e_descartado_sozinho(self):
        invalidas = [
            vaga(""),
            vaga("   "),
            VagaExterna(titulo=None, empresa="X", plataforma="A", url="sem-titulo"),
            vaga("data-errada", publicada_em="2026-10-01"),
            vaga("com-hora", publicada_em=datetime(2026, 10, 1, 12)),
            vaga("nul", titulo="Dev\x00Python"),
            vaga("remoto-texto", remoto="sim"),
            {"url": "dict"},
        ]

        novas = self.repo.registrar([*invalidas, vaga("u1")], "python")

        self.assertEqual(novas, 1)
        self.assertEqual([v.url for v in self.repo.listar(somente_novas=False, limite=10)], ["u1"])

    def test_termo_vazio_e_recusado_antes_de_gravar(self):
        for termo in ("", "   "):
            with self.subTest(termo=termo), self.assertRaises(DadoInvalido):
                self.repo.registrar([vaga("u1")], termo)
        self.assertEqual(self.repo.listar(somente_novas=False, limite=10), [])

    def test_marcar_vistas_tira_da_lista_de_novas_e_conta_so_as_que_mudaram(self):
        self.repo.registrar([vaga("u1"), vaga("u2"), vaga("u3")], "python")

        self.assertEqual(self.repo.marcar_vistas(["u1", "u2", "u1", "nao-existe"]), 2)
        self.assertEqual(self.repo.marcar_vistas(["u1"]), 0)
        self.assertEqual(self.repo.marcar_vistas([]), 0)

        self.assertEqual([v.url for v in self.repo.listar(somente_novas=True, limite=10)], ["u3"])
        self.assertEqual(len(self.repo.listar(somente_novas=False, limite=10)), 3)

    def test_listar_mais_recentes_primeiro_e_respeita_o_limite(self):
        self.repo.registrar([vaga("antiga")], "python")
        self.repo.registrar([vaga("b", publicada_em=date(2026, 1, 1)), vaga("d"), vaga("a"), vaga("c", publicada_em=date(2026, 5, 1))], "python")

        # Mesmo registro (mesmo instante): publicada_em desc, sem data por último, depois a URL.
        self.assertEqual([v.url for v in self.repo.listar(somente_novas=True, limite=10)], ["c", "b", "a", "d", "antiga"])
        self.assertEqual([v.url for v in self.repo.listar(somente_novas=True, limite=2)], ["c", "b"])

    def test_limite_fora_do_teto_e_dado_invalido(self):
        for limite in (0, -1, 501, True):
            with self.subTest(limite=limite), self.assertRaises(DadoInvalido):
                self.repo.listar(somente_novas=True, limite=limite)


class VagaEncontradaMemoriaTest(ContratoVagaEncontrada, unittest.TestCase):
    def setUp(self):
        self.repo = VagaEncontradaRepositoryMemoria(1)


def _schema_descartavel(teste: unittest.TestCase, prefixo: str):
    """Conexão com search_path num schema novo, com as migrations aplicadas duas vezes; apagado no fim."""
    from psycopg import sql

    from src.repository.conexao import conectar

    conn = conectar()
    teste.addCleanup(conn.close)
    schema = f"{prefixo}_{uuid.uuid4().hex[:12]}"
    conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    teste.addCleanup(conn.execute, sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
    conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
    for migration in sorted(MIGRATIONS.glob("*.sql")):
        texto = migration.read_text(encoding="utf-8")
        conn.execute(texto)
        conn.execute(texto)  # idempotente
    return conn, schema


@unittest.skipUnless(os.environ.get("PGHOST"), "PGHOST não definido: integração com Postgres pulada")
class VagaEncontradaPostgresTest(ContratoVagaEncontrada, unittest.TestCase):
    def setUp(self):
        from src.repository.postgres import VagaEncontradaRepositoryPostgres

        self.conn, _ = _schema_descartavel(self, "teste_coleta")
        self.repo = VagaEncontradaRepositoryPostgres(self.conn, 1)

    def test_migration_cria_tabela_e_indice(self):
        self.assertTrue((MIGRATIONS / "002_vaga_encontrada.sql").exists())
        indices = {
            linha[0]
            for linha in self.conn.execute(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'vaga_encontrada' AND schemaname = current_schema()"
            ).fetchall()
        }
        self.assertIn("vaga_encontrada_novas_idx", indices)

    def test_falha_no_meio_desfaz_o_registro_inteiro(self):
        from psycopg import errors

        self.repo.registrar([vaga("ja-existia")], "python")
        # Gatilho só deste schema: a gravação de u2 quebra depois de u1 e do UPDATE de ja-existia.
        self.conn.execute(
            "CREATE FUNCTION quebra_u2() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
            "IF NEW.url = 'u2' THEN RAISE EXCEPTION 'falha simulada'; END IF; RETURN NEW; END $$"
        )
        self.conn.execute(
            "CREATE TRIGGER quebra_u2 BEFORE INSERT ON vaga_encontrada FOR EACH ROW EXECUTE FUNCTION quebra_u2()"
        )

        with self.assertRaises(errors.RaiseException):
            self.repo.registrar([vaga("u1"), vaga("ja-existia"), vaga("u2")], "django")

        [linha] = self.repo.listar(somente_novas=False, limite=10)
        self.assertEqual((linha.url, linha.termos), ("ja-existia", ["python"]))


class ColetaServiceTest(unittest.TestCase):
    def setUp(self):
        self.perfis = PerfilBuscaRepositoryMemoria(1)
        self.vagas = VagaEncontradaRepositoryMemoria(1)
        self.client = ClientPorTermo(
            "A",
            {
                "python": [vaga("u1"), vaga("u2", titulo="Dev Python Sênior")],
                "django": [vaga("u1", titulo="Dev Python Django"), vaga("u3", titulo="Dev Django")],
            },
        )

    def servico(self, *clients) -> ColetaService:
        return ColetaService(RecomendacaoService(BuscaService(list(clients) or [self.client])), self.perfis, self.vagas)

    def test_sem_perfil_salvo_e_dado_invalido(self):
        with self.assertRaisesRegex(DadoInvalido, "Nenhum perfil salvo"):
            self.servico().coletar()
        self.assertEqual(self.client.termos_buscados, [])

    def test_perfil_sem_termos_ou_so_com_termos_vazios_e_dado_invalido(self):
        for termos in ([], ["", "  ", "—"]):
            with self.subTest(termos=termos):
                self.perfis.salvar(Perfil(termos=termos))
                with self.assertRaisesRegex(DadoInvalido, "não tem termos"):
                    self.servico().coletar()
        self.assertEqual(self.client.termos_buscados, [])

    def test_roda_cada_termo_aplica_o_perfil_e_conta_novas_por_termo(self):
        self.perfis.salvar(Perfil(termos=["python", "django"], excluir=["sênior"]))

        resultado = self.servico().coletar()

        self.assertEqual(self.client.termos_buscados, ["python", "django"])
        self.assertEqual([(t.termo, t.encontradas, t.novas) for t in resultado.termos], [("python", 1, 1), ("django", 2, 1)])
        self.assertEqual(resultado.novas, 2)
        self.assertFalse(resultado.falhou)
        linhas = {v.url: v for v in self.vagas.listar(somente_novas=False, limite=10)}
        self.assertEqual(set(linhas), {"u1", "u3"})  # u2 é Sênior: o perfil exclui
        self.assertEqual(linhas["u1"].termos, ["python", "django"])

    def test_segunda_coleta_nao_conta_novas_e_nao_apaga_vista_em(self):
        self.perfis.salvar(Perfil(termos=["python"]))
        self.servico().coletar()
        self.vagas.marcar_vistas(["u1"])

        resultado = self.servico().coletar()

        self.assertEqual([(t.encontradas, t.novas) for t in resultado.termos], [(2, 0)])
        self.assertEqual([v.url for v in self.vagas.listar(somente_novas=True, limite=10)], ["u2"])

    def test_provider_quebrado_nao_derruba_a_coleta(self):
        self.perfis.salvar(Perfil(termos=["python", "django"]))

        resultado = self.servico(ClientQuebrado(), self.client).coletar()

        self.assertEqual([(t.termo, t.novas) for t in resultado.termos], [("python", 2), ("django", 1)])
        for termo in resultado.termos:
            self.assertEqual(termo.erros, {"Quebrado": "TimeoutError: sem resposta"})
        self.assertFalse(resultado.falhou)

    def test_todos_os_providers_quebrados_e_falha(self):
        self.perfis.salvar(Perfil(termos=["python"]))

        resultado = self.servico(ClientQuebrado()).coletar()

        self.assertTrue(resultado.falhou)
        self.assertEqual(resultado.novas, 0)

    def test_provider_respondendo_sem_vaga_nao_e_falha(self):
        self.perfis.salvar(Perfil(termos=["kotlin"]))

        resultado = self.servico(ClientQuebrado(), self.client).coletar()

        self.assertEqual(resultado.termos[0].encontradas, 0)
        self.assertFalse(resultado.falhou)

    def test_termos_repetidos_por_grafia_rodam_uma_vez(self):
        self.assertEqual(
            termos_da_coleta(["Front-end", "front end", "Frontend", "Analista", "analistas", " Java ", "JavaScript", ""]),
            ["Front-end", "Analista", "Java", "JavaScript"],
        )


class ApiColetaTest(unittest.TestCase):
    def setUp(self):
        self.perfis = PerfilBuscaRepositoryMemoria(1)
        self.client = ClientPorTermo("A", {"python": [vaga("u1"), vaga("u2")], "django": [vaga("u1")]})
        busca = BuscaService([ClientQuebrado(), self.client])
        self.api = TestClient(criar_app(self.perfis, CurriculoRepositoryMemoria(1), busca))

    def test_coleta_sem_perfil_e_422(self):
        resposta = self.api.post("/coletas", json={})

        self.assertEqual(resposta.status_code, 422)
        self.assertIn("Nenhum perfil salvo", resposta.json()["detail"])

    def test_coleta_com_perfil_sem_termos_e_422(self):
        self.api.put("/perfil", json={"termos": []})

        self.assertEqual(self.api.post("/coletas", json={}).status_code, 422)

    def test_coleta_lista_marca_vistas_e_coleta_de_novo(self):
        self.api.put("/perfil", json={"termos": ["python", "django"]})

        coleta = self.api.post("/coletas", json={})

        self.assertEqual(coleta.status_code, 200)
        erro = {"Quebrado": "TimeoutError: sem resposta"}
        self.assertEqual(
            coleta.json(),
            {
                "termos": [
                    {"termo": "python", "encontradas": 2, "novas": 2, "erros": erro},
                    {"termo": "django", "encontradas": 1, "novas": 0, "erros": erro},
                ],
                "novas": 2,
            },
        )
        novas = self.api.get("/vagas/encontradas").json()
        self.assertEqual(sorted(v["url"] for v in novas), ["u1", "u2"])
        self.assertEqual(next(v for v in novas if v["url"] == "u1")["termos"], ["python", "django"])

        vistas = self.api.post("/vagas/vistas", json={"urls": ["u1"]})
        self.assertEqual(vistas.json(), {"marcadas": 1})
        self.assertEqual([v["url"] for v in self.api.get("/vagas/encontradas?novas=true").json()], ["u2"])
        self.assertEqual(len(self.api.get("/vagas/encontradas?novas=false").json()), 2)

        segunda = self.api.post("/coletas", json={}).json()
        self.assertEqual(segunda["novas"], 0)
        self.assertEqual([v["url"] for v in self.api.get("/vagas/encontradas").json()], ["u2"])

    def test_limite_fora_do_teto_e_422(self):
        for limite in ("0", "501", "-3", "abc"):
            with self.subTest(limite=limite):
                self.assertEqual(self.api.get(f"/vagas/encontradas?limite={limite}").status_code, 422)
        self.assertEqual(self.api.get("/vagas/encontradas?limite=500").status_code, 200)

    def test_vistas_com_corpo_invalido_e_422(self):
        for corpo in ({}, {"urls": "u1"}, {"urls": [1]}, {"urls": [], "extra": 1}, {"urls": ["u"] * 501}):
            with self.subTest(corpo=str(corpo)[:40]):
                self.assertEqual(self.api.post("/vagas/vistas", json=corpo).status_code, 422)


class FalhaDoBanco(VagaEncontradaRepositoryMemoria):
    def registrar(self, vagas, termo):
        raise ValueError("PGPORT inválido")


class ApiColetaErroDoServidorTest(unittest.TestCase):
    def test_value_error_do_repositorio_na_coleta_vira_500(self):
        perfis = PerfilBuscaRepositoryMemoria(1)
        perfis.salvar(Perfil(termos=["python"]))
        api = TestClient(
            criar_app(perfis, CurriculoRepositoryMemoria(1), BuscaService([ClientFixo("A", [vaga("u1")])]), encontradas=FalhaDoBanco(1)),
            raise_server_exceptions=False,
        )

        self.assertEqual(api.post("/coletas", json={}).status_code, 500)


@unittest.skipUnless(os.environ.get("PGHOST"), "PGHOST não definido: coleta com Postgres pulada")
class ApiColetaPostgresTest(unittest.TestCase):
    """A coleta pela API com os repositórios Postgres pelo pool, como na produção."""

    def test_coleta_de_ponta_a_ponta_pelo_pool(self):
        from psycopg_pool import ConnectionPool

        from src.controller.api import _PorChamada
        from src.repository.conexao import parametros
        from src.repository.postgres import (
            CurriculoRepositoryPostgres,
            PerfilBuscaRepositoryPostgres,
            VagaEncontradaRepositoryPostgres,
        )

        _, schema = _schema_descartavel(self, "teste_api_coleta")
        pool = ConnectionPool(
            kwargs={**parametros(), "autocommit": True, "options": f"-c search_path={schema}"},
            min_size=1,
            max_size=2,
            open=True,
        )
        self.addCleanup(pool.close)
        client = ClientPorTermo("A", {"python": [vaga("u1"), vaga("u2")], "django": [vaga("u1")]})
        api = TestClient(
            criar_app(
                _PorChamada(lambda: pool, lambda: PerfilBuscaRepositoryPostgres, 1),
                _PorChamada(lambda: pool, lambda: CurriculoRepositoryPostgres, 1),
                BuscaService([ClientQuebrado(), client]),
                encontradas=_PorChamada(lambda: pool, lambda: VagaEncontradaRepositoryPostgres, 1),
            )
        )

        self.assertEqual(api.post("/coletas", json={}).status_code, 422)
        api.put("/perfil", json={"termos": ["python", "django"]})
        self.assertEqual(api.post("/coletas", json={}).json()["novas"], 2)
        self.assertEqual(api.post("/vagas/vistas", json={"urls": ["u1", "u2"]}).json(), {"marcadas": 2})
        self.assertEqual(api.post("/coletas", json={}).json()["novas"], 0)
        self.assertEqual(api.get("/vagas/encontradas").json(), [])
        todas = api.get("/vagas/encontradas?novas=false").json()
        self.assertEqual({v["url"]: v["termos"] for v in todas}, {"u1": ["python", "django"], "u2": ["python"]})
        self.assertTrue(all(v["vista_em"] for v in todas))


class CliColetaTest(unittest.TestCase):
    def setUp(self):
        self.perfis = PerfilBuscaRepositoryMemoria(1)
        self.vagas = VagaEncontradaRepositoryMemoria(1)
        self.saida, self.erro = io.StringIO(), io.StringIO()

    def rodar(self, *clients) -> int:
        servico = ColetaService(RecomendacaoService(BuscaService(list(clients))), self.perfis, self.vagas)
        return rodar(lambda: servico, self.saida, self.erro)

    def test_uma_linha_por_termo_e_sai_com_zero_mesmo_com_provider_quebrado(self):
        self.perfis.salvar(Perfil(termos=["python", "django"]))

        codigo = self.rodar(ClientQuebrado(), ClientPorTermo("A", {"python": [vaga("u1")]}))

        self.assertEqual(codigo, 0)
        self.assertEqual(
            self.saida.getvalue().splitlines(),
            [
                "python: 1 encontradas, 1 novas | erros: Quebrado: TimeoutError: sem resposta",
                "django: 0 encontradas, 0 novas | erros: Quebrado: TimeoutError: sem resposta",
            ],
        )
        self.assertEqual(self.erro.getvalue(), "")

    def test_sem_perfil_sai_com_um(self):
        codigo = self.rodar(ClientFixo("A", [vaga("u1")]))

        self.assertEqual(codigo, 1)
        self.assertIn("Nenhum perfil salvo", self.erro.getvalue())
        self.assertEqual(self.saida.getvalue(), "")

    def test_todos_os_providers_quebrados_sai_com_um(self):
        self.perfis.salvar(Perfil(termos=["python"]))

        self.assertEqual(self.rodar(ClientQuebrado()), 1)
        self.assertIn("todos os providers", self.erro.getvalue())

    def test_banco_fora_do_ar_sai_com_um(self):
        def sem_banco():
            raise OSError("connection refused")

        self.assertEqual(rodar(sem_banco, self.saida, self.erro), 1)
        self.assertIn("OSError: connection refused", self.erro.getvalue())


class ProducaoTest(unittest.TestCase):
    def test_producao_guarda_o_historico_no_postgres_pelo_pool(self):
        # Sem `encontradas`, criar_app cai na memória: a produção perderia tudo a cada restart.
        from unittest import mock

        from src.controller import api
        from src.repository.postgres import VagaEncontradaRepositoryPostgres

        with mock.patch.object(api, "criar_app") as criar:
            api.criar_app_producao()

        encontradas = criar.call_args.kwargs["encontradas"]
        self.assertIsInstance(encontradas, api._PorChamada)
        self.assertIs(encontradas._classe(), VagaEncontradaRepositoryPostgres)
        self.assertIs(encontradas._pool, criar.call_args.kwargs["perfis"]._pool)
