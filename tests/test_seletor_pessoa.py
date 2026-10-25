"""Seletor de pessoa (sem senha): cookie `vagas_pessoa`, cabeçalho `X-Person-Id` e `POST /tela/pessoa`.

Tudo escrito à mão: nomes e vagas inventados (nada aqui é resposta gravada nem dado de gente de verdade).
"""

import json
import os
import re
import unittest
from html import unescape
from unittest import mock

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from src.controller.api import PESSOA_PADRAO, criar_app
from src.controller.pessoa_selecionada import COOKIE_PESSOA, interpretar_id
from src.model.perfil import Perfil
from src.model.provider_pessoa import ProviderPessoa
from src.model.vaga_externa import VagaExterna
from src.repository.base import (
    CurriculoRepositoryMemoria,
    PerfilBuscaRepositoryMemoria,
    VagaEncontradaRepositoryMemoria,
)
from src.repository.candidatura import CandidaturaRepositoryMemoria, vaga_por_url_em
from src.repository.pedido_ia import PedidoIARepositoryMemoria
from src.repository.pessoa import PessoaRepositoryMemoria
from src.repository.pessoa_provider import PessoaProviderRepositoryMemoria
from src.service.busca_service import BuscaService
from src.service.coleta_service import RepositoriosDaPessoa
from tests.test_coleta import ClientPorTermo

HX = {"HX-Request": "true", "Origin": "http://testserver"}
JSON = {"Content-Type": "application/json"}
URL_A = "https://exemplo.test/vaga/a"  # nas duas pessoas, com título diferente
URL_B = "https://exemplo.test/vaga/b"  # só na pessoa 2 (salva por ela)
URL_C = "https://exemplo.test/vaga/c"  # nas duas pessoas; só a 2 se candidatou
NOME_UM = "Pessoa Teste A"
NOME_DOIS = "Pessoa Teste B"


def vaga(url: str, titulo: str) -> VagaExterna:
    return VagaExterna(titulo=titulo, empresa="Empresa Teste", plataforma="Teste", url=url)


class PedidosEspiao(PedidoIARepositoryMemoria):
    """Conta quantas vezes a leitura das métricas passou por ESTE repositório (o da pessoa dona dele)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.leituras_de_metricas = 0

    def listar_prontos_compatibilidade(self, *args, **kwargs):
        self.leituras_de_metricas += 1
        return super().listar_prontos_compatibilidade(*args, **kwargs)


class Mundo:
    """Duas pessoas no mesmo "banco" em memória, com dados diferentes para as mesmas URLs."""

    def __init__(self, nomes=(NOME_UM, NOME_DOIS), providers=()):
        self.ids = list(range(1, len(nomes) + 1))
        self.pessoas = PessoaRepositoryMemoria()
        self.perfis, self.vagas, self.curriculos, self.pedidos, self.candidaturas, self.fontes = {}, {}, {}, {}, {}, {}
        fontes_comuns = PessoaProviderRepositoryMemoria(PESSOA_PADRAO)
        for i, nome in zip(self.ids, nomes):
            self.pessoas.adicionar(i, nome)
            primeiro = i == 1
            self.perfis[i] = PerfilBuscaRepositoryMemoria(i, None if primeiro else self.perfis[1])
            self.vagas[i] = VagaEncontradaRepositoryMemoria(i, compartilhado=None if primeiro else self.vagas[1])
            self.curriculos[i] = CurriculoRepositoryMemoria(i, None if primeiro else self.curriculos[1])
            self.pedidos[i] = PedidosEspiao(
                i, vaga_por_url_em(self.vagas[i]), compartilhado=None if primeiro else self.pedidos[1]
            )
            self.candidaturas[i] = CandidaturaRepositoryMemoria(
                i, vaga_por_url_em(self.vagas[i]), None if primeiro else self.candidaturas[1]
            )
            self.fontes[i] = PessoaProviderRepositoryMemoria(i, fontes_comuns)
        self.providers = providers
        self.app = self.criar_app()

    def repositorios(self, pessoa_id: int) -> RepositoriosDaPessoa:
        return RepositoriosDaPessoa(
            self.perfis[pessoa_id],
            self.vagas[pessoa_id],
            self.curriculos[pessoa_id],
            self.pedidos[pessoa_id],
            self.candidaturas[pessoa_id],
        )

    def criar_app(self) -> FastAPI:
        return criar_app(
            self.perfis[1],
            self.curriculos[1],
            BuscaService(list(self.providers)),
            encontradas=self.vagas[1],
            candidaturas=self.candidaturas[1],
            pedidos_ia=self.pedidos[1],
            pessoas=self.pessoas,
            repositorios_da_pessoa=self.repositorios,
            providers_da_pessoa=lambda pessoa_id: self.fontes[pessoa_id],
        )

    def cliente(self, cookie: str | None = None, **kwargs) -> TestClient:
        api = TestClient(self.app, **kwargs)
        if cookie is not None:
            api.cookies.set(COOKIE_PESSOA, cookie)
        return api


def mundo_com_dados() -> Mundo:
    m = Mundo()
    m.vagas[1].registrar([vaga(URL_A, "Analista Alfa Um"), vaga(URL_C, "Analista Gama Um")], "termo")
    m.vagas[2].registrar(
        [vaga(URL_A, "Analista Alfa Dois"), vaga(URL_B, "Analista Beta Dois"), vaga(URL_C, "Analista Gama Dois")],
        "termo",
    )
    m.vagas[2].marcar_salva(URL_B, True)
    m.perfis[1].salvar(Perfil(termos=["termo da pessoa um"], ocultar_candidatadas=False))
    m.perfis[2].salvar(Perfil(termos=["termo da pessoa dois"], ocultar_candidatadas=False))
    m.candidaturas[2].criar(URL_C)
    m.fontes[2].salvar([ProviderPessoa("ashby", {"empresas": ["empresaquedois"]})])
    m.curriculos[2].salvar(_base("Base da pessoa dois"))
    return m


def _base(titulo):
    from src.model.curriculo import Curriculo

    return Curriculo(tipo="base", titulo=titulo, conteudo_md="# base de teste")


def textarea(html, nome):
    achado = re.search(rf'<textarea[^>]*name="{nome}"[^>]*>(.*?)</textarea>', html, re.S)
    assert achado, nome
    return unescape(achado.group(1))


class InterpretarIdTest(unittest.TestCase):
    def test_so_inteiro_positivo_em_digitos_ascii(self):
        for bom, esperado in (("1", 1), (" 2 ", 2), ("007", 7), ("123456789012345678", 123456789012345678)):
            self.assertEqual(interpretar_id(bom), esperado, bom)
        for ruim in ("", " ", "0", "-1", "+1", "1.0", "1e3", "abc", "1 2", "١٢", "9" * 19, "9" * 5000, None, 1):
            self.assertIsNone(interpretar_id(ruim), repr(ruim)[:20])


class TelaPorPessoaTest(unittest.TestCase):
    def setUp(self):
        self.mundo = mundo_com_dados()

    def test_sem_cookie_a_tela_e_a_da_pessoa_1_como_sempre(self):
        html = self.mundo.cliente().get("/tela").text

        self.assertIn("Analista Alfa Um", html)
        self.assertIn("Analista Gama Um", html)
        for da_dois in ("Alfa Dois", "Beta Dois", "Gama Dois"):
            self.assertNotIn(da_dois, html)

    def test_com_o_cookie_da_2_a_lista_so_tem_as_vagas_da_2(self):
        html = self.mundo.cliente("2").get("/tela").text

        for da_dois in ("Analista Alfa Dois", "Analista Beta Dois", "Analista Gama Dois"):
            self.assertIn(da_dois, html)
        for da_um in ("Alfa Um", "Gama Um"):
            self.assertNotIn(da_um, html)

    def test_detalhe_da_mesma_url_e_o_da_pessoa_escolhida(self):
        for cookie, presente, ausente in (("1", "Alfa Um", "Alfa Dois"), ("2", "Alfa Dois", "Alfa Um")):
            with self.subTest(cookie=cookie):
                html = self.mundo.cliente(cookie).get("/tela/vagas/detalhe", params={"url": URL_A}, headers=HX).text
                self.assertIn(presente, html)
                self.assertNotIn(ausente, html)

    def test_salvas_sao_as_da_pessoa(self):
        da_dois = self.mundo.cliente("2").get("/tela", params={"salvas": "1"}).text
        da_um = self.mundo.cliente().get("/tela", params={"salvas": "1"}).text

        self.assertIn("Analista Beta Dois", da_dois)
        self.assertNotIn("Analista Beta Dois", da_um)
        self.assertNotIn("Analista Alfa Um", da_um)  # a aba Salvas da 1 está vazia

    def test_candidaturas_sao_as_da_pessoa(self):
        da_dois = self.mundo.cliente("2").get("/tela/candidaturas", headers=HX).text
        da_um = self.mundo.cliente().get("/tela/candidaturas", headers=HX).text

        self.assertIn("Analista Gama Dois", da_dois)
        self.assertNotIn("Gama", da_um)
        self.assertIn("Nenhuma candidatura ainda", da_um)

    def test_meu_perfil_e_as_fontes_de_vaga_sao_as_da_pessoa(self):
        da_dois = self.mundo.cliente("2").get("/tela/perfil").text
        da_um = self.mundo.cliente().get("/tela/perfil").text

        self.assertEqual(textarea(da_dois, "termos"), "termo da pessoa dois")
        self.assertEqual(textarea(da_dois, "ashby_empresas"), "empresaquedois")
        self.assertEqual(textarea(da_um, "termos"), "termo da pessoa um")
        self.assertEqual(textarea(da_um, "ashby_empresas"), "")

    def test_salvar_o_perfil_e_as_fontes_na_tela_grava_na_pessoa_do_cookie(self):
        api = self.mundo.cliente("2")

        self.assertEqual(api.put("/tela/perfil", data={"termos": "novo termo dois"}, headers=HX).status_code, 200)
        self.assertEqual(
            api.put("/tela/perfil/providers", data={"usar": ["ashby"], "ashby_empresas": "outraempresa"}, headers=HX).status_code,
            200,
        )

        self.assertEqual(self.mundo.perfis[2].obter().termos, ["novo termo dois"])
        self.assertEqual(self.mundo.perfis[1].obter().termos, ["termo da pessoa um"])
        self.assertEqual(self.mundo.fontes[2].listar(), [ProviderPessoa("ashby", {"empresas": ["outraempresa"]})])
        self.assertEqual(self.mundo.fontes[1].listar(), [])

    def test_salvar_numa_aba_aberta_antes_de_trocar_a_pessoa_grava_na_pessoa_da_aba(self):
        # A aba abriu o perfil da pessoa 1; outra aba trocou o seletor para a 2 (o cookie agora é 2).
        api = self.mundo.cliente("1")
        pagina = api.get("/tela/perfil").text
        cabecalhos = json.loads(unescape(re.search(r"<body hx-headers='([^']*)'", pagina).group(1)))
        api.cookies.set(COOKIE_PESSOA, "2")

        resposta = api.put("/tela/perfil", data={"termos": "salvo na aba da um"}, headers={**HX, **cabecalhos})

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(self.mundo.perfis[1].obter().termos, ["salvo na aba da um"])
        self.assertEqual(self.mundo.perfis[2].obter().termos, ["termo da pessoa dois"])  # a 2 não é sobrescrita

    def test_marcar_salva_e_vista_e_aplicada_na_tela_gravam_na_pessoa_do_cookie(self):
        api = self.mundo.cliente("2")

        api.post("/tela/vagas/salva", data={"url": URL_A, "salvar": "1"}, headers=HX)
        api.post("/tela/vagas/vista", data={"url": URL_A}, headers=HX)

        self.assertIsNotNone(self.mundo.vagas[2].obter(URL_A).salva_em)
        self.assertIsNone(self.mundo.vagas[1].obter(URL_A).salva_em)
        self.assertIsNotNone(self.mundo.vagas[2].obter(URL_A).vista_em)
        self.assertIsNone(self.mundo.vagas[1].obter(URL_A).vista_em)

        api.post("/tela/candidaturas", data={"url": URL_A}, headers=HX)

        self.assertIsNotNone(self.mundo.candidaturas[2].obter_por_url(URL_A))
        self.assertIsNone(self.mundo.candidaturas[1].obter_por_url(URL_A))

    def test_ia_da_tela_pede_para_a_pessoa_do_cookie(self):
        self.mundo.curriculos[1].salvar(_base("Base da pessoa um"))
        api = self.mundo.cliente("2")

        resposta = api.post("/tela/vagas/ia", data={"vaga_url": URL_B, "tipo": "compatibilidade"}, headers=HX)
        api.post("/tela/vagas/ia/rotulo", data={"vaga_url": URL_B, "faixa": "alta"}, headers=HX)

        self.assertEqual(resposta.status_code, 202, resposta.text)
        self.assertEqual(len(self.mundo.pedidos[2].urls_ativas("compatibilidade")), 1)
        self.assertEqual(self.mundo.pedidos[1].urls_ativas("compatibilidade"), set())
        self.assertEqual(self.mundo.pedidos[2].obter_rotulo(URL_B).value, "alta")

    def test_curriculos_da_tela_sao_os_da_pessoa(self):
        for pessoa in (1, 2):
            base = self.mundo.curriculos[pessoa].obter_base() or self.mundo.curriculos[pessoa].salvar(_base("b"))
            adaptado = self.mundo.curriculos[pessoa].salvar(
                _adaptado(f"Adaptado da pessoa {pessoa}", base.id, URL_A)
            )
            setattr(self, f"adaptado{pessoa}", adaptado)

        da_dois = self.mundo.cliente("2").get("/tela/curriculos/da-vaga", params={"url": URL_A}, headers=HX).text
        baixado = self.mundo.cliente("1").get(f"/tela/curriculos/{self.adaptado2.id}.md")

        self.assertIn("Adaptado da pessoa 2", da_dois)
        self.assertNotIn("Adaptado da pessoa 1", da_dois)
        self.assertEqual(baixado.status_code, 404)  # o adaptado da 2 não abre pela 1
        self.assertEqual(self.mundo.cliente("2").get(f"/tela/curriculos/{self.adaptado2.id}.md").status_code, 200)

    def test_coleta_da_tela_mostra_o_resumo_da_pessoa_do_cookie(self):
        provider = ClientPorTermo("Teste", {"termo da pessoa um": [vaga("https://exemplo.test/x1", "Vaga X1")]})
        provider.por_termo["termo da pessoa dois"] = [vaga("https://exemplo.test/y1", "Vaga Y1"), vaga("https://exemplo.test/y2", "Vaga Y2")]
        mundo = mundo_com_dados()
        mundo.fontes[2].salvar([])  # sem fontes próprias, a pessoa 2 usa os providers do ambiente (o `provider`)
        mundo.providers = [provider]
        mundo.app = mundo.criar_app()

        html = mundo.cliente("2").post("/tela/coletas", data={}, headers=HX).text

        self.assertEqual(sorted(provider.termos_buscados), ["termo da pessoa dois", "termo da pessoa um"])  # coletou todas
        self.assertIn("Vaga Y1", html)
        self.assertNotIn("Vaga X1", html)


def _adaptado(titulo, base_id, vaga_url):
    from src.model.curriculo import Curriculo

    return Curriculo(tipo="adaptado", titulo=titulo, conteudo_md="# adaptado", base_id=base_id, vaga_url=vaga_url)


class CookieInvalidoTest(unittest.TestCase):
    def test_cookie_torto_ou_de_pessoa_que_nao_existe_cai_na_pessoa_1(self):
        mundo = mundo_com_dados()

        for cookie in ("abc", "", "0", "-2", "3", "99", "1.5", "2 2", "9" * 5000):
            with self.subTest(cookie=cookie[:12]):
                resposta = mundo.cliente(cookie).get("/tela")
                self.assertEqual(resposta.status_code, 200)
                self.assertIn("Analista Alfa Um", resposta.text)
                self.assertNotIn("Alfa Dois", resposta.text)

    def test_cookie_invalido_na_api_json_tambem_cai_na_1(self):
        mundo = mundo_com_dados()

        resposta = mundo.cliente("99").get("/perfil")

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json()["termos"], ["termo da pessoa um"])


class ApiPorCabecalhoTest(unittest.TestCase):
    def setUp(self):
        self.mundo = mundo_com_dados()
        self.api = self.mundo.cliente()

    def com(self, id_):
        return {"X-Person-Id": id_}

    def test_sem_cabecalho_nem_cookie_e_a_pessoa_1(self):
        self.assertEqual(self.api.get("/perfil").json()["termos"], ["termo da pessoa um"])

    def test_o_cabecalho_escolhe_a_pessoa_em_cada_rota_json(self):
        dois = self.com("2")

        self.assertEqual(self.api.get("/perfil", headers=dois).json()["termos"], ["termo da pessoa dois"])
        self.assertEqual(self.api.get("/curriculos/base", headers=dois).json()["titulo"], "Base da pessoa dois")
        self.assertEqual(self.api.get("/curriculos/base").status_code, 404)  # a 1 não tem base
        self.assertEqual(self.api.get("/curriculos/adaptados", headers=dois).json(), [])
        self.assertEqual(
            self.api.get("/perfil/providers", headers=dois).json(),
            {"providers": [{"provider": "ashby", "config": {"empresas": ["empresaquedois"]}}]},
        )
        self.assertEqual(self.api.get("/perfil/providers").json(), {"providers": []})
        urls_dois = {v["url"] for v in self.api.get("/vagas/encontradas", params={"novas": "false"}, headers=dois).json()}
        urls_um = {v["url"] for v in self.api.get("/vagas/encontradas", params={"novas": "false"}).json()}
        self.assertEqual(urls_dois, {URL_A, URL_B, URL_C})
        self.assertEqual(urls_um, {URL_A, URL_C})
        titulos = {v["titulo"] for v in self.api.get("/vagas/encontradas", params={"novas": "false"}, headers=dois).json()}
        self.assertIn("Analista Alfa Dois", titulos)
        self.assertEqual(
            [c["vaga_url"] for c in self.api.get("/candidaturas", headers=dois).json()], [URL_C]
        )
        self.assertEqual(self.api.get("/candidaturas").json(), [])

    def test_escritas_json_gravam_so_na_pessoa_do_cabecalho(self):
        dois = {**self.com("2"), **JSON}

        self.assertEqual(self.api.put("/perfil", json={"termos": ["via cabecalho"]}, headers=dois).status_code, 200)
        self.assertEqual(self.api.put("/curriculos/base", json={"titulo": "Nova", "conteudo_md": "# n"}, headers=dois).status_code, 200)
        self.assertEqual(self.api.put("/perfil/providers", json={"providers": []}, headers=dois).status_code, 200)
        self.assertEqual(self.api.post("/vagas/vistas", json={"urls": [URL_A]}, headers=dois).json(), {"marcadas": 1})
        self.assertEqual(self.api.post("/candidaturas", json={"vaga_url": URL_A}, headers=dois).status_code, 201)
        adaptado = self.api.post("/curriculos/adaptados", json={"titulo": "Ad", "conteudo_md": "# a", "vaga_url": URL_A}, headers=dois)
        self.assertEqual(adaptado.status_code, 201, adaptado.text)
        rotulo = self.api.put("/vagas/rotulo-ia", json={"vaga_url": URL_B, "faixa": "alta"}, headers=dois)
        self.assertEqual(rotulo.status_code, 200, rotulo.text)

        self.assertEqual(self.mundo.perfis[2].obter().termos, ["via cabecalho"])
        self.assertEqual(self.mundo.perfis[1].obter().termos, ["termo da pessoa um"])
        self.assertEqual(self.mundo.curriculos[2].obter_base().titulo, "Nova")
        self.assertIsNone(self.mundo.curriculos[1].obter_base())
        self.assertEqual(self.mundo.fontes[2].listar(), [])
        self.assertIsNotNone(self.mundo.vagas[2].obter(URL_A).vista_em)
        self.assertIsNone(self.mundo.vagas[1].obter(URL_A).vista_em)
        self.assertIsNotNone(self.mundo.candidaturas[2].obter_por_url(URL_A))
        self.assertIsNone(self.mundo.candidaturas[1].obter_por_url(URL_A))
        self.assertEqual([c.titulo for c in self.mundo.curriculos[2].listar_adaptados()], ["Ad"])
        self.assertEqual(self.mundo.curriculos[1].listar_adaptados(), [])
        self.assertEqual(self.mundo.pedidos[2].obter_rotulo(URL_B).value, "alta")
        self.assertIsNone(self.mundo.pedidos[1].obter_rotulo(URL_B))

    def test_metricas_leem_os_pedidos_da_pessoa_do_cabecalho(self):
        self.assertEqual(self.api.get("/ia/metricas", headers=self.com("2")).status_code, 200)

        self.assertEqual(self.mundo.pedidos[2].leituras_de_metricas, 1)
        self.assertEqual(self.mundo.pedidos[1].leituras_de_metricas, 0)

    def test_busca_ao_vivo_usa_as_fontes_da_pessoa_do_cabecalho(self):
        provider = ClientPorTermo("Teste", {"python": [vaga("https://exemplo.test/p1", "Dev Python")]})
        mundo = mundo_com_dados()
        mundo.fontes[2].salvar([])  # sem fontes próprias, a pessoa 2 usa os providers do ambiente (o `provider`)
        mundo.providers = [provider]
        mundo.app = mundo.criar_app()

        dois = mundo.cliente().get("/vagas", params={"termo": "python"}, headers=self.com("2"))

        self.assertEqual(dois.status_code, 200)
        self.assertEqual([v["titulo"] for v in dois.json()["vagas"]], ["Dev Python"])
        # O perfil aplicado é o da pessoa 2 (excluir só dela): prova que o `perfis` do contexto é o dela.
        mundo.perfis[2].salvar(Perfil(termos=["python"], excluir=["python"]))
        self.assertEqual(
            mundo.cliente().get("/vagas", params={"termo": "python"}, headers=self.com("2")).json()["vagas"], []
        )
        self.assertEqual(
            len(mundo.cliente().get("/vagas", params={"termo": "python"}).json()["vagas"]), 1
        )

    def test_coleta_roda_todas_e_devolve_o_resultado_da_pessoa_do_cabecalho(self):
        provider = ClientPorTermo(
            "Teste",
            {
                "termo da pessoa um": [vaga("https://exemplo.test/x1", "Vaga X1")],
                "termo da pessoa dois": [vaga("https://exemplo.test/y1", "Vaga Y1"), vaga("https://exemplo.test/y2", "Vaga Y2")],
            },
        )
        mundo = mundo_com_dados()
        mundo.fontes[2].salvar([])  # sem fontes próprias, a pessoa 2 usa os providers do ambiente (o `provider`)
        mundo.providers = [provider]
        mundo.app = mundo.criar_app()

        dois = mundo.cliente().post("/coletas", json={}, headers={**self.com("2"), **JSON})
        um = mundo.cliente().post("/coletas", json={}, headers=JSON)

        self.assertEqual(dois.status_code, 200, dois.text)
        self.assertEqual([t["termo"] for t in dois.json()["termos"]], ["termo da pessoa dois"])
        self.assertEqual(dois.json()["novas"], 2)
        self.assertEqual([t["termo"] for t in um.json()["termos"]], ["termo da pessoa um"])
        self.assertEqual(um.json()["novas"], 0)  # a primeira coleta já gravou a vaga X1 (da pessoa 1)
        self.assertEqual(sorted(provider.termos_buscados), sorted(["termo da pessoa dois", "termo da pessoa um"] * 2))
        self.assertIn("https://exemplo.test/x1", {v.url for v in mundo.vagas[1].listar(False, 50)})
        self.assertIn("https://exemplo.test/y1", {v.url for v in mundo.vagas[2].listar(False, 50)})
        self.assertNotIn("https://exemplo.test/y1", {v.url for v in mundo.vagas[1].listar(False, 50)})

    def test_cabecalho_invalido_e_422_em_json_e_na_tela(self):
        gigante = "9" * 5000
        for valor in ("99", "abc", "0", "-1", "", " ", "1.5", "+2", gigante):
            with self.subTest(valor=valor[:12]):
                json_ = self.api.get("/perfil", headers=self.com(valor))
                self.assertEqual(json_.status_code, 422, json_.text)
                self.assertIn("X-Person-Id", json_.json()["detail"])
                tela = self.api.get("/tela", headers=self.com(valor))
                self.assertEqual(tela.status_code, 422)
                self.assertIn("X-Person-Id", tela.text)
                self.assertIn("<html", tela.text)  # o aviso da tela, não o JSON

    def test_cabecalho_vale_mais_que_o_cookie(self):
        api = self.mundo.cliente("1")

        self.assertEqual(api.get("/perfil", headers=self.com("2")).json()["termos"], ["termo da pessoa dois"])
        self.assertIn("Alfa Dois", api.get("/tela", headers=self.com("2")).text)
        # Cabeçalho inexistente NÃO cai no cookie nem na pessoa 1: é erro.
        self.assertEqual(self.mundo.cliente("2").get("/perfil", headers=self.com("99")).status_code, 422)

    def test_cabecalho_com_espacos_nas_pontas_vale(self):
        self.assertEqual(self.api.get("/perfil", headers=self.com(" 2 ")).json()["termos"], ["termo da pessoa dois"])


class TrocarPessoaTest(unittest.TestCase):
    def setUp(self):
        self.mundo = mundo_com_dados()
        self.api = self.mundo.cliente()

    def test_grava_o_cookie_e_manda_para_a_tela(self):
        resposta = self.api.post("/tela/pessoa", data={"pessoa_id": "2"}, headers=HX)

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.headers["HX-Redirect"], "/tela")
        cookie = resposta.headers["set-cookie"]
        self.assertTrue(cookie.startswith(f"{COOKIE_PESSOA}=2;"), cookie)
        atributos = cookie.lower()
        for esperado in ("httponly", "samesite=lax", "path=/;", f"max-age={365 * 24 * 60 * 60}"):
            self.assertIn(esperado, atributos)

    def test_depois_de_trocar_a_tela_e_a_api_seguem_a_pessoa_nova(self):
        self.api.post("/tela/pessoa", data={"pessoa_id": "2"}, headers=HX)

        self.assertIn("Analista Alfa Dois", self.api.get("/tela").text)
        self.assertEqual(self.api.get("/perfil").json()["termos"], ["termo da pessoa dois"])
        self.api.post("/tela/pessoa", data={"pessoa_id": "1"}, headers=HX)
        self.assertIn("Analista Alfa Um", self.api.get("/tela").text)

    def test_sem_a_protecao_da_tela_e_403_e_nao_grava_cookie(self):
        casos = (
            {},  # sem HX-Request (formulário de outro site)
            {"HX-Request": "true"},  # sem Origin nem Sec-Fetch-Site
            {"HX-Request": "true", "Origin": "http://outro.test"},
            {"HX-Request": "true", "Origin": "null"},
        )
        for cabecalhos in casos:
            with self.subTest(cabecalhos=cabecalhos):
                resposta = self.mundo.cliente().post("/tela/pessoa", data={"pessoa_id": "2"}, headers=cabecalhos)
                self.assertEqual(resposta.status_code, 403)
                self.assertNotIn("set-cookie", resposta.headers)

    def test_pessoa_inexistente_ou_torta_e_422_com_o_aviso_da_tela_e_sem_cookie(self):
        for valor in ("99", "0", "-1", "abc", "", "1.5", "9" * 5000):
            with self.subTest(valor=valor[:12]):
                resposta = self.api.post("/tela/pessoa", data={"pessoa_id": valor}, headers=HX)
                self.assertEqual(resposta.status_code, 422)
                self.assertEqual(resposta.headers["HX-Retarget"], "#aviso")
                self.assertIn("pessoa_id", resposta.text)
                self.assertNotIn("set-cookie", resposta.headers)
        self.assertNotIn(COOKIE_PESSOA, self.api.cookies)

    def test_sem_o_campo_e_422(self):
        self.assertEqual(self.api.post("/tela/pessoa", headers=HX).status_code, 422)

    def test_resposta_de_tela_nao_e_cacheada(self):
        resposta = self.api.post("/tela/pessoa", data={"pessoa_id": "2"}, headers=HX)

        self.assertEqual(resposta.headers["Cache-Control"], "no-store")


class SeletorNoCabecalhoTest(unittest.TestCase):
    def selecionadas(self, html):
        return re.findall(r'<option value="(\d+)" selected>', html)

    def test_com_uma_pessoa_so_o_seletor_nao_aparece(self):
        mundo = Mundo(nomes=(NOME_UM,))

        for caminho in ("/tela", "/tela/perfil"):
            html = mundo.cliente().get(caminho).text
            self.assertNotIn("seletor-pessoa", html, caminho)
            self.assertNotIn("/tela/pessoa", html, caminho)
            self.assertNotIn(NOME_UM, html, caminho)

    def test_com_duas_pessoas_aparece_com_os_nomes_e_a_escolhida_marcada(self):
        mundo = Mundo()

        padrao = mundo.cliente().get("/tela").text
        dois = mundo.cliente("2").get("/tela").text
        invalido = mundo.cliente("77").get("/tela").text

        for html in (padrao, dois):
            self.assertIn('hx-post="/tela/pessoa"', html)
            self.assertIn('name="pessoa_id"', html)
            self.assertIn(f">{NOME_UM}</option>", html)
            self.assertIn(f">{NOME_DOIS}</option>", html)
        self.assertEqual(self.selecionadas(padrao), ["1"])
        self.assertEqual(self.selecionadas(dois), ["2"])
        self.assertEqual(self.selecionadas(invalido), ["1"])  # cookie inválido cai na 1, e o seletor mostra isso

    def test_aparece_nas_outras_paginas_da_tela(self):
        mundo = Mundo()

        self.assertEqual(self.selecionadas(mundo.cliente("2").get("/tela/perfil").text), ["2"])

    def test_o_nome_e_escapado(self):
        perigoso = '<script>alert("x")</script> & <b>Zé</b>'
        mundo = Mundo(nomes=(NOME_UM, perigoso))

        html = mundo.cliente().get("/tela").text

        self.assertNotIn("<script>alert", html)
        self.assertNotIn("<b>Zé</b>", html)
        self.assertIn("&lt;script&gt;alert(&#34;x&#34;)&lt;/script&gt; &amp; &lt;b&gt;Zé&lt;/b&gt;", html)

    def test_sem_js_nem_css_inline(self):
        html = Mundo().cliente().get("/tela").text

        self.assertNotRegex(html, r"<script(?![^>]*\bsrc=)")
        self.assertNotIn("<style", html)
        self.assertNotRegex(html, r'\bstyle="')
        self.assertNotRegex(html, r'\son[a-z]+="')

    def test_a_ordem_e_a_dos_ids(self):
        mundo = Mundo(nomes=("Zeta Teste", "Alfa Teste"))

        html = mundo.cliente().get("/tela").text

        self.assertLess(html.index("Zeta Teste"), html.index("Alfa Teste"))

    def test_nome_da_pessoa_nao_vai_para_o_log(self):
        mundo = Mundo(nomes=("Nome Que Nao Pode Logar", NOME_DOIS))
        api = mundo.cliente("2")

        with self.assertLogs("src", level="DEBUG") as capturado:
            import logging

            logging.getLogger("src.teste").debug("marcador")  # assertLogs exige pelo menos um registro
            api.get("/tela")
            api.post("/tela/pessoa", data={"pessoa_id": "2"}, headers=HX)
            api.post("/tela/pessoa", data={"pessoa_id": "99"}, headers=HX)
            api.get("/perfil", headers={"X-Person-Id": "99"})
        texto = "\n".join(capturado.output)
        self.assertNotIn("Nome Que Nao Pode Logar", texto)
        self.assertNotIn(NOME_DOIS, texto)


# --- Toda rota com dados da pessoa tem que usar a dependência -------------------------------------------------


def _depende_de(dependente, chamada) -> bool:
    return any(d.call is chamada or _depende_de(d, chamada) for d in dependente.dependencies)


def rotas_da_api(app: FastAPI):
    """(métodos, caminho, dependant) de cada rota da API, também as de routers incluídos. O FastAPI novo guarda
    `include_router` como ramos preguiçosos em `app.routes`: `iter_route_contexts` os resolve (com o prefixo e as
    dependências do include); numa versão sem ele, `app.routes` já tem as `APIRoute`."""
    from fastapi import routing

    if hasattr(routing, "iter_route_contexts"):
        for contexto in routing.iter_route_contexts(app.router.routes):
            if isinstance(contexto.original_route, APIRoute):
                yield contexto.methods, contexto.path, contexto.dependant
    else:
        for rota in app.routes:
            if isinstance(rota, APIRoute):
                yield rota.methods, rota.path, rota.dependant


def rotas_sem_pessoa(app: FastAPI, chamada, excecoes: set[tuple[str, str]]) -> list[tuple[str, str]]:
    """(método, caminho) das rotas da API que não usam a dependência de pessoa, fora das exceções explícitas."""
    faltando = []
    for metodos, caminho, dependente in rotas_da_api(app):
        for metodo in sorted(metodos - {"HEAD"}):
            if (metodo, caminho) not in excecoes and not _depende_de(dependente, chamada):
                faltando.append((metodo, caminho))
    return faltando


# Fora da pessoa da requisição, de propósito:
EXCECOES = {
    ("GET", "/"),  # redireciona para /tela
    ("GET", "/saude"),
    ("POST", "/pedidos-ia/proximo"),  # fila global do worker (Bearer): a pessoa vem do pedido
    ("PUT", "/pedidos-ia/{id}"),  # idem
    ("POST", "/tela/pessoa"),  # troca a pessoa: não lê dado de ninguém
}


class TodaRotaUsaAPessoaTest(unittest.TestCase):
    def test_nenhuma_rota_da_tela_nem_json_fica_de_fora(self):
        app = Mundo().app

        faltando = rotas_sem_pessoa(app, app.state.pessoa_da_requisicao, EXCECOES)

        self.assertEqual(faltando, [], "rota sem a dependência de pessoa (cairia na pessoa 1)")

    def test_as_excecoes_existem_e_o_teste_cobre_muitas_rotas(self):
        app = Mundo().app
        rotas = {(m, caminho) for metodos, caminho, _ in rotas_da_api(app) for m in metodos}

        self.assertTrue(EXCECOES <= rotas, EXCECOES - rotas)  # exceção velha não fica esquecida
        com_pessoa = [
            (m, p) for (m, p) in rotas if (m, p) not in EXCECOES and m != "HEAD"
        ]
        self.assertGreater(len(com_pessoa), 40)
        self.assertGreaterEqual(len([1 for _, p in com_pessoa if p.startswith("/tela")]), 25)

    def test_o_verificador_acusa_a_rota_que_esqueceu_a_dependencia(self):
        from fastapi import Depends

        def pessoa() -> int:
            return 1

        pequeno = FastAPI()

        @pequeno.get("/com")
        def com(_: int = Depends(pessoa)):
            return {}

        @pequeno.get("/sem")
        def sem():
            return {}

        self.assertEqual(rotas_sem_pessoa(pequeno, pessoa, set()), [("GET", "/sem")])
        self.assertEqual(rotas_sem_pessoa(pequeno, pessoa, {("GET", "/sem")}), [])


# --- Repositório de pessoas -------------------------------------------------------------------------------------


class PessoaRepositorioTest(unittest.TestCase):
    def test_memoria_lista_com_nome_em_ordem_de_id(self):
        pessoas = PessoaRepositoryMemoria()
        pessoas.adicionar(3, "Pessoa Teste C")
        pessoas.adicionar(1)

        listadas = pessoas.listar_com_nome()

        self.assertEqual([(p.id, p.nome_exibicao) for p in listadas], [(1, "Pessoa Teste 1"), (3, "Pessoa Teste C")])
        self.assertEqual(pessoas.listar(), [1, 3])


@unittest.skipUnless(os.environ.get("PGHOST"), "PGHOST não definido: seletor com Postgres pulado")
class SeletorPostgresTest(unittest.TestCase):
    def setUp(self):
        from src.repository.pessoa_postgres import PessoaRepositoryPostgres
        from tests.test_coleta import _schema_descartavel

        self.conn, self.schema = _schema_descartavel(self, "teste_seletor")
        self.conn.execute("INSERT INTO pessoa (nome_exibicao) VALUES ('Pessoa Teste B')")
        self.pessoas = PessoaRepositoryPostgres(self.conn)

    def test_lista_com_nome_em_ordem_de_id(self):
        listadas = self.pessoas.listar_com_nome()

        self.assertEqual([p.id for p in listadas], [1, 2])
        self.assertEqual(listadas[1].nome_exibicao, "Pessoa Teste B")

    def test_producao_cada_requisicao_usa_a_pessoa_do_cookie_com_uma_conexao_do_pool(self):
        from psycopg_pool import ConnectionPool

        from src.controller import api as modulo
        from src.repository.candidatura_postgres import CandidaturaRepositoryPostgres
        from src.repository.conexao import parametros
        from src.repository.postgres import PerfilBuscaRepositoryPostgres, VagaEncontradaRepositoryPostgres

        pool = ConnectionPool(
            kwargs={**parametros(), "autocommit": True, "options": f"-c search_path={self.schema}"},
            min_size=1,
            max_size=1,  # uma conexão só: um repositório que a segurasse entre chamadas travaria a requisição
            open=True,
        )
        self.addCleanup(pool.close)
        for pessoa, titulo in ((1, "Analista Pg Um"), (2, "Analista Pg Dois")):
            VagaEncontradaRepositoryPostgres(self.conn, pessoa).registrar([vaga(URL_A, titulo)], "termo")
            PerfilBuscaRepositoryPostgres(self.conn, pessoa).salvar(Perfil(termos=[f"termo pg {pessoa}"]))
        CandidaturaRepositoryPostgres(self.conn, 2).criar(URL_A)

        with mock.patch.object(modulo, "_PoolPreguicoso", lambda: (lambda: pool)):
            app = modulo.criar_app_producao()
        api = TestClient(app)

        um = api.get("/tela")
        dois = TestClient(app, cookies={COOKIE_PESSOA: "2"})
        self.assertIn("Analista Pg Um", um.text)
        self.assertNotIn("Analista Pg Dois", um.text)
        self.assertIn(f'<option value="2">', um.text)
        self.assertEqual(api.get("/perfil").json()["termos"], ["termo pg 1"])
        self.assertEqual(api.get("/perfil", headers={"X-Person-Id": "2"}).json()["termos"], ["termo pg 2"])
        self.assertEqual(dois.get("/perfil").json()["termos"], ["termo pg 2"])
        self.assertIn("Analista Pg Dois", dois.get("/tela/candidaturas", headers=HX).text)
        self.assertEqual(api.get("/candidaturas").json(), [])
        self.assertEqual([c["vaga_url"] for c in dois.get("/candidaturas").json()], [URL_A])
        self.assertEqual(api.get("/perfil", headers={"X-Person-Id": "9"}).status_code, 422)
        trocou = api.post("/tela/pessoa", data={"pessoa_id": "2"}, headers=HX)
        self.assertEqual(trocou.status_code, 200)
        self.assertEqual(api.get("/perfil").json()["termos"], ["termo pg 2"])


if __name__ == "__main__":
    unittest.main()
