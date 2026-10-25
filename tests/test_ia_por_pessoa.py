"""IA por pessoa: o payload do worker, a conferência do currículo adaptado, o `pedido_id` do POST, o rodízio da
cota entre pessoas e o teto diário global. Memória e Postgres (este só com PGHOST).

Vagas, currículos e perfis daqui são inventados e escritos à mão (não são respostas gravadas).
"""

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from fastapi.testclient import TestClient

from src.controller.api import criar_app
from src.model.curriculo import Curriculo
from src.model.pedido_ia import PRIORIDADE_LOTE, PRIORIDADE_PESSOA
from src.model.perfil import Perfil
from src.model.vaga_externa import VagaExterna
from src.repository.base import (
    CurriculoRepositoryMemoria,
    PerfilBuscaRepositoryMemoria,
    VagaEncontradaRepositoryMemoria,
)
from src.repository.candidatura import vaga_por_url_em
from src.repository.pedido_ia import PedidoIARepositoryMemoria
from src.service.busca_service import BuscaService
from src.service.pedido_ia_service import FabricasPorPessoa, PedidoIAService

URL = "https://exemplo.com/vaga-mesma-url"
OUTRA_URL = "https://exemplo.com/vaga-outra"
TOKEN = "token-de-teste"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
INICIO = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
URLS = [f"https://exemplo.com/lote-{i}" for i in range(8)]

COMPATIBILIDADE = {"nota": 82, "faixa": "alta", "atende": [], "falta": [], "eliminatorios": [], "resumo": "Boa."}


def minutos(n: int) -> datetime:
    return INICIO + timedelta(minutes=n)


class Relogio:
    def __init__(self):
        self.agora = INICIO

    def __call__(self):
        return self.agora


class DuasPessoasEmMemoria:
    """O app em memória com as pessoas 1 e 2 no mesmo "banco"; a requisição é da pessoa 1."""

    def __init__(self, base_da_pessoa_2: bool = True):
        self.relogio = Relogio()
        self.vagas = {1: VagaEncontradaRepositoryMemoria(1)}
        self.vagas[2] = VagaEncontradaRepositoryMemoria(2, compartilhado=self.vagas[1])
        self.curriculos = {1: CurriculoRepositoryMemoria(1)}
        self.curriculos[2] = CurriculoRepositoryMemoria(2, self.curriculos[1])
        self.perfis = {1: PerfilBuscaRepositoryMemoria(1)}
        self.perfis[2] = PerfilBuscaRepositoryMemoria(2, self.perfis[1])
        self.pedidos = {1: PedidoIARepositoryMemoria(1, vaga_por_url_em(self.vagas[1]), self.relogio)}
        self.pedidos[2] = PedidoIARepositoryMemoria(
            2, vaga_por_url_em(self.vagas[2]), self.relogio, compartilhado=self.pedidos[1]
        )
        # A mesma URL é uma vaga de cada pessoa, com título e descrição próprios.
        self.vagas[1].registrar([VagaExterna("Analista de Dados Um", "Acme", "Remotar", URL, "Brasil", True)], "dados")
        self.vagas[2].registrar([VagaExterna("Data Engineer Dois", "Globex", "Ashby", URL, "Remote", True)], "data")
        self.base = {
            1: self.curriculos[1].salvar(Curriculo(tipo="base", titulo="Base Um", conteudo_md="# Um")),
        }
        if base_da_pessoa_2:
            self.base[2] = self.curriculos[2].salvar(Curriculo(tipo="base", titulo="Base Two", conteudo_md="# Two"))
        self.perfis[1].salvar(Perfil(termos=["dados"]))
        self.perfis[2].salvar(Perfil(termos=["data engineer"]))
        self.fabricas = FabricasPorPessoa(self.perfis.__getitem__, self.curriculos.__getitem__, self.vagas.__getitem__)
        self.app = criar_app(
            self.perfis[1],
            self.curriculos[1],
            BuscaService([]),
            encontradas=self.vagas[1],
            pedidos_ia=self.pedidos[1],
            fabricas=self.fabricas,
        )
        self.cliente = TestClient(self.app)

    def servico_da_pessoa(self, pessoa: int, max_lote_por_dia=None) -> PedidoIAService:
        return PedidoIAService(
            self.pedidos[pessoa],
            self.vagas[pessoa],
            self.perfis[pessoa],
            self.curriculos[pessoa],
            max_lote_por_dia=max_lote_por_dia,
            relogio=self.relogio,
        )

    def adaptado(self, pessoa: int, vaga_url: str = URL) -> Curriculo:
        return self.curriculos[pessoa].salvar(
            Curriculo(tipo="adaptado", titulo="Adaptado", conteudo_md="# x", base_id=self.base[pessoa].id, vaga_url=vaga_url)
        )


class ComToken(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"VAGAS_IA_TOKEN": TOKEN})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("VAGAS_IA_TOKEN_FILE", None)
        self.m = DuasPessoasEmMemoria()

    def proximo(self):
        return self.m.cliente.post("/pedidos-ia/proximo", json={}, headers=AUTH)

    def put(self, id, corpo):
        return self.m.cliente.put(f"/pedidos-ia/{id}", json=corpo, headers=AUTH)


class PayloadDoWorkerTest(ComToken):
    def test_pedido_da_pessoa_2_sai_com_a_base_o_perfil_e_a_vaga_dela(self):
        pedido, _ = self.m.pedidos[2].criar(URL, "compatibilidade")

        recebido = self.proximo().json()

        self.assertEqual(recebido["id"], pedido.id)
        self.assertEqual(recebido["curriculo_base"], {"id": self.m.base[2].id, "titulo": "Base Two", "conteudo_md": "# Two"})
        self.assertEqual(recebido["perfil"]["termos"], ["data engineer"])
        self.assertEqual((recebido["vaga"]["titulo"], recebido["vaga"]["empresa"]), ("Data Engineer Dois", "Globex"))
        # A base gravada no pedido é a que o worker recebeu.
        self.assertEqual(self.m.pedidos[2].obter(pedido.id).curriculo_base_id, self.m.base[2].id)

    def test_cada_pedido_sai_com_os_dados_da_sua_pessoa(self):
        um, _ = self.m.pedidos[1].criar(URL, "compatibilidade")
        dois, _ = self.m.pedidos[2].criar(URL, "compatibilidade")

        primeiro, segundo = self.proximo().json(), self.proximo().json()

        self.assertEqual((primeiro["id"], primeiro["curriculo_base"]["titulo"], primeiro["perfil"]["termos"]), (um.id, "Base Um", ["dados"]))
        self.assertEqual((segundo["id"], segundo["curriculo_base"]["titulo"], segundo["perfil"]["termos"]), (dois.id, "Base Two", ["data engineer"]))

    def test_pessoa_2_sem_base_vira_erro_mesmo_com_a_base_da_pessoa_1(self):
        m = DuasPessoasEmMemoria(base_da_pessoa_2=False)
        self.m = m
        pedido, _ = m.pedidos[2].criar(URL, "compatibilidade")

        self.assertEqual(self.proximo().status_code, 204)

        final = m.pedidos[2].obter(pedido.id)
        self.assertEqual((final.estado.value, final.erro), ("erro", "Não há currículo base"))

    def test_pessoa_sem_fabrica_falha_alto_em_vez_de_usar_a_pessoa_1(self):
        # Sem `fabricas`, o app só conhece a pessoa da requisição.
        m = self.m
        app = criar_app(m.perfis[1], m.curriculos[1], BuscaService([]), encontradas=m.vagas[1], pedidos_ia=m.pedidos[1])
        m.pedidos[2].criar(URL, "compatibilidade")

        resposta = TestClient(app, raise_server_exceptions=False).post("/pedidos-ia/proximo", json={}, headers=AUTH)

        self.assertEqual(resposta.status_code, 500)


class ConcluirCurriculoTest(ComToken):
    def corpo(self, curriculo_id):
        return {
            "tentativa": 1, "estado": "pronto", "prompt_versao": "curriculo-v1", "modelo": "m",
            "resultado": {"curriculo_id": curriculo_id, "titulo": "Analista", "o_que_mudou": ["x"], "lacunas": []},
        }

    def rodando_da_pessoa_2(self):
        pedido, _ = self.m.pedidos[2].criar(URL, "curriculo")
        self.assertEqual(self.proximo().json()["id"], pedido.id)
        return pedido

    def test_recusa_curriculo_adaptado_de_outra_pessoa_mesmo_da_mesma_vaga(self):
        pedido = self.rodando_da_pessoa_2()
        da_pessoa_1 = self.m.adaptado(1)

        resposta = self.put(pedido.id, self.corpo(da_pessoa_1.id))

        self.assertEqual(resposta.status_code, 422, resposta.text)
        atual = self.m.pedidos[2].obter(pedido.id)
        self.assertEqual((atual.estado.value, atual.curriculo_id), ("rodando", None))

    def test_aceita_o_adaptado_da_propria_pessoa(self):
        pedido = self.rodando_da_pessoa_2()
        da_pessoa_2 = self.m.adaptado(2)

        resposta = self.put(pedido.id, self.corpo(da_pessoa_2.id))

        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertEqual(self.m.pedidos[2].obter(pedido.id).curriculo_id, da_pessoa_2.id)

    def test_pessoa_1_nao_aceita_o_adaptado_da_pessoa_2(self):
        pedido, _ = self.m.pedidos[1].criar(URL, "curriculo")
        self.proximo()

        self.assertEqual(self.put(pedido.id, self.corpo(self.m.adaptado(2).id)).status_code, 422)


class PostAdaptadoComPedidoTest(ComToken):
    def corpo(self, **extra):
        return {"titulo": "Adaptado", "conteudo_md": "# adaptado", "vaga_url": URL, **extra}

    def pedido_da_pessoa_2(self, tipo="curriculo"):
        pedido, _ = self.m.pedidos[2].criar(URL, tipo)
        if tipo == "curriculo":
            self.assertEqual(self.proximo().json()["id"], pedido.id)
        return pedido

    def test_com_pedido_id_grava_na_pessoa_do_pedido_com_a_base_dela(self):
        pedido = self.pedido_da_pessoa_2()

        resposta = self.m.cliente.post("/curriculos/adaptados", json=self.corpo(pedido_id=pedido.id))

        self.assertEqual(resposta.status_code, 201, resposta.text)
        salvo = resposta.json()
        self.assertEqual(salvo["base_id"], self.m.base[2].id)
        self.assertEqual([c.id for c in self.m.curriculos[2].listar_adaptados()], [salvo["id"]])
        self.assertEqual(self.m.curriculos[1].listar_adaptados(), [])

    def test_sem_pedido_id_vale_a_pessoa_da_requisicao(self):
        resposta = self.m.cliente.post("/curriculos/adaptados", json=self.corpo())

        self.assertEqual(resposta.status_code, 201, resposta.text)
        self.assertEqual(resposta.json()["base_id"], self.m.base[1].id)
        self.assertEqual(len(self.m.curriculos[1].listar_adaptados()), 1)
        self.assertEqual(self.m.curriculos[2].listar_adaptados(), [])

    def test_base_id_de_outra_pessoa_com_pedido_id_e_recusado(self):
        pedido = self.pedido_da_pessoa_2()

        resposta = self.m.cliente.post(
            "/curriculos/adaptados", json=self.corpo(pedido_id=pedido.id, base_id=self.m.base[1].id)
        )

        self.assertEqual(resposta.status_code, 404, resposta.text)
        self.assertEqual(self.m.curriculos[1].listar_adaptados() + self.m.curriculos[2].listar_adaptados(), [])

    def test_pedido_id_invalido_e_422_e_nada_e_gravado(self):
        self.m.vagas[2].registrar([VagaExterna("Outra", "Globex", "Ashby", OUTRA_URL, "Remote", True)], "data")
        pedido = self.pedido_da_pessoa_2()
        compat = self.pedido_da_pessoa_2("compatibilidade")  # pendente e de compatibilidade
        casos = {
            "inexistente": self.corpo(pedido_id=9999),
            "vaga diferente": self.corpo(pedido_id=pedido.id, vaga_url=OUTRA_URL),
            "não é de currículo": self.corpo(pedido_id=compat.id),
            "zero": self.corpo(pedido_id=0),
            "negativo": self.corpo(pedido_id=-1),
            "enorme": self.corpo(pedido_id=2**70),
            "texto": self.corpo(pedido_id="1"),
        }
        for nome, corpo in casos.items():
            with self.subTest(nome):
                self.assertEqual(self.m.cliente.post("/curriculos/adaptados", json=corpo).status_code, 422)
        self.assertEqual(self.m.curriculos[1].listar_adaptados() + self.m.curriculos[2].listar_adaptados(), [])

    def test_pedido_que_nao_esta_rodando_e_422(self):
        pedido = self.pedido_da_pessoa_2()
        self.m.pedidos[2].falhar(pedido.id, 1, "deu ruim")

        self.assertEqual(self.m.cliente.post("/curriculos/adaptados", json=self.corpo(pedido_id=pedido.id)).status_code, 422)
        self.assertEqual(self.m.curriculos[2].listar_adaptados(), [])


class ContratoRodizio:
    """Mixin: o teste concreto monta `self.repos` ({1: Repos, 2: Repos}) e `definir_criado_em`."""

    def criar(self, pessoa: int, url: str, minuto: int, prioridade=PRIORIDADE_LOTE, tipo="compatibilidade"):
        pedido, criado = self.repos[pessoa].pedidos.criar(url, tipo, prioridade=prioridade)
        self.assertTrue(criado)
        self.definir_criado_em(pedido.id, minutos(minuto))
        return pedido

    def ordem(self, quantos: int, inicio: int = 100) -> list[int]:
        """O worker pega `quantos` pedidos, um por minuto."""
        fila = self.repos[1].pedidos
        return [fila.proximo(minutos(inicio + i)).id for i in range(quantos)]

    def test_rodizio_alterna_as_pessoas_no_lote(self):
        a1, b1, c1 = (self.criar(1, URLS[i], i) for i in range(3))
        a2, b2 = (self.criar(2, URLS[i], 3 + i) for i in range(2))

        self.assertEqual(self.ordem(5), [a1.id, a2.id, b1.id, b2.id, c1.id])

    def test_rodizio_comeca_pela_pessoa_que_nunca_teve_lote_reivindicado(self):
        a1 = self.criar(1, URLS[0], 0)
        b1 = self.criar(1, URLS[1], 1)
        a2 = self.criar(2, URLS[2], 2)
        self.assertEqual(self.ordem(1), [a1.id])  # a pessoa 1 reivindica às 100
        c2 = self.criar(2, URLS[3], 3)

        # A pessoa 2 nunca teve lote reivindicado; depois dela, a pessoa 1 (reivindicou há mais tempo).
        self.assertEqual(self.ordem(3, inicio=200), [a2.id, b1.id, c2.id])

    def test_com_uma_pessoa_so_a_ordem_e_a_de_sempre(self):
        x = self.criar(1, URLS[0], 0)
        y = self.criar(1, URLS[1], 1)
        clique = self.criar(1, URLS[2], 2, prioridade=PRIORIDADE_PESSOA)
        w = self.criar(1, URLS[3], 3)
        curriculo = self.criar(1, URLS[4], 4, prioridade=PRIORIDADE_PESSOA, tipo="curriculo")

        self.assertEqual(self.ordem(5), [clique.id, curriculo.id, x.id, y.id, w.id])

    def test_clique_passa_na_frente_e_nao_conta_como_lote_reivindicado(self):
        a2 = self.criar(2, URLS[0], 0)
        a1 = self.criar(1, URLS[1], 1)
        clique2 = self.criar(2, URLS[2], 2, prioridade=PRIORIDADE_PESSOA)

        # O clique da pessoa 2 vai primeiro. Ele não é lote: as duas pessoas seguem sem lote reivindicado
        # e vale o mais antigo (a2); se o clique contasse, a pessoa 1 passaria na frente.
        self.assertEqual(self.ordem(3), [clique2.id, a2.id, a1.id])

    def test_preso_segue_depois_dos_pendentes_sem_rodizio(self):
        preso = self.criar(1, URLS[0], 0)
        self.assertEqual(self.ordem(1, inicio=10), [preso.id])  # rodando às 10
        n1 = self.criar(1, URLS[1], 20)
        n2 = self.criar(2, URLS[2], 21)

        # Uma hora depois o primeiro está preso: os pendentes vão antes (a pessoa 2 nunca teve lote), depois ele.
        depois = [self.repos[1].pedidos.proximo(minutos(70 + i)) for i in range(3)]
        self.assertEqual([p.id for p in depois], [n2.id, n1.id, preso.id])
        self.assertEqual(depois[2].tentativas, 2)

    def test_teto_diario_conta_o_lote_de_todas_as_pessoas(self):
        self.criar(1, URLS[0], 0)
        self.criar(1, URLS[1], 1)
        self.criar(2, URLS[2], 2)
        self.criar(2, URLS[3], 3, prioridade=PRIORIDADE_PESSOA)  # clique não é lote

        for pessoa in (1, 2):
            with self.subTest(pessoa=pessoa):
                self.assertEqual(self.repos[pessoa].pedidos.contar_lote_desde(INICIO), 3)
                self.assertEqual(self.repos[pessoa].pedidos.contar_lote_desde(minutos(2)), 1)


class Pessoa:
    def __init__(self, pedidos, vagas):
        self.pedidos, self.vagas = pedidos, vagas


class RodizioMemoriaTest(ContratoRodizio, unittest.TestCase):
    def setUp(self):
        self.m = DuasPessoasEmMemoria()
        self.repos = {p: Pessoa(self.m.pedidos[p], self.m.vagas[p]) for p in (1, 2)}
        for p in (1, 2):
            self.m.vagas[p].registrar([VagaExterna("T", "E", "P", u, "Brasil", True) for u in URLS], "lote")

    def definir_criado_em(self, id, quando):
        self.m.pedidos[1]._linhas[id].criado_em = quando


class TetoGlobalMemoriaTest(unittest.TestCase):
    def test_o_servico_de_uma_pessoa_desconta_o_lote_da_outra(self):
        m = DuasPessoasEmMemoria()
        for p in (1, 2):
            m.vagas[p].registrar([VagaExterna("T", "E", "P", u, "Brasil", True) for u in URLS], "lote")
        um, dois = m.servico_da_pessoa(1, max_lote_por_dia=3), m.servico_da_pessoa(2, max_lote_por_dia=3)

        self.assertEqual(um.enfileirar_lote(m.vagas[1].obter(u) for u in URLS[:2]), 2)
        self.assertEqual(dois.restante_lote(), 1)
        self.assertEqual(dois.enfileirar_lote(m.vagas[2].obter(u) for u in URLS[:4]), 1)
        self.assertEqual((um.restante_lote(), dois.restante_lote()), (0, 0))
        self.assertEqual(um.enfileirar_lote(m.vagas[1].obter(u) for u in URLS[2:4]), 0)

    def test_a_janela_de_24_h_vale_para_todas_as_pessoas(self):
        m = DuasPessoasEmMemoria()
        for p in (1, 2):
            m.vagas[p].registrar([VagaExterna("T", "E", "P", u, "Brasil", True) for u in URLS], "lote")
        um, dois = m.servico_da_pessoa(1, max_lote_por_dia=2), m.servico_da_pessoa(2, max_lote_por_dia=2)
        self.assertEqual(um.enfileirar_lote(m.vagas[1].obter(u) for u in URLS[:2]), 2)

        m.relogio.agora += timedelta(hours=24, minutes=1)

        self.assertEqual(dois.restante_lote(), 2)


@unittest.skipUnless(os.environ.get("PGHOST"), "PGHOST não definido: integração com Postgres pulada")
class RodizioPostgresTest(ContratoRodizio, unittest.TestCase):
    def setUp(self):
        from src.repository.pedido_ia_postgres import PedidoIARepositoryPostgres
        from src.repository.postgres import VagaEncontradaRepositoryPostgres
        from tests.test_postgres_pedido_ia import _schema

        self.conn, self.schema = _schema(self)
        self.conn.execute("INSERT INTO pessoa (id, nome_exibicao) VALUES (2, 'Pessoa 2')")
        self.repos = {}
        for p in (1, 2):
            vagas = VagaEncontradaRepositoryPostgres(self.conn, p)
            vagas.registrar([VagaExterna("T", "E", "P", u, "Brasil", True) for u in URLS], "lote")
            self.repos[p] = Pessoa(PedidoIARepositoryPostgres(self.conn, p), vagas)

    def definir_criado_em(self, id, quando):
        self.conn.execute("UPDATE pedido_ia SET criado_em = %s WHERE id = %s", (quando, id))

    def test_teto_global_no_servico_com_repositorios_postgres(self):
        from src.repository.postgres import CurriculoRepositoryPostgres, PerfilBuscaRepositoryPostgres

        servicos = {}
        for p in (1, 2):
            curriculos = CurriculoRepositoryPostgres(self.conn, p)
            curriculos.salvar(Curriculo(tipo="base", titulo=f"Base {p}", conteudo_md="# b"))
            servicos[p] = PedidoIAService(
                self.repos[p].pedidos,
                self.repos[p].vagas,
                PerfilBuscaRepositoryPostgres(self.conn, p),
                curriculos,
                max_lote_por_dia=3,
            )

        self.assertEqual(servicos[1].enfileirar_lote(self.repos[1].vagas.obter(u) for u in URLS[:2]), 2)
        self.assertEqual(servicos[2].enfileirar_lote(self.repos[2].vagas.obter(u) for u in URLS[:4]), 1)
        self.assertEqual(servicos[1].restante_lote(), 0)

    def test_a_014_cria_os_indices_do_rodizio_e_do_teto_global(self):
        nomes = {l[0] for l in self.conn.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'pedido_ia'")}
        self.assertTrue({"pedido_ia_lote_reivindicado_idx", "pedido_ia_lote_criado_idx"} <= nomes)

    def test_api_ponta_a_ponta_com_duas_pessoas_e_uma_conexao_do_pool_por_chamada(self):
        from src.controller.api import _PorChamada
        from src.repository.pedido_ia_postgres import PedidoIARepositoryPostgres
        from src.repository.postgres import (
            CurriculoRepositoryPostgres,
            PerfilBuscaRepositoryPostgres,
            VagaEncontradaRepositoryPostgres,
        )
        from tests.test_candidaturas import _pool

        pool = _pool(self, self.schema)
        classes = {
            "perfis": PerfilBuscaRepositoryPostgres,
            "curriculos": CurriculoRepositoryPostgres,
            "encontradas": VagaEncontradaRepositoryPostgres,
        }

        def por_chamada(nome, pessoa):
            return _PorChamada(lambda: pool, lambda: classes[nome], pessoa)

        for p, titulo, termo in ((1, "Base Um", "dados"), (2, "Base Two", "data engineer")):
            CurriculoRepositoryPostgres(self.conn, p).salvar(Curriculo(tipo="base", titulo=titulo, conteudo_md=f"# {p}"))
            PerfilBuscaRepositoryPostgres(self.conn, p).salvar(Perfil(termos=[termo]))
        fabricas = FabricasPorPessoa(
            perfis=lambda p: por_chamada("perfis", p),
            curriculos=lambda p: por_chamada("curriculos", p),
            encontradas=lambda p: por_chamada("encontradas", p),
        )
        app = criar_app(
            por_chamada("perfis", 1),
            por_chamada("curriculos", 1),
            BuscaService([]),
            encontradas=por_chamada("encontradas", 1),
            pedidos_ia=_PorChamada(lambda: pool, lambda: PedidoIARepositoryPostgres, 1),
            fabricas=fabricas,
        )
        api = TestClient(app, raise_server_exceptions=False)
        pedido, _ = self.repos[2].pedidos.criar(URLS[0], "curriculo")

        with mock.patch.dict(os.environ, {"VAGAS_IA_TOKEN": TOKEN}):
            recebido = api.post("/pedidos-ia/proximo", json={}, headers=AUTH).json()
            self.assertEqual(recebido["id"], pedido.id)
            self.assertEqual(recebido["curriculo_base"]["titulo"], "Base Two")
            self.assertEqual(recebido["perfil"]["termos"], ["data engineer"])

            corpo = {"titulo": "Adaptado", "conteudo_md": "# a", "vaga_url": URLS[0], "pedido_id": pedido.id}
            salvo = api.post("/curriculos/adaptados", json=corpo)
            self.assertEqual(salvo.status_code, 201, salvo.text)
            dona = self.conn.execute("SELECT pessoa_id FROM curriculo WHERE id = %s", (salvo.json()["id"],)).fetchone()[0]
            self.assertEqual(dona, 2)

            # O adaptado da pessoa 1 para a mesma vaga não vale para o pedido da pessoa 2.
            da_um = CurriculoRepositoryPostgres(self.conn, 1).salvar(
                Curriculo(tipo="adaptado", titulo="x", conteudo_md="# x", vaga_url=URLS[0],
                          base_id=CurriculoRepositoryPostgres(self.conn, 1).obter_base().id)
            )

            def resultado(curriculo_id):
                return {
                    "tentativa": 1, "estado": "pronto", "prompt_versao": "curriculo-v1", "modelo": "m",
                    "resultado": {"curriculo_id": curriculo_id, "titulo": "t", "o_que_mudou": ["x"], "lacunas": []},
                }

            self.assertEqual(api.put(f"/pedidos-ia/{pedido.id}", json=resultado(da_um.id), headers=AUTH).status_code, 422)
            self.assertEqual(
                api.put(f"/pedidos-ia/{pedido.id}", json=resultado(salvo.json()["id"]), headers=AUTH).status_code, 200
            )


class FiacaoProducaoTest(unittest.TestCase):
    def test_producao_cria_os_repositorios_da_pessoa_do_pedido_a_cada_chamada(self):
        from src.controller import api

        with mock.patch.object(api, "criar_app") as criar:
            api.criar_app_producao()
        fabricas = criar.call_args.kwargs["fabricas"]

        for nome, classe in (
            ("perfis", api._perfis_postgres),
            ("curriculos", api._curriculos_postgres),
            ("encontradas", api._encontradas_postgres),
        ):
            repositorio = getattr(fabricas, nome)(2)
            self.assertIsInstance(repositorio, api._PorChamada)
            self.assertEqual(repositorio._pessoa_id, 2)
            self.assertIs(repositorio._classe(), classe())
        # Uma fábrica nova a cada pedido: nada compartilhado entre as pessoas.
        self.assertIsNot(fabricas.perfis(2), fabricas.perfis(3))


if __name__ == "__main__":
    unittest.main()
