import unittest
from datetime import date

from src.model.perfil import Perfil
from src.model.vaga import Modalidade, Nivel
from src.model.vaga_externa import VagaExterna
from src.service.busca_service import BuscaService
from src.service.recomendacao_service import RecomendacaoService, aplicar_perfil, nivel_do_titulo, titulo_combina
from tests.test_busca_service import ClientFixo, ClientQuebrado

HOJE = date(2026, 10, 11)


def vaga(titulo="Dev Python", url=None, remoto=None, local=None, publicada_em=None) -> VagaExterna:
    return VagaExterna(
        titulo=titulo, empresa="X", plataforma="A", url=url or titulo, local=local, remoto=remoto, publicada_em=publicada_em
    )


class AplicarPerfilTest(unittest.TestCase):
    def test_exclui_termo_no_titulo_sem_acento_nem_caixa(self):
        vagas = [vaga("Desenvolvedor SÊNIOR"), vaga("Dev Júnior"), vaga("Estágio em dados")]

        filtradas = aplicar_perfil(vagas, Perfil(excluir=["senior", "ESTAGIO"]), HOJE)

        self.assertEqual([v.titulo for v in filtradas], ["Dev Júnior"])


    def test_termo_de_excluir_vazio_depois_de_normalizado_e_ignorado(self):
        vagas = [vaga("Analista de Dados"), vaga("Analista Fiscal")]

        filtradas = aplicar_perfil(vagas, Perfil(excluir=["", "  ", "—", "🚫"]), HOJE)

        self.assertEqual(filtradas, vagas)

    def test_so_remoto_mantem_remotas_e_as_sem_modalidade_com_local_remoto(self):
        vagas = [
            vaga("a", remoto=True),
            vaga("b", remoto=False, local="Remote"),
            vaga("c", remoto=None, local="Remoto - Brasil"),
            vaga("d", remoto=None, local="São Paulo"),
            vaga("e", remoto=None),
        ]

        filtradas = aplicar_perfil(vagas, Perfil(modalidades=[Modalidade.REMOTO]), HOJE)

        self.assertEqual([v.titulo for v in filtradas], ["a", "c"])


    def test_so_remotas_com_modalidade_repetida(self):
        vagas = [vaga("Remota", url="u1", remoto=True), vaga("Presencial", url="u2", remoto=False)]

        filtradas = aplicar_perfil(vagas, Perfil(modalidades=[Modalidade.REMOTO, Modalidade.REMOTO]), HOJE)

        self.assertEqual([v.titulo for v in filtradas], ["Remota"])
    def test_remoto_e_hibrido_nao_filtra_modalidade(self):
        vagas = [vaga("a", remoto=True), vaga("b", remoto=False)]

        filtradas = aplicar_perfil(vagas, Perfil(modalidades=[Modalidade.REMOTO, Modalidade.HIBRIDO]), HOJE)

        self.assertEqual(len(filtradas), 2)

    def test_publicada_desde_dias_corta_antigas_e_mantem_sem_data(self):
        vagas = [
            vaga("limite", publicada_em=date(2026, 10, 4)),
            vaga("antiga", publicada_em=date(2026, 10, 3)),
            vaga("sem data"),
        ]

        filtradas = aplicar_perfil(vagas, Perfil(publicada_desde_dias=7), HOJE)

        self.assertEqual([v.titulo for v in filtradas], ["limite", "sem data"])


class RecomendacaoServiceTest(unittest.TestCase):
    def test_aplica_perfil_e_preserva_erros_dos_providers(self):
        busca = BuscaService([ClientQuebrado(), ClientFixo("A", [vaga("Dev Sênior"), vaga("Dev Pleno")])])

        resultado = RecomendacaoService(busca).recomendar("dev", Perfil(excluir=["senior"]), HOJE)

        self.assertEqual([v.titulo for v in resultado.vagas], ["Dev Pleno"])
        self.assertEqual(resultado.erros, {"Quebrado": "TimeoutError: sem resposta"})

    def test_sem_perfil_devolve_a_busca_inteira(self):
        busca = BuscaService([ClientFixo("A", [vaga("Dev Sênior")])])

        resultado = RecomendacaoService(busca).recomendar("dev", None, HOJE)

        self.assertEqual(len(resultado.vagas), 1)


class TituloCombinaTest(unittest.TestCase):
    def test_todas_as_palavras_do_termo_fora_as_de_ligacao(self):
        self.assertTrue(titulo_combina("Analista de Dados Pleno", "analista de dados"))
        self.assertTrue(titulo_combina("Dados: Analista Jr", "analista de dados"))
        self.assertFalse(titulo_combina("Analista de QA", "analista de dados"))
        self.assertFalse(titulo_combina("Analista de BI", "analista de dados"))

    def test_sem_acento_nem_caixa(self):
        self.assertTrue(titulo_combina("ANALISTA DE CONCILIAÇÃO", "analista de conciliacao"))

    def test_recomendar_corta_titulo_que_nao_combina_mesmo_sem_perfil(self):
        busca = BuscaService([ClientFixo("A", [vaga("Analista de Dados", url="u1"), vaga("Analista de QA", url="u2")])])

        resultado = RecomendacaoService(busca).recomendar("analista de dados", None, HOJE)

        self.assertEqual([v.titulo for v in resultado.vagas], ["Analista de Dados"])


class NivelTest(unittest.TestCase):
    def test_nivel_pelo_titulo(self):
        self.assertEqual(nivel_do_titulo("Analista de Dados Jr"), Nivel.JUNIOR)
        self.assertEqual(nivel_do_titulo("Analista de Custos Pl."), Nivel.PLENO)
        self.assertEqual(nivel_do_titulo("Analista SÊNIOR"), Nivel.SENIOR)
        self.assertIsNone(nivel_do_titulo("Analista de Dados"))
        self.assertIsNone(nivel_do_titulo("Analista Pleno/Sênior"))

    def test_corta_nivel_fora_do_perfil_e_mantem_os_sem_nivel(self):
        vagas = [vaga("Analista Júnior"), vaga("Analista Sênior"), vaga("Analista"), vaga("Analista Pleno/Sênior")]

        filtradas = aplicar_perfil(vagas, Perfil(niveis=[Nivel.JUNIOR]), HOJE)

        self.assertEqual([v.titulo for v in filtradas], ["Analista Júnior", "Analista", "Analista Pleno/Sênior"])


class LocalidadeTest(unittest.TestCase):
    def test_mantem_a_cidade_as_remotas_e_as_sem_local(self):
        vagas = [
            vaga("a", local="Rio de Janeiro, RJ", remoto=False),
            vaga("b", local="Rio de Janeiro, Rio de Janeiro, Brasil"),
            vaga("c", local="São Paulo, SP", remoto=True),
            vaga("d", local="Remoto - Brasil"),
            vaga("e"),
            vaga("f", local="São Paulo, SP", remoto=False),
            vaga("g", local="Barueri, SP"),
        ]

        filtradas = aplicar_perfil(vagas, Perfil(localidades=["Rio de Janeiro, RJ"]), HOJE)

        self.assertEqual([v.titulo for v in filtradas], ["a", "b", "c", "d", "e"])

    def test_sem_localidade_no_perfil_nada_e_cortado(self):
        vagas = [vaga("f", local="São Paulo, SP", remoto=False)]

        self.assertEqual(aplicar_perfil(vagas, Perfil(), HOJE), vagas)


class OrdenacaoPorSkillsTest(unittest.TestCase):
    def test_mais_skills_no_titulo_primeiro_e_empate_mantem_a_ordem(self):
        vagas = [vaga("Analista 1"), vaga("Analista Power BI"), vaga("Analista 2"), vaga("Analista SQL e Power BI")]

        filtradas = aplicar_perfil(vagas, Perfil(skills=["SQL", "Power BI", ""]), HOJE)

        self.assertEqual(
            [v.titulo for v in filtradas], ["Analista SQL e Power BI", "Analista Power BI", "Analista 1", "Analista 2"]
        )

