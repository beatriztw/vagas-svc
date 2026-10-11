import unittest
from datetime import date

from src.client.base import ProviderClient
from src.model.vaga_externa import VagaExterna
from src.service.busca_service import BuscaService


def vaga(url: str, publicada_em: date | None = None, plataforma: str = "A") -> VagaExterna:
    return VagaExterna(titulo="Dev", empresa="X", plataforma=plataforma, url=url, publicada_em=publicada_em)


class ClientFixo(ProviderClient):
    def __init__(self, nome: str, vagas: list[VagaExterna]):
        self.nome = nome
        self.vagas = vagas

    def buscar(self, termo):
        return self.vagas


class ClientQuebrado(ProviderClient):
    nome = "Quebrado"

    def buscar(self, termo):
        raise TimeoutError("sem resposta")


class BuscaServiceTest(unittest.TestCase):
    def test_junta_remove_duplicadas_e_ordena_mais_recentes_primeiro(self):
        service = BuscaService([
            ClientFixo("A", [vaga("u1", date(2026, 1, 1)), vaga("u2")]),
            ClientFixo("B", [vaga("u1", date(2026, 5, 1), "B"), vaga("u3", date(2026, 3, 1), "B")]),
        ])

        resultado = service.buscar("dev")

        self.assertEqual([v.url for v in resultado.vagas], ["u3", "u1", "u2"])
        self.assertEqual(resultado.vagas[1].plataforma, "A")
        self.assertEqual(resultado.erros, {})

    def test_falha_de_um_provider_nao_interrompe_os_outros(self):
        service = BuscaService([ClientQuebrado(), ClientFixo("A", [vaga("u1")])])

        resultado = service.buscar("dev")

        self.assertEqual([v.url for v in resultado.vagas], ["u1"])
        self.assertEqual(resultado.erros, {"Quebrado": "TimeoutError: sem resposta"})


if __name__ == "__main__":
    unittest.main()
