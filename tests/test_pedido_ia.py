"""Fila de pedidos de IA: repositório em memória, serviço e rotas do worker (Bearer) pela API.

Vagas, currículos e resultados daqui são inventados e escritos à mão (não são respostas gravadas).
"""

import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from fastapi.testclient import TestClient

from src.controller.api import criar_app
from src.model.curriculo import Curriculo
from src.model.detalhe_vaga import Salario
from src.model.erros import DadoInvalido
from src.model.pedido_ia import (
    MAX_ERRO,
    PRAZO_RODANDO,
    EstadoPedidoIA,
    Faixa,
    PedidoIA,
    TipoPedidoIA,
)
from src.model.perfil import Perfil
from src.model.resultado_ia import (
    validar_prompt_versao,
    validar_resultado_compatibilidade,
    validar_resultado_curriculo,
)
from src.model.vaga import Modalidade
from src.model.vaga_externa import VagaExterna
from src.repository.base import (
    CurriculoRepositoryMemoria,
    PerfilBuscaRepositoryMemoria,
    VagaEncontradaRepositoryMemoria,
)
from src.repository.pedido_ia import PedidoIARepositoryMemoria
from src.repository.candidatura import vaga_por_url_em
from src.service.busca_service import BuscaService

URL_A = "https://exemplo.com/vaga-a"
URL_B = "https://exemplo.com/vaga-b"
TOKEN = "token-de-teste"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
INICIO = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

COMPATIBILIDADE = {
    "nota": 82,
    "faixa": "alta",
    "atende": [{"requisito": "SQL avançado", "evidencia_no_curriculo": "Consultas SQL em bases grandes"}],
    "falta": [{"requisito": "Airflow", "obrigatorio": False}],
    "eliminatorios": [],
    "resumo": "Boa aderência.",
}


class Relogio:
    def __init__(self):
        self.agora = INICIO

    def __call__(self):
        return self.agora

    def avancar(self, **kwargs):
        self.agora += timedelta(**kwargs)


def vaga(url: str, titulo: str = "Analista de Dados", empresa: str = "Acme") -> VagaExterna:
    return VagaExterna(titulo=titulo, empresa=empresa, plataforma="Remotar", url=url, local="Cidade X", remoto=True)


class Montagem:
    """App em memória com duas vagas, um currículo base e o relógio controlado."""

    def __init__(self, com_base: bool = True, com_perfil: bool = False):
        self.relogio = Relogio()
        self.encontradas = VagaEncontradaRepositoryMemoria(1)
        self.encontradas.registrar([vaga(URL_A), vaga(URL_B, "Engenheiro de Dados", "Beta")], "dados")
        self.curriculos = CurriculoRepositoryMemoria(1)
        self.base = self.curriculos.salvar(Curriculo(tipo="base", titulo="Base", conteudo_md="# Base\nConsultas SQL")) if com_base else None
        self.perfis = PerfilBuscaRepositoryMemoria(1)
        if com_perfil:
            self.perfis.salvar(Perfil(termos=["dados"], modalidades=[Modalidade.REMOTO], skills=["sql"]))
        self.pedidos = PedidoIARepositoryMemoria(1, vaga_por_url_em(self.encontradas), self.relogio)
        self.app = criar_app(
            self.perfis, self.curriculos, BuscaService([]), encontradas=self.encontradas, pedidos_ia=self.pedidos
        )
        self.servico = self.app.state.pedido_ia_service
        self.cliente = TestClient(self.app)

    def adaptado(self, vaga_url: str = URL_A) -> Curriculo:
        return self.curriculos.salvar(
            Curriculo(tipo="adaptado", titulo="Adaptado", conteudo_md="# x", base_id=self.base.id, vaga_url=vaga_url)
        )


class ComToken(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"VAGAS_IA_TOKEN": TOKEN})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("VAGAS_IA_TOKEN_FILE", None)
        self.m = Montagem()

    def proximo(self, **kwargs):
        return self.m.cliente.post("/pedidos-ia/proximo", json={}, headers=AUTH, **kwargs)

    def put(self, id, corpo, **kwargs):
        return self.m.cliente.put(f"/pedidos-ia/{id}", json=corpo, headers=AUTH, **kwargs)


class RepositorioMemoriaTest(unittest.TestCase):
    def setUp(self):
        self.relogio = Relogio()
        self.repo = PedidoIARepositoryMemoria(1, relogio=self.relogio)

    def test_criar_e_idempotente_com_o_ativo(self):
        primeiro, criado = self.repo.criar(URL_A, "compatibilidade")
        segundo, criado2 = self.repo.criar(URL_A, TipoPedidoIA.COMPATIBILIDADE)

        self.assertTrue(criado)
        self.assertFalse(criado2)
        self.assertEqual(primeiro.id, segundo.id)
        # Tipo diferente e vaga diferente têm o próprio pedido.
        self.assertTrue(self.repo.criar(URL_A, "curriculo")[1])
        self.assertTrue(self.repo.criar(URL_B, "compatibilidade")[1])

    def test_reexecucao_depois_do_pronto_cria_um_novo(self):
        antigo, _ = self.repo.criar(URL_A, "compatibilidade")
        rodando = self.repo.proximo()
        self.repo.concluir(antigo.id, rodando.tentativas, COMPATIBILIDADE, "compatibilidade-v1", "m", 82, "alta")

        novo, criado = self.repo.criar(URL_A, "compatibilidade")

        self.assertTrue(criado)
        self.assertNotEqual(novo.id, antigo.id)
        self.assertEqual(self.repo.obter(antigo.id).estado, EstadoPedidoIA.PRONTO)
        self.assertEqual(self.repo.ultimo(URL_A, "compatibilidade").id, novo.id)

    def test_ordem_da_fila_e_fila_vazia(self):
        self.assertIsNone(self.repo.proximo())
        a, _ = self.repo.criar(URL_A, "compatibilidade")
        self.relogio.avancar(seconds=1)
        b, _ = self.repo.criar(URL_B, "compatibilidade")

        self.assertEqual(self.repo.proximo().id, a.id)
        self.assertEqual(self.repo.proximo().id, b.id)
        self.assertIsNone(self.repo.proximo())

    def test_preso_ha_15_min_e_1_s_volta_e_14_min_nao(self):
        pedido, _ = self.repo.criar(URL_A, "compatibilidade")
        self.assertEqual(self.repo.proximo().tentativas, 1)

        self.relogio.avancar(minutes=14)
        self.assertIsNone(self.repo.proximo())
        self.relogio.avancar(minutes=1)  # exatamente 15 min: ainda não passou do prazo
        self.assertIsNone(self.repo.proximo())
        self.relogio.avancar(seconds=1)
        de_novo = self.repo.proximo()

        self.assertEqual((de_novo.id, de_novo.tentativas), (pedido.id, 2))
        self.assertEqual(de_novo.reivindicado_em, self.relogio.agora)

    def test_pendente_passa_antes_do_preso(self):
        preso, _ = self.repo.criar(URL_A, "compatibilidade")
        self.repo.proximo()
        self.relogio.avancar(seconds=1)
        novo, _ = self.repo.criar(URL_B, "compatibilidade")
        self.relogio.avancar(minutes=20)

        self.assertEqual(self.repo.proximo().id, novo.id)
        self.assertEqual(self.repo.proximo().id, preso.id)

    def test_na_terceira_expiracao_vira_erro_com_a_mensagem_fixa(self):
        pedido, _ = self.repo.criar(URL_A, "compatibilidade")
        for tentativa in (1, 2, 3):
            self.assertEqual(self.repo.proximo().tentativas, tentativa)
            self.relogio.avancar(minutes=15, seconds=1)

        self.assertIsNone(self.repo.proximo())
        final = self.repo.obter(pedido.id)
        self.assertEqual(final.estado, EstadoPedidoIA.ERRO)
        self.assertEqual(final.erro, "O worker não terminou depois de 3 tentativas")
        self.assertEqual(final.tentativas, 3)
        self.assertFalse(final.ativo)
        # Terminou: pedir de novo cria um novo pedido.
        self.assertTrue(self.repo.criar(URL_A, "compatibilidade")[1])

    def test_concluir_e_falhar_exigem_rodando_e_a_tentativa_atual(self):
        pedido, _ = self.repo.criar(URL_A, "compatibilidade")
        self.assertIsNone(self.repo.falhar(pedido.id, 1, "x"))  # ainda pendente
        self.repo.proximo()
        self.assertIsNone(self.repo.falhar(pedido.id, 2, "x"))  # tentativa de outra rodada
        self.assertIsNone(self.repo.falhar(999, 1, "x"))
        self.assertEqual(self.repo.falhar(pedido.id, 1, "x" * 600).erro, "x" * MAX_ERRO)
        self.assertIsNone(self.repo.falhar(pedido.id, 1, "x"))  # estado final

    def test_ativo(self):
        self.assertTrue(PedidoIA(1, TipoPedidoIA.CURRICULO, URL_A).ativo)
        self.assertTrue(PedidoIA(1, TipoPedidoIA.CURRICULO, URL_A, estado=EstadoPedidoIA.RODANDO).ativo)
        self.assertFalse(PedidoIA(1, TipoPedidoIA.CURRICULO, URL_A, estado=EstadoPedidoIA.PRONTO).ativo)
        self.assertFalse(PedidoIA(1, TipoPedidoIA.CURRICULO, URL_A, estado=EstadoPedidoIA.ERRO).ativo)

    def test_listar_prontos_compatibilidade_filtra_versao_e_tipo(self):
        for url, tipo in ((URL_A, "compatibilidade"), (URL_B, "compatibilidade"), (URL_A, "curriculo")):
            self.repo.criar(url, tipo)
        versoes = {1: "compatibilidade-v1", 2: "compatibilidade-v2", 3: "curriculo-v1"}
        for _ in range(3):
            p = self.repo.proximo()
            self.repo.concluir(p.id, 1, {}, versoes[p.id], "m", 50 if p.id < 3 else None, "média" if p.id < 3 else None)

        self.assertEqual([p.id for p in self.repo.listar_prontos_compatibilidade()], [1, 2])
        self.assertEqual([p.id for p in self.repo.listar_prontos_compatibilidade("compatibilidade-v2")], [2])

    def test_rotulo_grava_troca_apaga(self):
        self.assertIsNone(self.repo.obter_rotulo(URL_A))
        self.repo.gravar_rotulo(URL_A, "alta")
        self.repo.gravar_rotulo(URL_A, Faixa.MEDIA)
        self.repo.gravar_rotulo(URL_B, "baixa")
        self.assertEqual(self.repo.obter_rotulo(URL_A), Faixa.MEDIA)
        self.assertEqual(self.repo.listar_rotulos(), {URL_A: "média", URL_B: "baixa"})
        self.repo.gravar_rotulo(URL_A, None)
        self.assertEqual(self.repo.listar_rotulos(), {URL_B: "baixa"})
        with self.assertRaises(DadoInvalido):
            self.repo.gravar_rotulo(URL_A, "media")

    def test_tipo_invalido_e_dado_invalido(self):
        with self.assertRaises(DadoInvalido):
            self.repo.criar(URL_A, "outro")


class ResultadoIATest(unittest.TestCase):
    def invalido(self, **mudancas):
        r = copy.deepcopy(COMPATIBILIDADE)
        r.update(mudancas)
        with self.assertRaises(DadoInvalido):
            validar_resultado_compatibilidade(r)

    def test_resultado_valido_passa(self):
        self.assertEqual(validar_resultado_compatibilidade(copy.deepcopy(COMPATIBILIDADE)), COMPATIBILIDADE)

    def test_faixas_nos_limites_da_nota(self):
        for nota, faixa in ((100, "alta"), (70, "alta"), (69, "média"), (40, "média"), (39, "baixa"), (0, "baixa")):
            validar_resultado_compatibilidade({**COMPATIBILIDADE, "nota": nota, "faixa": faixa})
        self.invalido(nota=69, faixa="alta")
        self.invalido(nota=39, faixa="média")
        self.invalido(nota=70, faixa="média")

    def test_faixa_sem_acento_e_nota_fora_de_faixa_ou_bool(self):
        self.invalido(nota=70, faixa="media")
        self.invalido(nota=101)
        self.invalido(nota=-1, faixa="baixa")
        self.invalido(nota=True, faixa="baixa")
        self.invalido(nota=82.0)

    def test_chaves_extras_ou_ausentes(self):
        self.invalido(extra=1)
        r = copy.deepcopy(COMPATIBILIDADE)
        del r["resumo"]
        with self.assertRaises(DadoInvalido):
            validar_resultado_compatibilidade(r)
        self.invalido(atende=[{"requisito": "a", "evidencia_no_curriculo": "b", "x": 1}])
        self.invalido(falta=[{"requisito": "a"}])

    def test_strings_vazias_com_espaco_nas_pontas_ou_longas_demais(self):
        self.invalido(resumo="   ")
        self.invalido(resumo=" com espaço")
        self.invalido(resumo="x" * 1501)
        self.invalido(resumo="com\x00nul")
        self.invalido(atende=[{"requisito": "a", "evidencia_no_curriculo": "x" * 501}])
        self.invalido(eliminatorios=["  "])
        self.invalido(falta=[{"requisito": "a", "obrigatorio": "sim"}])

    def test_limites_de_itens(self):
        self.invalido(atende=[{"requisito": "a", "evidencia_no_curriculo": "b"}] * 31)
        self.invalido(falta=[{"requisito": "a", "obrigatorio": True}] * 31)
        self.invalido(eliminatorios=["a"] * 21)

    def test_nao_ecoa_o_conteudo_no_motivo(self):
        with self.assertRaises(DadoInvalido) as ctx:
            validar_resultado_compatibilidade({**COMPATIBILIDADE, "chave-secreta-xyz": 1})
        self.assertNotIn("chave-secreta-xyz", str(ctx.exception))

    def test_resultado_do_curriculo(self):
        ok = {"curriculo_id": 3, "titulo": "Analista - Acme", "o_que_mudou": ["Reordenei"], "lacunas": []}
        self.assertEqual(validar_resultado_curriculo(dict(ok)), ok)
        for ruim in (
            {**ok, "curriculo_id": 0},
            {**ok, "curriculo_id": True},
            {**ok, "o_que_mudou": []},
            {**ok, "lacunas": ["x"] * 31},
            {**ok, "conteudo_md": "# fora do resultado"},
            {k: v for k, v in ok.items() if k != "titulo"},
        ):
            with self.assertRaises(DadoInvalido):
                validar_resultado_curriculo(ruim)

    def test_prompt_versao_casa_o_tipo(self):
        self.assertEqual(validar_prompt_versao(TipoPedidoIA.COMPATIBILIDADE, "compatibilidade-v12"), "compatibilidade-v12")
        for tipo, versao in (
            (TipoPedidoIA.COMPATIBILIDADE, "curriculo-v1"),
            (TipoPedidoIA.CURRICULO, "compatibilidade-v1"),
            (TipoPedidoIA.CURRICULO, "curriculo-v1\n"),
            (TipoPedidoIA.CURRICULO, "curriculo-v"),
        ):
            with self.assertRaises(DadoInvalido):
                validar_prompt_versao(tipo, versao)


class ProximoTest(ComToken):
    def test_json_do_pedido_tem_as_chaves_exatas_e_descricao_nula_sem_a_frente_c(self):
        self.m.servico.pedir(URL_A, "compatibilidade")

        resposta = self.proximo()

        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertEqual(set(corpo), {"id", "tipo", "tentativa", "vaga", "curriculo_base", "perfil"})
        self.assertEqual((corpo["tipo"], corpo["tentativa"]), ("compatibilidade", 1))
        self.assertEqual(
            set(corpo["vaga"]),
            {"url", "titulo", "empresa", "plataforma", "local", "remoto", "publicada_em", "descricao", "requisitos", "subtitulo",
             "salario"},
        )
        self.assertIsNone(corpo["vaga"]["salario"])
        self.assertEqual(corpo["vaga"]["url"], URL_A)
        self.assertIsNone(corpo["vaga"]["descricao"])
        self.assertIsNone(corpo["vaga"]["subtitulo"])
        self.assertEqual(corpo["vaga"]["requisitos"], [])
        self.assertEqual(set(corpo["curriculo_base"]), {"id", "titulo", "conteudo_md"})
        self.assertEqual(corpo["curriculo_base"]["id"], self.m.base.id)
        self.assertIsNone(corpo["perfil"])
        self.assertEqual(resposta.headers["cache-control"], "no-store")

    def test_perfil_sai_com_os_valores_dos_enums(self):
        m = Montagem(com_perfil=True)
        m.servico.pedir(URL_A, "curriculo")

        corpo = m.cliente.post("/pedidos-ia/proximo", json={}, headers=AUTH).json()

        self.assertEqual(
            corpo["perfil"],
            {
                "termos": ["dados"], "excluir": [], "modalidades": ["Remoto"], "localidades": [], "plataformas": [],
                "niveis": [], "skills": ["sql"], "publicada_desde_dias": None, "ocultar_candidatadas": True,
                "salario_minimo": None,
            },
        )

    def test_campos_da_frente_c_sao_lidos_com_getattr(self):
        class Req:
            texto, obrigatorio = "SQL avançado", True

        real = self.m.encontradas.obter(URL_A)
        real.descricao, real.requisitos, real.subtitulo = "Texto puro", [Req(), {"texto": "Python", "obrigatorio": False}, 5], "Time de dados"
        self.m.servico.pedir(URL_A, "compatibilidade")

        # A memória devolve cópias: a vaga com os campos da frente C entra pelo `obter`.
        with mock.patch.object(self.m.encontradas, "obter", return_value=real):
            vaga_json = self.proximo().json()["vaga"]

        self.assertEqual(vaga_json["descricao"], "Texto puro")
        self.assertEqual(vaga_json["subtitulo"], "Time de dados")
        self.assertEqual(
            vaga_json["requisitos"],
            [{"texto": "SQL avançado", "obrigatorio": True}, {"texto": "Python", "obrigatorio": False}],
        )

    def test_salario_da_vaga_vai_como_texto_e_o_minimo_do_perfil_junto(self):
        # A IA compara os dois: vaga que paga o mínimo ou menos tem a nota limitada (compatibilidade-v3).
        real = self.m.encontradas.obter(URL_A)
        real.salario = Salario(minimo=300000, periodo="mês")
        self.m.servico.pedir(URL_A, "compatibilidade")

        with mock.patch.object(self.m.encontradas, "obter", return_value=real):
            vaga_json = self.proximo().json()["vaga"]

        self.assertEqual(vaga_json["salario"], Salario(minimo=300000, periodo="mês").texto())
        self.assertIn("R$ 3.000", vaga_json["salario"])

        m = Montagem(com_perfil=True)
        perfil = m.perfis.obter()
        perfil.salario_minimo = 3050
        m.perfis.salvar(perfil)
        m.servico.pedir(URL_A, "compatibilidade")
        self.assertEqual(m.cliente.post("/pedidos-ia/proximo", json={}, headers=AUTH).json()["perfil"]["salario_minimo"], 3050)

    def test_fila_vazia_responde_204_sem_corpo(self):
        resposta = self.proximo()

        self.assertEqual(resposta.status_code, 204)
        self.assertEqual(resposta.content, b"")
        self.assertEqual(resposta.headers["cache-control"], "no-store")

    def test_ordem_da_fila_pela_api(self):
        self.m.servico.pedir(URL_A, "compatibilidade")
        self.m.relogio.avancar(seconds=1)
        self.m.servico.pedir(URL_B, "curriculo")

        self.assertEqual([self.proximo().json()["vaga"]["url"] for _ in range(2)], [URL_A, URL_B])
        self.assertEqual(self.proximo().status_code, 204)

    def test_preso_volta_com_a_tentativa_seguinte(self):
        self.m.servico.pedir(URL_A, "compatibilidade")
        self.assertEqual(self.proximo().json()["tentativa"], 1)
        self.m.relogio.avancar(minutes=14)
        self.assertEqual(self.proximo().status_code, 204)
        self.m.relogio.avancar(minutes=1, seconds=1)

        self.assertEqual(self.proximo().json()["tentativa"], 2)

    def test_na_terceira_expiracao_vira_erro_pela_api(self):
        pedido, _ = self.m.servico.pedir(URL_A, "compatibilidade")
        for _ in range(3):
            self.assertEqual(self.proximo().status_code, 200)
            self.m.relogio.avancar(minutes=15, seconds=1)

        self.assertEqual(self.proximo().status_code, 204)
        final = self.m.servico.obter(pedido.id)
        self.assertEqual((final.estado, final.erro), (EstadoPedidoIA.ERRO, "O worker não terminou depois de 3 tentativas"))

    def test_sem_base_o_pedido_vira_erro_e_passa_ao_seguinte(self):
        self.m.servico.pedir(URL_A, "compatibilidade")
        self.m.servico.pedir(URL_B, "compatibilidade")
        # A base some depois de pedir (nada a remove hoje; a primeira leitura devolve None para provar o ramo).
        with mock.patch.object(self.m.curriculos, "obter_base", side_effect=[None, self.m.base, self.m.base]):
            resposta = self.proximo()

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json()["vaga"]["url"], URL_B)
        primeiro = self.m.servico.ultimos(URL_A)["compatibilidade"]
        self.assertEqual((primeiro.estado, primeiro.erro), (EstadoPedidoIA.ERRO, "Não há currículo base"))

    def test_sem_base_e_sem_outro_pedido_responde_204(self):
        self.m.servico.pedir(URL_A, "compatibilidade")
        with mock.patch.object(self.m.curriculos, "obter_base", return_value=None):
            resposta = self.proximo()

        self.assertEqual(resposta.status_code, 204)
        self.assertEqual(self.m.servico.ultimos(URL_A)["compatibilidade"].erro, "Não há currículo base")


class PedirTest(ComToken):
    def test_recusa_tipo_url_vaga_desconhecida_e_falta_de_base(self):
        casos = (
            (URL_A, "outro"),
            ("", "compatibilidade"),
            ("   ", "compatibilidade"),
            ("https://exemplo.com/" + "x" * 2048, "compatibilidade"),
            ("https://exemplo.com/fora-do-historico", "compatibilidade"),
        )
        for url, tipo in casos:
            with self.subTest(url=url[:30], tipo=tipo), self.assertRaises(DadoInvalido):
                self.m.servico.pedir(url, tipo)
        sem_base = Montagem(com_base=False)
        with self.assertRaisesRegex(DadoInvalido, "Salve um currículo base"):
            sem_base.servico.pedir(URL_A, "compatibilidade")

    def test_pedir_de_novo_devolve_o_ativo(self):
        a, criado = self.m.servico.pedir(URL_A, "compatibilidade")
        b, criado_de_novo = self.m.servico.pedir(URL_A, "compatibilidade")

        self.assertEqual((criado, criado_de_novo, a.id), (True, False, b.id))

    def test_ultimos_traz_um_por_tipo(self):
        self.assertEqual(self.m.servico.ultimos(URL_A), {"compatibilidade": None, "curriculo": None})
        pedido, _ = self.m.servico.pedir(URL_A, "curriculo")

        ultimos = self.m.servico.ultimos(URL_A)

        self.assertIsNone(ultimos["compatibilidade"])
        self.assertEqual(ultimos["curriculo"].id, pedido.id)


class PutTest(ComToken):
    def rodando(self, tipo="compatibilidade", url=URL_A):
        self.m.servico.pedir(url, tipo)
        return self.proximo().json()

    def corpo_pronto(self, **extras):
        return {
            "tentativa": 1, "estado": "pronto", "prompt_versao": "compatibilidade-v1", "modelo": "claude-sonnet-5-5",
            "resultado": copy.deepcopy(COMPATIBILIDADE), **extras,
        }

    def estado(self, id):
        return self.m.servico.obter(id)

    def test_compatibilidade_pronta_grava_nota_e_faixa(self):
        pedido = self.rodando()

        resposta = self.put(pedido["id"], self.corpo_pronto())

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {"id": pedido["id"], "estado": "pronto"})
        gravado = self.estado(pedido["id"])
        self.assertEqual((gravado.nota, gravado.faixa, gravado.prompt_versao), (82, Faixa.ALTA, "compatibilidade-v1"))
        self.assertEqual(gravado.modelo, "claude-sonnet-5-5")
        self.assertEqual(gravado.resultado, COMPATIBILIDADE)
        self.assertEqual(resposta.headers["cache-control"], "no-store")

    def test_erro_do_worker_vira_erro(self):
        pedido = self.rodando()

        resposta = self.put(
            pedido["id"],
            {"tentativa": 1, "estado": "erro", "prompt_versao": "compatibilidade-v1", "modelo": "m", "erro": "tempo esgotado (420 s)"},
        )

        self.assertEqual(resposta.json()["estado"], "erro")
        self.assertEqual(self.estado(pedido["id"]).erro, "tempo esgotado (420 s)")

    def test_erro_de_600_caracteres_e_cortado_para_500(self):
        pedido = self.rodando()

        resposta = self.put(
            pedido["id"],
            {"tentativa": 1, "estado": "erro", "prompt_versao": "compatibilidade-v1", "modelo": "m", "erro": "e" * 600},
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(self.estado(pedido["id"]).erro, "e" * 500)

    def test_tentativa_antiga_e_409_e_nada_muda(self):
        pedido = self.rodando()
        self.m.relogio.avancar(minutes=15, seconds=1)
        self.assertEqual(self.proximo().json()["tentativa"], 2)

        resposta = self.put(pedido["id"], self.corpo_pronto(tentativa=1))

        self.assertEqual(resposta.status_code, 409)
        self.assertEqual(self.estado(pedido["id"]).estado, EstadoPedidoIA.RODANDO)

    def test_pedido_pronto_e_409(self):
        pedido = self.rodando()
        self.assertEqual(self.put(pedido["id"], self.corpo_pronto()).status_code, 200)

        self.assertEqual(self.put(pedido["id"], self.corpo_pronto()).status_code, 409)

    def test_pedido_pendente_e_409_e_desconhecido_e_404(self):
        pendente, _ = self.m.servico.pedir(URL_A, "compatibilidade")

        self.assertEqual(self.put(pendente.id, self.corpo_pronto()).status_code, 409)
        self.assertEqual(self.put(9999, self.corpo_pronto()).status_code, 404)
        self.assertEqual(self.put(2**70, self.corpo_pronto()).status_code, 404)

    def assert_422_sem_mudar(self, corpo):
        pedido = self.rodando()
        resposta = self.put(pedido["id"], corpo)
        self.assertEqual(resposta.status_code, 422, resposta.text)
        atual = self.estado(pedido["id"])
        self.assertEqual((atual.estado, atual.resultado, atual.nota), (EstadoPedidoIA.RODANDO, None, None))

    def test_faixa_incoerente_com_a_nota(self):
        self.assert_422_sem_mudar(self.corpo_pronto(resultado={**COMPATIBILIDADE, "nota": 30}))

    def test_nota_101(self):
        self.assert_422_sem_mudar(self.corpo_pronto(resultado={**COMPATIBILIDADE, "nota": 101}))

    def test_nota_bool(self):
        self.assert_422_sem_mudar(self.corpo_pronto(resultado={**COMPATIBILIDADE, "nota": True, "faixa": "baixa"}))

    def test_chave_extra_no_resultado(self):
        self.assert_422_sem_mudar(self.corpo_pronto(resultado={**COMPATIBILIDADE, "extra": 1}))

    def test_string_so_de_espacos(self):
        self.assert_422_sem_mudar(self.corpo_pronto(resultado={**COMPATIBILIDADE, "resumo": "    "}))

    def test_resultado_acima_de_64_kb(self):
        # Dentro de todos os limites do schema (itens e tamanhos), mas com mais de 64 KB em UTF-8: só o teto recusa.
        grande = {
            **COMPATIBILIDADE,
            "resumo": "é" * 1500,
            "eliminatorios": ["é" * 300] * 20,
            "atende": [{"requisito": "é" * 300, "evidencia_no_curriculo": "é" * 500}] * 30,
            "falta": [{"requisito": "é" * 300, "obrigatorio": True}] * 30,
        }
        validar_resultado_compatibilidade(grande)
        self.assertGreater(len(json.dumps(grande, ensure_ascii=False).encode()), 64 * 1024)
        self.assert_422_sem_mudar(self.corpo_pronto(resultado=grande))

    def test_prompt_versao_de_outro_tipo(self):
        self.assert_422_sem_mudar(self.corpo_pronto(prompt_versao="curriculo-v1"))

    def test_corpo_malformado_e_422(self):
        pedido = self.rodando()
        for corpo in (
            {**self.corpo_pronto(), "extra": 1},
            self.corpo_pronto(erro="x"),  # pronto proíbe erro
            {"tentativa": 1, "estado": "erro", "prompt_versao": "compatibilidade-v1", "modelo": "m"},  # erro sem texto
            {"tentativa": 1, "estado": "erro", "prompt_versao": "compatibilidade-v1", "modelo": "m", "erro": "  "},
            {"tentativa": 1, "estado": "erro", "prompt_versao": "compatibilidade-v1", "modelo": "m", "erro": "x", "resultado": {}},
            {k: v for k, v in self.corpo_pronto().items() if k != "resultado"},
            self.corpo_pronto(tentativa=True),
            self.corpo_pronto(modelo=""),
            self.corpo_pronto(modelo="m" * 101),
            self.corpo_pronto(modelo="claude\x00x"),  # NUL estouraria no Postgres (500)
            self.corpo_pronto(modelo="claude\nx"),
            self.corpo_pronto(prompt_versao="compatibilidade-v" + "9" * 7),
            self.corpo_pronto(prompt_versao="Compat-1"),
            self.corpo_pronto(estado="rodando"),
        ):
            with self.subTest(corpo=str(corpo)[:60]):
                self.assertEqual(self.put(pedido["id"], corpo).status_code, 422)
        self.assertEqual(self.put("abc", self.corpo_pronto()).status_code, 422)
        self.assertEqual(self.estado(pedido["id"]).estado, EstadoPedidoIA.RODANDO)

    def corpo_curriculo(self, curriculo_id):
        return {
            "tentativa": 1, "estado": "pronto", "prompt_versao": "curriculo-v1", "modelo": "m",
            "resultado": {"curriculo_id": curriculo_id, "titulo": "Analista - Acme", "o_que_mudou": ["Reordenei"], "lacunas": []},
        }

    def test_curriculo_pronto_grava_curriculo_id(self):
        pedido = self.rodando("curriculo")
        adaptado = self.m.adaptado(URL_A)

        resposta = self.put(pedido["id"], self.corpo_curriculo(adaptado.id))

        self.assertEqual(resposta.status_code, 200, resposta.text)
        gravado = self.estado(pedido["id"])
        self.assertEqual((gravado.estado, gravado.curriculo_id, gravado.nota), (EstadoPedidoIA.PRONTO, adaptado.id, None))

    def test_curriculo_de_outra_vaga_base_ou_inexistente_e_422(self):
        pedido = self.rodando("curriculo")
        for curriculo_id in (self.m.adaptado(URL_B).id, self.m.base.id, 9999):
            with self.subTest(curriculo_id=curriculo_id):
                self.assertEqual(self.put(pedido["id"], self.corpo_curriculo(curriculo_id)).status_code, 422)
        atual = self.estado(pedido["id"])
        self.assertEqual((atual.estado, atual.curriculo_id), (EstadoPedidoIA.RODANDO, None))

    def test_compatibilidade_nao_aceita_resultado_de_curriculo(self):
        pedido = self.rodando()
        adaptado = self.m.adaptado(URL_A)
        corpo = {**self.corpo_curriculo(adaptado.id), "prompt_versao": "compatibilidade-v1"}

        self.assertEqual(self.put(pedido["id"], corpo).status_code, 422)


class AuthTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        for nome in ("VAGAS_IA_TOKEN", "VAGAS_IA_TOKEN_FILE"):
            os.environ.pop(nome, None)
        self.m = Montagem()
        self.m.servico.pedir(URL_A, "compatibilidade")

    def chamadas(self, headers):
        return (
            self.m.cliente.post("/pedidos-ia/proximo", json={}, headers=headers),
            self.m.cliente.put("/pedidos-ia/1", json={}, headers=headers),
        )

    def test_sem_token_configurado_503_e_a_app_segue_de_pe(self):
        for resposta in self.chamadas(AUTH):
            self.assertEqual(resposta.status_code, 503)
            self.assertEqual(resposta.json(), {"detail": "Token do worker de IA não configurado"})
        self.assertEqual(self.m.cliente.get("/saude").status_code, 200)
        self.assertEqual(self.m.servico.obter(1).estado, EstadoPedidoIA.PENDENTE)

    def test_arquivo_vazio_ou_ilegivel_e_503(self):
        with tempfile.TemporaryDirectory() as pasta:
            vazio = os.path.join(pasta, "vazio")
            with open(vazio, "w") as f:
                f.write("  \n")
            for caminho in (vazio, os.path.join(pasta, "nao-existe"), pasta):
                with self.subTest(caminho=os.path.basename(caminho)), mock.patch.dict(
                    os.environ, {"VAGAS_IA_TOKEN_FILE": caminho}
                ):
                    self.assertEqual(self.chamadas(AUTH)[0].status_code, 503)

    def test_token_do_arquivo_vale_e_vence_a_variavel(self):
        with tempfile.TemporaryDirectory() as pasta:
            caminho = os.path.join(pasta, "token")
            with open(caminho, "w") as f:
                f.write("do-arquivo\n")
            with mock.patch.dict(os.environ, {"VAGAS_IA_TOKEN_FILE": caminho, "VAGAS_IA_TOKEN": "da-variavel"}):
                self.assertEqual(self.chamadas({"Authorization": "Bearer da-variavel"})[0].status_code, 401)
                self.assertEqual(self.chamadas({"Authorization": "Bearer do-arquivo"})[0].status_code, 200)

    def test_sem_header_ou_com_token_errado_e_401_com_www_authenticate(self):
        with mock.patch.dict(os.environ, {"VAGAS_IA_TOKEN": TOKEN}):
            for headers in (
                {},
                {"Authorization": "Bearer errado"},
                {"Authorization": "Bearer"},
                {"Authorization": TOKEN},
                {"Authorization": f"Basic {TOKEN}"},
                {"Authorization": "Bearer t\xc3\xb4ken".encode("latin-1")},
            ):
                for resposta in self.chamadas(headers):
                    with self.subTest(headers=str(headers)):
                        self.assertEqual(resposta.status_code, 401)
                        self.assertEqual(resposta.headers["www-authenticate"], "Bearer")
                        self.assertEqual(resposta.headers["cache-control"], "no-store")
            self.assertEqual(self.m.servico.obter(1).estado, EstadoPedidoIA.PENDENTE)

    def test_sem_token_o_corpo_invalido_nao_e_lido(self):
        with mock.patch.dict(os.environ, {"VAGAS_IA_TOKEN": TOKEN}):
            resposta = self.m.cliente.put("/pedidos-ia/1", json={"lixo": 1})
        self.assertEqual(resposta.status_code, 401)

    def test_escrita_sem_content_type_json_e_415(self):
        with mock.patch.dict(os.environ, {"VAGAS_IA_TOKEN": TOKEN}):
            resposta = self.m.cliente.post("/pedidos-ia/proximo", content="{}", headers={**AUTH, "Content-Type": "text/plain"})
        self.assertEqual(resposta.status_code, 415)
        self.assertEqual(self.m.servico.obter(1).estado, EstadoPedidoIA.PENDENTE)

    def test_aceita_o_token_com_content_type_json_e_charset(self):
        with mock.patch.dict(os.environ, {"VAGAS_IA_TOKEN": TOKEN}):
            resposta = self.m.cliente.post(
                "/pedidos-ia/proximo", content="{}", headers={**AUTH, "Content-Type": "application/json; charset=utf-8"}
            )
        self.assertEqual(resposta.status_code, 200)


class RotuloTest(ComToken):
    def rotular(self, vaga_url, faixa):
        return self.m.cliente.put("/vagas/rotulo-ia", json={"vaga_url": vaga_url, "faixa": faixa})

    def test_grava_troca_e_apaga(self):
        self.assertEqual(self.rotular(URL_A, "alta").json(), {"vaga_url": URL_A, "faixa": "alta"})
        self.assertEqual(self.m.servico.rotulo(URL_A), Faixa.ALTA)
        self.assertEqual(self.rotular(URL_A, "média").json(), {"vaga_url": URL_A, "faixa": "média"})
        self.assertEqual(self.m.pedidos.listar_rotulos(), {URL_A: "média"})
        apagado = self.rotular(URL_A, None)
        self.assertEqual((apagado.status_code, apagado.json()), (200, {"vaga_url": URL_A, "faixa": None}))
        self.assertEqual(self.m.pedidos.listar_rotulos(), {})

    def test_faixa_sem_acento_ou_fora_da_lista_e_422(self):
        self.rotular(URL_A, "alta")
        for faixa in ("media", "ALTA", "", "regular", 3):
            with self.subTest(faixa=faixa):
                self.assertEqual(self.rotular(URL_A, faixa).status_code, 422)
        self.assertEqual(self.m.cliente.put("/vagas/rotulo-ia", json={"vaga_url": URL_A}).status_code, 422)
        self.assertEqual(self.m.servico.rotulo(URL_A), Faixa.ALTA)

    def test_vaga_desconhecida_e_422(self):
        resposta = self.rotular("https://exemplo.com/fora", "alta")
        self.assertEqual(resposta.status_code, 422)
        self.assertEqual(self.m.pedidos.listar_rotulos(), {})

    def test_exige_content_type_json(self):
        resposta = self.m.cliente.put("/vagas/rotulo-ia", content='{"vaga_url": "x", "faixa": "alta"}', headers={"Content-Type": "text/plain"})
        self.assertEqual(resposta.status_code, 415)

    def test_nao_exige_bearer(self):
        # Rota de rótulo é da tela/JSON aberto, como o resto da API interna.
        os.environ.pop("VAGAS_IA_TOKEN", None)
        self.assertEqual(self.rotular(URL_A, "baixa").status_code, 200)


class FiacaoTest(unittest.TestCase):
    def test_criar_app_sem_pedidos_ia_usa_memoria_e_expoe_o_servico(self):
        app = criar_app(PerfilBuscaRepositoryMemoria(1), CurriculoRepositoryMemoria(1), BuscaService([]))
        self.assertIsNotNone(app.state.pedido_ia_service)

    def test_producao_liga_o_repositorio_postgres_por_chamada(self):
        from src.controller import api

        with mock.patch.object(api, "criar_app") as criar:
            api.criar_app_producao()
        pedidos = criar.call_args.kwargs["pedidos_ia"]
        self.assertIsInstance(pedidos, api._PorChamada)
        self.assertEqual(api._pedidos_ia_postgres().__name__, "PedidoIARepositoryPostgres")


if __name__ == "__main__":
    unittest.main()
