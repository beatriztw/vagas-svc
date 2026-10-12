"""Fila de pedidos de IA contra um Postgres de verdade. Só roda com PGHOST definido (o CI sobe um postgres:16-alpine).

Cada teste cria um schema descartável, aplica todas as migrations, em ordem, DUAS vezes (idempotência) e o apaga
no fim. Vagas e resultados são inventados e escritos à mão.
"""

import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
# Todas, como a subida da app: o repositório lê colunas de outras (a 004 traz `expirada_em`).
APLICAR = tuple(sorted(p.name for p in MIGRATIONS.glob("*.sql")))
URL_A = "https://exemplo.com/vaga-a"
URL_B = "https://exemplo.com/vaga-b"
RESULTADO = {
    "nota": 55,
    "faixa": "média",
    "atende": [],
    "falta": [],
    "eliminatorios": [],
    "resumo": "Aderência parcial.",
}


def _schema(teste: unittest.TestCase):
    from psycopg import sql

    from src.repository.conexao import conectar

    conn = conectar()
    teste.addCleanup(conn.close)
    schema = f"teste_pedido_ia_{uuid.uuid4().hex[:12]}"
    conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    teste.addCleanup(conn.execute, sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
    conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
    for nome in APLICAR:
        texto = (MIGRATIONS / nome).read_text(encoding="utf-8")
        conn.execute(texto)
        conn.execute(texto)  # idempotente: a segunda aplicação não falha
    return conn, schema


def _conexao_extra(teste: unittest.TestCase, schema: str):
    from src.repository.conexao import parametros

    import psycopg

    conn = psycopg.connect(**parametros(), autocommit=True, options=f"-c search_path={schema}")
    teste.addCleanup(conn.close)
    return conn


@unittest.skipUnless(os.environ.get("PGHOST"), "PGHOST não definido: integração com Postgres pulada")
class PedidoIAPostgresTest(unittest.TestCase):
    def setUp(self):
        import psycopg

        from src.model.vaga_externa import VagaExterna
        from src.repository.pedido_ia_postgres import PedidoIARepositoryPostgres
        from src.repository.postgres import VagaEncontradaRepositoryPostgres

        self.errors = psycopg.errors
        self.conn, self.schema = _schema(self)
        VagaEncontradaRepositoryPostgres(self.conn).registrar(
            [VagaExterna(titulo="Analista", empresa="Acme", plataforma="Remotar", url=u) for u in (URL_A, URL_B)], "dados"
        )
        self.repo = PedidoIARepositoryPostgres(self.conn)

    def linha(self, id):
        return self.conn.execute("SELECT estado, tentativas, erro FROM pedido_ia WHERE id = %s", (id,)).fetchone()

    def test_criar_e_idempotente_com_o_ativo_e_reexecucao_cria_novo(self):
        primeiro, criado = self.repo.criar(URL_A, "compatibilidade")
        segundo, criado2 = self.repo.criar(URL_A, "compatibilidade")
        self.assertEqual((criado, criado2, primeiro.id), (True, False, segundo.id))
        self.assertTrue(self.repo.criar(URL_A, "curriculo")[1])

        rodando = self.repo.proximo()
        self.repo.concluir(rodando.id, 1, RESULTADO, "compatibilidade-v1", "m", 55, "média")
        novo, criado3 = self.repo.criar(URL_A, "compatibilidade")

        self.assertTrue(criado3)
        self.assertNotEqual(novo.id, primeiro.id)
        self.assertEqual(self.repo.ultimo(URL_A, "compatibilidade").id, novo.id)

    def test_vaga_fora_do_historico_e_dado_invalido(self):
        from src.model.erros import DadoInvalido

        with self.assertRaises(DadoInvalido):
            self.repo.criar("https://exemplo.com/fora", "compatibilidade")
        with self.assertRaises(DadoInvalido):
            self.repo.gravar_rotulo("https://exemplo.com/fora", "alta")
        # A transação desfeita não deixa a conexão inutilizada.
        self.assertTrue(self.repo.criar(URL_A, "compatibilidade")[1])

    def test_indice_parcial_barra_dois_ativos_mas_nao_os_terminados(self):
        self.conn.execute("INSERT INTO pedido_ia (vaga_url, tipo) VALUES (%s, 'compatibilidade')", (URL_A,))
        with self.assertRaises(self.errors.UniqueViolation):
            self.conn.execute("INSERT INTO pedido_ia (vaga_url, tipo) VALUES (%s, 'compatibilidade')", (URL_A,))
        self.conn.execute("UPDATE pedido_ia SET estado = 'erro', erro = 'x'")
        self.conn.execute("INSERT INTO pedido_ia (vaga_url, tipo) VALUES (%s, 'compatibilidade')", (URL_A,))

    def test_ordem_da_fila_e_fila_vazia(self):
        self.assertIsNone(self.repo.proximo())
        a, _ = self.repo.criar(URL_A, "compatibilidade")
        b, _ = self.repo.criar(URL_B, "compatibilidade")
        self.assertEqual([self.repo.proximo().id, self.repo.proximo().id], [a.id, b.id])
        self.assertIsNone(self.repo.proximo())

    def test_duas_conexoes_no_proximo_nao_pegam_o_mesmo_pedido(self):
        a, _ = self.repo.criar(URL_A, "compatibilidade")
        b, _ = self.repo.criar(URL_B, "compatibilidade")
        from src.repository.pedido_ia_postgres import PedidoIARepositoryPostgres

        outra = _conexao_extra(self, self.schema)
        # Sem SKIP LOCKED o proximo esperaria a trava: com o limite, o teste falha em vez de travar.
        outra.execute("SET lock_timeout = '2s'")
        # A primeira conexão segura o pedido mais antigo travado, no meio da sua transação: a segunda
        # tem que pular a linha travada (SKIP LOCKED) em vez de esperar ou pegar a mesma.
        with self.conn.transaction():
            self.conn.execute("SELECT id FROM pedido_ia WHERE id = %s FOR UPDATE", (a.id,))
            durante = PedidoIARepositoryPostgres(outra).proximo()
        self.assertEqual(durante.id, b.id)
        depois = PedidoIARepositoryPostgres(outra).proximo()
        self.assertEqual(depois.id, a.id)

    def test_varios_workers_simultaneos_reivindicam_pedidos_distintos(self):
        from src.repository.pedido_ia_postgres import PedidoIARepositoryPostgres

        total = 6
        from src.model.vaga_externa import VagaExterna
        from src.repository.postgres import VagaEncontradaRepositoryPostgres

        urls = [f"https://exemplo.com/lote-{i}" for i in range(total)]
        VagaEncontradaRepositoryPostgres(self.conn).registrar(
            [VagaExterna(titulo="T", empresa="E", plataforma="P", url=u) for u in urls], "lote"
        )
        for u in urls:
            self.repo.criar(u, "compatibilidade")
        conexoes = [_conexao_extra(self, self.schema) for _ in range(total)]
        with ThreadPoolExecutor(max_workers=total) as executor:
            pegos = list(executor.map(lambda c: PedidoIARepositoryPostgres(c).proximo(), conexoes))

        ids = [p.id for p in pegos if p is not None]
        self.assertEqual(len(ids), total)
        self.assertEqual(len(set(ids)), total)

    def test_preso_volta_e_na_terceira_expiracao_vira_erro(self):
        pedido, _ = self.repo.criar(URL_A, "compatibilidade")
        agora = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(self.repo.proximo(agora).tentativas, 1)

        self.assertIsNone(self.repo.proximo(agora + timedelta(minutes=15)))  # no prazo exato ainda não é preso
        segunda = self.repo.proximo(agora + timedelta(minutes=15, seconds=1))
        self.assertEqual((segunda.id, segunda.tentativas), (pedido.id, 2))
        terceira = self.repo.proximo(agora + timedelta(minutes=31))
        self.assertEqual(terceira.tentativas, 3)

        self.assertIsNone(self.repo.proximo(agora + timedelta(minutes=47)))
        self.assertEqual(self.linha(pedido.id), ("erro", 3, "O worker não terminou depois de 3 tentativas"))
        self.assertIsNotNone(self.conn.execute("SELECT terminado_em FROM pedido_ia").fetchone()[0])

    def test_pendente_passa_antes_do_preso(self):
        preso, _ = self.repo.criar(URL_A, "compatibilidade")
        agora = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.repo.proximo(agora)
        novo, _ = self.repo.criar(URL_B, "compatibilidade")

        depois = agora + timedelta(hours=1)
        self.assertEqual(self.repo.proximo(depois).id, novo.id)
        self.assertEqual(self.repo.proximo(depois).id, preso.id)

    def test_concluir_e_falhar_so_com_rodando_e_a_tentativa_atual(self):
        pedido, _ = self.repo.criar(URL_A, "compatibilidade")
        self.assertIsNone(self.repo.concluir(pedido.id, 1, RESULTADO, "compatibilidade-v1", "m", 55, "média"))
        self.repo.proximo()
        self.assertIsNone(self.repo.concluir(pedido.id, 2, RESULTADO, "compatibilidade-v1", "m", 55, "média"))
        self.assertIsNone(self.repo.falhar(pedido.id, 2, "x"))
        self.assertIsNone(self.repo.concluir(2**70, 1, RESULTADO, "compatibilidade-v1", "m"))

        pronto = self.repo.concluir(pedido.id, 1, RESULTADO, "compatibilidade-v1", "claude-x", 55, "média")

        self.assertEqual((pronto.estado.value, pronto.nota, pronto.faixa.value, pronto.modelo), ("pronto", 55, "média", "claude-x"))
        self.assertEqual(pronto.resultado, RESULTADO)  # JSONB de ida e volta
        self.assertIsNotNone(pronto.terminado_em)
        self.assertIsNone(self.repo.falhar(pedido.id, 1, "x"))

    def test_falhar_corta_o_erro_em_500(self):
        pedido, _ = self.repo.criar(URL_A, "compatibilidade")
        self.repo.proximo()

        falho = self.repo.falhar(pedido.id, 1, "e" * 600, "compatibilidade-v1", "m")

        self.assertEqual(falho.erro, "e" * 500)
        self.assertEqual(falho.estado.value, "erro")

    def test_checks_recusam_pronto_sem_resultado_e_faixa_invalida(self):
        self.conn.execute("INSERT INTO pedido_ia (vaga_url, tipo) VALUES (%s, 'compatibilidade')", (URL_A,))
        recusados = (
            "UPDATE pedido_ia SET estado = 'pronto', prompt_versao = 'compatibilidade-v1'",  # sem resultado
            "UPDATE pedido_ia SET estado = 'pronto', resultado = '{}'",  # sem prompt_versao
            "UPDATE pedido_ia SET faixa = 'media'",  # sem acento
            "UPDATE pedido_ia SET faixa = 'ALTA'",
            "UPDATE pedido_ia SET nota = 101",
            "UPDATE pedido_ia SET estado = 'rodando'",  # sem reivindicado_em
            "UPDATE pedido_ia SET estado = 'erro'",  # sem texto de erro
            "UPDATE pedido_ia SET estado = 'cancelado'",
            "UPDATE pedido_ia SET tentativas = 4",
            "UPDATE pedido_ia SET resultado = '[]'",  # não é objeto
            "UPDATE pedido_ia SET prompt_versao = 'compatibilidade'",
            "UPDATE pedido_ia SET erro = repeat('x', 501)",
            "UPDATE pedido_ia SET tipo = 'outro'",
        )
        for comando in recusados:
            with self.subTest(comando=comando), self.assertRaises(self.errors.CheckViolation):
                self.conn.execute(comando)
        self.assertEqual(self.conn.execute("SELECT estado FROM pedido_ia").fetchone()[0], "pendente")

    def test_curriculo_id_tem_chave_estrangeira(self):
        self.conn.execute("INSERT INTO pedido_ia (vaga_url, tipo) VALUES (%s, 'curriculo')", (URL_A,))
        with self.assertRaises(self.errors.ForeignKeyViolation):
            self.conn.execute("UPDATE pedido_ia SET curriculo_id = 999")

    def test_listar_prontos_compatibilidade_por_versao(self):
        for url in (URL_A, URL_B):
            self.repo.criar(url, "compatibilidade")
        self.repo.criar(URL_A, "curriculo")
        for versao in ("compatibilidade-v1", "compatibilidade-v2"):
            p = self.repo.proximo()
            self.repo.concluir(p.id, 1, RESULTADO, versao, "m", 55, "média")
        p = self.repo.proximo()
        self.repo.concluir(p.id, 1, {"curriculo_id": 1}, "curriculo-v1", "m")

        self.assertEqual(len(self.repo.listar_prontos_compatibilidade()), 2)
        self.assertEqual([p.prompt_versao for p in self.repo.listar_prontos_compatibilidade("compatibilidade-v2")], ["compatibilidade-v2"])

    def test_rotulo_grava_troca_apaga_e_recusa_grafia_sem_acento(self):
        from src.model.erros import DadoInvalido

        self.repo.gravar_rotulo(URL_A, "alta")
        self.repo.gravar_rotulo(URL_A, "média")
        self.repo.gravar_rotulo(URL_B, "baixa")
        self.assertEqual(self.repo.obter_rotulo(URL_A).value, "média")
        self.assertEqual(self.repo.listar_rotulos(), {URL_A: "média", URL_B: "baixa"})
        self.repo.gravar_rotulo(URL_A, None)
        self.assertIsNone(self.repo.obter_rotulo(URL_A))
        with self.assertRaises(DadoInvalido):
            self.repo.gravar_rotulo(URL_B, "media")
        with self.assertRaises(self.errors.CheckViolation):
            self.conn.execute("UPDATE rotulo_ia SET faixa = 'media'")

    def concluir_compat(self, base_id, versao, nota, faixa, url=URL_A):
        p, _ = self.repo.criar(url, "compatibilidade", curriculo_base_id=base_id)
        r = self.repo.proximo()
        self.repo.concluir(r.id, r.tentativas, {**RESULTADO, "nota": nota, "faixa": faixa}, versao, "m", nota, faixa)
        return p

    def base(self, titulo="Base"):
        from src.model.curriculo import Curriculo
        from src.repository.postgres import CurriculoRepositoryPostgres

        return CurriculoRepositoryPostgres(self.conn).salvar(Curriculo(tipo="base", titulo=titulo, conteudo_md="# Base"))

    def test_clique_da_pessoa_passa_na_frente_do_lote_e_promove_o_ativo_do_lote(self):
        lote_a, _ = self.repo.criar(URL_A, "compatibilidade", prioridade=1)
        lote_b, _ = self.repo.criar(URL_B, "compatibilidade", prioridade=1)
        self.conn.execute("UPDATE pedido_ia SET criado_em = now() - interval '1 hour'")

        promovido, criado = self.repo.criar(URL_B, "compatibilidade")
        self.assertEqual((promovido.id, criado, promovido.prioridade), (lote_b.id, False, 0))
        # O lote não rebaixa o pedido da pessoa.
        self.assertEqual(self.repo.criar(URL_B, "compatibilidade", prioridade=1)[0].prioridade, 0)
        self.assertEqual([self.repo.proximo().id, self.repo.proximo().id], [lote_b.id, lote_a.id])

    def test_coluna_prioridade_so_aceita_0_e_1_e_base_tem_chave_estrangeira(self):
        with self.assertRaises(self.errors.CheckViolation):
            self.conn.execute("INSERT INTO pedido_ia (vaga_url, tipo, prioridade) VALUES (%s, 'curriculo', 2)", (URL_A,))
        with self.assertRaises(self.errors.ForeignKeyViolation):
            self.conn.execute(
                "INSERT INTO pedido_ia (vaga_url, tipo, curriculo_base_id) VALUES (%s, 'curriculo', 999999)", (URL_A,)
            )

    def test_notas_so_da_base_e_da_versao_e_a_mais_recente_numa_consulta(self):
        from src.model.pedido_ia import Faixa, NotaIA

        b1, b2 = self.base("B1").id, self.base("B2").id
        self.concluir_compat(b1, "compatibilidade-v1", 30, "baixa")
        self.concluir_compat(b1, "compatibilidade-v1", 80, "alta")
        self.concluir_compat(b2, "compatibilidade-v1", 50, "média")
        self.concluir_compat(b1, "compatibilidade-v2", 60, "média")
        self.concluir_compat(b1, "compatibilidade-v1", 20, "baixa", URL_B)

        self.assertEqual(
            self.repo.notas_compatibilidade(b1, "compatibilidade-v1"),
            {URL_A: NotaIA(80, Faixa.ALTA), URL_B: NotaIA(20, Faixa.BAIXA)},
        )
        self.assertEqual(self.repo.notas_compatibilidade(b2, "compatibilidade-v1"), {URL_A: NotaIA(50, Faixa.MEDIA)})
        self.assertEqual(self.repo.notas_compatibilidade(b1, None)[URL_A], NotaIA(60, Faixa.MEDIA))
        self.assertEqual(self.repo.versoes_compatibilidade(), {"compatibilidade-v1", "compatibilidade-v2"})

    def test_contar_lote_desde_e_urls_ativas(self):
        self.repo.criar(URL_A, "compatibilidade", prioridade=1)
        self.repo.criar(URL_B, "compatibilidade", prioridade=1)
        self.repo.criar(URL_A, "curriculo")
        self.conn.execute("UPDATE pedido_ia SET criado_em = now() - interval '25 hours' WHERE vaga_url = %s", (URL_B,))

        agora = self.conn.execute("SELECT now()").fetchone()[0]
        self.assertEqual(self.repo.contar_lote_desde(agora - timedelta(hours=24)), 1)
        self.assertEqual(self.repo.urls_ativas("compatibilidade"), {URL_A, URL_B})
        self.assertEqual(self.repo.urls_ativas("curriculo"), {URL_A})

    def test_api_ponta_a_ponta_pelo_pool(self):
        from fastapi.testclient import TestClient

        from src.controller.api import _PorChamada, criar_app
        from src.model.curriculo import Curriculo
        from src.repository.pedido_ia_postgres import PedidoIARepositoryPostgres
        from src.repository.postgres import (
            CurriculoRepositoryPostgres,
            PerfilBuscaRepositoryPostgres,
            VagaEncontradaRepositoryPostgres,
        )
        from src.service.busca_service import BuscaService
        from tests.test_candidaturas import _pool

        pool = _pool(self, self.schema)
        CurriculoRepositoryPostgres(self.conn).salvar(
            Curriculo(tipo="base", titulo="Base", conteudo_md="# Base")
        )
        app = criar_app(
            _PorChamada(lambda: pool, lambda: PerfilBuscaRepositoryPostgres),
            _PorChamada(lambda: pool, lambda: CurriculoRepositoryPostgres),
            BuscaService([]),
            encontradas=_PorChamada(lambda: pool, lambda: VagaEncontradaRepositoryPostgres),
            pedidos_ia=_PorChamada(lambda: pool, lambda: PedidoIARepositoryPostgres),
        )
        api = TestClient(app, raise_server_exceptions=False)
        auth = {"Authorization": "Bearer tok"}
        with mock.patch.dict(os.environ, {"VAGAS_IA_TOKEN": "tok"}):
            pedido, _ = app.state.pedido_ia_service.pedir(URL_A, "compatibilidade")
            recebido = api.post("/pedidos-ia/proximo", json={}, headers=auth).json()
            self.assertEqual((recebido["id"], recebido["tentativa"]), (pedido.id, 1))
            corpo = {"tentativa": 1, "estado": "pronto", "prompt_versao": "compatibilidade-v1", "modelo": "m", "resultado": RESULTADO}
            self.assertEqual(api.put(f"/pedidos-ia/{pedido.id}", json=corpo, headers=auth).status_code, 200)
            self.assertEqual(api.put(f"/pedidos-ia/{pedido.id}", json=corpo, headers=auth).status_code, 409)
            self.assertEqual(api.post("/pedidos-ia/proximo", json={}, headers=auth).status_code, 204)
        self.assertEqual(api.put("/vagas/rotulo-ia", json={"vaga_url": URL_A, "faixa": "média"}).status_code, 200)
        self.assertEqual(app.state.pedido_ia_service.obter(pedido.id).nota, 55)


if __name__ == "__main__":
    unittest.main()
