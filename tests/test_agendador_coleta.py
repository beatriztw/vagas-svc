"""Coleta agendada pela própria app. Vagas e perfil são inventados, escritos à mão."""

import os
import threading
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from src.controller.api import criar_app
from src.model.erros import DadoInvalido
from src.model.perfil import Perfil
from src.repository.base import (
    CurriculoRepositoryMemoria,
    PerfilBuscaRepositoryMemoria,
    VagaEncontradaRepositoryMemoria,
)
from src.repository.coleta_agendada import RegistroColetaAgendadaMemoria
from src.repository.trava import ColetaEmAndamento
from src.service.agendador_coleta import (
    ESPERA_BANCO_S,
    MAX_SONO_S,
    PADRAO_HORARIOS,
    AgendadorColeta,
    horarios_da_config,
    ler_horarios,
    proximo_horario,
    resumir,
    ultimo_horario,
)
from src.service.busca_service import BuscaService
from src.service.coleta_service import ColetaService
from src.service.recomendacao_service import RecomendacaoService
from tests.test_busca_service import ClientQuebrado
from tests.test_coleta import ClientPorTermo, _schema_descartavel, vaga

HORARIOS = [(8, 0), (18, 0)]


def utc(*partes) -> datetime:
    return datetime(*partes, tzinfo=timezone.utc)


class Relogio:
    def __init__(self, agora: datetime):
        self.agora = agora

    def __call__(self):
        return self.agora


class Coletas:
    """`coletar` falso: conta as chamadas e devolve (ou levanta) o que mandarem."""

    def __init__(self, resposta=None):
        self.chamadas = 0
        self.resposta = resposta

    def __call__(self):
        self.chamadas += 1
        if isinstance(self.resposta, BaseException):
            raise self.resposta
        return self.resposta


def coleta_real(*providers) -> ColetaService:
    perfis = PerfilBuscaRepositoryMemoria(1)
    perfis.salvar(Perfil(termos=["python"]))
    return ColetaService.de_uma_pessoa(RecomendacaoService(BuscaService(list(providers))), perfis, VagaEncontradaRepositoryMemoria(1))


class HorariosTest(unittest.TestCase):
    def test_ler_horarios_sem_repetidos_e_em_ordem(self):
        self.assertEqual(ler_horarios("18:00, 08:00,18:00"), [(8, 0), (18, 0)])
        self.assertEqual(ler_horarios(PADRAO_HORARIOS), HORARIOS)
        self.assertEqual(ler_horarios(""), [])
        self.assertEqual(ler_horarios(" , "), [])

    def test_horario_invalido_e_dado_invalido(self):
        for ruim in ("8", "08:0", "24:00", "07:60", "x:00", "08h00", "-1:00"):
            with self.subTest(ruim=ruim), self.assertRaises(DadoInvalido):
                ler_horarios(ruim)

    def test_config_sem_variavel_vazia_e_invalida(self):
        self.assertEqual(horarios_da_config({}), HORARIOS)
        self.assertEqual(horarios_da_config({"COLETA_HORARIOS": ""}), [])
        with self.assertLogs("src.service.agendador_coleta", "WARNING") as logs:
            self.assertEqual(horarios_da_config({"COLETA_HORARIOS": "25:00"}), [])
        self.assertIn("desligada", logs.output[0])

    def test_fuso_de_brasilia(self):
        # 07:59 em Brasília é 10:59 UTC: o último horário foi 18:00 da véspera, o próximo é 08:00.
        agora = utc(2026, 10, 13, 10, 59)
        self.assertEqual(ultimo_horario(agora, HORARIOS), utc(2026, 10, 12, 21, 0))
        self.assertEqual(proximo_horario(agora, HORARIOS), utc(2026, 10, 13, 11, 0))
        self.assertEqual(ultimo_horario(utc(2026, 10, 13, 11, 0), HORARIOS), utc(2026, 10, 13, 11, 0))

    def test_virada_do_dia(self):
        # 23:30 do dia 12 em Brasília já é dia 13 em UTC.
        agora = utc(2026, 10, 13, 2, 30)
        self.assertEqual(ultimo_horario(agora, HORARIOS), utc(2026, 10, 12, 21, 0))
        self.assertEqual(proximo_horario(agora, HORARIOS), utc(2026, 10, 13, 11, 0))
        um_so = [(0, 30)]
        self.assertEqual(ultimo_horario(utc(2026, 10, 13, 3, 0), um_so), utc(2026, 10, 12, 3, 30))
        self.assertEqual(proximo_horario(utc(2026, 10, 13, 3, 0), um_so), utc(2026, 10, 13, 3, 30))


class PassoTest(unittest.TestCase):
    def montar(self, agora, resposta=None, registro=None):
        self.relogio = Relogio(agora)
        self.coletas = Coletas(resposta)
        self.registro = registro or RegistroColetaAgendadaMemoria()
        return AgendadorColeta(self.coletas, self.registro, HORARIOS, relogio=self.relogio)

    def test_coleta_o_horario_uma_vez_e_dorme_ate_o_proximo_com_teto(self):
        resultado = coleta_real(ClientPorTermo("A", {"python": [vaga("u1")]})).coletar()
        agendador = self.montar(utc(2026, 10, 13, 11, 1), resultado)

        espera = agendador.passo()
        self.assertEqual(self.coletas.chamadas, 1)
        self.assertEqual(espera, MAX_SONO_S)
        self.assertEqual(self.registro.horarios, {utc(2026, 10, 13, 11, 0): "ok termos=1 novas=1 termos_com_erro=0"})

        self.relogio.agora += timedelta(minutes=30)
        agendador.passo()
        self.assertEqual(self.coletas.chamadas, 1)  # o mesmo horário não roda de novo

        self.relogio.agora = utc(2026, 10, 13, 20, 59, 30)
        self.assertEqual(agendador.passo(), 30)  # acorda no próximo horário, não depois

    def test_recupera_horario_perdido_ate_6_h(self):
        agendador = self.montar(utc(2026, 10, 13, 16, 59), coleta_real(ClientPorTermo("A", {})).coletar())
        agendador.passo()
        self.assertEqual(self.coletas.chamadas, 1)

    def test_horario_perdido_ha_mais_de_6_h_fica_para_o_proximo(self):
        agendador = self.montar(utc(2026, 10, 13, 17, 1))
        agendador.passo()
        self.assertEqual(self.coletas.chamadas, 0)
        self.assertEqual(self.registro.horarios, {})

    def test_reinicio_nao_repete_o_horario_registrado(self):
        registro = RegistroColetaAgendadaMemoria()
        resultado = coleta_real(ClientPorTermo("A", {})).coletar()
        self.montar(utc(2026, 10, 13, 11, 5), resultado, registro).passo()
        # A app reinicia: um agendador novo, o mesmo banco.
        novo = self.montar(utc(2026, 10, 13, 11, 6), resultado, registro)
        novo.passo()
        self.assertEqual(self.coletas.chamadas, 0)

    def test_excecoes_da_coleta_sao_registradas_e_nao_derrubam(self):
        casos = {
            ColetaEmAndamento(): "em andamento",
            DadoInvalido("Nenhum perfil salvo"): "sem perfil ou sem termos",
            RuntimeError("detalhe-que-nao-pode-ir-para-o-log"): "erro RuntimeError",
        }
        for excecao, resumo in casos.items():
            with self.subTest(resumo=resumo):
                agendador = self.montar(utc(2026, 10, 13, 11, 5), excecao)
                with self.assertLogs("src.service.agendador_coleta", "INFO") as logs:
                    self.assertGreater(agendador.passo(), 0)
                self.assertEqual(self.registro.horarios[utc(2026, 10, 13, 11, 0)], resumo)
                self.assertNotIn("detalhe-que-nao-pode-ir-para-o-log", "\n".join(logs.output))

    def test_banco_fora_ao_reservar_tenta_de_novo_sem_marcar(self):
        class RegistroFora(RegistroColetaAgendadaMemoria):
            def reservar(self, horario):
                raise OSError("connection refused")

        agendador = self.montar(utc(2026, 10, 13, 11, 5), coleta_real(ClientPorTermo("A", {})).coletar(), RegistroFora())
        self.assertEqual(agendador.passo(), ESPERA_BANCO_S)
        self.assertEqual(self.coletas.chamadas, 0)
        agendador.registro = RegistroColetaAgendadaMemoria()  # o banco voltou
        agendador.passo()
        self.assertEqual(self.coletas.chamadas, 1)

    def test_resumo_so_com_contagens_e_falha_dos_providers(self):
        self.assertEqual(resumir(coleta_real(ClientQuebrado()).coletar()), "falhou termos=1 novas=0 termos_com_erro=1")

    def test_da_config_desligada_devolve_none(self):
        for env in ({"COLETA_HORARIOS": ""}, {"COLETA_HORARIOS": "8h"}):
            self.assertIsNone(AgendadorColeta.da_config(Coletas(), RegistroColetaAgendadaMemoria(), env))
        ligado = AgendadorColeta.da_config(Coletas(), RegistroColetaAgendadaMemoria(), {})
        self.assertEqual(ligado.horarios, HORARIOS)


def threads_do_agendador():
    return [t for t in threading.enumerate() if t.name == "coleta-agendada"]


class AppTest(unittest.TestCase):
    def app(self, **kwargs):
        return criar_app(PerfilBuscaRepositoryMemoria(1), CurriculoRepositoryMemoria(1), BuscaService([]), **kwargs)

    def test_criar_app_sem_agendador_nao_inicia_thread(self):
        app = self.app()
        with TestClient(app) as cliente:
            self.assertEqual(cliente.get("/saude").status_code, 200)
            self.assertIsNone(app.state.agendador)
            self.assertEqual(threads_do_agendador(), [])

    def test_lifespan_inicia_depois_das_migrations_e_para_no_shutdown(self):
        ordem, recebido = [], {}

        def fabrica(coletar):
            recebido["coletar"] = coletar
            # Relógio fora da janela de qualquer horário: a thread roda sem coletar nada.
            return AgendadorColeta(coletar, RegistroColetaAgendadaMemoria(), [(8, 0)], relogio=lambda: utc(2026, 10, 13, 22, 0))

        app = self.app(ao_subir=lambda: ordem.append("migrations"), agendador=fabrica)
        agendador = app.state.agendador
        iniciar = agendador.iniciar
        agendador.iniciar = lambda: (ordem.append("agendador"), iniciar())
        with TestClient(app):
            self.assertEqual(ordem, ["migrations", "agendador"])
            self.assertEqual(len(threads_do_agendador()), 1)
        self.assertEqual(threads_do_agendador(), [])
        # A fábrica recebe a MESMA coleta do POST /coletas (com a trava e o lote da IA), agora seguida da
        # conferência na fonte (sem `conferencia`, como aqui, só a coleta).
        with mock.patch.object(ColetaService, "coletar", autospec=True, return_value="resultado") as coletar:
            self.assertEqual(recebido["coletar"](), "resultado")
        self.assertEqual(coletar.call_count, 1)

    def test_config_desligada_sobe_a_app_sem_agendador(self):
        app = self.app(agendador=lambda coletar: AgendadorColeta.da_config(coletar, RegistroColetaAgendadaMemoria(), {"COLETA_HORARIOS": "x"}))
        with TestClient(app) as cliente:
            self.assertEqual(cliente.get("/saude").status_code, 200)
            self.assertEqual(threads_do_agendador(), [])


@unittest.skipUnless(os.environ.get("PGHOST"), "PGHOST não definido: integração com Postgres pulada")
class RegistroPostgresTest(unittest.TestCase):
    def setUp(self):
        from src.repository.coleta_agendada import RegistroColetaAgendadaPostgres

        self.conn, self.schema = _schema_descartavel(self, "teste_agenda")
        self.registro = RegistroColetaAgendadaPostgres(self.conn)

    def outra_conexao(self):
        from psycopg import sql

        from src.repository.conexao import conectar

        conn = conectar()
        self.addCleanup(conn.close)
        conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
        return conn

    def test_reserva_so_uma_vez_e_grava_o_resumo(self):
        horario = utc(2026, 10, 13, 11, 0)
        self.assertTrue(self.registro.reservar(horario))
        self.assertFalse(self.registro.reservar(horario))
        self.registro.concluir(horario, "ok termos=1 novas=2 termos_com_erro=0")
        linha = self.conn.execute("SELECT resultado, terminada_em IS NOT NULL FROM coleta_agendada").fetchone()
        self.assertEqual(linha, ("ok termos=1 novas=2 termos_com_erro=0", True))

    def test_resultado_longo_e_cortado(self):
        horario = utc(2026, 10, 13, 11, 0)
        self.registro.reservar(horario)
        self.registro.concluir(horario, "x" * 900)
        self.assertEqual(self.conn.execute("SELECT length(resultado) FROM coleta_agendada").fetchone()[0], 500)

    def test_duas_instancias_ao_mesmo_tempo_so_uma_coleta(self):
        from src.repository.coleta_agendada import RegistroColetaAgendadaPostgres

        coletas = Coletas(coleta_real(ClientPorTermo("A", {})).coletar())
        relogio = Relogio(utc(2026, 10, 13, 11, 5))
        agendadores = [
            AgendadorColeta(coletas, RegistroColetaAgendadaPostgres(self.outra_conexao()), HORARIOS, relogio=relogio)
            for _ in range(4)
        ]
        largada = threading.Barrier(len(agendadores))

        def rodar(a):
            largada.wait()
            a.passo()

        threads = [threading.Thread(target=rodar, args=(a,)) for a in agendadores]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertEqual(coletas.chamadas, 1)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM coleta_agendada").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
