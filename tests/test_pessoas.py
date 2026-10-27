"""Duas pessoas no mesmo banco não se enxergam: perfil, currículo, vagas (vista, salva, detalhe, expiração),
candidaturas, pedidos de IA, rótulos e pausa do lote. O mesmo contrato roda nos repositórios em memória e
no Postgres (só com PGHOST). Dados inventados, escritos à mão.
"""

import os
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone

from src.model.curriculo import Curriculo
from src.model.detalhe_vaga import DetalheVaga
from src.model.erros import DadoInvalido
from src.model.pedido_ia import Faixa, TipoPedidoIA
from src.model.perfil import Perfil
from src.model.vaga import Modalidade
from src.model.vaga_externa import VagaExterna
from src.repository.base import (
    CurriculoRepositoryMemoria,
    PerfilBuscaRepositoryMemoria,
    VagaEncontradaRepositoryMemoria,
)
from src.repository.candidatura import CandidaturaRepositoryMemoria, vaga_por_url_em
from src.repository.pedido_ia import PedidoIARepositoryMemoria
from tests.test_postgres import aplicar_todas_duas_vezes

URL = "https://exemplo.com/vaga/mesma-url"
URL_SO_DA_UM = "https://exemplo.com/vaga/so-da-pessoa-1"


def vaga(titulo: str, url: str = URL) -> VagaExterna:
    return VagaExterna(titulo, "Acme", "Teste", url, "Brasil", True, date(2026, 10, 1))


class Repos:
    """Os cinco repositórios de uma pessoa."""

    def __init__(self, perfis, curriculos, vagas, candidaturas, pedidos):
        self.perfis, self.curriculos, self.vagas = perfis, curriculos, vagas
        self.candidaturas, self.pedidos = candidaturas, pedidos


class ContratoDuasPessoas:
    """Mixin: o teste concreto monta `self.um` e `self.dois` (os `Repos` das pessoas 1 e 2)."""

    def test_objetivo_usd_e_faixa_da_mesma_url_sao_por_pessoa(self):
        from dataclasses import replace
        from src.model.detalhe_vaga import Salario
        from src.service.historico_service import HistoricoService

        self.um.perfis.salvar(Perfil(termos=["python"], salario_minimo_usd=10000))
        self.dois.perfis.salvar(Perfil(termos=["python"]))
        self.um.vagas.registrar([replace(vaga("Dev Python"), detalhe=DetalheVaga(
            descricao="Base salary USD 100k-130k per year."))], "python")
        self.dois.vagas.registrar([replace(vaga("Dev Python"), detalhe=DetalheVaga(
            salario=Salario(300000, 400000, "BRL", "monthly")))], "python")
        um = HistoricoService(self.um.vagas, self.um.perfis)
        dois = HistoricoService(self.dois.vagas, self.dois.perfis)
        self.assertEqual([v.url for v in um.visiveis()], [URL])
        self.assertEqual(um.visiveis()[0].salario.moeda, "USD")
        self.assertEqual(dois.visiveis()[0].salario.moeda, "BRL")
        self.assertEqual(self.um.perfis.obter().salario_minimo_usd, 10000)
        self.assertIsNone(self.dois.perfis.obter().salario_minimo_usd)
        self.um.perfis.salvar(Perfil(termos=["python"], salario_minimo_usd=11000))
        self.assertEqual(um.contar(False), 0)
        self.assertEqual(dois.contar(False), 1)

    def test_perfis_separados(self):
        self.um.perfis.salvar(Perfil(termos=["python"], modalidades=[Modalidade.REMOTO]))
        self.assertIsNone(self.dois.perfis.obter())
        self.dois.perfis.salvar(Perfil(termos=["go"], modalidades=[Modalidade.HIBRIDO]))
        self.assertEqual(self.um.perfis.obter().termos, ["python"])
        self.assertEqual(self.dois.perfis.obter().termos, ["go"])
        self.um.perfis.salvar(Perfil(termos=["rust"]))
        self.assertEqual(self.dois.perfis.obter().termos, ["go"])

    def test_curriculos_separados(self):
        base_um = self.um.curriculos.salvar(Curriculo("base", "base um", "# um"))
        self.assertIsNone(self.dois.curriculos.obter_base())
        base_dois = self.dois.curriculos.salvar(Curriculo("base", "base dois", "# dois"))
        self.assertEqual(self.um.curriculos.obter_base().id, base_um.id)
        self.assertEqual(self.dois.curriculos.obter_base().id, base_dois.id)
        # O id de outra pessoa não abre.
        self.assertIsNone(self.dois.curriculos.obter(base_um.id))
        self.assertIsNone(self.um.curriculos.obter(base_dois.id))
        adaptado = self.um.curriculos.salvar(Curriculo("adaptado", "a", "# a", base_id=base_um.id, vaga_url=URL))
        self.assertEqual(self.um.curriculos.listar_adaptados(URL), [adaptado])
        self.assertEqual(self.dois.curriculos.listar_adaptados(URL), [])
        self.assertEqual(self.dois.curriculos.listar_adaptados(), [])

    def test_adaptado_nao_aponta_para_a_base_de_outra_pessoa(self):
        base_um = self.um.curriculos.salvar(Curriculo("base", "base um", "# um"))
        with self.assertRaises(DadoInvalido):
            self.dois.curriculos.salvar(Curriculo("adaptado", "a", "# a", base_id=base_um.id, vaga_url=URL))
        self.assertEqual(self.dois.curriculos.listar_adaptados(), [])
        self.assertEqual(self.um.curriculos.listar_adaptados(), [])

    def test_mesma_url_sao_duas_vagas_com_vista_e_salva_separadas(self):
        self.assertEqual(self.um.vagas.registrar([vaga("Dev Um")], "python"), 1)
        # A mesma URL é nova para a pessoa 2: nada de "já existia".
        self.assertEqual(self.dois.vagas.registrar([vaga("Dev Dois")], "go"), 1)
        self.assertEqual(self.um.vagas.obter(URL).titulo, "Dev Um")
        self.assertEqual(self.dois.vagas.obter(URL).titulo, "Dev Dois")
        self.assertEqual(self.um.vagas.obter(URL).termos, ["python"])
        self.assertEqual(self.dois.vagas.obter(URL).termos, ["go"])

        self.assertEqual(self.um.vagas.marcar_vistas([URL]), 1)
        self.assertEqual(self.um.vagas.contar(somente_novas=True), 0)
        self.assertEqual(self.dois.vagas.contar(somente_novas=True), 1)
        self.assertIsNone(self.dois.vagas.obter(URL).vista_em)

        self.assertTrue(self.um.vagas.marcar_salva(URL, True))
        self.assertEqual(self.um.vagas.contar_salvas(), 1)
        self.assertEqual(self.dois.vagas.contar_salvas(), 0)
        self.assertEqual(self.dois.vagas.listar_salvas(10), [])
        self.assertEqual([v.url for v in self.um.vagas.listar_salvas(10)], [URL])

    def test_listas_e_contadores_so_da_pessoa(self):
        self.um.vagas.registrar([vaga("Dev Um"), vaga("Outra", URL_SO_DA_UM)], "python")
        self.dois.vagas.registrar([vaga("Dev Dois")], "go")
        self.assertEqual(sorted(v.url for v in self.um.vagas.listar(False, 10)), sorted([URL, URL_SO_DA_UM]))
        self.assertEqual([v.url for v in self.dois.vagas.listar(False, 10)], [URL])
        self.assertEqual((self.um.vagas.contar(False), self.dois.vagas.contar(False)), (2, 1))
        self.assertEqual(len(self.um.vagas.listar_recentes(False, 10)), 2)
        self.assertEqual(len(self.dois.vagas.listar_recentes(False, 10)), 1)
        # A busca por palavra também fica na pessoa.
        self.assertEqual(self.dois.vagas.listar(False, 10, q="outra"), [])
        self.assertEqual(self.dois.vagas.contar(False, q="outra"), 0)
        self.assertEqual(self.um.vagas.contar(False, q="outra"), 1)

    def test_vaga_da_pessoa_1_nao_existe_para_a_2(self):
        self.um.vagas.registrar([vaga("So Um", URL_SO_DA_UM)], "python")
        self.assertIsNone(self.dois.vagas.obter(URL_SO_DA_UM))
        self.assertEqual(self.dois.vagas.marcar_vistas([URL_SO_DA_UM]), 0)
        self.assertFalse(self.dois.vagas.marcar_salva(URL_SO_DA_UM, True))
        self.assertFalse(self.dois.vagas.marcar_expirada(URL_SO_DA_UM))
        self.assertFalse(self.dois.vagas.gravar_detalhe(URL_SO_DA_UM, DetalheVaga(descricao="texto")))
        vaga_um = self.um.vagas.obter(URL_SO_DA_UM)
        self.assertIsNone(vaga_um.vista_em)
        self.assertIsNone(vaga_um.salva_em)
        self.assertIsNone(vaga_um.expirada_em)
        self.assertIsNone(vaga_um.descricao)

    def test_detalhe_e_expiracao_separados(self):
        self.um.vagas.registrar([vaga("Dev Um")], "python")
        self.dois.vagas.registrar([vaga("Dev Dois")], "go")
        self.assertTrue(self.um.vagas.gravar_detalhe(URL, DetalheVaga(descricao="só da pessoa 1")))
        self.assertEqual(self.um.vagas.obter(URL).descricao, "só da pessoa 1")
        self.assertIsNone(self.dois.vagas.obter(URL).descricao)
        self.assertIsNone(self.dois.vagas.obter(URL).detalhada_em)

        self.assertTrue(self.um.vagas.marcar_expirada(URL))
        self.assertIsNotNone(self.um.vagas.obter(URL).expirada_em)
        self.assertIsNone(self.dois.vagas.obter(URL).expirada_em)
        self.assertEqual(self.um.vagas.contar(False), 0)
        self.assertEqual(self.dois.vagas.contar(False), 1)

    def test_ultima_coleta_so_da_pessoa(self):
        self.assertIsNone(self.dois.vagas.ultima_coleta())
        self.um.vagas.registrar([vaga("Dev Um")], "python")
        self.assertIsNotNone(self.um.vagas.ultima_coleta())
        self.assertIsNone(self.dois.vagas.ultima_coleta())

    def test_candidaturas_separadas_na_mesma_url(self):
        self.um.vagas.registrar([vaga("Dev Um")], "python")
        self.dois.vagas.registrar([vaga("Dev Dois")], "go")
        do_um, criada_um = self.um.candidaturas.criar(URL)
        self.assertTrue(criada_um)
        self.assertEqual(do_um.titulo, "Dev Um")
        self.assertIsNone(self.dois.candidaturas.obter_por_url(URL))
        self.assertEqual((self.dois.candidaturas.contar(), self.dois.candidaturas.listar()), (0, []))
        self.assertIsNone(self.dois.candidaturas.obter(do_um.id))

        # A pessoa 2 aplica na mesma URL: é outra candidatura, com a vaga dela.
        do_dois, criada_dois = self.dois.candidaturas.criar(URL)
        self.assertTrue(criada_dois)
        self.assertNotEqual(do_dois.id, do_um.id)
        self.assertEqual(do_dois.titulo, "Dev Dois")
        self.assertEqual(self.um.candidaturas.contar(), 1)

        # Mexer na candidatura da outra pessoa pelo id não acha nada.
        self.assertIsNone(self.dois.candidaturas.atualizar_status(do_um.id, "recusada"))
        self.assertFalse(self.dois.candidaturas.remover(do_um.id))
        self.assertEqual(self.um.candidaturas.obter(do_um.id).status.value, "enviada")
        self.assertTrue(self.um.candidaturas.remover(do_um.id))
        self.assertEqual(self.dois.candidaturas.contar(), 1)

    def test_candidatura_em_vaga_que_so_existe_na_outra_pessoa_e_dado_invalido(self):
        self.um.vagas.registrar([vaga("So Um", URL_SO_DA_UM)], "python")
        with self.assertRaises(DadoInvalido):
            self.dois.candidaturas.criar(URL_SO_DA_UM)
        self.assertEqual((self.um.candidaturas.contar(), self.dois.candidaturas.contar()), (0, 0))

    def test_pedidos_de_ia_separados_na_mesma_url(self):
        self.um.vagas.registrar([vaga("Dev Um")], "python")
        self.dois.vagas.registrar([vaga("Dev Dois")], "go")
        pedido_um, criado_um = self.um.pedidos.criar(URL, "compatibilidade")
        # Um ativo por (pessoa, vaga, tipo): a pessoa 2 não recebe o ativo da 1.
        pedido_dois, criado_dois = self.dois.pedidos.criar(URL, "compatibilidade")
        self.assertTrue(criado_um and criado_dois)
        self.assertNotEqual(pedido_um.id, pedido_dois.id)
        self.assertEqual((pedido_um.pessoa_id, pedido_dois.pessoa_id), (1, 2))
        # E dentro da mesma pessoa o ativo continua sendo devolvido.
        repetido, criado = self.um.pedidos.criar(URL, "compatibilidade")
        self.assertEqual((repetido.id, criado), (pedido_um.id, False))

        self.assertEqual(self.um.pedidos.ultimo(URL, "compatibilidade").id, pedido_um.id)
        self.assertEqual(self.dois.pedidos.ultimo(URL, "compatibilidade").id, pedido_dois.id)
        self.assertIsNone(self.dois.pedidos.ultimo(URL, "curriculo"))
        self.assertEqual(self.um.pedidos.urls_ativas(TipoPedidoIA.COMPATIBILIDADE), {URL})
        self.assertEqual(self.um.pedidos.obter(pedido_um.id).id, pedido_um.id)
        self.assertIsNone(self.um.pedidos.obter(pedido_dois.id))
        self.assertIsNone(self.dois.pedidos.obter(pedido_um.id))

    def test_pedido_em_vaga_que_so_existe_na_outra_pessoa_e_dado_invalido(self):
        self.um.vagas.registrar([vaga("So Um", URL_SO_DA_UM)], "python")
        with self.assertRaises(DadoInvalido):
            self.dois.pedidos.criar(URL_SO_DA_UM, "compatibilidade")
        with self.assertRaises(DadoInvalido):
            self.dois.pedidos.gravar_rotulo(URL_SO_DA_UM, "alta")
        self.assertEqual(self.um.pedidos.urls_ativas(TipoPedidoIA.COMPATIBILIDADE), set())

    def test_fila_do_worker_e_global_e_o_pedido_traz_a_pessoa(self):
        self.um.vagas.registrar([vaga("Dev Um")], "python")
        self.dois.vagas.registrar([vaga("Dev Dois")], "go")
        pedido_um, _ = self.um.pedidos.criar(URL, "compatibilidade")
        pedido_dois, _ = self.dois.pedidos.criar(URL, "compatibilidade")

        # O repositório de uma pessoa reivindica o pedido de qualquer uma: a fila é de todas.
        primeiro = self.dois.pedidos.proximo()
        segundo = self.dois.pedidos.proximo()
        self.assertEqual([(primeiro.id, primeiro.pessoa_id), (segundo.id, segundo.pessoa_id)], [
            (pedido_um.id, 1),
            (pedido_dois.id, 2),
        ])
        self.assertIsNone(self.dois.pedidos.proximo())
        # `obter` é da pessoa; `obter_da_fila` é do worker.
        self.assertIsNone(self.dois.pedidos.obter(pedido_um.id))
        self.assertEqual(self.dois.pedidos.obter_da_fila(pedido_um.id).pessoa_id, 1)
        resultado = {"nota": 70, "faixa": "alta"}
        pronto = self.dois.pedidos.concluir(
            pedido_um.id, 1, resultado, "compatibilidade-v1", "m", nota=70, faixa=Faixa.ALTA
        )
        self.assertEqual((pronto.id, pronto.pessoa_id), (pedido_um.id, 1))
        # O resultado fica na pessoa do pedido.
        self.assertEqual(self.um.pedidos.obter(pedido_um.id).nota, 70)
        self.assertEqual(self.um.pedidos.versoes_compatibilidade(), {"compatibilidade-v1"})
        self.assertEqual(self.dois.pedidos.versoes_compatibilidade(), set())
        self.assertEqual([p.id for p in self.um.pedidos.listar_prontos_compatibilidade()], [pedido_um.id])
        self.assertEqual(self.dois.pedidos.listar_prontos_compatibilidade(), [])

    def test_notas_e_erros_so_da_pessoa(self):
        self.um.vagas.registrar([vaga("Dev Um")], "python")
        self.dois.vagas.registrar([vaga("Dev Dois")], "go")
        base_um = self.um.curriculos.salvar(Curriculo("base", "um", "# um"))
        base_dois = self.dois.curriculos.salvar(Curriculo("base", "dois", "# dois"))
        pedido, _ = self.um.pedidos.criar(URL, "compatibilidade", curriculo_base_id=base_um.id)
        self.um.pedidos.proximo()
        self.um.pedidos.concluir(pedido.id, 1, {"nota": 80}, "compatibilidade-v1", "m", nota=80, faixa="alta")
        self.assertEqual(set(self.um.pedidos.notas_compatibilidade(base_um.id, None)), {URL})
        self.assertEqual(self.dois.pedidos.notas_compatibilidade(base_um.id, None), {})
        self.assertEqual(self.dois.pedidos.notas_compatibilidade(base_dois.id, None), {})

        com_erro, _ = self.dois.pedidos.criar(URL, "compatibilidade", curriculo_base_id=base_dois.id)
        reivindicado = self.dois.pedidos.proximo()
        self.assertEqual(reivindicado.id, com_erro.id)
        self.dois.pedidos.falhar(com_erro.id, 1, "deu ruim", "compatibilidade-v1", "m")
        desde = datetime.now(timezone.utc) - timedelta(hours=1)
        self.assertEqual(self.dois.pedidos.urls_com_erro_desde(base_dois.id, desde), {URL})
        self.assertEqual(self.um.pedidos.urls_com_erro_desde(base_dois.id, desde), set())

    def test_rotulos_separados(self):
        self.um.vagas.registrar([vaga("Dev Um")], "python")
        self.dois.vagas.registrar([vaga("Dev Dois")], "go")
        self.assertEqual(self.um.pedidos.gravar_rotulo(URL, "alta"), Faixa.ALTA)
        self.assertIsNone(self.dois.pedidos.obter_rotulo(URL))
        self.assertEqual(self.dois.pedidos.listar_rotulos(), {})
        self.assertEqual(self.dois.pedidos.gravar_rotulo(URL, "baixa"), Faixa.BAIXA)
        self.assertEqual(self.um.pedidos.listar_rotulos(), {URL: "alta"})
        self.assertEqual(self.dois.pedidos.listar_rotulos(), {URL: "baixa"})
        # Apagar o da pessoa 1 não mexe no da 2.
        self.assertIsNone(self.um.pedidos.gravar_rotulo(URL, None))
        self.assertEqual(self.dois.pedidos.obter_rotulo(URL), Faixa.BAIXA)

    def test_pausa_e_lote_separados(self):
        self.um.vagas.registrar([vaga("Dev Um")], "python")
        self.dois.vagas.registrar([vaga("Dev Dois")], "go")
        self.um.pedidos.criar(URL, "compatibilidade", prioridade=1)
        self.dois.pedidos.criar(URL, "compatibilidade", prioridade=1)
        desde = datetime.now(timezone.utc) - timedelta(hours=1)
        # O teto diário é a cota do worker: a contagem do lote é global, igual vista de qualquer pessoa.
        self.assertEqual((self.um.pedidos.contar_lote_desde(desde), self.dois.pedidos.contar_lote_desde(desde)), (2, 2))

        self.assertFalse(self.um.pedidos.lote_pausado())
        self.assertEqual(self.um.pedidos.pausar_lote(), 1)
        self.assertTrue(self.um.pedidos.lote_pausado())
        # A pessoa 2 segue sem pausa e com o lote dela na fila.
        self.assertFalse(self.dois.pedidos.lote_pausado())
        self.assertEqual(self.dois.pedidos.urls_ativas(TipoPedidoIA.COMPATIBILIDADE), {URL})
        self.assertEqual(self.um.pedidos.urls_ativas(TipoPedidoIA.COMPATIBILIDADE), set())
        self.assertEqual((self.um.pedidos.contar_lote_desde(desde), self.dois.pedidos.contar_lote_desde(desde)), (1, 1))

        self.um.pedidos.retomar_lote()
        self.assertFalse(self.um.pedidos.lote_pausado())
        self.assertEqual(self.dois.pedidos.pausar_lote(), 1)
        self.assertTrue(self.dois.pedidos.lote_pausado())
        self.assertFalse(self.um.pedidos.lote_pausado())


class DuasPessoasMemoriaTest(ContratoDuasPessoas, unittest.TestCase):
    def setUp(self):
        def repos(pessoa: int, outro: Repos | None) -> Repos:
            vagas = VagaEncontradaRepositoryMemoria(pessoa, compartilhado=outro and outro.vagas)
            return Repos(
                PerfilBuscaRepositoryMemoria(pessoa, outro and outro.perfis),
                CurriculoRepositoryMemoria(pessoa, outro and outro.curriculos),
                vagas,
                CandidaturaRepositoryMemoria(pessoa, vaga_por_url_em(vagas), outro and outro.candidaturas),
                PedidoIARepositoryMemoria(pessoa, vaga_por_url_em(vagas), compartilhado=outro and outro.pedidos),
            )

        self.um = repos(1, None)
        self.dois = repos(2, self.um)


class WorkerLeAFilaDeTodasAsPessoasTest(unittest.TestCase):
    """O serviço do worker confere o pedido pelo `obter_da_fila`: o `obter` só acha os da pessoa do repositório."""

    def test_falhar_pedido_de_outra_pessoa_pela_fila(self):
        from src.service.pedido_ia_service import PedidoIAService

        vagas_um = VagaEncontradaRepositoryMemoria(1)
        vagas_dois = VagaEncontradaRepositoryMemoria(2, compartilhado=vagas_um)
        pedidos_um = PedidoIARepositoryMemoria(1, vaga_por_url_em(vagas_um))
        pedidos_dois = PedidoIARepositoryMemoria(2, vaga_por_url_em(vagas_dois), compartilhado=pedidos_um)
        vagas_dois.registrar([vaga("Dev Dois")], "go")
        pedido, _ = pedidos_dois.criar(URL, "compatibilidade")
        reivindicado = pedidos_um.proximo()  # o worker, com o repositório da pessoa 1, pega o da 2
        self.assertEqual((reivindicado.id, reivindicado.pessoa_id), (pedido.id, 2))
        self.assertIsNone(pedidos_um.obter(pedido.id))

        servico = PedidoIAService(
            pedidos_um,
            vagas_um,
            PerfilBuscaRepositoryMemoria(1),
            CurriculoRepositoryMemoria(1),
            max_lote_por_dia=None,
        )
        falho = servico.falhar(pedido.id, 1, "deu ruim", "compatibilidade-v1", "m")

        self.assertEqual((falho.id, falho.pessoa_id, falho.estado.value), (pedido.id, 2, "erro"))
        self.assertEqual(pedidos_dois.obter(pedido.id).erro, "deu ruim")


@unittest.skipUnless(os.environ.get("PGHOST"), "PGHOST não definido: integração com Postgres pulada")
class DuasPessoasPostgresTest(ContratoDuasPessoas, unittest.TestCase):
    def setUp(self):
        from psycopg import sql

        from src.repository.candidatura_postgres import CandidaturaRepositoryPostgres
        from src.repository.conexao import conectar
        from src.repository.pedido_ia_postgres import PedidoIARepositoryPostgres
        from src.repository.postgres import (
            CurriculoRepositoryPostgres,
            PerfilBuscaRepositoryPostgres,
            VagaEncontradaRepositoryPostgres,
        )

        self.conn = conectar()
        self.addCleanup(self.conn.close)
        schema = f"teste_pessoas_{uuid.uuid4().hex[:12]}"
        self.conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        self.addCleanup(self.conn.execute, sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        self.conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        aplicar_todas_duas_vezes(self.conn)
        self.conn.execute("INSERT INTO pessoa (id, nome_exibicao) VALUES (2, 'Pessoa 2')")

        def repos(pessoa: int) -> Repos:
            return Repos(
                PerfilBuscaRepositoryPostgres(self.conn, pessoa),
                CurriculoRepositoryPostgres(self.conn, pessoa),
                VagaEncontradaRepositoryPostgres(self.conn, pessoa),
                CandidaturaRepositoryPostgres(self.conn, pessoa),
                PedidoIARepositoryPostgres(self.conn, pessoa),
            )

        self.um, self.dois = repos(1), repos(2)


if __name__ == "__main__":
    unittest.main()
