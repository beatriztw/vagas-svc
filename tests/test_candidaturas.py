"""Candidaturas: repositório (memória e Postgres), serviço, API JSON e fiação de produção.

As vagas daqui são escritas à mão (não são respostas gravadas de provider).
"""

import os
import re
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from src.controller.api import criar_app
from src.model.candidatura import STATUS_PADRAO, Candidatura, CandidaturaComVaga, StatusCandidatura
from src.model.erros import DadoInvalido
from src.model.vaga_externa import VagaExterna
from src.repository.base import (
    CurriculoRepositoryMemoria,
    PerfilBuscaRepositoryMemoria,
    VagaEncontradaRepositoryMemoria,
)
from src.repository.candidatura import CandidaturaRepository, CandidaturaRepositoryMemoria, vaga_por_url_em
from src.service.busca_service import BuscaService
from src.service.candidatura_service import CandidaturaService

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
URL_A = "https://exemplo.com/vaga-a"
URL_B = "https://exemplo.com/vaga-b?a=1&b=2#c=d"


def vaga(url: str, titulo: str = "Dev Python", empresa: str = "Empresa X") -> VagaExterna:
    return VagaExterna(titulo=titulo, empresa=empresa, plataforma="A", url=url)


class ContratoCandidatura:
    """Mesmo contrato para a memória e o Postgres. Quem herda cria `self.vagas` e `self.repo`."""

    def setUp(self):
        self.criar_repositorios()
        self.vagas.registrar([vaga(URL_A, "Dev Python", "Alfa"), vaga(URL_B, "Analista", "Beta")], "python")

    def test_criar_devolve_enviada_com_titulo_e_empresa_da_vaga(self):
        candidatura, criada = self.repo.criar(URL_A)

        self.assertTrue(criada)
        self.assertIsInstance(candidatura, CandidaturaComVaga)
        self.assertIsInstance(candidatura, Candidatura)
        self.assertEqual(
            (candidatura.vaga_url, candidatura.status, candidatura.titulo, candidatura.empresa),
            (URL_A, StatusCandidatura.ENVIADA, "Dev Python", "Alfa"),
        )
        self.assertIsInstance(candidatura.aplicada_em, datetime)
        self.assertIsNotNone(candidatura.aplicada_em.tzinfo)
        self.assertEqual(self.repo.contar(), 1)

    def test_repetida_nao_duplica_e_devolve_a_existente(self):
        primeira, _ = self.repo.criar(URL_A)
        self.repo.atualizar_status(primeira.id, StatusCandidatura.ENTREVISTA)

        segunda, criada = self.repo.criar(URL_A)

        self.assertFalse(criada)
        self.assertEqual(segunda.id, primeira.id)
        # Repetir não volta o status para "enviada".
        self.assertEqual(segunda.status, StatusCandidatura.ENTREVISTA)
        self.assertEqual(self.repo.contar(), 1)

    def test_vaga_fora_do_historico_e_dado_invalido(self):
        with self.assertRaisesRegex(DadoInvalido, "vaga não encontrada"):
            self.repo.criar("https://exemplo.com/nao-coletada")
        self.assertEqual(self.repo.contar(), 0)
        # Depois do erro o repositório segue funcionando.
        self.assertTrue(self.repo.criar(URL_A)[1])

    def test_url_invalida_e_dado_invalido(self):
        for url in ("", "   ", None, 7, ["x"], "a\x00b"):
            with self.subTest(url=url), self.assertRaises(DadoInvalido):
                self.repo.criar(url)
        self.assertEqual(self.repo.contar(), 0)

    def test_listar_mais_recentes_primeiro_com_desempate_por_id(self):
        a, _ = self.repo.criar(URL_A)
        b, _ = self.repo.criar(URL_B)

        self.assertEqual([c.id for c in self.repo.listar()], [b.id, a.id])

    def test_listar_desempata_por_id_quando_aplicada_em_e_igual(self):
        self.vagas.registrar([vaga("https://exemplo.com/c")], "python")
        a, _ = self.repo.criar(URL_A)
        b, _ = self.repo.criar(URL_B)
        c, _ = self.repo.criar("https://exemplo.com/c")
        self.igualar_aplicada_em()

        self.assertEqual([x.id for x in self.repo.listar()], [c.id, b.id, a.id])

    def test_obter_e_obter_por_url(self):
        criada, _ = self.repo.criar(URL_B)

        self.assertEqual(self.repo.obter(criada.id), criada)
        self.assertEqual(self.repo.obter_por_url(URL_B), criada)
        self.assertIsNone(self.repo.obter(criada.id + 1000))
        self.assertIsNone(self.repo.obter(0))
        self.assertIsNone(self.repo.obter(2**70))
        self.assertIsNone(self.repo.obter_por_url(URL_A))
        with self.assertRaises(DadoInvalido):
            self.repo.obter("1")
        with self.assertRaises(DadoInvalido):
            self.repo.obter_por_url("")

    def test_atualizar_status_aceita_enum_e_valor(self):
        criada, _ = self.repo.criar(URL_A)

        a = self.repo.atualizar_status(criada.id, StatusCandidatura.EM_ANALISE)
        b = self.repo.atualizar_status(criada.id, "recusada")

        self.assertEqual(a.status, StatusCandidatura.EM_ANALISE)
        self.assertEqual(b.status, StatusCandidatura.RECUSADA)
        self.assertEqual((b.titulo, b.empresa), ("Dev Python", "Alfa"))
        self.assertGreaterEqual(b.atualizada_em, criada.atualizada_em)
        self.assertEqual(b.aplicada_em, criada.aplicada_em)
        self.assertEqual(self.repo.obter(criada.id).status, StatusCandidatura.RECUSADA)

    def test_status_fora_do_enum_e_dado_invalido_e_nao_grava(self):
        criada, _ = self.repo.criar(URL_A)
        for status in ("aprovada", "Entrevista", "", None, 3, "enviada\x00"):
            with self.subTest(status=status), self.assertRaises(DadoInvalido):
                self.repo.atualizar_status(criada.id, status)
        self.assertEqual(self.repo.obter(criada.id).status, StatusCandidatura.ENVIADA)

    def test_atualizar_status_inexistente_devolve_none(self):
        self.assertIsNone(self.repo.atualizar_status(999, StatusCandidatura.OFERTA))
        self.assertIsNone(self.repo.atualizar_status(2**70, StatusCandidatura.OFERTA))

    def test_remover_desfaz_e_permite_marcar_de_novo(self):
        criada, _ = self.repo.criar(URL_A)

        self.assertTrue(self.repo.remover(criada.id))
        self.assertFalse(self.repo.remover(criada.id))
        self.assertFalse(self.repo.remover(2**70))
        self.assertEqual(self.repo.contar(), 0)
        nova, criada_de_novo = self.repo.criar(URL_A)
        self.assertTrue(criada_de_novo)
        self.assertEqual(nova.status, StatusCandidatura.ENVIADA)

    def test_titulo_da_vaga_atualizado_pela_coleta_aparece_na_leitura(self):
        criada, _ = self.repo.criar(URL_A)
        self.vagas.registrar([vaga(URL_A, "Dev Python Sênior", "Alfa")], "django")

        self.assertEqual(self.repo.obter(criada.id).titulo, "Dev Python Sênior")


class CandidaturaMemoriaTest(ContratoCandidatura, unittest.TestCase):
    def criar_repositorios(self):
        self.vagas = VagaEncontradaRepositoryMemoria()
        self.repo = CandidaturaRepositoryMemoria(vaga_por_url_em(self.vagas))

    def igualar_aplicada_em(self):
        instante = next(iter(self.repo._linhas.values())).aplicada_em
        for id, linha in list(self.repo._linhas.items()):
            self.repo._linhas[id] = type(linha)(**{**vars(linha), "aplicada_em": instante})


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


def _pool(teste: unittest.TestCase, schema: str, tamanho: int = 2):
    from psycopg_pool import ConnectionPool

    from src.repository.conexao import parametros

    pool = ConnectionPool(
        kwargs={**parametros(), "autocommit": True, "options": f"-c search_path={schema}"},
        min_size=1,
        max_size=tamanho,
        open=True,
    )
    teste.addCleanup(pool.close)
    return pool


@unittest.skipUnless(os.environ.get("PGHOST"), "PGHOST não definido: integração com Postgres pulada")
class CandidaturaPostgresTest(ContratoCandidatura, unittest.TestCase):
    def criar_repositorios(self):
        from src.repository.candidatura_postgres import CandidaturaRepositoryPostgres
        from src.repository.postgres import VagaEncontradaRepositoryPostgres

        self.conn, self.schema = _schema_descartavel(self, "teste_candidatura")
        self.vagas = VagaEncontradaRepositoryPostgres(self.conn)
        self.repo = CandidaturaRepositoryPostgres(self.conn)

    def igualar_aplicada_em(self):
        self.conn.execute("UPDATE candidatura SET aplicada_em = '2026-01-01T00:00:00Z'")

    def test_migration_cria_tabela_e_indice(self):
        indices = {
            linha[0]
            for linha in self.conn.execute(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'candidatura' AND schemaname = current_schema()"
            ).fetchall()
        }
        self.assertIn("candidatura_aplicada_idx", indices)

    def test_check_do_banco_recusa_status_fora_do_enum(self):
        from psycopg import errors

        with self.assertRaises(errors.CheckViolation):
            self.conn.execute("INSERT INTO candidatura (vaga_url, status) VALUES (%s, 'aprovada')", (URL_A,))
        self.assertEqual(self.repo.contar(), 0)

    def test_chave_estrangeira_e_unica_no_banco(self):
        from psycopg import errors

        with self.assertRaises(errors.ForeignKeyViolation):
            self.conn.execute("INSERT INTO candidatura (vaga_url) VALUES ('nao-existe')")
        self.conn.execute("INSERT INTO candidatura (vaga_url) VALUES (%s)", (URL_A,))
        with self.assertRaises(errors.UniqueViolation):
            self.conn.execute("INSERT INTO candidatura (vaga_url) VALUES (%s)", (URL_A,))

    def test_erro_do_banco_que_nao_e_a_vaga_inexistente_nao_vira_dado_invalido(self):
        from psycopg import errors

        # Um gatilho que quebra o INSERT: o erro tem que subir como veio (500), não como 422.
        self.conn.execute(
            "CREATE FUNCTION quebra_insert() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
            "RAISE EXCEPTION 'falha simulada'; END $$"
        )
        self.conn.execute(
            "CREATE TRIGGER quebra_insert BEFORE INSERT ON candidatura FOR EACH ROW EXECUTE FUNCTION quebra_insert()"
        )

        with self.assertRaises(errors.RaiseException):
            self.repo.criar(URL_A)

    def test_duas_threads_marcando_a_mesma_vaga_criam_uma_so(self):
        from src.repository.candidatura_postgres import CandidaturaRepositoryPostgres

        pool = _pool(self, self.schema, tamanho=8)
        largada = threading.Barrier(8)

        def marcar(_):
            with pool.connection() as conn:
                largada.wait(timeout=10)
                return CandidaturaRepositoryPostgres(conn).criar(URL_A)

        with ThreadPoolExecutor(max_workers=8) as executor:
            resultados = list(executor.map(marcar, range(8)))

        self.assertEqual(sum(1 for _, criada in resultados if criada), 1)
        self.assertEqual({c.id for c, _ in resultados}, {resultados[0][0].id})
        self.assertEqual(self.repo.contar(), 1)


def api_com_memoria(candidaturas=None, **kwargs):
    encontradas = VagaEncontradaRepositoryMemoria()
    encontradas.registrar([vaga(URL_A, "Dev Python", "Alfa"), vaga(URL_B, "Analista", "Beta")], "python")
    app = criar_app(
        PerfilBuscaRepositoryMemoria(),
        CurriculoRepositoryMemoria(),
        BuscaService([]),
        encontradas=encontradas,
        candidaturas=candidaturas,
    )
    return TestClient(app, **kwargs)


class ApiCandidaturaTest(unittest.TestCase):
    def setUp(self):
        self.api = api_com_memoria()

    def test_post_cria_com_201_e_repetida_devolve_200_com_o_mesmo_id(self):
        primeira = self.api.post("/candidaturas", json={"vaga_url": URL_A})
        segunda = self.api.post("/candidaturas", json={"vaga_url": URL_A})

        self.assertEqual(primeira.status_code, 201)
        self.assertEqual(segunda.status_code, 200)
        corpo = primeira.json()
        self.assertEqual(corpo["id"], segunda.json()["id"])
        self.assertEqual(
            (corpo["vaga_url"], corpo["status"], corpo["status_rotulo"], corpo["titulo"], corpo["empresa"]),
            (URL_A, "enviada", "Enviada", "Dev Python", "Alfa"),
        )
        self.assertEqual(len(self.api.get("/candidaturas").json()), 1)

    def test_vaga_fora_do_historico_e_422(self):
        resposta = self.api.post("/candidaturas", json={"vaga_url": "https://exemplo.com/nao-coletada"})

        self.assertEqual(resposta.status_code, 422)
        self.assertIn("vaga não encontrada", resposta.json()["detail"])
        self.assertEqual(self.api.get("/candidaturas").json(), [])

    def test_corpo_invalido_e_422(self):
        for corpo in ({}, {"vaga_url": ""}, {"vaga_url": 3}, {"vaga_url": URL_A, "status": "oferta"}, {"vaga_url": "a\x00b"}):
            with self.subTest(corpo=corpo):
                self.assertEqual(self.api.post("/candidaturas", json=corpo).status_code, 422)

    def test_post_sem_content_type_json_e_415_e_nao_grava(self):
        # Blob sem tipo (fetch no-cors de outro site) não manda Content-Type.
        corpo = '{"vaga_url": "%s"}' % URL_A
        for cabecalhos in ({}, {"Content-Type": "text/plain"}, {"Content-Type": "application/x-www-form-urlencoded"}):
            with self.subTest(cabecalhos=cabecalhos):
                resposta = self.api.post("/candidaturas", content=corpo, headers=cabecalhos)
                self.assertEqual(resposta.status_code, 415)
        self.assertEqual(self.api.get("/candidaturas").json(), [])
        ok = self.api.post(
            "/candidaturas", content=corpo, headers={"Content-Type": "application/json; charset=utf-8"}
        )
        self.assertEqual(ok.status_code, 201)

    def test_get_lista_mais_recentes_primeiro(self):
        self.api.post("/candidaturas", json={"vaga_url": URL_A})
        self.api.post("/candidaturas", json={"vaga_url": URL_B})

        urls = [c["vaga_url"] for c in self.api.get("/candidaturas").json()]

        self.assertEqual(urls, [URL_B, URL_A])

    def test_patch_muda_o_status(self):
        id = self.api.post("/candidaturas", json={"vaga_url": URL_A}).json()["id"]

        resposta = self.api.patch(f"/candidaturas/{id}", json={"status": "em_analise"})

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual((resposta.json()["status"], resposta.json()["status_rotulo"]), ("em_analise", "Em análise"))
        self.assertEqual(self.api.get("/candidaturas").json()[0]["status"], "em_analise")

    def test_patch_status_fora_do_enum_e_422_e_nao_muda(self):
        id = self.api.post("/candidaturas", json={"vaga_url": URL_A}).json()["id"]
        for corpo in ({"status": "aprovada"}, {"status": "Entrevista"}, {"status": None}, {}, {"status": "oferta", "x": 1}):
            with self.subTest(corpo=corpo):
                self.assertEqual(self.api.patch(f"/candidaturas/{id}", json=corpo).status_code, 422)
        self.assertEqual(self.api.get("/candidaturas").json()[0]["status"], "enviada")

    def test_patch_e_delete_de_inexistente_sao_404(self):
        self.assertEqual(self.api.patch("/candidaturas/99", json={"status": "oferta"}).status_code, 404)
        self.assertEqual(self.api.delete("/candidaturas/99").status_code, 404)
        self.assertEqual(self.api.patch("/candidaturas/abc", json={"status": "oferta"}).status_code, 422)

    def test_delete_desfaz_com_204(self):
        id = self.api.post("/candidaturas", json={"vaga_url": URL_A}).json()["id"]

        resposta = self.api.delete(f"/candidaturas/{id}")

        self.assertEqual((resposta.status_code, resposta.content), (204, b""))
        self.assertEqual(self.api.get("/candidaturas").json(), [])
        self.assertEqual(self.api.delete(f"/candidaturas/{id}").status_code, 404)


class RepoComFalha(CandidaturaRepository):
    """Repositório que quebra como um erro de configuração/banco (não é DadoInvalido)."""

    def _falha(self, *_):
        raise ValueError("configuração inválida")

    criar = listar = contar = obter = obter_por_url = atualizar_status = remover = _falha


class ApiCandidaturaErroDoServidorTest(unittest.TestCase):
    def test_value_error_do_repositorio_e_500_nao_422(self):
        api = api_com_memoria(RepoComFalha(), raise_server_exceptions=False)

        self.assertEqual(api.post("/candidaturas", json={"vaga_url": URL_A}).status_code, 500)
        self.assertEqual(api.get("/candidaturas").status_code, 500)
        self.assertEqual(api.patch("/candidaturas/1", json={"status": "oferta"}).status_code, 500)
        self.assertEqual(api.delete("/candidaturas/1").status_code, 500)

    def test_dado_invalido_do_repositorio_e_422(self):
        class Recusa(RepoComFalha):
            def _falha(self, *_):
                raise DadoInvalido("recusado")

            criar = _falha

        api = api_com_memoria(Recusa(), raise_server_exceptions=False)

        self.assertEqual(api.post("/candidaturas", json={"vaga_url": URL_A}).status_code, 422)


class CandidaturaServiceTest(unittest.TestCase):
    def test_fluxo_marcar_listar_mudar_desfazer(self):
        vagas = VagaEncontradaRepositoryMemoria()
        vagas.registrar([vaga(URL_A)], "python")
        servico = CandidaturaService(CandidaturaRepositoryMemoria(vaga_por_url_em(vagas)))

        candidatura, criada = servico.marcar_aplicada(URL_A)
        self.assertTrue(criada)
        self.assertEqual(servico.contar(), 1)
        self.assertEqual([c.id for c in servico.listar()], [candidatura.id])
        self.assertEqual(servico.mudar_status(candidatura.id, "oferta").status, StatusCandidatura.OFERTA)
        self.assertEqual(servico.obter_por_url(URL_A).id, candidatura.id)
        self.assertTrue(servico.desfazer(candidatura.id))
        self.assertIsNone(servico.obter(candidatura.id))


class StatusCandidaturaTest(unittest.TestCase):
    def test_rotulos_de_exibicao(self):
        self.assertEqual(
            {s.value: s.rotulo for s in StatusCandidatura},
            {
                "enviada": "Enviada",
                "em_analise": "Em análise",
                "entrevista": "Entrevista",
                "recusada": "Recusada",
                "oferta": "Oferta",
            },
        )

    def test_migration_e_enum_tem_os_mesmos_status(self):
        texto = (MIGRATIONS / "003_candidatura.sql").read_text(encoding="utf-8")
        check = re.search(r"CHECK \(status IN \(([^)]*)\)\)", texto).group(1)
        self.assertEqual(re.findall(r"'([^']*)'", check), [s.value for s in StatusCandidatura])
        self.assertIn(f"DEFAULT '{STATUS_PADRAO.value}'", texto)


@unittest.skipUnless(os.environ.get("PGHOST"), "PGHOST não definido: integração com Postgres pulada")
class ApiCandidaturaPostgresTest(unittest.TestCase):
    """A API de candidaturas com os repositórios Postgres pelo pool, como na produção."""

    def test_ponta_a_ponta_pelo_pool(self):
        from src.controller.api import _PorChamada
        from src.repository.candidatura_postgres import CandidaturaRepositoryPostgres
        from src.repository.postgres import (
            CurriculoRepositoryPostgres,
            PerfilBuscaRepositoryPostgres,
            VagaEncontradaRepositoryPostgres,
        )

        conn, schema = _schema_descartavel(self, "teste_api_candidatura")
        VagaEncontradaRepositoryPostgres(conn).registrar([vaga(URL_A, "Dev Python", "Alfa")], "python")
        pool = _pool(self, schema)
        api = TestClient(
            criar_app(
                _PorChamada(lambda: pool, lambda: PerfilBuscaRepositoryPostgres),
                _PorChamada(lambda: pool, lambda: CurriculoRepositoryPostgres),
                BuscaService([]),
                encontradas=_PorChamada(lambda: pool, lambda: VagaEncontradaRepositoryPostgres),
                candidaturas=_PorChamada(lambda: pool, lambda: CandidaturaRepositoryPostgres),
            ),
            raise_server_exceptions=False,
        )

        criada = api.post("/candidaturas", json={"vaga_url": URL_A})
        repetida = api.post("/candidaturas", json={"vaga_url": URL_A})
        self.assertEqual((criada.status_code, repetida.status_code), (201, 200))
        self.assertEqual(api.post("/candidaturas", json={"vaga_url": "nao-existe"}).status_code, 422)
        id = criada.json()["id"]
        self.assertEqual(api.patch(f"/candidaturas/{id}", json={"status": "entrevista"}).json()["titulo"], "Dev Python")
        self.assertEqual(api.patch(f"/candidaturas/{id}", json={"status": "aprovada"}).status_code, 422)
        self.assertEqual(api.patch("/candidaturas/99999", json={"status": "oferta"}).status_code, 404)
        self.assertEqual([c["status"] for c in api.get("/candidaturas").json()], ["entrevista"])
        self.assertEqual(api.delete(f"/candidaturas/{id}").status_code, 204)
        self.assertEqual(api.delete(f"/candidaturas/{id}").status_code, 404)


class ProducaoCandidaturaTest(unittest.TestCase):
    def test_producao_guarda_as_candidaturas_no_postgres_pelo_pool(self):
        # Sem `candidaturas`, criar_app cai na memória: a produção perderia tudo a cada restart.
        from src.controller import api
        from src.repository.candidatura_postgres import CandidaturaRepositoryPostgres

        with mock.patch.object(api, "criar_app") as criar:
            api.criar_app_producao()

        candidaturas = criar.call_args.kwargs["candidaturas"]
        self.assertIsInstance(candidaturas, api._PorChamada)
        self.assertIs(candidaturas._classe(), CandidaturaRepositoryPostgres)
        self.assertIs(candidaturas._pool, criar.call_args.kwargs["perfis"]._pool)


if __name__ == "__main__":
    unittest.main()
