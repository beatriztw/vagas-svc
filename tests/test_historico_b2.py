"""Histórico: anúncios repetidos agrupados (bug 4), foto do instante (bug 6) e filtros do perfil (bug 7).

Todas as vagas daqui são escritas à mão (não são respostas gravadas de provider).
"""

import unittest
from datetime import date
from urllib.parse import quote

from fastapi.testclient import TestClient

from src.controller.api import criar_app
from src.model.perfil import Perfil
from src.model.vaga_externa import VagaExterna
from src.repository.base import CurriculoRepositoryMemoria, PerfilBuscaRepositoryMemoria, VagaEncontradaRepositoryMemoria
from src.service.busca_service import BuscaService
from src.service.historico_service import HistoricoService, chave_do_grupo
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


if __name__ == "__main__":
    unittest.main()
