"""Histórico: anúncios repetidos agrupados (bug 4), foto do instante (bug 6) e filtros do perfil (bug 7).

Todas as vagas daqui são escritas à mão (não são respostas gravadas de provider).
"""

import unittest
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

from fastapi.testclient import TestClient

from src.controller.api import criar_app
from src.model.perfil import Perfil
from src.model.vaga_externa import VagaExterna
from src.repository.base import CurriculoRepositoryMemoria, PerfilBuscaRepositoryMemoria, VagaEncontradaRepositoryMemoria
from src.service.busca_service import BuscaService
from src.service.coleta_service import ColetaService
from src.model.erros import DadoInvalido
from src.service.recomendacao_service import RecomendacaoService, aplicar_perfil, filtrar_pelo_perfil
from src.service.historico_service import HistoricoService, chave_do_grupo, ler_ate
from tests.test_tela_lista import HX, Pagina

HOJE = date(2026, 10, 12)
MUTACAO = {"HX-Request": "true", "Origin": "http://testserver"}


def v(url, titulo="Analista de Power BI Sênior", empresa="Deal Group", plataforma="Remotar", **campos):
    campos.setdefault("local", "Remoto")
    campos.setdefault("remoto", True)
    return VagaExterna(titulo, empresa, plataforma, url, **campos)


def urls(vagas) -> list[str]:
    return [x.url for x in vagas]


def registrar_um_a_um(repo, vagas, termo="t"):
    """Um `registrar` por vaga: cada uma com a sua `primeira_vez_em`, na ordem dada (a última é a mais nova)."""
    for vaga in vagas:
        repo.registrar([vaga], termo)


class ChaveDoGrupoTest(unittest.TestCase):
    def chave(self, titulo, empresa):
        return chave_do_grupo(_encontrada(titulo, empresa))

    def test_normaliza_acento_caixa_hifen_plural_e_ligacao(self):
        base = self.chave("Analista de Power BI Sênior", "Deal Group")
        self.assertIsNotNone(base)
        for titulo, empresa in [
            ("ANALISTA DE POWER BI SENIOR", "DEAL GROUP"),
            ("Analistas de Power BI Sênior", "Deal Groups"),
            ("analista power bi sênior", "deal group"),
            ("Analista  de  Power BI Sênior!", "Deal Group "),
        ]:
            with self.subTest(titulo=titulo):
                self.assertEqual(self.chave(titulo, empresa), base)

    def test_hifen_entre_letras_junta_como_no_resto_do_projeto(self):
        self.assertEqual(self.chave("Front-end Developer", "X Corp"), self.chave("Front end Developer", "X Corp"))
        self.assertEqual(self.chave("Front-end Developer", "X Corp"), self.chave("Frontend Developer", "X Corp"))

    def test_palavra_diferente_ou_empresa_diferente_nao_agrupa(self):
        base = self.chave("Analista de Power BI Sênior", "Deal Group")
        self.assertNotEqual(base, self.chave("Analista de Power BI Pleno", "Deal Group"))
        self.assertNotEqual(base, self.chave("Analista de Power BI Sênior", "Outra Empresa"))
        self.assertNotEqual(self.chave("Java Developer", "X"), self.chave("JavaScript Developer", "X"))

    def test_sem_empresa_ou_sem_titulo_util_nao_tem_chave(self):
        for titulo, empresa in [("Analista de Dados", ""), ("Analista de Dados", "  "), ("Analista de Dados", "—"), ("de", "X"), ("", "X")]:
            with self.subTest(titulo=titulo, empresa=empresa):
                self.assertIsNone(self.chave(titulo, empresa))


def _encontrada(titulo, empresa):
    from src.model.vaga_encontrada import VagaEncontrada

    return VagaEncontrada(url="u", titulo=titulo, empresa=empresa, plataforma="P")


class ContratoAgrupamento:
    """Mesmos casos na memória e no Postgres; a subclasse define `self.repo` e `self.perfis`."""

    def setUp(self):
        self.criar()
        self.servico = HistoricoService(self.repo, self.perfis)

    def pagina(self, limite=50, deslocamento=0, novas=False):
        return self.servico.pagina(novas, limite, deslocamento=deslocamento, hoje=HOJE)

    def test_duplicata_vira_uma_vaga_e_o_contador_conta_o_grupo(self):
        registrar_um_a_um(self.repo, [v("a1"), v("a2"), v("outra", "Analista de BI", "Zeta")])

        for perfil in (None, Perfil(termos=["analista"])):
            with self.subTest(perfil=perfil):
                if perfil:
                    self.perfis.salvar(perfil)
                pagina = self.pagina()
                self.assertEqual(pagina.total, 2)
                self.assertEqual(len(pagina.vagas), 2)
                self.assertEqual(self.servico.contar(False, hoje=HOJE), 2)

    def test_mostra_o_anuncio_mais_recente_do_grupo(self):
        registrar_um_a_um(self.repo, [v("a1"), v("a2"), v("a3")])

        self.assertEqual(urls(self.pagina().vagas), ["a3"])

    def test_grupo_fica_na_posicao_do_melhor_ranqueado_e_nao_na_do_anuncio_mostrado(self):
        # Com o perfil, a1 (a mais antiga) tem publicada_em maior: ranqueia acima de "meio"; a2 (a mais nova
        # coletada) ranqueia abaixo. O grupo fica acima de "meio" mostrando a2.
        self.perfis.salvar(Perfil(termos=["power bi"]))
        registrar_um_a_um(
            self.repo,
            [
                v("a1", publicada_em=date(2026, 10, 10)),
                v("meio", "Analista de Power BI", "Zeta", publicada_em=date(2026, 10, 5)),
                v("a2", publicada_em=date(2026, 10, 1)),
            ],
        )

        self.assertEqual(urls(self.pagina().vagas), ["a2", "meio"])

    def test_paginacao_nao_parte_o_grupo_e_nao_repete(self):
        vagas = []
        for i in range(7):
            vagas += [v(f"g{i}-a", f"Analista {i}", f"Empresa {i}"), v(f"g{i}-b", f"Analista {i}", f"Empresa {i}")]
        registrar_um_a_um(self.repo, vagas)

        lidas = []
        for deslocamento in (0, 3, 6):
            pagina = self.pagina(limite=3, deslocamento=deslocamento)
            self.assertEqual(pagina.total, 7)
            lidas += urls(pagina.vagas)

        self.assertEqual(len(lidas), 7)
        self.assertEqual(len(set(lidas)), 7)
        self.assertTrue(all(u.endswith("-b") for u in lidas), "mostra sempre o mais recente de cada grupo")

    def test_empresa_vazia_nao_agrupa_titulos_genericos(self):
        registrar_um_a_um(
            self.repo, [v("s1", "Analista de Dados", ""), v("s2", "Analista de Dados", ""), v("s3", "Analista de Dados", "Z")]
        )

        self.assertEqual(self.pagina().total, 3)
        self.assertEqual(self.servico.outros_anuncios(self.repo.obter("s1"), hoje=HOJE), [])

    def test_outros_anuncios_lista_os_demais_do_grupo_do_mais_recente_ao_mais_antigo(self):
        registrar_um_a_um(self.repo, [v("a1"), v("a2"), v("a3"), v("outra", "Analista de BI", "Zeta")])

        self.assertEqual(urls(self.servico.outros_anuncios(self.repo.obter("a3"), hoje=HOJE)), ["a2", "a1"])
        self.assertEqual(urls(self.servico.outros_anuncios(self.repo.obter("a1"), hoje=HOJE)), ["a3", "a2"])
        self.assertEqual(self.servico.outros_anuncios(self.repo.obter("outra"), hoje=HOJE), [])
        self.assertEqual(self.servico.urls_do_grupo(self.repo.obter("a3"), hoje=HOJE), ["a3", "a2", "a1"])

    def test_outros_anuncios_respeita_o_perfil(self):
        registrar_um_a_um(self.repo, [v("a1"), v("a2")])
        # Mesma chave exige o mesmo título: o perfil esconde pelo local, não pelo título.
        self.perfis.salvar(Perfil(termos=["power bi"], localidades=["Curitiba"]))
        registrar_um_a_um(self.repo, [v("a3", local="Curitiba", remoto=False), v("a4", local="Recife", remoto=False)])

        self.assertEqual(urls(self.servico.outros_anuncios(self.repo.obter("a3"), hoje=HOJE)), ["a2", "a1"])
        self.assertNotIn("a4", self.servico.urls_do_grupo(self.repo.obter("a3"), hoje=HOJE))

    def test_com_busca_o_grupo_aparece_uma_vez(self):
        registrar_um_a_um(self.repo, [v("a1"), v("a2")])

        pagina = self.servico.pagina(False, 50, q="power", hoje=HOJE)

        self.assertEqual((urls(pagina.vagas), pagina.total), (["a2"], 1))


class AgrupamentoMemoriaTest(ContratoAgrupamento, unittest.TestCase):
    def criar(self):
        self.repo = VagaEncontradaRepositoryMemoria()
        self.perfis = PerfilBuscaRepositoryMemoria()


def criar_tela(vagas=(), perfil=None):
    repo = VagaEncontradaRepositoryMemoria()
    registrar_um_a_um(repo, vagas)
    perfis = PerfilBuscaRepositoryMemoria()
    if perfil:
        perfis.salvar(perfil)
    app = criar_app(perfis, CurriculoRepositoryMemoria(), BuscaService([]), encontradas=repo)
    return TestClient(app, raise_server_exceptions=False), repo


class TelaAgrupamentoTest(unittest.TestCase):
    def test_lista_mostra_um_card_e_o_detalhe_lista_os_outros_anuncios_com_link_codificado(self):
        a1, a2 = "https://exemplo.test/vaga com espaço/1?a=1&b=2#c", "https://exemplo.test/conciliação/2"
        cliente, _ = criar_tela([v(a1, plataforma="Trampos"), v(a2)])

        pagina = Pagina(cliente.get("/tela").text)

        self.assertEqual([c.attrs["data-url"] for c in pagina.cards()], [a2])
        self.assertIn("1 encontradas", pagina.por_id("vagas").texto)
        [secao] = [e for e in pagina.tags("section") if "outros-anuncios" in (e.attrs.get("class") or "")]
        self.assertIn("Outros anúncios", secao.texto)
        # O link dos outros anúncios aponta para a outra url, codificada (ida e volta).
        links = [e for e in pagina.tags("a") if e.attrs.get("href") == "/tela?url=" + quote(a1, safe="/")]
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].attrs["hx-get"], "/tela/vagas/detalhe?url=" + quote(a1, safe="/"))
        self.assertIn("Trampos", links[0].texto)
        volta = Pagina(cliente.get(links[0].attrs["hx-get"], headers=HX).text)
        self.assertEqual(volta.por_id("detalhe").attrs["data-url"], a1)
        # O detalhe do outro anúncio lista o primeiro como "outro".
        self.assertTrue([e for e in volta.tags("a") if e.attrs.get("href") == "/tela?url=" + quote(a2, safe="/")])

    def test_sem_duplicata_o_detalhe_nao_tem_outros_anuncios(self):
        cliente, _ = criar_tela([v("https://exemplo.test/1"), v("https://exemplo.test/2", "Outro Cargo")])

        self.assertNotIn("Outros anúncios", cliente.get("/tela/vagas/detalhe?url=https://exemplo.test/1", headers=HX).text)
        self.assertNotIn("Outros anúncios", cliente.get("/tela").text)

    def test_titulo_de_outro_anuncio_e_escapado(self):
        cliente, _ = criar_tela([v("https://exemplo.test/1", plataforma="<script>x</script>"), v("https://exemplo.test/2")])

        texto = cliente.get("/tela/vagas/detalhe?url=https://exemplo.test/2", headers=HX).text

        self.assertNotIn("<script>", texto)
        self.assertIn("&lt;script&gt;", texto)

    def test_marcar_vista_na_tela_marca_o_grupo_inteiro_e_so_ele(self):
        cliente, repo = criar_tela([v("a1"), v("a2"), v("a3"), v("outra", "Analista de BI", "Zeta")])

        resposta = cliente.post("/tela/vagas/vista", data={"url": "a3"}, headers=MUTACAO)

        self.assertEqual(resposta.status_code, 204)
        self.assertTrue(all(repo.obter(u).vista_em for u in ("a1", "a2", "a3")))
        self.assertIsNone(repo.obter("outra").vista_em)
        # Em Novas só sobra a outra vaga: o grupo não volta como nova.
        novas = Pagina(cliente.get("/tela?novas=1").text)
        self.assertEqual([c.attrs["data-url"] for c in novas.cards()], ["outra"])

    def test_marcar_vista_pela_rota_json_marca_so_as_urls_dadas(self):
        cliente, repo = criar_tela([v("a1"), v("a2")])

        self.assertEqual(cliente.post("/vagas/vistas", json={"urls": ["a2"]}).json(), {"marcadas": 1})

        self.assertIsNone(repo.obter("a1").vista_em)

    def test_vista_de_url_inexistente_continua_204(self):
        cliente, _ = criar_tela([v("a1")])

        self.assertEqual(cliente.post("/tela/vagas/vista", data={"url": "nao-existe"}, headers=MUTACAO).status_code, 204)


class ContratoFoto:
    """A foto do instante (`ate`), na memória e no Postgres. A subclasse define `criar` e `agora`
    (o relógio do mesmo lado do `primeira_vez_em`: o do banco no Postgres)."""

    def setUp(self):
        self.criar()
        self.servico = HistoricoService(self.repo, self.perfis)

    def distintas(self, n, prefixo="v", **campos):
        # Uma empresa por vaga: nada agrupa. Títulos com o mesmo termo do perfil.
        return [v(f"{prefixo}{i:02}", f"Analista {prefixo}{i:02}", f"Empresa {prefixo}{i:02}", **campos) for i in range(n)]

    def test_vaga_coletada_depois_do_instante_nao_entra_e_o_limite_inclui(self):
        registrar_um_a_um(self.repo, self.distintas(3, "a"))
        instante = self.agora()
        registrar_um_a_um(self.repo, self.distintas(2, "b"))

        self.assertEqual(len(self.repo.listar_recentes(False, 100)), 5)
        com_foto = self.repo.listar_recentes(False, 100, ate=instante)
        self.assertEqual(sorted(urls(com_foto)), ["a00", "a01", "a02"])
        # O instante exato da primeira vez ainda entra (<=).
        exata = self.repo.obter("a01").primeira_vez_em
        self.assertEqual(sorted(urls(self.repo.listar_recentes(False, 100, ate=exata))), ["a00", "a01"])

    def test_em_novas_vista_depois_do_instante_continua_e_vista_antes_sai(self):
        registrar_um_a_um(self.repo, self.distintas(3, "a"))
        antes = self.agora()
        self.repo.marcar_vistas(["a00"])
        depois = self.agora()

        self.assertEqual(sorted(urls(self.repo.listar_recentes(True, 100, ate=antes))), ["a00", "a01", "a02"])
        self.assertEqual(sorted(urls(self.repo.listar_recentes(True, 100, ate=depois))), ["a01", "a02"])
        # Sem foto, o filtro de sempre (não vistas).
        self.assertEqual(sorted(urls(self.repo.listar_recentes(True, 100))), ["a01", "a02"])
        # Em Todas, vista não muda a janela.
        self.assertEqual(len(self.repo.listar_recentes(False, 100, ate=depois)), 3)

    def test_ate_sem_fuso_e_dado_invalido(self):
        for ate in (datetime(2026, 10, 12, 3, 0), "2026-10-12"):
            with self.subTest(ate=ate):
                with self.assertRaises(DadoInvalido):
                    self.repo.listar_recentes(False, 10, ate=ate)
                with self.assertRaises(DadoInvalido):
                    self.servico.pagina(False, 10, ate=ate)

    def test_paginas_nao_repetem_nem_pulam_com_coleta_no_meio(self):
        self.perfis.salvar(Perfil(termos=["analista"]))
        registrar_um_a_um(self.repo, self.distintas(12, "a"))
        primeira = self.servico.pagina(False, 5, hoje=HOJE)
        esperado = urls(self.servico.pagina(False, 50, hoje=HOJE, ate=primeira.ate).vagas)
        self.assertEqual(len(esperado), 12)

        # Coleta no meio: vagas que ranqueiam acima (publicada_em mais nova) das já lidas.
        registrar_um_a_um(self.repo, self.distintas(4, "z", publicada_em=date(2026, 10, 11)))
        lidas = urls(primeira.vagas)
        for deslocamento in (5, 10):
            pagina = self.servico.pagina(False, 5, deslocamento=deslocamento, hoje=HOJE, ate=primeira.ate)
            self.assertEqual(pagina.total, 12, "o contador é o da foto")
            lidas += urls(pagina.vagas)

        self.assertEqual(lidas, esperado)
        # Uma foto nova enxerga a coleta.
        self.assertEqual(self.servico.pagina(False, 5, hoje=HOJE).total, 16)

    def test_paginas_nao_pulam_com_vista_no_meio_em_novas(self):
        registrar_um_a_um(self.repo, self.distintas(12, "a"))
        primeira = self.servico.pagina(True, 5, hoje=HOJE)
        self.assertEqual(primeira.total, 12)

        self.repo.marcar_vistas(urls(primeira.vagas)[:3])  # a pessoa abriu três cards
        lidas = urls(primeira.vagas)
        for deslocamento in (5, 10):
            pagina = self.servico.pagina(True, 5, deslocamento=deslocamento, hoje=HOJE, ate=primeira.ate)
            self.assertEqual(pagina.total, 12)
            lidas += urls(pagina.vagas)

        self.assertEqual(len(lidas), 12)
        self.assertEqual(len(set(lidas)), 12)
        # Uma foto nova só tem as 9 que sobraram.
        self.assertEqual(self.servico.pagina(True, 5, hoje=HOJE).total, 9)

    def test_duplicata_coletada_depois_do_instante_nao_troca_o_anuncio_mostrado(self):
        registrar_um_a_um(self.repo, [v("a1")])
        instante = self.agora()
        registrar_um_a_um(self.repo, [v("a2")])

        self.assertEqual(urls(self.servico.pagina(False, 10, hoje=HOJE, ate=instante).vagas), ["a1"])
        self.assertEqual(urls(self.servico.pagina(False, 10, hoje=HOJE).vagas), ["a2"])

    def test_sem_ate_a_primeira_pagina_devolve_o_instante_usado(self):
        antes = datetime.now(timezone.utc)

        pagina = self.servico.pagina(False, 10, hoje=HOJE)

        self.assertIsNotNone(pagina.ate.tzinfo)
        self.assertGreaterEqual(pagina.ate, antes - timedelta(seconds=1))
        self.assertEqual(self.servico.pagina(False, 10, hoje=HOJE, ate=pagina.ate).ate, pagina.ate)


    def test_a_foto_sem_ate_usa_o_relogio_injetado(self):
        registrar_um_a_um(self.repo, self.distintas(2, "a"))
        futuro = self.agora() + timedelta(days=30)
        passado = self.agora() - timedelta(days=30)

        com_futuro = HistoricoService(self.repo, self.perfis, agora=lambda: futuro).pagina(False, 10, hoje=HOJE)
        com_passado = HistoricoService(self.repo, self.perfis, agora=lambda: passado).pagina(False, 10, hoje=HOJE)

        self.assertEqual((com_futuro.ate, len(com_futuro.vagas)), (futuro, 2))
        self.assertEqual((com_passado.ate, com_passado.vagas), (passado, []), "relógio fixo antes da coleta: nada entra")


class FotoMemoriaTest(ContratoFoto, unittest.TestCase):
    def criar(self):
        self.repo = VagaEncontradaRepositoryMemoria()
        self.perfis = PerfilBuscaRepositoryMemoria()

    def agora(self):
        return datetime.now(timezone.utc)


class LerAteTest(unittest.TestCase):
    def test_aceita_iso_com_fuso_e_devolve_utc(self):
        for texto in ("2026-10-12T03:20:00+00:00", "2026-10-12T03:20:00Z", "2026-10-12T00:20:00-03:00", "2026-10-12T06:20:00+03:00"):
            with self.subTest(texto=texto):
                self.assertEqual(ler_ate(texto), datetime(2026, 10, 12, 3, 20, tzinfo=timezone.utc))
        self.assertEqual(ler_ate("2026-10-12T03:20:00.123456+00:00").microsecond, 123456)

    def test_vazio_e_none(self):
        self.assertIsNone(ler_ate(None))
        self.assertIsNone(ler_ate(""))
        self.assertIsNone(ler_ate("  "))

    def test_invalido_e_dado_invalido(self):
        for texto in (
            "ontem",
            "2026-10-12",
            "2026-10-12T03:20:00",  # sem fuso
            "2026-10-12T03:20:00 00:00",  # o + do fuso virou espaço numa url sem codificar
            "x" * 100,
            "9999-12-31T23:59:59-23:59",  # estoura ao converter para UTC
            "0001-01-01T00:00:00+23:59",
        ):
            with self.subTest(texto=texto):
                with self.assertRaises(DadoInvalido):
                    ler_ate(texto)


def lidas_pelo_carregar_mais(cliente, rota="/tela", antes_da_segunda=None) -> list[str]:
    """Primeira página e o sentinela até o fim; `antes_da_segunda` roda entre a 1ª e a 2ª página."""
    pagina = Pagina(cliente.get(rota).text)
    lidas = [c.attrs["data-url"] for c in pagina.cards()]
    primeira = True
    while True:
        sentinelas = [e for e in pagina.tags("li") if "mais" in (e.attrs.get("class") or "")]
        if not sentinelas:
            return lidas
        if primeira and antes_da_segunda:
            antes_da_segunda()
        primeira = False
        resposta = cliente.get(sentinelas[0].attrs["hx-get"], headers=HX)
        assert resposta.status_code == 200, resposta.text
        pagina = Pagina(resposta.text)
        lidas += [c.attrs["data-url"] for c in pagina.cards()]


def distintas(n, prefixo="v", **campos):
    return [v(f"{prefixo}{i:03}", f"Analista {prefixo}{i:03}", f"Empresa {prefixo}{i:03}", **campos) for i in range(n)]


class TelaFotoTest(unittest.TestCase):
    def test_coleta_no_meio_do_carregar_mais_nao_repete_card_sem_perfil(self):
        cliente, repo = criar_tela(distintas(120, "a"))
        # Sem perfil: mais recentes primeiro. Uma coleta nova entra NO TOPO depois da 1ª página.
        esperado = [c.attrs["data-url"] for c in Pagina(cliente.get("/tela").text).cards()]
        self.assertEqual(len(esperado), 50)

        lidas = lidas_pelo_carregar_mais(cliente, antes_da_segunda=lambda: registrar_um_a_um(repo, distintas(5, "z")))

        self.assertEqual(len(lidas), 120)
        self.assertEqual(len(set(lidas)), 120, "sem card repetido")
        self.assertFalse([u for u in lidas if u.startswith("z")], "a coleta nova só aparece na próxima foto")

    def test_coleta_no_meio_nao_repete_nem_pula_com_perfil(self):
        cliente, repo = criar_tela(distintas(120, "a"), perfil=Perfil(termos=["analista"]))
        sem_coleta = lidas_pelo_carregar_mais(cliente)

        lidas = lidas_pelo_carregar_mais(
            cliente, antes_da_segunda=lambda: registrar_um_a_um(repo, distintas(5, "z", publicada_em=date.today()))
        )

        self.assertEqual(lidas, sem_coleta)

    def test_vista_no_meio_do_carregar_mais_em_novas_nao_pula_card(self):
        cliente, repo = criar_tela(distintas(120, "a"))
        primeira = [c.attrs["data-url"] for c in Pagina(cliente.get("/tela?novas=1").text).cards()]

        lidas = lidas_pelo_carregar_mais(
            cliente, "/tela?novas=1", antes_da_segunda=lambda: repo.marcar_vistas(primeira[:5])
        )

        self.assertEqual(len(lidas), 120, "nenhuma vaga pulada")
        self.assertEqual(len(set(lidas)), 120)
        # Nova foto: as cinco vistas saíram de Novas.
        self.assertEqual(len(lidas_pelo_carregar_mais(cliente, "/tela?novas=1")), 115)

    def test_o_sentinela_leva_o_instante_codificado_e_volta(self):
        cliente, _ = criar_tela(distintas(60, "a"))
        sentinela = next(e for e in Pagina(cliente.get("/tela").text).tags("li") if "mais" in (e.attrs.get("class") or ""))

        proximo = sentinela.attrs["hx-get"]

        self.assertRegex(proximo, r"[?&]ate=\d{4}-\d{2}-\d{2}T[\d.%A-F]+%2B00%3A00")
        self.assertNotIn("+", proximo)
        self.assertEqual(cliente.get(proximo, headers=HX).status_code, 200)

    def test_ate_com_outro_fuso_codificado_funciona(self):
        cliente, _ = criar_tela(distintas(3, "a"))
        ate = (datetime.now(timezone.utc) + timedelta(hours=1)).astimezone(timezone(timedelta(hours=3))).isoformat()
        self.assertTrue(ate.endswith("+03:00"))

        resposta = cliente.get("/tela/vagas", params={"ate": ate}, headers=HX)  # o httpx codifica o +

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(len(Pagina(resposta.text).cards()), 3)

    def test_ate_no_passado_tira_o_que_foi_coletado_depois(self):
        cliente, _ = criar_tela(distintas(3, "a"))
        passado = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

        resposta = cliente.get("/tela/vagas", params={"ate": passado}, headers=HX)

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(Pagina(resposta.text).cards(), [])

    def test_ate_invalido_e_422_html_nunca_500(self):
        cliente, _ = criar_tela(distintas(3, "a"))
        for ate in ("ontem", "2026-10-12T03:20:00", "2026-10-12T03:20:00 00:00", "x" * 100, "9999-12-31T23:59:59-23:59"):
            for cabecalhos in (HX, {}):
                with self.subTest(ate=ate[:30], htmx=bool(cabecalhos)):
                    resposta = cliente.get("/tela/vagas", params={"ate": ate}, headers=cabecalhos)
                    self.assertEqual(resposta.status_code, 422)
                    self.assertNotIn('"detail"', resposta.text)
                    self.assertIn("ate", resposta.text)

    def test_o_mais_cru_sem_codificar_tambem_nao_vira_500(self):
        cliente, _ = criar_tela(distintas(3, "a"))

        # `+` solto numa url escrita à mão: o servidor lê espaço.
        resposta = cliente.get("/tela/vagas?ate=2026-10-12T03:20:00+00:00", headers=HX)

        self.assertEqual(resposta.status_code, 422)


class JsonFotoTest(unittest.TestCase):
    def test_devolve_o_instante_no_cabecalho_e_o_corpo_continua_a_lista(self):
        cliente, _ = criar_tela(distintas(3, "a"))

        resposta = cliente.get("/vagas/encontradas?novas=false")

        self.assertEqual(len(resposta.json()), 3)
        self.assertIsInstance(resposta.json(), list)
        usado = ler_ate(resposta.headers["X-Vagas-Ate"])
        self.assertIsNotNone(usado)

    def test_paginando_com_o_cabecalho_nao_repete_com_coleta_no_meio(self):
        cliente, repo = criar_tela(distintas(12, "a"))
        primeira = cliente.get("/vagas/encontradas?novas=false&limite=5")
        ate = primeira.headers["X-Vagas-Ate"]
        lidas = [x["url"] for x in primeira.json()]

        registrar_um_a_um(repo, distintas(4, "z"))
        for deslocamento in (5, 10):
            pagina = cliente.get("/vagas/encontradas", params={"novas": "false", "limite": 5, "deslocamento": deslocamento, "ate": ate})
            self.assertEqual(pagina.headers["X-Vagas-Ate"], ate)
            lidas += [x["url"] for x in pagina.json()]

        self.assertEqual(len(lidas), 12)
        self.assertEqual(len(set(lidas)), 12)
        self.assertFalse([u for u in lidas if u.startswith("z")])

    def test_ate_invalido_e_422_json(self):
        cliente, _ = criar_tela(distintas(1, "a"))

        for ate in ("ontem", "2026-10-12T03:20:00", "x" * 100):
            with self.subTest(ate=ate[:20]):
                resposta = cliente.get("/vagas/encontradas", params={"ate": ate})
                self.assertEqual(resposta.status_code, 422)
                self.assertIn("detail", resposta.json())


class PlataformasTest(unittest.TestCase):
    VAGAS = [
        v("r", "Analista A", "E1", plataforma="Remotar"),
        v("l", "Analista B", "E2", plataforma="LinkedIn (Apify)"),
        v("t", "Analista C", "E3", plataforma="Trampos"),
        v("r2", "Analista D", "E4", plataforma="Remotar2"),
    ]

    def filtrar(self, plataformas):
        return urls(filtrar_pelo_perfil(self.VAGAS, Perfil(plataformas=plataformas), HOJE))

    def test_sem_plataformas_nao_filtra(self):
        self.assertEqual(self.filtrar([]), ["r", "l", "t", "r2"])

    def test_so_as_plataformas_do_perfil_por_palavra_sem_acento_nem_caixa(self):
        self.assertEqual(self.filtrar(["remotar"]), ["r"])
        self.assertEqual(self.filtrar(["LINKEDIN"]), ["l"])
        self.assertEqual(self.filtrar(["Trâmpos", "REMOTAR"]), ["r", "t"])
        self.assertEqual(self.filtrar(["Remota"]), [], "palavra inteira: Remota não casa com Remotar")
        self.assertEqual(self.filtrar(["Trampos", "Remotar"]), ["r", "t"])

    def test_entrada_sem_palavra_e_ignorada(self):
        self.assertEqual(self.filtrar(["", "—"]), ["r", "l", "t", "r2"])
        self.assertEqual(self.filtrar(["", "Trampos"]), ["t"])

    def test_vale_na_busca_ao_vivo_tambem(self):
        self.assertEqual(urls(aplicar_perfil(self.VAGAS, Perfil(plataformas=["Trampos"]), HOJE)), ["t"])


class ClientContado:
    """Provider escrito à mão que conta as chamadas (o pago é cobrado por chamada)."""

    def __init__(self, nome, vagas=(), erro=None):
        self.nome = nome
        self.vagas = list(vagas)
        self.erro = erro
        self.chamadas = 0

    def buscar(self, termo):
        self.chamadas += 1
        if self.erro:
            raise self.erro
        return self.vagas


class PlataformasNaColetaTest(unittest.TestCase):
    """O provider fora de `plataformas` não é consultado; o histórico filtra na leitura."""

    def setUp(self):
        self.remotar = ClientContado("Remotar", [v("r", "Analista A", "E1", plataforma="Remotar")])
        self.apify = ClientContado("LinkedIn (Apify)", [v("l", "Analista B", "E2", plataforma="LinkedIn (Apify)")])
        self.busca = BuscaService([self.remotar, self.apify])
        self.repo = VagaEncontradaRepositoryMemoria()
        self.perfis = PerfilBuscaRepositoryMemoria()
        self.coleta = ColetaService(RecomendacaoService(self.busca), self.perfis, self.repo)

    def test_coleta_nao_chama_o_provider_fora_das_plataformas(self):
        self.perfis.salvar(Perfil(termos=["analista", "dados"], plataformas=["Remotar"]))

        resultado = self.coleta.coletar(HOJE)

        self.assertEqual(self.apify.chamadas, 0, "o actor pago não pode rodar para ser descartado")
        self.assertEqual(self.remotar.chamadas, 2)
        self.assertEqual(sorted(urls(self.repo.listar(False, 50))), ["r"])
        self.assertFalse(resultado.falhou)

    def test_sem_plataformas_chama_todos(self):
        self.perfis.salvar(Perfil(termos=["analista"]))
        self.coleta.coletar(HOJE)
        self.assertEqual((self.remotar.chamadas, self.apify.chamadas), (1, 1))

    def test_falhou_conta_so_os_providers_consultados(self):
        self.remotar.erro = TimeoutError("sem resposta")
        self.perfis.salvar(Perfil(termos=["analista"], plataformas=["remotar"]))

        self.assertTrue(self.coleta.coletar(HOJE).falhou)

    def test_nenhum_provider_casa_e_dado_invalido_sem_chamar_ninguem(self):
        self.perfis.salvar(Perfil(termos=["analista"], plataformas=["Indeed"]))

        with self.assertRaises(DadoInvalido):
            self.coleta.coletar(HOJE)
        self.assertEqual((self.remotar.chamadas, self.apify.chamadas), (0, 0))

    def test_busca_ao_vivo_tambem_nao_chama(self):
        resultado = RecomendacaoService(self.busca).recomendar("analista", Perfil(plataformas=["LinkedIn"]), HOJE)

        self.assertEqual(urls(resultado.vagas), ["l"])
        self.assertEqual(self.remotar.chamadas, 0)

    def test_historico_ja_coletado_obedece_ao_perfil_na_leitura(self):
        # Coletado com o perfil sem restrição; depois o perfil passa a ter só Remotar e volta.
        self.perfis.salvar(Perfil(termos=["analista"]))
        self.coleta.coletar(HOJE)
        historico = HistoricoService(self.repo, self.perfis)

        self.perfis.salvar(Perfil(termos=["analista"], plataformas=["Remotar"]))
        self.assertEqual(urls(historico.pagina(False, 50, hoje=HOJE).vagas), ["r"])
        self.perfis.salvar(Perfil(termos=["analista"]))
        self.assertEqual(sorted(urls(historico.pagina(False, 50, hoje=HOJE).vagas)), ["l", "r"])


class ContratoCandidatadas:
    """`ocultar_candidatadas` no serviço, com a lista de candidaturas injetada (memória e Postgres)."""

    def setUp(self):
        self.criar()
        self.candidaturas = []
        self.servico = HistoricoService(self.repo, self.perfis, candidatadas=lambda: self.candidaturas)
        registrar_um_a_um(
            self.repo,
            [v("a1"), v("a2"), v("b", "Analista de BI", "Zeta"), v("c", "Analista de Dados", "Zeta")],
        )

    def aplicar(self, url):
        vaga = self.repo.obter(url)
        self.candidaturas.append(_candidatura(url, vaga.titulo, vaga.empresa, datetime.now(timezone.utc)))

    def lista(self):
        return sorted(urls(self.servico.pagina(False, 50, hoje=HOJE).vagas))

    def test_perfil_com_ocultar_esconde_a_vaga_aplicada(self):
        self.perfis.salvar(Perfil(termos=["analista"], ocultar_candidatadas=True))
        self.aplicar("b")

        self.assertEqual(self.lista(), ["a2", "c"])
        self.assertEqual(self.servico.pagina(False, 50, hoje=HOJE).total, 2)

    def test_esconde_tambem_o_outro_anuncio_do_mesmo_grupo(self):
        self.perfis.salvar(Perfil(termos=["analista"], ocultar_candidatadas=True))
        self.aplicar("a1")  # a2 é o mesmo anúncio com outra URL

        self.assertEqual(self.lista(), ["b", "c"])

    def test_sem_ocultar_ou_sem_perfil_a_vaga_aplicada_continua(self):
        self.aplicar("b")
        self.assertEqual(self.lista(), ["a2", "b", "c"], "sem perfil salvo não há flag")
        self.perfis.salvar(Perfil(termos=["analista"], ocultar_candidatadas=False))
        self.assertEqual(self.lista(), ["a2", "b", "c"])

    def test_desfazer_a_candidatura_traz_a_vaga_de_volta(self):
        self.perfis.salvar(Perfil(termos=["analista"], ocultar_candidatadas=True))
        self.aplicar("b")
        self.assertNotIn("b", self.lista())

        self.candidaturas.clear()

        self.assertIn("b", self.lista())

    def test_candidatura_feita_entre_as_paginas_nao_faz_pular_vaga(self):
        self.perfis.salvar(Perfil(termos=["analista"], ocultar_candidatadas=True))
        registrar_um_a_um(self.repo, [v(f"d{i:02}", f"Analista d{i:02}", f"Empresa d{i:02}") for i in range(8)])
        primeira = self.servico.pagina(False, 3, hoje=HOJE)
        esperado = urls(self.servico.pagina(False, 50, hoje=HOJE, ate=primeira.ate).vagas)
        self.assertEqual(len(esperado), 11)

        self.aplicar(primeira.vagas[0].url)  # aplicou num card da 1ª página e rolou até o fim
        lidas, totais = urls(primeira.vagas), []
        for deslocamento in (3, 6, 9):
            pagina = self.servico.pagina(False, 3, deslocamento=deslocamento, hoje=HOJE, ate=primeira.ate)
            totais.append(pagina.total)
            lidas += urls(pagina.vagas)

        self.assertEqual(lidas, esperado, "nenhuma vaga pulada nem repetida")
        self.assertEqual(totais, [11, 11, 11], "o contador é o da foto")
        # Uma foto nova já esconde a aplicada.
        self.assertNotIn(primeira.vagas[0].url, urls(self.servico.pagina(False, 50, hoje=HOJE).vagas))

    def test_candidatura_de_antes_da_foto_continua_escondida_nas_paginas_seguintes(self):
        self.perfis.salvar(Perfil(termos=["analista"], ocultar_candidatadas=True))
        self.aplicar("b")
        primeira = self.servico.pagina(False, 1, hoje=HOJE)

        seguintes = self.servico.pagina(False, 50, deslocamento=1, hoje=HOJE, ate=primeira.ate)

        self.assertEqual(primeira.total, 2)
        self.assertNotIn("b", urls(primeira.vagas) + urls(seguintes.vagas))

    def test_sem_a_injecao_nao_esconde_nada(self):
        self.perfis.salvar(Perfil(termos=["analista"], ocultar_candidatadas=True))
        self.aplicar("b")

        self.assertEqual(sorted(urls(HistoricoService(self.repo, self.perfis).pagina(False, 50, hoje=HOJE).vagas)), ["a2", "b", "c"])


def _candidatura(url, titulo, empresa, aplicada_em):
    from types import SimpleNamespace

    return SimpleNamespace(vaga_url=url, titulo=titulo, empresa=empresa, aplicada_em=aplicada_em)


class CandidatadasMemoriaTest(ContratoCandidatadas, unittest.TestCase):
    def criar(self):
        self.repo = VagaEncontradaRepositoryMemoria()
        self.perfis = PerfilBuscaRepositoryMemoria()


class ApiOcultarCandidatadasTest(unittest.TestCase):
    def test_aplicar_pela_api_some_da_lista_json_e_da_tela_mas_o_link_direto_abre(self):
        cliente, repo = criar_tela([v("https://exemplo.test/1"), v("https://exemplo.test/2", "Outro Cargo", "Zeta")], perfil=Perfil(termos=["analista", "outro"]))
        self.assertEqual(cliente.post("/candidaturas", json={"vaga_url": "https://exemplo.test/1"}).status_code, 201)

        self.assertEqual([x["url"] for x in cliente.get("/vagas/encontradas?novas=false").json()], ["https://exemplo.test/2"])
        pagina = Pagina(cliente.get("/tela").text)
        self.assertEqual([c.attrs["data-url"] for c in pagina.cards()], ["https://exemplo.test/2"])
        direto = cliente.get("/tela/vagas/detalhe?url=https://exemplo.test/1", headers=HX)
        self.assertEqual(direto.status_code, 200)

    def test_perfil_com_ocultar_false_mantem_a_aplicada(self):
        cliente, _ = criar_tela([v("https://exemplo.test/1")], perfil=Perfil(termos=["analista"], ocultar_candidatadas=False))
        cliente.post("/candidaturas", json={"vaga_url": "https://exemplo.test/1"})

        self.assertEqual(len(cliente.get("/vagas/encontradas?novas=false").json()), 1)


if __name__ == "__main__":
    unittest.main()
