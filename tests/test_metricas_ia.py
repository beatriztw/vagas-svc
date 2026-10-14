"""Métricas de acurácia da IA: funções puras e a rota GET /ia/metricas.

Pedidos e notas daqui são inventados e escritos à mão.
"""

import unittest

from fastapi.testclient import TestClient

from src.controller.api import criar_app
from src.model.pedido_ia import EstadoPedidoIA, Faixa, PedidoIA, TipoPedidoIA
from src.repository.base import CurriculoRepositoryMemoria, PerfilBuscaRepositoryMemoria
from src.repository.pedido_ia import PedidoIARepositoryMemoria
from src.service import metricas_ia as m
from src.service.busca_service import BuscaService

V1 = "compatibilidade-v1"
V2 = "compatibilidade-v2"


def faixa_da(nota: int) -> Faixa:
    return Faixa.ALTA if nota >= 70 else Faixa.MEDIA if nota >= 40 else Faixa.BAIXA


def pronto(id, url, nota, versao=V1) -> PedidoIA:
    return PedidoIA(
        id, TipoPedidoIA.COMPATIBILIDADE, url, EstadoPedidoIA.PRONTO,
        resultado={}, prompt_versao=versao, nota=nota, faixa=faixa_da(nota),
    )


class FuncoesPurasTest(unittest.TestCase):
    def test_ordinal(self):
        self.assertEqual([m.ordinal("baixa"), m.ordinal("média"), m.ordinal(Faixa.ALTA)], [0, 1, 2])
        with self.assertRaises(ValueError):
            m.ordinal("media")

    def test_postos_medios_nos_empates(self):
        self.assertEqual(m.postos([10, 20, 20, 30]), [1, 2.5, 2.5, 4])
        self.assertEqual(m.postos([30, 10, 20]), [3, 1, 2])

    def test_spearman(self):
        self.assertEqual(m.spearman([1, 2, 3, 4], [10, 20, 30, 40]), 1.0)
        self.assertEqual(m.spearman([1, 2, 3, 4], [40, 30, 20, 10]), -1.0)
        self.assertIsNone(m.spearman([1, 2], [1, 2]))
        self.assertIsNone(m.spearman([5, 5, 5], [1, 2, 3]))
        self.assertIsNone(m.spearman([1, 2, 3], [7, 7, 7]))
        self.assertEqual(m.spearman([1, 2, 3, 4], [1, 3, 2, 4]), 0.8)

    def test_concordancia_matriz_completa_e_taxa(self):
        c = m.concordancia([("alta", "alta"), ("alta", "média"), ("baixa", "baixa"), ("média", "média")])
        self.assertEqual((c["n"], c["taxa"]), (4, 0.75))
        self.assertEqual(c["matriz"]["alta"], {"alta": 1, "média": 1, "baixa": 0})
        self.assertEqual(set(c["matriz"]), {"alta", "média", "baixa"})
        self.assertTrue(all(set(l) == {"alta", "média", "baixa"} for l in c["matriz"].values()))
        vazio = m.concordancia([])
        self.assertEqual((vazio["n"], vazio["taxa"]), (0, None))
        self.assertEqual(sum(sum(l.values()) for l in vazio["matriz"].values()), 0)

    def test_estabilidade_reexecucoes(self):
        e = m.estabilidade({"a": [pronto(1, "a", 60), pronto(2, "a", 70), pronto(3, "a", 65)]})
        self.assertEqual(
            e, {"vagas": 1, "desvio_nota_medio": 4.0825, "amplitude_nota_max": 10, "faixa_igual_taxa": 0.0}
        )

    def test_estabilidade_faixa_sempre_igual_e_vaga_com_um_pronto_fica_fora(self):
        e = m.estabilidade({
            "a": [pronto(1, "a", 50), pronto(2, "a", 55)],
            "b": [pronto(3, "b", 10)],
        })
        self.assertEqual((e["vagas"], e["faixa_igual_taxa"], e["amplitude_nota_max"]), (1, 1.0, 5))
        self.assertEqual(m.estabilidade({"b": [pronto(3, "b", 10)]}),
                         {"vagas": 0, "desvio_nota_medio": None, "amplitude_nota_max": None, "faixa_igual_taxa": None})

    def test_calcular_so_o_pronto_mais_recente_e_ignora_outra_versao(self):
        prontos = [
            pronto(1, "a", 20), pronto(2, "a", 90),  # a: conta o 2 (alta)
            pronto(3, "b", 10), pronto(4, "b", 80, V2),  # b: v2 ignorada
            pronto(5, "c", 50),
        ]
        r = m.calcular(prontos, {"a": "alta", "b": "baixa", "c": "alta"}, V1)
        self.assertEqual((r["pedidos_prontos"], r["vagas_com_resultado"], r["rotuladas"]), (4, 3, 3))
        self.assertEqual(r["concordancia_faixa"]["matriz"]["alta"], {"alta": 1, "média": 1, "baixa": 0})
        self.assertEqual(r["concordancia_faixa"]["matriz"]["baixa"]["baixa"], 1)
        self.assertEqual(r["concordancia_faixa"]["taxa"], 0.6667)
        self.assertEqual(r["correlacao_ordem"]["n"], 3)
        self.assertEqual(r["estabilidade"]["vagas"], 1)  # só a tem 2 prontos da v1

    def test_calcular_sem_prontos(self):
        r = m.calcular([], {}, None)
        self.assertEqual(r["prompt_versao"], None)
        self.assertEqual((r["pedidos_prontos"], r["vagas_com_resultado"], r["rotuladas"]), (0, 0, 0))
        self.assertIsNone(r["correlacao_ordem"]["spearman"])

    def test_versao_mais_recente_compara_como_numero(self):
        ps = [pronto(1, "a", 50, "compatibilidade-v9"), pronto(2, "a", 50, "compatibilidade-v10")]
        self.assertEqual(m.versao_mais_recente(ps), "compatibilidade-v10")
        self.assertIsNone(m.versao_mais_recente([]))

    def test_versao_com_digitos_demais_e_ignorada_sem_estourar(self):
        # int() recusa string com mais de 4300 dígitos; a versão gigante nem entra na conta.
        gigante = "compatibilidade-v" + "9" * 5000
        ps = [pronto(1, "a", 50, gigante), pronto(2, "a", 50, "compatibilidade-v2")]
        self.assertEqual(m.versao_mais_recente(ps), "compatibilidade-v2")


class RotaMetricasTest(unittest.TestCase):
    def setUp(self):
        self.repo = PedidoIARepositoryMemoria()
        self.client = TestClient(criar_app(
            PerfilBuscaRepositoryMemoria(), CurriculoRepositoryMemoria(), BuscaService([]), pedidos_ia=self.repo
        ))

    def _pronto(self, url, nota, versao=V1):
        self.repo.criar(url, "compatibilidade")
        p = self.repo.proximo()
        self.repo.concluir(p.id, p.tentativas, {"nota": nota}, versao, "m", nota, faixa_da(nota))

    def test_sem_nenhum_pronto(self):
        r = self.client.get("/ia/metricas")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertIsNone(r.json()["prompt_versao"])
        self.assertEqual(r.json()["pedidos_prontos"], 0)

    def test_chaves_exatas_e_selecao_automatica_da_versao(self):
        self._pronto("https://exemplo.com/a", 80, V1)
        self._pronto("https://exemplo.com/b", 30, V2)
        self.repo.gravar_rotulo("https://exemplo.com/b", "baixa")
        corpo = self.client.get("/ia/metricas").json()
        self.assertEqual(
            set(corpo),
            {"prompt_versao", "pedidos_prontos", "vagas_com_resultado", "rotuladas",
             "concordancia_faixa", "correlacao_ordem", "estabilidade"},
        )
        self.assertEqual(set(corpo["concordancia_faixa"]), {"n", "taxa", "matriz"})
        self.assertEqual(set(corpo["correlacao_ordem"]), {"n", "spearman"})
        self.assertEqual(set(corpo["estabilidade"]),
                         {"vagas", "desvio_nota_medio", "amplitude_nota_max", "faixa_igual_taxa"})
        self.assertEqual((corpo["prompt_versao"], corpo["pedidos_prontos"], corpo["rotuladas"]), (V2, 1, 1))
        self.assertEqual(self.client.get("/ia/metricas", params={"prompt_versao": V1}).json()["prompt_versao"], V1)

    def test_versao_invalida_e_422(self):
        for ruim in ("curriculo-v1", "compatibilidade-v", "compatibilidade-v1x", "", "x", "compatibilidade-v" + "9" * 7):
            self.assertEqual(
                self.client.get("/ia/metricas", params={"prompt_versao": ruim}).status_code, 422, ruim
            )


if __name__ == "__main__":
    unittest.main()
