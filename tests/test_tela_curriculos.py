"""Currículo adaptado no detalhe da vaga e o download `.md`.

Currículos e vagas ESCRITOS À MÃO, com texto genérico inventado (nada de pessoa real).
"""

import os
import unittest
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from src.controller.api import criar_app
from src.controller.tela_curriculos import nome_do_arquivo
from src.model.curriculo import Curriculo
from src.model.vaga_externa import VagaExterna
from src.repository.base import CurriculoRepositoryMemoria, PerfilBuscaRepositoryMemoria, VagaEncontradaRepositoryMemoria
from src.service.busca_service import BuscaService
from tests.test_tela_lista import URLS_DIFICEIS, Pagina

HX = {"HX-Request": "true"}
URL = "https://exemplo.test/vaga/1"


def montar(*urls: str):
    encontradas = VagaEncontradaRepositoryMemoria(1)
    encontradas.registrar([VagaExterna("Analista de Dados", "Empresa Teste", "Remotar", u) for u in urls or (URL,)], "dados")
    curriculos = CurriculoRepositoryMemoria(1)
    app = criar_app(PerfilBuscaRepositoryMemoria(1), curriculos, BuscaService([]), encontradas=encontradas)
    return TestClient(app, raise_server_exceptions=False), curriculos


def com_base(curriculos) -> Curriculo:
    return curriculos.salvar(Curriculo("base", "Base geral", "# Base inventada\nTexto de exemplo."))


def adaptado(curriculos, vaga_url: str, titulo: str = "Currículo para Analista", md: str = "# Exemplo\nTexto inventado.") -> Curriculo:
    base = curriculos.obter_base() or com_base(curriculos)
    return curriculos.salvar(Curriculo("adaptado", titulo, md, base_id=base.id, vaga_url=vaga_url))


class BlocoNoDetalheTest(unittest.TestCase):
    def test_detalhe_pede_o_bloco_com_a_url_da_vaga_ida_e_volta(self):
        for url in [URL, *URLS_DIFICEIS]:
            with self.subTest(url=url):
                api, curriculos = montar(url)
                adaptado(curriculos, url, md="# Marca inventada")
                detalhe = Pagina(api.get("/tela/vagas/detalhe", params={"url": url}, headers=HX).text)

                alvo = detalhe.por_id("curriculo-adaptado")
                self.assertEqual(alvo.attrs["hx-trigger"], "load")
                partes = urlsplit(alvo.attrs["hx-get"])
                self.assertEqual(partes.path, "/tela/curriculos/da-vaga")
                self.assertEqual(parse_qs(partes.query, keep_blank_values=True)["url"], [url])
                # Segue o hx-get como o navegador seguiria e confere que o bloco da vaga volta.
                bloco = api.get(alvo.attrs["hx-get"], headers=HX)
                self.assertIn("Marca inventada", bloco.text)

    def test_mostra_o_mais_recente_so_da_vaga_com_titulo_data_e_link(self):
        outra = "https://exemplo.test/vaga/2"
        api, curriculos = montar(URL, outra)
        adaptado(curriculos, URL, titulo="Antigo", md="velho")
        recente = adaptado(curriculos, URL, titulo="Recente", md="novo")
        adaptado(curriculos, outra, titulo="De outra vaga", md="outro")

        pagina = Pagina(api.get("/tela/curriculos/da-vaga", params={"url": URL}, headers=HX).text)

        self.assertEqual(pagina.por_id("t-curriculo").texto, "Recente")
        self.assertEqual(pagina.tags("pre")[0].texto, "novo")
        link = next(e for e in pagina.tags("a") if e.texto == "Baixar .md")
        self.assertEqual(link.attrs["href"], f"/tela/curriculos/{recente.id}.md")
        self.assertTrue(any(e.texto == recente.criado_em.strftime("%d/%m/%Y") for e in pagina.tags("span")))

    def test_sem_adaptado_da_vaga_o_bloco_vem_vazio(self):
        api, curriculos = montar(URL)
        com_base(curriculos)
        adaptado(curriculos, "https://exemplo.test/outra")

        resposta = api.get("/tela/curriculos/da-vaga", params={"url": URL}, headers=HX)

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.text.strip(), "")
        self.assertEqual(api.get("/tela/curriculos/da-vaga", headers=HX).text.strip(), "")

    def test_markdown_vai_como_texto_escapado_nunca_como_html(self):
        api, curriculos = montar(URL)
        adaptado(curriculos, URL, titulo="<b>t</b>", md='# Título\n<script>alert(1)</script><img src=x onerror=alert(2)> **negrito** [l](javascript:x)')

        corpo = api.get("/tela/curriculos/da-vaga", params={"url": URL}, headers=HX).text

        self.assertNotIn("<script", corpo)
        self.assertNotIn("<img", corpo)
        self.assertNotIn("<b>", corpo)
        self.assertIn("&lt;script&gt;", corpo)
        pagina = Pagina(corpo)
        self.assertEqual(len(pagina.tags("pre")), 1)
        self.assertIn("**negrito**", pagina.tags("pre")[0].texto)
        self.assertEqual(pagina.tags("h1") + pagina.tags("strong") + pagina.tags("script"), [])

    def test_base_nao_aparece_no_detalhe(self):
        api, curriculos = montar(URL)
        curriculos.salvar(Curriculo("base", "Base", "# Base", vaga_url=URL))

        self.assertEqual(api.get("/tela/curriculos/da-vaga", params={"url": URL}, headers=HX).text.strip(), "")


class DownloadTest(unittest.TestCase):
    def test_baixa_o_markdown_com_cabecalhos_de_download(self):
        api, curriculos = montar()
        c = adaptado(curriculos, URL, titulo="Currículo para Analista de Dados", md="# Exemplo\nçã é texto\n")

        r = api.get(f"/tela/curriculos/{c.id}.md")

        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "text/markdown; charset=utf-8")
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        disposicao = r.headers["content-disposition"]
        self.assertTrue(disposicao.startswith('attachment; filename="curriculo-para-analista-de-dados.md"'), disposicao)
        self.assertIn("filename*=UTF-8''Curr%C3%ADculo%20para%20Analista%20de%20Dados.md", disposicao)
        self.assertEqual(r.content, "# Exemplo\nçã é texto\n".encode("utf-8"))

    def test_titulo_hostil_nao_escapa_do_cabecalho_nem_do_diretorio(self):
        api, curriculos = montar()
        for titulo in ['a"b; filename="x.exe', "linha1\r\nSet-Cookie: x=1", "../../etc/passwd", "a/b\\c", "..", "Ação ñ ü", "   ", "x" * 500]:
            with self.subTest(titulo=titulo):
                c = adaptado(curriculos, URL, titulo=titulo)

                r = api.get(f"/tela/curriculos/{c.id}.md")

                self.assertEqual(r.status_code, 200)
                self.assertNotIn("set-cookie", r.headers)
                disposicao = r.headers["content-disposition"]
                self.assertNotIn("\r", disposicao)
                self.assertNotIn("\n", disposicao)
                nome = disposicao.split('filename="', 1)[1].split('"', 1)[0]
                self.assertRegex(nome, r"^[a-z0-9_-]+\.md$")
                self.assertLessEqual(len(nome), 63)
                self.assertEqual(disposicao.count('filename="'), 1)

    def test_nome_do_arquivo_derivado_do_titulo(self):
        self.assertEqual(nome_do_arquivo("Currículo — Dados/BI", 7), "curriculo-dados-bi.md")
        self.assertEqual(nome_do_arquivo("../..", 7), "curriculo-7.md")
        self.assertEqual(nome_do_arquivo("日本語", 9), "curriculo-9.md")
        self.assertEqual(nome_do_arquivo("a" * 200, 1), "a" * 60 + ".md")

    def test_id_inexistente_invalido_ou_base_dao_404(self):
        api, curriculos = montar()
        base = com_base(curriculos)
        for caminho in ["999.md", "0.md", "abc.md", "-1.md", "1.5.md", "%20.md", f"{'9' * 40}.md", "..%2f1.md", f"{base.id}.md", f"{base.id}", "adaptado.md"]:
            with self.subTest(caminho=caminho):
                r = api.get(f"/tela/curriculos/{caminho}")
                self.assertEqual(r.status_code, 404)
                self.assertNotIn("attachment", r.headers.get("content-disposition", ""))
                self.assertEqual(r.headers["cache-control"], "no-store")

    def test_get_nao_grava_nada(self):
        api, curriculos = montar()
        c = adaptado(curriculos, URL)
        antes = (curriculos.listar_adaptados(), curriculos.obter_base())

        api.get(f"/tela/curriculos/{c.id}.md")
        api.get("/tela/curriculos/da-vaga", params={"url": URL}, headers=HX)

        self.assertEqual((curriculos.listar_adaptados(), curriculos.obter_base()), antes)


class FiltroDoRepositorioTest(unittest.TestCase):
    def test_memoria_filtra_por_vaga_url_exata_do_mais_recente_ao_mais_antigo(self):
        curriculos = CurriculoRepositoryMemoria(1)
        a = adaptado(curriculos, "u1", titulo="a")
        adaptado(curriculos, "u2", titulo="b")
        c = adaptado(curriculos, "u1", titulo="c")
        adaptado(curriculos, "u1/", titulo="d")

        self.assertEqual([x.id for x in curriculos.listar_adaptados("u1")], [c.id, a.id])
        self.assertEqual(curriculos.listar_adaptados("nada"), [])
        self.assertEqual(len(curriculos.listar_adaptados()), 4)


@unittest.skipUnless(os.environ.get("PGHOST"), "PGHOST não definido: integração com Postgres pulada")
class FiltroPostgresTest(unittest.TestCase):
    def setUp(self):
        import uuid

        from psycopg import sql

        from src.repository.conexao import conectar
        from src.repository.postgres import CurriculoRepositoryPostgres
        from tests.test_postgres import aplicar_todas_duas_vezes

        self.conn = conectar()
        self.addCleanup(self.conn.close)
        schema = f"teste_{uuid.uuid4().hex[:12]}"
        self.conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        self.addCleanup(self.conn.execute, sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        self.conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        aplicar_todas_duas_vezes(self.conn)
        self.curriculos = CurriculoRepositoryPostgres(self.conn, 1)

    def test_filtra_por_vaga_url_com_caracteres_dificeis(self):
        base = self.curriculos.salvar(Curriculo("base", "b", "# b"))
        for url in URLS_DIFICEIS:
            with self.subTest(url=url):
                antigo = self.curriculos.salvar(Curriculo("adaptado", "a", "x", base_id=base.id, vaga_url=url))
                novo = self.curriculos.salvar(Curriculo("adaptado", "n", "y", base_id=base.id, vaga_url=url))
                self.curriculos.salvar(Curriculo("adaptado", "o", "z", base_id=base.id, vaga_url=url + "x"))

                self.assertEqual([c.id for c in self.curriculos.listar_adaptados(url)], [novo.id, antigo.id])
        self.assertEqual(self.curriculos.listar_adaptados("nada"), [])
        self.assertEqual(len(self.curriculos.listar_adaptados()), 12)


if __name__ == "__main__":
    unittest.main()
