"""Lote de compatibilidade: prioridade na fila, nota válida (base e versão) e o teto diário.

Vagas, currículos e resultados daqui são inventados e escritos à mão (não são respostas gravadas).
"""

import unittest
from datetime import datetime, timedelta, timezone

from src.model.curriculo import Curriculo
from src.model.erros import DadoInvalido
from src.model.pedido_ia import PRIORIDADE_LOTE, Faixa, NotaIA, TipoPedidoIA
from src.model.perfil import Perfil
from src.model.vaga import Modalidade
from src.model.vaga_externa import VagaExterna
from src.repository.base import (
    CurriculoRepositoryMemoria,
    PerfilBuscaRepositoryMemoria,
    VagaEncontradaRepositoryMemoria,
)
from src.repository.candidatura import vaga_por_url_em
from src.repository.pedido_ia import PedidoIARepositoryMemoria
from src.service.historico_service import HistoricoService
from src.service.pedido_ia_service import (
    LOTE_MAX_POR_DIA_PADRAO,
    PedidoIAService,
    lote_max_por_dia,
)

INICIO = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
URLS = [f"https://exemplo.com/vaga-{i}" for i in range(6)]


def compat(nota: int, faixa: str) -> dict:
    return {"nota": nota, "faixa": faixa, "atende": [], "falta": [], "eliminatorios": [], "resumo": "Resumo."}


class Relogio:
    def __init__(self):
        self.agora = INICIO

    def __call__(self):
        return self.agora


class Montagem:
    def __init__(self, max_por_dia: int = 40, com_base: bool = True):
        self.relogio = Relogio()
        self.encontradas = VagaEncontradaRepositoryMemoria()
        self.encontradas.registrar(
            [
                VagaExterna(titulo=f"Analista de Dados {i}", empresa="Acme", plataforma="Remotar", url=u, remoto=True)
                for i, u in enumerate(URLS)
            ],
            "dados",
        )
        self.curriculos = CurriculoRepositoryMemoria()
        self.base = (
            self.curriculos.salvar(Curriculo(tipo="base", titulo="Base", conteudo_md="# Base")) if com_base else None
        )
        self.perfis = PerfilBuscaRepositoryMemoria()
        self.pedidos = PedidoIARepositoryMemoria(vaga_por_url_em(self.encontradas), self.relogio)
        self.servico = PedidoIAService(
            self.pedidos, self.encontradas, self.perfis, self.curriculos, max_lote_por_dia=max_por_dia, relogio=self.relogio
        )
        self.historico = HistoricoService(self.encontradas, self.perfis)

    def vagas(self, *indices):
        return [self.encontradas.obter(URLS[i]) for i in indices]

    def responder(self, nota=70, faixa="alta", versao="compatibilidade-v1"):
        """O worker pega o próximo e devolve pronto."""
        p = self.pedidos.proximo()
        self.pedidos.concluir(p.id, p.tentativas, compat(nota, faixa), versao, "m", nota, faixa)
        return p


class PrioridadeTest(unittest.TestCase):
    def setUp(self):
        self.relogio = Relogio()
        self.repo = PedidoIARepositoryMemoria(relogio=self.relogio)

    def test_clique_da_pessoa_passa_na_frente_do_lote_mais_antigo(self):
        lote, _ = self.repo.criar(URLS[0], "compatibilidade", prioridade=PRIORIDADE_LOTE)
        self.relogio.agora += timedelta(minutes=5)
        pessoa, _ = self.repo.criar(URLS[1], "compatibilidade")

        self.assertEqual([self.repo.proximo().id, self.repo.proximo().id], [pessoa.id, lote.id])

    def test_pedido_da_pessoa_sobre_o_ativo_do_lote_sobe_a_prioridade(self):
        lote, _ = self.repo.criar(URLS[0], "compatibilidade", prioridade=PRIORIDADE_LOTE)
        outro, _ = self.repo.criar(URLS[1], "compatibilidade", prioridade=PRIORIDADE_LOTE)

        mesmo, criado = self.repo.criar(URLS[0], "compatibilidade")

        self.assertEqual((mesmo.id, criado, mesmo.prioridade), (lote.id, False, 0))
        # E o lote não rebaixa o pedido da pessoa.
        self.assertEqual(self.repo.criar(URLS[0], "compatibilidade", prioridade=PRIORIDADE_LOTE)[0].prioridade, 0)
        self.assertEqual(self.repo.proximo().id, lote.id)
        self.assertEqual(self.repo.proximo().id, outro.id)

    def test_prioridade_fora_de_0_e_1_e_dado_invalido(self):
        for ruim in (2, -1, True, "1"):
            with self.subTest(ruim=ruim), self.assertRaises(DadoInvalido):
                self.repo.criar(URLS[0], "compatibilidade", prioridade=ruim)

    def test_notas_so_da_base_e_da_versao_e_a_mais_recente(self):
        for base, versao, nota, faixa in ((1, "compatibilidade-v1", 30, "baixa"), (1, "compatibilidade-v1", 80, "alta"),
                                          (2, "compatibilidade-v1", 50, "média"), (1, "compatibilidade-v2", 60, "média")):
            p, _ = self.repo.criar(URLS[0], "compatibilidade", curriculo_base_id=base)
            r = self.repo.proximo()
            self.repo.concluir(p.id, r.tentativas, compat(nota, faixa), versao, "m", nota, faixa)

        self.assertEqual(self.repo.notas_compatibilidade(1, "compatibilidade-v1"), {URLS[0]: NotaIA(80, Faixa.ALTA)})
        self.assertEqual(self.repo.notas_compatibilidade(2, "compatibilidade-v1"), {URLS[0]: NotaIA(50, Faixa.MEDIA)})
        self.assertEqual(self.repo.notas_compatibilidade(1, None), {URLS[0]: NotaIA(60, Faixa.MEDIA)})
        self.assertEqual(self.repo.versoes_compatibilidade(), {"compatibilidade-v1", "compatibilidade-v2"})

    def test_contar_lote_desde_e_urls_ativas(self):
        self.repo.criar(URLS[0], "compatibilidade", prioridade=PRIORIDADE_LOTE)
        self.relogio.agora += timedelta(hours=2)
        self.repo.criar(URLS[1], "compatibilidade", prioridade=PRIORIDADE_LOTE)
        self.repo.criar(URLS[2], "compatibilidade")
        self.repo.criar(URLS[3], "curriculo")

        self.assertEqual(self.repo.contar_lote_desde(INICIO), 2)
        self.assertEqual(self.repo.contar_lote_desde(INICIO + timedelta(hours=1)), 1)
        self.assertEqual(self.repo.urls_ativas("compatibilidade"), {URLS[0], URLS[1], URLS[2]})


class LoteTest(unittest.TestCase):
    def test_enfileira_na_ordem_dada_com_base_e_prioridade_do_lote(self):
        m = Montagem()
        self.assertEqual(m.servico.enfileirar_lote(m.vagas(3, 1, 4)), 3)

        ativos = [m.pedidos.ultimo(URLS[i], "compatibilidade") for i in (3, 1, 4)]
        self.assertEqual([p.prioridade for p in ativos], [1, 1, 1])
        self.assertEqual({p.curriculo_base_id for p in ativos}, {m.base.id})
        self.assertEqual([m.pedidos.proximo().vaga_url for _ in range(3)], [URLS[3], URLS[1], URLS[4]])

    def test_pula_ativo_e_nota_valida_e_respeita_o_limite(self):
        m = Montagem()
        m.servico.pedir(URLS[0], "compatibilidade")
        m.responder()  # URLS[0] tem nota válida
        m.servico.pedir(URLS[1], "compatibilidade")  # URLS[1] ativo

        self.assertEqual(m.servico.enfileirar_lote(m.vagas(0, 1, 2, 3, 4), limite=2), 2)
        self.assertEqual(m.pedidos.urls_ativas("compatibilidade"), {URLS[1], URLS[2], URLS[3]})
        # Rodar de novo não duplica nada.
        self.assertEqual(m.servico.enfileirar_lote(m.vagas(0, 1, 2, 3)), 0)

    def test_nota_de_outra_base_ou_de_versao_antiga_volta_para_o_lote(self):
        m = Montagem()
        m.servico.pedir(URLS[0], "compatibilidade")
        m.responder(versao="compatibilidade-v1")
        m.servico.pedir(URLS[1], "compatibilidade")
        m.responder(versao="compatibilidade-v2")  # a versão atual passa a ser a v2

        self.assertEqual(m.servico.notas_validas(), {URLS[1]: NotaIA(70, Faixa.ALTA)})
        self.assertEqual(m.servico.enfileirar_lote(m.vagas(0, 1)), 1)
        self.assertEqual(m.pedidos.urls_ativas("compatibilidade"), {URLS[0]})

        m.pedidos.proximo()  # esvazia
        m.curriculos.salvar(Curriculo(tipo="base", titulo="Base 2", conteudo_md="# Base 2"))
        self.assertEqual(m.servico.notas_validas(), {})
        self.assertEqual(m.servico.enfileirar_lote(m.vagas(1)), 1)

    def test_pedido_sem_base_registrada_nao_conta_como_avaliado(self):
        m = Montagem()
        p, _ = m.pedidos.criar(URLS[0], "compatibilidade")  # como os pedidos de antes da 007
        r = m.pedidos.proximo()
        m.pedidos.concluir(p.id, r.tentativas, compat(80, "alta"), "compatibilidade-v1", "m", 80, "alta")

        self.assertEqual(m.servico.notas_validas(), {})
        self.assertEqual(m.servico.enfileirar_lote(m.vagas(0)), 1)

    def test_teto_diario_conta_as_ultimas_24_h(self):
        m = Montagem(max_por_dia=3)
        self.assertEqual(m.servico.enfileirar_lote(m.vagas(0, 1)), 2)
        self.assertEqual(m.servico.enfileirar_lote(m.vagas(2, 3, 4)), 1)
        self.assertEqual(m.servico.enfileirar_lote(m.vagas(4, 5)), 0)

        m.relogio.agora += timedelta(hours=24, seconds=1)
        self.assertEqual(m.servico.enfileirar_lote(m.vagas(4, 5)), 2)

    def test_sem_base_ou_teto_zero_nao_enfileira(self):
        self.assertEqual(Montagem(com_base=False).servico.enfileirar_lote(Montagem().vagas(0)), 0)
        m = Montagem(max_por_dia=0)
        self.assertEqual(m.servico.enfileirar_lote(m.vagas(0, 1)), 0)
        self.assertEqual(m.pedidos.urls_ativas("compatibilidade"), set())

    def test_vaga_que_saiu_do_historico_e_pulada(self):
        m = Montagem()
        sumida = VagaExterna(titulo="x", empresa="y", plataforma="Remotar", url="https://exemplo.com/sumiu")
        self.assertEqual(m.servico.enfileirar_lote([sumida, *m.vagas(0)]), 1)

    def test_progresso_conta_avaliadas_e_na_fila_entre_as_candidatas(self):
        m = Montagem()
        m.servico.pedir(URLS[0], "compatibilidade")
        m.responder()
        m.servico.enfileirar_lote(m.vagas(1, 2))

        self.assertEqual(m.servico.progresso_lote(m.vagas(0, 1, 2, 3)), (1, 2))
        self.assertEqual(m.servico.progresso_lote(m.vagas(3)), (0, 0))

    def test_pedir_grava_a_base_vigente(self):
        m = Montagem()
        pedido, _ = m.servico.pedir(URLS[0], "compatibilidade")
        self.assertEqual((pedido.curriculo_base_id, pedido.prioridade), (m.base.id, 0))

    def test_base_trocada_antes_do_worker_pegar_fica_gravada_a_que_ele_recebeu(self):
        m = Montagem()
        m.servico.pedir(URLS[0], "compatibilidade")
        nova = m.curriculos.salvar(Curriculo(tipo="base", titulo="Base 2", conteudo_md="# Base 2"))

        entregue = m.servico.proximo()

        self.assertEqual(entregue["curriculo_base"]["id"], nova.id)
        self.assertEqual(m.pedidos.obter(entregue["id"]).curriculo_base_id, nova.id)

    def test_marcar_base_so_vale_para_o_rodando_da_tentativa_atual(self):
        m = Montagem()
        pedido, _ = m.servico.pedir(URLS[0], "compatibilidade")
        self.assertIsNone(m.pedidos.marcar_base(pedido.id, 1, m.base.id))  # ainda pendente
        rodando = m.pedidos.proximo()
        self.assertIsNone(m.pedidos.marcar_base(rodando.id, rodando.tentativas + 1, m.base.id))
        self.assertEqual(m.pedidos.marcar_base(rodando.id, rodando.tentativas, m.base.id).curriculo_base_id, m.base.id)

    def test_vaga_que_deu_erro_fica_fora_do_lote_por_7_dias(self):
        m = Montagem()
        m.servico.enfileirar_lote(m.vagas(0))
        p = m.pedidos.proximo()
        m.pedidos.falhar(p.id, p.tentativas, "tempo esgotado (420 s)")

        m.relogio.agora += timedelta(days=1)  # coleta do dia seguinte
        self.assertEqual(m.servico.enfileirar_lote(m.vagas(0, 1)), 1)
        self.assertEqual(m.pedidos.urls_ativas("compatibilidade"), {URLS[1]})

        m.relogio.agora += timedelta(days=7)  # passou a espera: volta
        self.assertEqual(m.servico.enfileirar_lote(m.vagas(0)), 1)

    def test_erro_com_outra_base_nao_segura_a_vaga(self):
        m = Montagem()
        m.servico.enfileirar_lote(m.vagas(0))
        p = m.pedidos.proximo()
        m.pedidos.falhar(p.id, p.tentativas, "x")
        m.curriculos.salvar(Curriculo(tipo="base", titulo="Base 2", conteudo_md="# Base 2"))
        m.relogio.agora += timedelta(days=1)
        self.assertEqual(m.servico.enfileirar_lote(m.vagas(0)), 1)

    def test_disparo_que_chega_com_o_lote_ocupado_espera_e_enfileira_as_vagas_dele(self):
        # O botão enfileira [0, 1] e, no meio, a coleta termina com a vaga nova 2: ela não pode se perder.
        import threading

        m = Montagem(max_por_dia=10)
        no_meio, pode_seguir, resultado = threading.Event(), threading.Event(), {}
        criar = m.pedidos.criar

        def criar_devagar(*args, **kwargs):
            no_meio.set()
            pode_seguir.wait(5)
            return criar(*args, **kwargs)

        m.pedidos.criar = criar_devagar
        botao = threading.Thread(target=lambda: resultado.setdefault("botao", m.servico.enfileirar_lote(m.vagas(0, 1))))
        botao.start()
        no_meio.wait(5)
        m.pedidos.criar = criar
        coleta = threading.Thread(target=lambda: resultado.setdefault("coleta", m.servico.enfileirar_lote(m.vagas(2))))
        coleta.start()
        pode_seguir.set()
        botao.join(5)
        coleta.join(5)

        self.assertEqual(resultado, {"botao": 2, "coleta": 1})
        self.assertEqual(m.pedidos.urls_ativas("compatibilidade"), {URLS[0], URLS[1], URLS[2]})

    def test_dois_enfileiramentos_juntos_nao_passam_do_teto(self):
        # O segundo disparo (botão, outra aba, a coleta do timer) chega com o primeiro no meio.
        m = Montagem(max_por_dia=2)
        segundo = []
        criar = m.pedidos.criar

        import threading

        outro = []

        def criar_e_disparar_outro(*args, **kwargs):
            if not segundo:
                segundo.append(None)  # só um disparo concorrente, em outra thread (o botão)
                t = threading.Thread(target=lambda: segundo.__setitem__(0, m.servico.enfileirar_lote(m.vagas(2, 3))))
                t.start()
                outro.append(t)
            return criar(*args, **kwargs)

        m.pedidos.criar = criar_e_disparar_outro
        self.assertEqual(m.servico.enfileirar_lote(m.vagas(0, 1, 2, 3)), 2)
        outro[0].join(5)
        self.assertEqual(segundo, [0])  # esperou a vez e encontrou o teto cheio
        self.assertEqual(m.pedidos.contar_lote_desde(m.relogio() - timedelta(hours=24)), 2)

    def test_candidatas_do_historico_seguem_o_perfil(self):
        m = Montagem()
        m.encontradas.registrar(
            [VagaExterna(titulo="Vendedor", empresa="Loja", plataforma="Remotar", url="https://exemplo.com/fora", remoto=True)],
            "vendas",
        )
        self.assertEqual(len(m.historico.visiveis()), 7)
        m.perfis.salvar(Perfil(termos=["analista de dados"], excluir=["vendedor"], modalidades=[Modalidade.REMOTO]))
        visiveis = m.historico.visiveis()
        self.assertEqual(sorted(v.url for v in visiveis), sorted(URLS))


class ConfigTest(unittest.TestCase):
    def test_ia_lote_max_por_dia(self):
        self.assertEqual(lote_max_por_dia({}), LOTE_MAX_POR_DIA_PADRAO)
        self.assertEqual(lote_max_por_dia({"IA_LOTE_MAX_POR_DIA": "15"}), 15)
        self.assertEqual(lote_max_por_dia({"IA_LOTE_MAX_POR_DIA": "0"}), 0)
        for ruim in ("", "dez", "-3", "2.5"):
            with self.subTest(ruim=ruim):
                self.assertEqual(lote_max_por_dia({"IA_LOTE_MAX_POR_DIA": ruim}), LOTE_MAX_POR_DIA_PADRAO)


if __name__ == "__main__":
    unittest.main()
