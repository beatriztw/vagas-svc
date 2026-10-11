import unittest
from datetime import date

from src.model.perfil import Perfil
from src.model.vaga import Modalidade
from src.model.vaga_externa import VagaExterna
from src.service.busca_service import BuscaService
from src.service.recomendacao_service import RecomendacaoService, aplicar_perfil
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
