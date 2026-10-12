"""Detalhe e expiração (migration 004) na memória e no Postgres de verdade (PGHOST), com o mesmo contrato.

Vagas escritas à mão (não são respostas gravadas de provider).
"""

import os
import unittest
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.model.detalhe_vaga import DetalheVaga, Requisito, Salario
from src.model.vaga_externa import VagaExterna
from src.model.perfil import Perfil
from src.repository.base import PerfilBuscaRepositoryMemoria, VagaEncontradaRepositoryMemoria
from src.service.historico_service import HistoricoService
from tests.test_coleta import _schema_descartavel

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
A = "https://remotar.com.br/job/1"
B = "https://remotar.com.br/job/2"
DETALHE = DetalheVaga(
    descricao="Linha 1\nLinha 2 – ação",
    requisitos=[Requisito("SQL", True), Requisito("Python", False)],
    salario=Salario(350000, 450000, "BRL", "monthly"),
    beneficios=["Vale-refeição"],
    subtitulo="Resumo",
)


def vaga(url: str, **kw) -> VagaExterna:
    return VagaExterna("Analista de Dados", "Empresa", "Remotar", url, **kw)


class ContratoDetalhe:
    repo = None

    def agora(self) -> datetime:
        raise NotImplementedError

    def passar_tempo(self, segundos: float) -> None:
        raise NotImplementedError

    def test_gravar_detalhe_e_obter_devolve_tudo(self):
        self.repo.registrar([vaga(A)], "dados")
        self.assertTrue(self.repo.gravar_detalhe(A, DETALHE))
        lida = self.repo.obter(A)
        self.assertEqual(lida.descricao, DETALHE.descricao)
        self.assertEqual(lida.requisitos, DETALHE.requisitos)
        self.assertEqual(lida.salario, DETALHE.salario)
        self.assertEqual(lida.beneficios, DETALHE.beneficios)
        self.assertEqual(lida.subtitulo, "Resumo")
        self.assertIsNotNone(lida.detalhada_em)
        self.assertIsNone(lida.expirada_em)

    def test_detalhe_sem_salario_e_sem_listas(self):
        self.repo.registrar([vaga(A)], "dados")
        self.repo.gravar_detalhe(A, DetalheVaga(descricao="x"))
        lida = self.repo.obter(A)
        self.assertEqual((lida.salario, lida.requisitos, lida.beneficios), (None, [], []))

    def test_gravar_detalhe_nao_mexe_em_vista_nem_primeira_vez(self):
        self.repo.registrar([vaga(A)], "dados")
        self.repo.marcar_vistas([A])
        antes = self.repo.obter(A)
        self.repo.gravar_detalhe(A, DETALHE)
        depois = self.repo.obter(A)
        self.assertEqual((depois.vista_em, depois.primeira_vez_em), (antes.vista_em, antes.primeira_vez_em))

    def test_url_inexistente_devolve_false(self):
        self.assertFalse(self.repo.gravar_detalhe(A, DETALHE))
        self.assertFalse(self.repo.marcar_expirada(A))

    def test_detalhe_expirado_marca_expirada_em(self):
        self.repo.registrar([vaga(A)], "dados")
        self.repo.gravar_detalhe(A, DetalheVaga(descricao="x", expirada_em=self.agora()))
        self.assertIsNotNone(self.repo.obter(A).expirada_em)
        self.assertEqual(self.repo.contar(False), 0)

    def test_expirada_some_de_listar_contar_e_listar_recentes_mas_obter_acha(self):
        self.repo.registrar([vaga(A), vaga(B)], "dados")
        self.repo.marcar_expirada(A)
        for novas in (False, True):
            self.assertEqual([v.url for v in self.repo.listar(novas, 10)], [B])
            self.assertEqual(self.repo.contar(novas), 1)
            self.assertEqual([v.url for v in self.repo.listar_recentes(novas, 10)], [B])
        self.assertEqual(self.repo.contar(False, q="analista"), 1)
        self.assertEqual([v.url for v in self.repo.listar(False, 10, q="analista")], [B])
        self.assertIsNotNone(self.repo.obter(A).expirada_em)

    def test_registrar_grava_o_detalhe_da_listagem_e_o_proximo_registrar_atualiza(self):
        self.repo.registrar([vaga(A, detalhe=DETALHE)], "dados")
        lida = self.repo.obter(A)
        self.assertEqual((lida.descricao, lida.requisitos, lida.salario), (DETALHE.descricao, DETALHE.requisitos, DETALHE.salario))
        self.assertIsNotNone(lida.detalhada_em)
        # A listagem de novo, com o texto mudado: sobrescreve.
        self.repo.registrar([vaga(A, detalhe=DetalheVaga(descricao="nova", beneficios=["VR"]))], "dados")
        lida = self.repo.obter(A)
        self.assertEqual((lida.descricao, lida.requisitos, lida.salario, lida.beneficios), ("nova", [], None, ["VR"]))
        # Vaga de outro provider (sem detalhe) não apaga o que já está gravado.
        self.repo.registrar([vaga(A)], "outro")
        self.assertEqual(self.repo.obter(A).descricao, "nova")
        self.assertIsNotNone(self.repo.obter(A).detalhada_em)

    def test_detalhe_estranho_na_listagem_descarta_so_o_detalhe(self):
        torto = DetalheVaga(
            descricao="a" * 20_001,
            requisitos=[Requisito("SQL", True), "nao e requisito", Requisito("x\x00y", True)],
            beneficios=["VR", None, 5],
            subtitulo="s" * 501,
            salario=Salario(-1, True),
        )
        self.assertEqual(self.repo.registrar([vaga(A, detalhe=torto), vaga(B, detalhe="texto")], "dados"), 2)
        lida = self.repo.obter(A)
        self.assertEqual((lida.descricao, lida.subtitulo, lida.salario), (None, None, None))
        self.assertEqual((lida.requisitos, lida.beneficios), ([Requisito("SQL", True)], ["VR"]))
        self.assertIsNone(self.repo.obter(B).descricao)

    def test_prazo_futuro_aparece_hoje_e_some_depois_do_prazo_sem_nova_coleta(self):
        self.repo.registrar([vaga(A, expirada_em=self.agora() + timedelta(seconds=2)), vaga(B)], "dados")
        self.assertEqual(self.repo.contar(False), 2)
        self.assertEqual(len(self.repo.listar(False, 10)), 2)
        self.assertEqual(len(self.repo.listar_recentes(False, 10)), 2)
        self.passar_tempo(3)
        self.assertEqual([v.url for v in self.repo.listar(False, 10)], [B])
        self.assertEqual(self.repo.contar(False), 1)
        self.assertEqual([v.url for v in self.repo.listar_recentes(False, 10)], [B])
        self.assertIsNotNone(self.repo.obter(A))

    def test_prazo_futuro_novo_nao_ressuscita_a_vaga_ja_expirada(self):
        self.repo.registrar([vaga(A, expirada_em=self.agora() - timedelta(days=1))], "dados")
        self.repo.registrar([vaga(A, expirada_em=self.agora() + timedelta(days=9))], "dados")
        self.assertEqual(self.repo.contar(False), 0)

    def test_prazo_futuro_pode_mudar_na_proxima_coleta(self):
        self.repo.registrar([vaga(A, expirada_em=self.agora() + timedelta(days=1))], "dados")
        novo = self.agora() + timedelta(days=9)
        self.repo.registrar([vaga(A, expirada_em=novo)], "dados")
        self.assertEqual(self.repo.obter(A).expirada_em, novo)

    def test_provider_sem_prazo_remove_o_prazo_futuro_e_a_vaga_segue_no_ar(self):
        # O anunciante tirou o prazo: a coleta seguinte traz `expires: false` (tem_prazo=False).
        self.repo.registrar([vaga(A, expirada_em=self.agora() + timedelta(seconds=2), tem_prazo=True)], "dados")
        self.repo.registrar([vaga(A, tem_prazo=False)], "dados")
        self.assertIsNone(self.repo.obter(A).expirada_em)
        self.passar_tempo(3)  # depois da data antiga
        self.assertEqual([v.url for v in self.repo.listar(False, 10)], [A])
        self.assertEqual(self.repo.contar(False), 1)

    def test_detalhe_sem_prazo_remove_o_prazo_futuro(self):
        self.repo.registrar([vaga(A, expirada_em=self.agora() + timedelta(days=9), tem_prazo=True)], "dados")
        self.repo.gravar_detalhe(A, DetalheVaga(descricao="x", tem_prazo=False))
        self.assertIsNone(self.repo.obter(A).expirada_em)
        self.assertEqual(self.repo.contar(False), 1)

    def test_sem_prazo_nao_ressuscita_a_vaga_ja_expirada(self):
        quando = datetime(2026, 10, 1, tzinfo=timezone.utc)
        self.repo.registrar([vaga(A, expirada_em=quando, tem_prazo=True)], "dados")
        self.repo.registrar([vaga(A, tem_prazo=False)], "dados")
        self.repo.gravar_detalhe(A, DetalheVaga(descricao="x", tem_prazo=False))
        self.assertEqual(self.repo.obter(A).expirada_em, quando)
        self.assertEqual(self.repo.contar(False), 0)

    def test_sem_dizer_do_prazo_ou_com_valor_torto_mantem_o_prazo_futuro(self):
        prazo = self.agora() + timedelta(days=9)
        self.repo.registrar([vaga(A, expirada_em=prazo, tem_prazo=True)], "dados")
        self.repo.registrar([vaga(A)], "dados")  # tem_prazo=None: campo ausente
        self.repo.registrar([vaga(A, tem_prazo="não")], "dados")  # torto: não é bool
        self.repo.registrar([vaga(A, tem_prazo=0)], "dados")  # 0 == False, mas não é bool
        self.repo.gravar_detalhe(A, DetalheVaga(descricao="x"))
        self.repo.gravar_detalhe(A, DetalheVaga(descricao="x", tem_prazo=0))
        self.assertEqual(self.repo.obter(A).expirada_em, prazo)

    def test_404_com_prazo_futuro_expira_agora(self):
        self.repo.registrar([vaga(A, expirada_em=self.agora() + timedelta(days=9))], "dados")
        self.repo.marcar_expirada(A)
        self.assertEqual(self.repo.contar(False), 0)

    def test_marcar_expirada_guarda_a_primeira_data(self):
        self.repo.registrar([vaga(A)], "dados")
        self.repo.marcar_expirada(A)
        primeira = self.repo.obter(A).expirada_em
        self.repo.marcar_expirada(A)
        self.assertEqual(self.repo.obter(A).expirada_em, primeira)

    def test_registrar_grava_expirada_da_listagem_e_nao_ressuscita(self):
        quando = datetime(2026, 10, 1, tzinfo=timezone.utc)
        self.assertEqual(self.repo.registrar([vaga(A, expirada_em=quando), vaga(B)], "dados"), 2)
        self.assertEqual(self.repo.obter(A).expirada_em, quando)
        self.assertEqual(self.repo.contar(False), 1)
        # Nova coleta que não traz expiração não ressuscita a vaga.
        self.repo.registrar([vaga(A)], "outro")
        self.assertEqual(self.repo.obter(A).expirada_em, quando)

    def test_registrar_nao_apaga_o_detalhe_e_data_estranha_descarta_so_a_expiracao(self):
        self.repo.registrar([vaga(A)], "dados")
        self.repo.gravar_detalhe(A, DETALHE)
        self.repo.registrar([vaga(A, expirada_em="ontem"), vaga(B, expirada_em=datetime(2026, 1, 1))], "dados")  # sem fuso
        self.assertEqual(self.repo.obter(A).descricao, DETALHE.descricao)
        self.assertIsNone(self.repo.obter(A).expirada_em)
        self.assertIsNone(self.repo.obter(B).expirada_em)
        self.assertEqual(self.repo.contar(False), 2)


class ContratoHistorico:
    """A vaga expirada tem que sumir também pelo `HistoricoService.pagina` (o que a tela e a API JSON
    usam), com e sem perfil salvo. Se o B2 (foto `ate` em `listar_recentes`) entrar na main sem a
    cláusula `expirada_em IS NULL OR expirada_em > ate` no ramo da foto do Postgres, e o
    `filtrar_e_ordenar(..., agora=ate)` na memória, estes testes falham: é lá que ela precisa entrar."""

    def preparar(self, com_perfil: bool):
        C = "https://remotar.com.br/job/3"
        self.repo.registrar(
            [
                vaga(A),
                vaga(B, expirada_em=self.agora() - timedelta(days=1)),  # prazo vencido
                vaga(C),
            ],
            "dados",
        )
        self.repo.marcar_expirada(C)  # saiu do ar (404 do provider)
        perfis = PerfilBuscaRepositoryMemoria()
        if com_perfil:
            perfis.salvar(Perfil(termos=["analista de dados"]))
        return HistoricoService(self.repo, perfis)

    def test_historico_sem_perfil_esconde_expirada_e_prazo_vencido(self):
        pagina = self.preparar(False).pagina(somente_novas=False, limite=10)
        self.assertEqual([v.url for v in pagina.vagas], [A])
        self.assertEqual(pagina.total, 1)

    def test_historico_com_perfil_esconde_expirada_e_prazo_vencido(self):
        servico = self.preparar(True)
        for novas in (False, True):
            pagina = servico.pagina(somente_novas=novas, limite=10)
            self.assertEqual([v.url for v in pagina.vagas], [A])
            self.assertEqual(pagina.total, 1)
        self.assertEqual(servico.contar(False, q="analista"), 1)


class MemoriaTest(ContratoDetalhe, ContratoHistorico, unittest.TestCase):
    def setUp(self):
        self.deslocamento = timedelta(0)
        self.repo = VagaEncontradaRepositoryMemoria(relogio=self.agora)

    def agora(self) -> datetime:
        return datetime.now(timezone.utc) + self.deslocamento

    def passar_tempo(self, segundos: float) -> None:
        self.deslocamento += timedelta(seconds=segundos)  # relogio injetado: sem esperar de verdade


@unittest.skipUnless(os.environ.get("PGHOST"), "PGHOST não definido: integração com Postgres pulada")
class PostgresTest(ContratoDetalhe, ContratoHistorico, unittest.TestCase):
    def setUp(self):
        from src.repository.postgres import VagaEncontradaRepositoryPostgres

        self.conn, self.schema = _schema_descartavel(self, "teste_detalhe")
        self.repo = VagaEncontradaRepositoryPostgres(self.conn)

    def agora(self) -> datetime:
        # A hora do BANCO (é o now() do filtro), não a da máquina.
        return self.conn.execute("SELECT now()").fetchone()[0]

    def passar_tempo(self, segundos: float) -> None:
        time.sleep(segundos)

    def test_migration_004_idempotente_acrescenta_as_colunas(self):
        colunas = {
            l[0]
            for l in self.conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'vaga_encontrada' "
                "AND table_schema = current_schema()"
            ).fetchall()
        }
        self.assertTrue(
            {"descricao", "requisitos", "salario", "beneficios", "subtitulo", "expirada_em", "detalhada_em"} <= colunas
        )

    def test_linha_antiga_da_002_sobrevive_a_004(self):
        # A 004 sobre dados já existentes: a coluna nova entra NULL e a vaga continua na lista.
        self.conn.execute("DROP TABLE vaga_encontrada CASCADE")
        self.conn.execute((MIGRATIONS / "002_vaga_encontrada.sql").read_text(encoding="utf-8"))
        self.conn.execute("INSERT INTO vaga_encontrada (url, titulo, empresa, plataforma) VALUES ('u', 't', 'e', 'p')")
        self.conn.execute((MIGRATIONS / "004_detalhe_vaga.sql").read_text(encoding="utf-8"))
        self.assertEqual(self.repo.contar(False), 1)
        self.assertIsNone(self.repo.obter("u").expirada_em)

    def test_checks_recusam_forma_errada(self):
        from psycopg import errors

        self.repo.registrar([vaga(A)], "dados")
        for coluna, valor in (
            ("requisitos", "'{}'::jsonb"),
            ("beneficios", "'\"x\"'::jsonb"),
            ("salario", "'[1]'::jsonb"),
            ("descricao", "repeat('a', 20001)"),
            ("subtitulo", "repeat('a', 501)"),
        ):
            with self.subTest(coluna=coluna), self.assertRaises(errors.CheckViolation):
                self.conn.execute(f"UPDATE vaga_encontrada SET {coluna} = {valor}")

    def test_item_torto_dentro_do_jsonb_descarta_so_o_item(self):
        self.repo.registrar([vaga(A)], "dados")
        self.conn.execute(
            "UPDATE vaga_encontrada SET requisitos = '[1, {\"texto\": \"SQL\", \"obrigatorio\": true}, {\"texto\": 5}]', "
            "beneficios = '[null, \"Vale\"]', salario = '{\"minimo\": \"x\", \"maximo\": 100}'"
        )
        lida = self.repo.obter(A)
        self.assertEqual(lida.requisitos, [Requisito("SQL", True)])
        self.assertEqual(lida.beneficios, ["Vale"])
        self.assertEqual(lida.salario, Salario(None, 100, "BRL", None))

    def test_uma_conexao_do_pool_por_chamada_via_tela(self):
        """O serviço de detalhe com o `_PorChamada` de verdade e um pool de 1 conexão: não trava
        (cada chamada devolve a conexão antes da próxima) e grava no schema certo."""
        from psycopg_pool import ConnectionPool

        from src.controller.api import _PorChamada
        from src.repository.conexao import parametros
        from src.repository.postgres import VagaEncontradaRepositoryPostgres
        from src.service.detalhe_service import DetalheService

        self.repo.registrar([vaga(A)], "dados")
        pool = ConnectionPool(
            kwargs={**parametros(), "autocommit": True, "options": f"-c search_path={self.schema}"},
            min_size=1,
            max_size=1,
            open=True,
        )
        self.addCleanup(pool.close)

        class Provider:
            def detalhar(self, id):
                return DETALHE

        encontradas = _PorChamada(lambda: pool, lambda: VagaEncontradaRepositoryPostgres)
        aberta = DetalheService(encontradas, Provider()).carregar(A)
        self.assertEqual(aberta.vaga.descricao, DETALHE.descricao)
        self.assertEqual(pool.get_stats()["pool_available"], 1)


if __name__ == "__main__":
    unittest.main()
