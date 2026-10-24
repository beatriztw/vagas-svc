"""Uma coleta por vez: trava em memória e no Postgres, serviço, API (409) e CLI.

As vagas daqui são escritas à mão (não são respostas gravadas de provider).
"""

import io
import os
import threading
import unittest

from fastapi.testclient import TestClient

from src.client.base import ProviderClient
from src.controller.api import criar_app
from src.controller.coleta import rodar
from src.model.erros import DadoInvalido
from src.model.perfil import Perfil
from src.repository.base import (
    CurriculoRepositoryMemoria,
    PerfilBuscaRepositoryMemoria,
    VagaEncontradaRepositoryMemoria,
)
from src.repository.trava import ColetaEmAndamento, TravaColetaMemoria
from src.service.busca_service import BuscaService
from src.service.coleta_service import ColetaService
from src.service.recomendacao_service import RecomendacaoService
from tests.test_coleta import _schema_descartavel, vaga

ESPERA = 10  # segundos: teto para um teste nunca ficar pendurado


class ProviderBloqueado(ProviderClient):
    """Provider fake: avisa que começou e só devolve quando o teste soltar."""

    nome = "Bloqueado"

    def __init__(self):
        self.comecou = threading.Event()
        self.soltar = threading.Event()
        self.chamadas = 0

    def buscar(self, termo):
        self.chamadas += 1
        self.comecou.set()
        if not self.soltar.wait(ESPERA):
            raise TimeoutError("o teste não soltou o provider")
        return [vaga("u1")]


def servico(client, perfis, vagas=None, trava=None) -> ColetaService:
    return ColetaService.de_uma_pessoa(
        RecomendacaoService(BuscaService([client])), perfis, vagas or VagaEncontradaRepositoryMemoria(1), trava
    )


def perfis_com(*termos: str) -> PerfilBuscaRepositoryMemoria:
    perfis = PerfilBuscaRepositoryMemoria(1)
    perfis.salvar(Perfil(termos=list(termos)))
    return perfis


class TravaMemoriaTest(unittest.TestCase):
    def test_segunda_nao_pega_enquanto_a_primeira_segura_e_pega_depois(self):
        trava = TravaColetaMemoria()

        with trava.segurar():
            with self.assertRaises(ColetaEmAndamento), trava.segurar():
                self.fail("não devia entrar")
        with trava.segurar():
            pass

    def test_libera_depois_de_excecao_no_bloco(self):
        trava = TravaColetaMemoria()

        with self.assertRaises(ValueError), trava.segurar():
            raise ValueError("falha no meio")

        with trava.segurar():
            pass

    def test_coleta_em_andamento_nao_e_dado_invalido(self):
        self.assertFalse(issubclass(ColetaEmAndamento, DadoInvalido))
        self.assertEqual(str(ColetaEmAndamento()), "coleta em andamento")


class ColetaServiceTravaTest(unittest.TestCase):
    def test_libera_a_trava_quando_a_coleta_falha_no_meio(self):
        class FalhaDoBanco(VagaEncontradaRepositoryMemoria):
            falhar = True

            def registrar(self, vagas, termo):
                if self.falhar:
                    raise ValueError("banco caiu")
                return super().registrar(vagas, termo)

        banco = FalhaDoBanco(1)
        coleta = servico(ProviderBloqueado(), perfis_com("python"), banco)
        coleta.recomendacao.busca.clients[0].soltar.set()

        with self.assertRaises(ValueError):
            coleta.coletar()
        banco.falhar = False

        self.assertEqual(coleta.coletar().novas, 1)

    def test_libera_a_trava_quando_nao_ha_perfil(self):
        coleta = servico(ProviderBloqueado(), PerfilBuscaRepositoryMemoria(1))

        for _ in range(2):  # a segunda vez seria ColetaEmAndamento se a trava tivesse ficado presa
            with self.assertRaises(DadoInvalido):
                coleta.coletar()

    def test_duas_coletas_no_mesmo_servico_a_segunda_nao_espera_nem_chama_o_provider(self):
        provider = ProviderBloqueado()
        coleta = servico(provider, perfis_com("python"))
        resultado = {}
        primeira = threading.Thread(target=lambda: resultado.update(r=coleta.coletar()))
        primeira.start()
        self.addCleanup(primeira.join, ESPERA)
        self.addCleanup(provider.soltar.set)
        self.assertTrue(provider.comecou.wait(ESPERA))

        with self.assertRaises(ColetaEmAndamento):
            coleta.coletar()

        provider.soltar.set()
        primeira.join(ESPERA)
        self.assertEqual(provider.chamadas, 1)
        self.assertEqual(resultado["r"].novas, 1)
        self.assertEqual(coleta.coletar().novas, 0)  # liberou: roda de novo
        self.assertEqual(provider.chamadas, 2)


class ApiTravaTest(unittest.TestCase):
    def test_post_coletas_com_outra_em_andamento_e_409_sem_esperar(self):
        provider = ProviderBloqueado()
        perfis = perfis_com("python")
        api = criar_app(perfis, CurriculoRepositoryMemoria(1), BuscaService([provider]))
        resposta = {}
        primeira = threading.Thread(target=lambda: resposta.update(r=TestClient(api).post("/coletas", json={})))
        primeira.start()
        self.addCleanup(primeira.join, ESPERA)
        self.addCleanup(provider.soltar.set)
        self.assertTrue(provider.comecou.wait(ESPERA))

        segunda = TestClient(api).post("/coletas", json={})

        self.assertEqual(segunda.status_code, 409)
        self.assertEqual(segunda.json(), {"detail": "coleta em andamento"})
        provider.soltar.set()
        primeira.join(ESPERA)
        self.assertEqual(resposta["r"].status_code, 200)
        self.assertEqual(resposta["r"].json()["novas"], 1)
        self.assertEqual(provider.chamadas, 1, "só a primeira coleta chamou o provider")
        self.assertEqual(TestClient(api).post("/coletas", json={}).status_code, 200)  # liberou

    def test_trava_compartilhada_entre_dois_apps(self):
        # Como a API e a tela/cron: a trava injetada vale para todos que a recebem.
        trava = TravaColetaMemoria()
        perfis = perfis_com("python")
        provider = ProviderBloqueado()
        provider.soltar.set()
        api = TestClient(criar_app(perfis, CurriculoRepositoryMemoria(1), BuscaService([provider]), trava=trava))

        with trava.segurar():
            self.assertEqual(api.post("/coletas", json={}).status_code, 409)
        self.assertEqual(provider.chamadas, 0)
        self.assertEqual(api.post("/coletas", json={}).status_code, 200)

    def test_erro_do_servidor_continua_500_e_libera_a_trava(self):
        class FalhaDoBanco(VagaEncontradaRepositoryMemoria):
            def registrar(self, vagas, termo):
                raise ValueError("PGPORT inválido")

        provider = ProviderBloqueado()
        provider.soltar.set()
        api = TestClient(
            criar_app(perfis_com("python"), CurriculoRepositoryMemoria(1), BuscaService([provider]), encontradas=FalhaDoBanco(1)),
            raise_server_exceptions=False,
        )

        self.assertEqual(api.post("/coletas", json={}).status_code, 500)
        self.assertEqual(api.post("/coletas", json={}).status_code, 500)  # 500 de novo, nunca 409


class CliTravaTest(unittest.TestCase):
    def test_com_outra_coleta_em_andamento_sai_com_um_e_avisa_no_stderr(self):
        provider = ProviderBloqueado()
        provider.soltar.set()
        trava = TravaColetaMemoria()
        coleta = servico(provider, perfis_com("python"), trava=trava)
        saida, erro = io.StringIO(), io.StringIO()

        with trava.segurar():
            codigo = rodar(lambda: coleta, saida, erro)

        self.assertEqual(codigo, 1)
        self.assertEqual(erro.getvalue(), "coleta em andamento\n")
        self.assertEqual(saida.getvalue(), "")
        self.assertEqual(provider.chamadas, 0)
        self.assertEqual(rodar(lambda: coleta, saida, erro), 0)  # livre: roda


@unittest.skipUnless(os.environ.get("PGHOST"), "PGHOST não definido: trava no Postgres pulada")
class TravaPostgresTest(unittest.TestCase):
    def test_duas_conexoes_a_segunda_nao_pega_enquanto_a_primeira_segura_e_pega_depois(self):
        from src.repository.conexao import conectar
        from src.repository.trava import TravaColetaPostgres

        primeira, segunda = TravaColetaPostgres(conectar), TravaColetaPostgres(conectar)

        with primeira.segurar():
            with self.assertRaises(ColetaEmAndamento), segunda.segurar():
                self.fail("não devia entrar")
            with self.assertRaises(ColetaEmAndamento), primeira.segurar():
                self.fail("nem a mesma instância, em outra conexão")
        with segunda.segurar():
            pass

    def test_libera_depois_de_excecao_no_bloco(self):
        from src.repository.conexao import conectar
        from src.repository.trava import TravaColetaPostgres

        trava = TravaColetaPostgres(conectar)

        with self.assertRaises(ValueError), trava.segurar():
            raise ValueError("falha no meio")

        with TravaColetaPostgres(conectar).segurar():
            pass

    def test_fechar_a_conexao_solta_a_trava(self):
        # Processo que morre no meio da coleta: o Postgres solta o lock da sessão.
        from src.repository.conexao import conectar
        from src.repository.trava import CHAVE_TRAVA_COLETA, TravaColetaPostgres

        conn = conectar()
        self.assertTrue(conn.execute("SELECT pg_try_advisory_lock(%s)", (CHAVE_TRAVA_COLETA,)).fetchone()[0])
        with self.assertRaises(ColetaEmAndamento), TravaColetaPostgres(conectar).segurar():
            self.fail("não devia entrar")
        conn.close()

        with TravaColetaPostgres(conectar).segurar():
            pass

    def test_esperar_tenta_de_novo_ate_o_teto_e_entao_desiste(self):
        from src.repository.conexao import conectar
        from src.repository.trava import CHAVE_TRAVA_LOTE_IA, TravaColetaPostgres

        dormidas = []
        with TravaColetaPostgres(conectar, chave=CHAVE_TRAVA_LOTE_IA).segurar():
            with self.assertRaises(ColetaEmAndamento):
                with TravaColetaPostgres(conectar, chave=CHAVE_TRAVA_LOTE_IA).segurar(esperar_s=1, intervalo_s=0.25, dormir=dormidas.append):
                    self.fail("não devia entrar")
        self.assertEqual(dormidas, [0.25] * 4)

    def test_trava_do_lote_da_ia_tem_chave_propria(self):
        # Coleta rodando não segura o lote, e dois lotes não rodam juntos.
        from src.repository.conexao import conectar
        from src.repository.trava import CHAVE_TRAVA_LOTE_IA, TravaColetaPostgres

        with TravaColetaPostgres(conectar).segurar():
            with TravaColetaPostgres(conectar, chave=CHAVE_TRAVA_LOTE_IA).segurar():
                with self.assertRaises(ColetaEmAndamento), TravaColetaPostgres(conectar, chave=CHAVE_TRAVA_LOTE_IA).segurar():
                    self.fail("não devia entrar")

    def test_falha_ao_conectar_nao_vira_coleta_em_andamento(self):
        from src.repository.trava import TravaColetaPostgres

        def sem_banco():
            raise OSError("connection refused")

        with self.assertRaises(OSError), TravaColetaPostgres(sem_banco).segurar():
            self.fail("não devia entrar")

    def test_dois_servicos_de_processos_diferentes_so_um_chama_o_provider(self):
        from src.repository.conexao import conectar
        from src.repository.trava import TravaColetaPostgres

        provider = ProviderBloqueado()
        perfis = perfis_com("python")
        # Duas instâncias independentes, como a API e o cron: nada em comum além do banco.
        a = servico(provider, perfis, trava=TravaColetaPostgres(conectar))
        b = servico(provider, perfis, trava=TravaColetaPostgres(conectar))
        primeira = threading.Thread(target=a.coletar)
        primeira.start()
        self.addCleanup(primeira.join, ESPERA)
        self.addCleanup(provider.soltar.set)
        self.assertTrue(provider.comecou.wait(ESPERA))

        with self.assertRaises(ColetaEmAndamento):
            b.coletar()

        provider.soltar.set()
        primeira.join(ESPERA)
        self.assertEqual(provider.chamadas, 1)

    def test_trava_nao_ocupa_o_pool_da_api(self):
        # Pool de UMA conexão: se a trava saísse dele, os repositories da coleta ficariam sem conexão.
        from psycopg_pool import ConnectionPool

        from src.controller.api import _PorChamada
        from src.repository.conexao import parametros
        from src.repository.postgres import (
            CurriculoRepositoryPostgres,
            PerfilBuscaRepositoryPostgres,
            VagaEncontradaRepositoryPostgres,
        )
        from src.repository.trava import TravaColetaPostgres

        _, schema = _schema_descartavel(self, "teste_trava_pool")
        pool = ConnectionPool(
            kwargs={**parametros(), "autocommit": True, "options": f"-c search_path={schema}"},
            min_size=1,
            max_size=1,
            timeout=3,
            open=True,
        )
        self.addCleanup(pool.close)
        provider = ProviderBloqueado()
        provider.soltar.set()
        api = TestClient(
            criar_app(
                _PorChamada(lambda: pool, lambda: PerfilBuscaRepositoryPostgres, 1),
                _PorChamada(lambda: pool, lambda: CurriculoRepositoryPostgres, 1),
                BuscaService([provider]),
                encontradas=_PorChamada(lambda: pool, lambda: VagaEncontradaRepositoryPostgres, 1),
                trava=TravaColetaPostgres(),
            )
        )
        api.put("/perfil", json={"termos": ["python"]})

        resposta = api.post("/coletas", json={})

        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertEqual(resposta.json()["novas"], 1)


class ConexaoFalsa:
    """Conexão fake: `falhas` diz quais SQL levantam (por pedaço do texto); anota o que rodou e se fechou."""

    def __init__(self, pegou=True, falhas=(), falha_no_close=False):
        self.pegou, self.falhas, self.falha_no_close = pegou, falhas, falha_no_close
        self.fechada = False
        self.sqls = []

    def execute(self, sql, params=None):
        self.sqls.append(sql)
        for pedaco in self.falhas:
            if pedaco in sql:
                raise OSError(f"conexão caiu em {pedaco}")
        outer = self

        class Resultado:
            def fetchone(self):
                return (outer.pegou,)

        return Resultado()

    def close(self):
        self.fechada = True
        if self.falha_no_close:
            raise OSError("close falhou")


class TravaPostgresFalhasTest(unittest.TestCase):
    """Sem banco de verdade: a conexão fake falha nos pontos que importam."""

    def trava(self, conn):
        from src.repository.trava import TravaColetaPostgres

        return TravaColetaPostgres(lambda: conn)

    def coleta(self, conn, banco=None):
        provider = ProviderBloqueado()
        provider.soltar.set()
        return servico(provider, perfis_com("python"), banco, trava=self.trava(conn))

    def test_falha_no_unlock_nao_muda_o_resultado_da_coleta(self):
        conn = ConexaoFalsa(falhas=("pg_advisory_unlock",))

        with self.assertLogs("src.repository.trava", "WARNING"):
            resultado = self.coleta(conn).coletar()

        self.assertEqual(resultado.novas, 1)
        self.assertTrue(conn.fechada)

    def test_falha_no_close_tambem_nao_mascara(self):
        conn = ConexaoFalsa(falhas=("pg_advisory_unlock",), falha_no_close=True)

        with self.assertLogs("src.repository.trava", "WARNING"):
            self.assertEqual(self.coleta(conn).coletar().novas, 1)

        self.assertTrue(conn.fechada)

    def test_excecao_original_da_coleta_sobe_mesmo_com_unlock_falhando(self):
        class FalhaDoBanco(VagaEncontradaRepositoryMemoria):
            def registrar(self, vagas, termo):
                raise ValueError("banco caiu")

        conn = ConexaoFalsa(falhas=("pg_advisory_unlock",))

        with self.assertLogs("src.repository.trava", "WARNING"), self.assertRaisesRegex(ValueError, "banco caiu"):
            self.coleta(conn, FalhaDoBanco(1)).coletar()

        self.assertTrue(conn.fechada)

    def test_falha_ao_tentar_o_lock_fecha_a_conexao_e_a_excecao_sobe(self):
        conn = ConexaoFalsa(falhas=("pg_try_advisory_lock",))

        with self.assertRaisesRegex(OSError, "pg_try_advisory_lock"), self.trava(conn).segurar():
            self.fail("não devia entrar")

        self.assertTrue(conn.fechada)

    def test_lock_ocupado_fecha_a_conexao(self):
        conn = ConexaoFalsa(pegou=False)

        with self.assertRaises(ColetaEmAndamento), self.trava(conn).segurar():
            self.fail("não devia entrar")

        self.assertTrue(conn.fechada)


@unittest.skipUnless(os.environ.get("PGHOST"), "PGHOST não definido: trava no Postgres pulada")
class TravaPostgresSessaoMortaTest(unittest.TestCase):
    def test_sessao_da_trava_derrubada_no_meio_da_coleta_nao_vira_falha(self):
        from src.repository.conexao import conectar
        from src.repository.trava import CHAVE_TRAVA_COLETA, TravaColetaPostgres

        admin = conectar()
        self.addCleanup(admin.close)

        class MataASessao(ProviderBloqueado):
            def buscar(self, termo):
                # Quem segura o lock é a sessão da trava: derruba-a no meio da coleta.
                admin.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_locks WHERE locktype = 'advisory' "
                    "AND (classid::bigint << 32 | objid::bigint) = %s AND granted AND pid <> pg_backend_pid()",
                    (CHAVE_TRAVA_COLETA,),
                )
                return super().buscar(termo)

        provider = MataASessao()
        provider.soltar.set()
        coleta = servico(provider, perfis_com("python"), trava=TravaColetaPostgres(conectar))

        with self.assertLogs("src.repository.trava", "WARNING"):
            resultado = coleta.coletar()

        self.assertEqual(resultado.novas, 1)
        with TravaColetaPostgres(conectar).segurar():  # o lock sumiu junto com a sessão
            pass


class ProducaoTravaTest(unittest.TestCase):
    def test_producao_usa_a_trava_do_postgres(self):
        from unittest import mock

        from src.controller import api
        from src.repository.trava import TravaColetaPostgres

        with mock.patch.object(api, "criar_app") as criar:
            api.criar_app_producao()

        self.assertIsInstance(criar.call_args.kwargs["trava"], TravaColetaPostgres)


if __name__ == "__main__":
    unittest.main()
