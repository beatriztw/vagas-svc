"""Coleta para várias pessoas: busca uma vez por termo, filtro de perfil e registro separados por pessoa.

Pessoas, perfis e vagas daqui são inventados e escritos à mão (não são respostas gravadas de provider).
"""

import io
import os
import unittest
from datetime import date

from tests.sessao import ClienteLogado

from src.controller.api import PESSOA_PADRAO, criar_app
from src.controller.coleta import rodar
from src.model.curriculo import Curriculo
from src.model.erros import DadoInvalido
from src.model.perfil import Perfil
from src.repository.base import (
    CurriculoRepositoryMemoria,
    PerfilBuscaRepositoryMemoria,
    VagaEncontradaRepositoryMemoria,
)
from src.repository.candidatura import vaga_por_url_em
from src.repository.pedido_ia import PedidoIARepositoryMemoria
from src.repository.pessoa import PessoaRepositoryMemoria
from src.repository.trava import TravaColetaMemoria
from src.service.agendador_coleta import resumir
from src.service.busca_service import BuscaService
from src.service.coleta_service import ColetaService, RepositoriosDaPessoa
from src.service.recomendacao_service import RecomendacaoService
from tests.test_busca_service import ClientQuebrado
from tests.test_coleta import ClientPorTermo, _schema_descartavel, vaga

HOJE = date(2026, 10, 20)


class Banco:
    """Os repositórios em memória de várias pessoas no mesmo "banco"."""

    def __init__(self, *pessoas: int):
        self.pessoas = PessoaRepositoryMemoria(pessoas)
        self.perfis: dict[int, PerfilBuscaRepositoryMemoria] = {}
        self.vagas: dict[int, VagaEncontradaRepositoryMemoria] = {}
        self.curriculos: dict[int, CurriculoRepositoryMemoria] = {}
        self.pedidos: dict[int, PedidoIARepositoryMemoria] = {}
        for p in pessoas:
            primeiro = p == pessoas[0]
            self.perfis[p] = PerfilBuscaRepositoryMemoria(p, None if primeiro else self.perfis[pessoas[0]])
            self.vagas[p] = VagaEncontradaRepositoryMemoria(
                p, compartilhado=None if primeiro else self.vagas[pessoas[0]]
            )
            self.curriculos[p] = CurriculoRepositoryMemoria(p, None if primeiro else self.curriculos[pessoas[0]])
            self.pedidos[p] = PedidoIARepositoryMemoria(
                p, vaga_por_url_em(self.vagas[p]), compartilhado=None if primeiro else self.pedidos[pessoas[0]]
            )

    def repositorios(self, pessoa_id: int) -> RepositoriosDaPessoa:
        return RepositoriosDaPessoa(
            self.perfis[pessoa_id], self.vagas[pessoa_id], self.curriculos[pessoa_id], self.pedidos[pessoa_id]
        )

    def servico(self, *providers, **extra) -> ColetaService:
        return ColetaService(
            RecomendacaoService(BuscaService(list(providers))), self.pessoas, self.repositorios, **extra
        )

    def urls(self, pessoa_id: int) -> set[str]:
        return {v.url for v in self.vagas[pessoa_id].listar(somente_novas=False, limite=100)}

    def app(self, *providers, com_pedidos: bool = True) -> ClienteLogado:
        padrao = PESSOA_PADRAO
        return ClienteLogado(
            criar_app(
                self.perfis[padrao],
                self.curriculos[padrao],
                BuscaService(list(providers)),
                encontradas=self.vagas[padrao],
                pedidos_ia=self.pedidos[padrao] if com_pedidos else None,
                pessoas=self.pessoas,
                repositorios_da_pessoa=self.repositorios,
            )
        )


class DuasPessoasTest(unittest.TestCase):
    def setUp(self):
        self.banco = Banco(1, 2)
        self.client = ClientPorTermo(
            "A", {"python": [vaga("u1", "Dev Python"), vaga("u2", "Dev Python Estagio"), vaga("u3", "Python Senior")]}
        )

    def test_mesmo_termo_busca_uma_vez_e_cada_pessoa_recebe_com_o_seu_filtro(self):
        self.banco.perfis[1].salvar(Perfil(termos=["python"], excluir=["estagio"]))
        self.banco.perfis[2].salvar(Perfil(termos=["python"]))

        resultado = self.banco.servico(self.client).coletar(HOJE)

        self.assertEqual(self.client.termos_buscados, ["python"])  # o provider (pago) uma vez só
        self.assertEqual(self.banco.urls(1), {"u1", "u3"})  # `excluir` cortou o estágio
        self.assertEqual(self.banco.urls(2), {"u1", "u2", "u3"})
        self.assertEqual([(p.pessoa_id, p.novas) for p in resultado.pessoas], [(1, 2), (2, 3)])

    def test_o_filtro_da_primeira_pessoa_nao_vaza_para_a_segunda_em_qualquer_ordem(self):
        # A pessoa que corta a vaga vem DEPOIS: o cache tem o resultado cru, não o já filtrado.
        self.banco.perfis[1].salvar(Perfil(termos=["python"]))
        self.banco.perfis[2].salvar(Perfil(termos=["python"], excluir=["estagio"]))

        self.banco.servico(self.client).coletar(HOJE)

        self.assertEqual(self.client.termos_buscados, ["python"])
        self.assertEqual(self.banco.urls(1), {"u1", "u2", "u3"})
        self.assertEqual(self.banco.urls(2), {"u1", "u3"})

    def test_termo_com_outra_grafia_conta_como_o_mesmo(self):
        client = ClientPorTermo("A", {"Front-end": [vaga("u1", "Dev Front End")], "front end": [vaga("u9")]})
        self.banco.perfis[1].salvar(Perfil(termos=["Front-end"]))
        self.banco.perfis[2].salvar(Perfil(termos=["front end"]))

        self.banco.servico(client).coletar(HOJE)

        self.assertEqual(client.termos_buscados, ["Front-end"])
        self.assertEqual(self.banco.urls(1), {"u1"})
        self.assertEqual(self.banco.urls(2), {"u1"})
        self.assertEqual(self.banco.vagas[2].listar(False, 10)[0].termos, ["front end"])  # o termo é o dela

    def test_termos_diferentes_buscam_cada_um(self):
        client = ClientPorTermo("A", {"python": [vaga("u1")], "go": [vaga("u2", "Dev Go")]})
        self.banco.perfis[1].salvar(Perfil(termos=["python"]))
        self.banco.perfis[2].salvar(Perfil(termos=["python", "go"]))

        self.banco.servico(client).coletar(HOJE)

        self.assertEqual(sorted(client.termos_buscados), ["go", "python"])
        self.assertEqual(self.banco.urls(1), {"u1"})
        self.assertEqual(self.banco.urls(2), {"u1", "u2"})

    def test_erro_do_provider_vale_para_as_duas_sem_buscar_de_novo(self):
        self.banco.perfis[1].salvar(Perfil(termos=["python"]))
        self.banco.perfis[2].salvar(Perfil(termos=["python"]))

        resultado = self.banco.servico(ClientQuebrado(), self.client).coletar(HOJE)

        self.assertEqual(self.client.termos_buscados, ["python"])
        self.assertEqual([list(p.termos[0].erros) for p in resultado.pessoas], [["Quebrado"], ["Quebrado"]])

    def test_pessoa_sem_perfil_ou_sem_termos_e_pulada_e_as_outras_coletam(self):
        banco = Banco(1, 2, 3)
        banco.perfis[2].salvar(Perfil(termos=["python"]))
        banco.perfis[3].salvar(Perfil(termos=["—"]))  # nada útil depois de normalizar

        resultado = banco.servico(self.client).coletar(HOJE)

        self.assertEqual([p.pessoa_id for p in resultado.pessoas], [2])
        self.assertEqual(resultado.puladas, [1, 3])
        self.assertEqual(banco.urls(2), {"u1", "u2", "u3"})
        self.assertEqual((banco.urls(1), banco.urls(3)), (set(), set()))

    def test_sem_perfil_com_termos_em_nenhuma_pessoa_e_dado_invalido(self):
        with self.assertRaisesRegex(DadoInvalido, "Nenhum perfil salvo"):
            self.banco.servico(self.client).coletar(HOJE)
        self.banco.perfis[2].salvar(Perfil(termos=[]))
        with self.assertRaisesRegex(DadoInvalido, "não tem termos"):
            self.banco.servico(self.client).coletar(HOJE)
        self.assertEqual(self.client.termos_buscados, [])

    def test_toda_a_coleta_roda_dentro_da_mesma_trava(self):
        self.banco.perfis[1].salvar(Perfil(termos=["python"]))
        self.banco.perfis[2].salvar(Perfil(termos=["go"]))
        trava = TravaColetaMemoria()
        presa = []

        class Espia(ClientPorTermo):
            def buscar(self, termo):
                presa.append(not trava._lock.acquire(blocking=False))  # True = alguém segura a trava
                return super().buscar(termo)

        self.banco.servico(Espia("A", {"python": [vaga("u1")]}), trava=trava).coletar(HOJE)

        self.assertEqual(presa, [True, True])  # as duas pessoas, a trava presa nas duas buscas
        with trava.segurar():  # e solta no fim
            pass

    def test_ao_terminar_roda_uma_vez_por_pessoa_coletada_e_a_falha_de_uma_nao_para_a_outra(self):
        banco = Banco(1, 2, 3)
        for p in (1, 3):
            banco.perfis[p].salvar(Perfil(termos=["python"]))  # a 2 não tem perfil: sem lote
        chamadas = []

        def ao_terminar(coletada):
            chamadas.append((coletada.pessoa_id, coletada.novas))
            if coletada.pessoa_id == 1:
                raise RuntimeError("fila fora")

        with self.assertLogs("src.service.coleta_service", "WARNING") as log:
            banco.servico(self.client, ao_terminar=ao_terminar).coletar(HOJE)

        self.assertEqual(chamadas, [(1, 3), (3, 3)])
        self.assertEqual(len(log.records), 1)
        self.assertIn("pessoa 1", log.output[0])
        self.assertIn("RuntimeError", log.output[0])
        self.assertNotIn("fila fora", log.output[0])

    def test_agendador_resume_as_contagens_somadas_sem_id(self):
        self.banco.perfis[1].salvar(Perfil(termos=["python", "go"]))
        self.banco.perfis[2].salvar(Perfil(termos=["python"]))
        client = ClientPorTermo("A", {"python": [vaga("u1"), vaga("u2")], "go": [vaga("u3", "Dev Go")]})

        resultado = self.banco.servico(client).coletar(HOJE)

        self.assertEqual(resumir(resultado), "ok termos=3 novas=5 termos_com_erro=0")

    def test_falhou_so_quando_nenhuma_pessoa_buscou_de_verdade(self):
        self.banco.perfis[1].salvar(Perfil(termos=["python"]))
        self.banco.perfis[2].salvar(Perfil(termos=["python"]))

        self.assertTrue(self.banco.servico(ClientQuebrado()).coletar(HOJE).falhou)
        self.assertFalse(self.banco.servico(ClientQuebrado(), self.client).coletar(HOJE).falhou)


class BancoFora(VagaEncontradaRepositoryMemoria):
    """Repositório de vagas de uma pessoa cujo `registrar` falha como um banco que caiu no meio."""

    def registrar(self, vagas, termo):
        raise ConnectionError("banco fora")


class ErroDeUmaPessoaTest(unittest.TestCase):
    def setUp(self):
        self.banco = Banco(1, 2, 3)
        for p in (1, 2, 3):
            self.banco.perfis[p].salvar(Perfil(termos=["python"]))
        self.banco.vagas[2] = BancoFora(2)
        self.client = ClientPorTermo("A", {"python": [vaga("u1", "Dev Python")]})

    def test_erro_de_banco_numa_pessoa_nao_derruba_as_outras(self):
        terminadas = []

        resultado = self.banco.servico(self.client, ao_terminar=lambda c: terminadas.append(c.pessoa_id)).coletar(
            HOJE
        )

        self.assertEqual([p.pessoa_id for p in resultado.pessoas], [1, 3])
        self.assertEqual(resultado.com_erro, [2])
        self.assertEqual(self.banco.urls(1), {"u1"})
        self.assertEqual(self.banco.urls(3), {"u1"})
        self.assertEqual(terminadas, [1, 3])  # o lote da IA de quem coletou é enfileirado
        self.assertIn("pessoas_com_erro=1", resumir(resultado))

    def test_post_coletas_da_pessoa_com_erro_e_erro_e_nao_um_vazio(self):
        # A pessoa da requisição (1) cai no meio; a 2 coleta. A resposta não pode dizer "0 novas" como se fosse sucesso.
        self.banco.vagas[1] = BancoFora(1)
        self.banco.vagas[2] = VagaEncontradaRepositoryMemoria(2)
        cliente = TestClient(self.banco.app(self.client).app, raise_server_exceptions=False)

        resposta = cliente.post("/coletas", json={})

        self.assertEqual(resposta.status_code, 500)
        self.assertEqual(self.banco.urls(2), {"u1"})  # a outra pessoa coletou mesmo assim

    def test_cli_avisa_a_pessoa_com_erro_e_sai_com_zero(self):
        saida, erro = io.StringIO(), io.StringIO()

        codigo = rodar(lambda: self.banco.servico(self.client), saida=saida, erro=erro)

        self.assertEqual(codigo, 0)
        self.assertIn("pessoa 2 | coleta falhou", erro.getvalue())
        self.assertNotIn("banco fora", erro.getvalue())  # só o id: a mensagem pode trazer dado do perfil


class CliMultipessoaTest(unittest.TestCase):
    def test_uma_linha_por_pessoa_e_termo_com_o_id_e_o_lote_de_cada_uma(self):
        banco = Banco(1, 2, 3)
        banco.perfis[1].salvar(Perfil(termos=["python"]))
        banco.perfis[2].salvar(Perfil(termos=["python", "go"]))
        client = ClientPorTermo("A", {"python": [vaga("u1"), vaga("u2")], "go": [vaga("u3", "Dev Go")]})
        saida, erro = io.StringIO(), io.StringIO()
        lotes = []

        codigo = rodar(lambda: banco.servico(client), saida, erro, pos_coleta=lambda p: lotes.append(p) or 10 + p)

        self.assertEqual(codigo, 0)
        self.assertEqual(
            saida.getvalue().splitlines(),
            [
                "pessoa 1 | python: 2 encontradas, 2 novas",
                "pessoa 2 | python: 2 encontradas, 2 novas",
                "pessoa 2 | go: 1 encontradas, 1 novas",
                "pessoa 3 | pulada: sem perfil ou sem termos",
                "pessoa 1 | ia: 11 vagas na fila",
                "pessoa 2 | ia: 12 vagas na fila",
            ],
        )
        self.assertEqual(lotes, [1, 2])
        self.assertEqual(erro.getvalue(), "")

    def test_lote_que_falha_para_uma_pessoa_nao_impede_a_outra(self):
        banco = Banco(1, 2)
        for p in (1, 2):
            banco.perfis[p].salvar(Perfil(termos=["python"]))
        saida, erro = io.StringIO(), io.StringIO()

        def lote(pessoa_id):
            if pessoa_id == 1:
                raise RuntimeError("segredo do banco")
            return 4

        codigo = rodar(lambda: banco.servico(ClientPorTermo("A", {"python": [vaga("u1")]})), saida, erro, lote)

        self.assertEqual(codigo, 0)
        self.assertIn("pessoa 2 | ia: 4 vagas na fila", saida.getvalue())
        self.assertEqual(erro.getvalue().strip(), "pessoa 1 | ia: lote falhou: RuntimeError")

    def test_sem_pessoa_com_perfil_sai_com_um(self):
        saida, erro = io.StringIO(), io.StringIO()
        self.assertEqual(rodar(lambda: Banco(1, 2).servico(ClientQuebrado()), saida, erro), 1)
        self.assertIn("Nenhum perfil salvo", erro.getvalue())


class ApiMultipessoaTest(unittest.TestCase):
    """POST /coletas coleta todas, devolve o formato de sempre para a PESSOA_PADRAO e enfileira o lote de cada uma."""

    def setUp(self):
        self.client = ClientPorTermo("A", {"python": [vaga("u1"), vaga("u2")]})

    def banco_com(self, *com_perfil: int, com_base: tuple[int, ...] = ()) -> Banco:
        banco = Banco(PESSOA_PADRAO, 2)
        for p in com_perfil:
            banco.perfis[p].salvar(Perfil(termos=["python"]))
        for p in com_base:
            banco.curriculos[p].salvar(Curriculo(tipo="base", titulo="Base", conteudo_md="# Base"))
        return banco

    def lote(self, banco: Banco, pessoa_id: int) -> set[str]:
        pedidos = banco.pedidos[pessoa_id]
        return {u for u in pedidos.urls_ativas("compatibilidade") if pedidos.ultimo(u, "compatibilidade").prioridade == 1}

    def test_formato_de_sempre_para_a_pessoa_padrao_e_lote_por_pessoa(self):
        banco = self.banco_com(PESSOA_PADRAO, 2, com_base=(PESSOA_PADRAO, 2))

        resposta = banco.app(self.client).post("/coletas", json={})

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(
            resposta.json(),
            {"termos": [{"termo": "python", "encontradas": 2, "novas": 2, "erros": {}}], "novas": 2},
        )
        self.assertEqual(self.client.termos_buscados, ["python"])
        self.assertEqual(banco.urls(2), {"u1", "u2"})  # a outra pessoa também foi coletada
        self.assertEqual(self.lote(banco, PESSOA_PADRAO), {"u1", "u2"})
        self.assertEqual(self.lote(banco, 2), {"u1", "u2"})

    def test_lote_e_de_cada_pessoa_e_so_de_quem_tem_base(self):
        banco = self.banco_com(PESSOA_PADRAO, 2, com_base=(PESSOA_PADRAO,))

        self.assertEqual(banco.app(self.client).post("/coletas", json={}).status_code, 200)

        self.assertEqual(self.lote(banco, PESSOA_PADRAO), {"u1", "u2"})
        self.assertEqual(self.lote(banco, 2), set())  # sem currículo base, nada vai para a fila dela

    def test_pessoa_padrao_sem_perfil_devolve_vazio_e_a_outra_coleta(self):
        banco = self.banco_com(2)

        self.assertEqual(banco.app(self.client).post("/coletas", json={}).json(), {"termos": [], "novas": 0})
        self.assertEqual(banco.urls(2), {"u1", "u2"})

    def test_422_so_quando_nenhuma_pessoa_tem_perfil(self):
        self.assertEqual(self.banco_com().app(self.client).post("/coletas", json={}).status_code, 422)
        self.assertEqual(self.client.termos_buscados, [])


@unittest.skipUnless(os.environ.get("PGHOST"), "PGHOST não definido: coleta com Postgres pulada")
class ColetaDuasPessoasPostgresTest(unittest.TestCase):
    def setUp(self):
        from src.repository.pessoa_postgres import PessoaRepositoryPostgres
        from src.repository.postgres import PerfilBuscaRepositoryPostgres, VagaEncontradaRepositoryPostgres

        self.conn, self.schema = _schema_descartavel(self, "teste_coleta_pessoas")
        self.conn.execute("INSERT INTO pessoa (nome_exibicao) VALUES ('Pessoa 2')")
        self.conn.execute("INSERT INTO pessoa (nome_exibicao) VALUES ('Pessoa 3')")
        self.pessoas = PessoaRepositoryPostgres(self.conn)
        self.perfis = {p: PerfilBuscaRepositoryPostgres(self.conn, p) for p in (1, 2, 3)}
        self.vagas = {p: VagaEncontradaRepositoryPostgres(self.conn, p) for p in (1, 2, 3)}
        self.client = ClientPorTermo("A", {"python": [vaga("u1"), vaga("u2", "Dev Python Estagio")]})

    def test_listar_devolve_os_ids_em_ordem(self):
        self.assertEqual(self.pessoas.listar(), [1, 2, 3])

    def test_coleta_no_banco_uma_busca_e_linhas_separadas_por_pessoa(self):
        self.perfis[1].salvar(Perfil(termos=["python"], excluir=["estagio"]))
        self.perfis[2].salvar(Perfil(termos=["python"]))  # a 3 não tem perfil: pulada
        servico = ColetaService(
            RecomendacaoService(BuscaService([self.client])),
            self.pessoas,
            lambda p: RepositoriosDaPessoa(self.perfis[p], self.vagas[p]),
        )

        resultado = servico.coletar(HOJE)

        self.assertEqual(self.client.termos_buscados, ["python"])
        self.assertEqual(resultado.puladas, [3])
        urls = {p: {v.url for v in self.vagas[p].listar(False, 10)} for p in (1, 2, 3)}
        self.assertEqual(urls, {1: {"u1"}, 2: {"u1", "u2"}, 3: set()})
        self.assertEqual(servico.coletar(HOJE).novas, 0)  # a segunda coleta não acha nada novo, por pessoa

    def test_pelo_pool_cada_chamada_usa_a_propria_conexao(self):
        from psycopg_pool import ConnectionPool

        from src.controller.api import _PorChamada, _encontradas_postgres, _perfis_postgres, _pessoas_postgres
        from src.repository.conexao import parametros

        pool = ConnectionPool(
            kwargs={**parametros(), "autocommit": True, "options": f"-c search_path={self.schema}"},
            min_size=1,
            max_size=1,  # uma conexão só: um repositório que a segurasse travaria a coleta
            open=True,
        )
        self.addCleanup(pool.close)
        for p in (1, 2):
            self.perfis[p].salvar(Perfil(termos=["python"]))
        servico = ColetaService(
            RecomendacaoService(BuscaService([self.client])),
            _PorChamada(lambda: pool, _pessoas_postgres),
            lambda p: RepositoriosDaPessoa(
                _PorChamada(lambda: pool, _perfis_postgres, p), _PorChamada(lambda: pool, _encontradas_postgres, p)
            ),
        )

        resultado = servico.coletar(HOJE)

        self.assertEqual([(c.pessoa_id, c.novas) for c in resultado.pessoas], [(1, 2), (2, 2)])
        self.assertEqual(self.client.termos_buscados, ["python"])


if __name__ == "__main__":
    unittest.main()
