"""Tela (B1): só o clique marca vista, a barra mostra a página (`/tela?...`) e local vazio some.

As vagas daqui são escritas à mão (não são respostas gravadas de provider).
"""

import unittest
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from src.model.vaga_externa import VagaExterna
from tests.test_tela_lista import HX, URLS_DIFICEIS, Pagina, criar, vaga

MUTACAO = {"HX-Request": "true", "Origin": "http://testserver"}
VISTA = "/tela/vagas/vista"


def tem_post_de_vista(texto: str) -> bool:
    return any(f.attrs.get("hx-post") == VISTA for f in Pagina(texto).tags("form"))


class VistaSoNoCliqueTest(unittest.TestCase):
    URL = "https://exemplo.test/vaga/1"

    def test_pagina_inteira_nao_traz_o_post_de_vista(self):
        cliente, repo = criar([vaga(1), vaga(2)])

        # 1ª vaga selecionada sozinha, deep link, e a rota do detalhe sem HTMX (F5 / link copiado).
        for caminho, params in (
            ("/tela", {}),
            ("/tela", {"url": self.URL}),
            ("/tela/vagas/detalhe", {"url": self.URL}),
        ):
            with self.subTest(caminho=caminho, params=params):
                resposta = cliente.get(caminho, params=params)
                self.assertEqual(resposta.status_code, 200)
                self.assertFalse(tem_post_de_vista(resposta.text))
        self.assertIsNone(repo.obter(self.URL).vista_em)

    def test_partial_do_clique_traz_o_post_de_vista_da_vaga_clicada(self):
        for url in URLS_DIFICEIS:
            with self.subTest(url=url):
                cliente, _ = criar([VagaExterna("T", "E", "Remotar", url)])

                texto = cliente.get("/tela/vagas/detalhe", params={"url": url}, headers=HX).text

                self.assertTrue(tem_post_de_vista(texto))
                pagina = Pagina(texto)
                campo = next(i for i in pagina.tags("input") if i.attrs.get("name") == "url")
                self.assertEqual(campo.attrs["value"], url)

    def test_f5_depois_do_clique_nao_marca_de_novo(self):
        cliente, repo = criar([vaga(1)])
        cliente.post(VISTA, data={"url": self.URL}, headers=MUTACAO)
        antes = repo.obter(self.URL).vista_em

        resposta = cliente.get("/tela", params={"url": self.URL})  # o F5 da url empurrada pelo card

        self.assertFalse(tem_post_de_vista(resposta.text))
        self.assertEqual(repo.obter(self.URL).vista_em, antes)


class BarraDaBuscaTest(unittest.TestCase):
    def push(self, cliente, **params):
        resposta = cliente.get("/tela/vagas", params=params, headers=HX)
        self.assertEqual(resposta.status_code, 200)
        return resposta.headers["HX-Push-Url"]

    def test_busca_empurra_a_pagina_com_q_novas_e_url_ida_e_volta(self):
        q = "conciliação & contas #1?"
        for url in URLS_DIFICEIS + ["https://x.com/a b?c=1&d=2#e ?f"]:
            with self.subTest(url=url):
                cliente, _ = criar([VagaExterna("T", "E", "Remotar", url)])

                push = self.push(cliente, q=q, novas="1", detalhe_url=url)

                partes = urlsplit(push)
                self.assertEqual(partes.path, "/tela")  # a página, nunca /tela/vagas
                self.assertEqual(parse_qs(partes.query), {"q": [q], "novas": ["1"], "url": [url]})
                self.assertNotRegex(partes.query.replace("%23", "").replace("%26", ""), r"[ #]")
                self.assertNotIn("#", push)
                # Ida e volta: o que foi empurrado, aberto (F5), seleciona a mesma vaga e mantém o filtro.
                inteira = Pagina(cliente.get(push).text)
                self.assertEqual(inteira.por_id("detalhe").attrs["data-url"], url)
                self.assertEqual(inteira.por_id("busca").attrs["value"], q)

    def test_sem_vaga_aberta_nem_filtro_a_barra_volta_para_tela(self):
        cliente, _ = criar([vaga(1)])

        self.assertEqual(self.push(cliente), "/tela")
        self.assertEqual(self.push(cliente, q="analista"), "/tela?q=analista")
        self.assertEqual(self.push(cliente, novas="0"), "/tela")

    def test_alternancia_novas_todas_tambem_preserva_a_vaga_aberta(self):
        cliente, _ = criar([vaga(1)])
        url = "https://exemplo.test/vaga/1"

        self.assertEqual(self.push(cliente, novas="1", detalhe_url=url), "/tela?" + urlencode({"novas": "1", "url": url}, quote_via=quote))

    def test_url_aberta_gigante_e_ignorada(self):
        cliente, _ = criar([vaga(1)])

        self.assertEqual(self.push(cliente, q="a", detalhe_url="https://x.test/" + "a" * 3000), "/tela?q=a")

    def test_carregar_mais_nao_muda_a_barra(self):
        cliente, _ = criar([vaga(i) for i in range(60)])

        resposta = cliente.get("/tela/vagas", params={"deslocamento": "50"}, headers=HX)

        self.assertNotIn("HX-Push-Url", resposta.headers)

    def test_campo_e_alternancia_incluem_a_url_do_detalhe_aberto(self):
        cliente, _ = criar([vaga(1)])
        pagina = Pagina(cliente.get("/tela").text)

        self.assertIn("#detalhe-url", pagina.por_id("busca").attrs["hx-include"])
        self.assertEqual(pagina.por_id("detalhe-url").attrs["name"], "detalhe_url")
        for link in (e for e in pagina.tags("a") if e.texto.strip() in ("Todas", "Novas")):
            self.assertEqual(link.attrs["hx-include"], "#detalhe-url")
            self.assertNotIn("hx-push-url", link.attrs)


class LocalVazioTest(unittest.TestCase):
    def test_card_e_detalhe_sem_local_mostram_so_a_empresa(self):
        for local in (None, "", "   "):
            with self.subTest(local=local):
                cliente, _ = criar([vaga(1, local=local)])

                pagina = Pagina(cliente.get("/tela").text)

                [card] = pagina.cards()
                sec = next(e for e in pagina.elementos if "card-sec" in (e.attrs.get("class") or ""))
                self.assertEqual(sec.texto.strip(), "Empresa Teste")
                self.assertNotIn("—", pagina.por_id("lista-vagas").texto)
                chips = [e.texto.strip() for e in pagina.tags("span") if e.attrs.get("class") == "chip-info"]
                self.assertEqual(chips, ["Empresa Teste", "não informado"])
                self.assertNotIn("—", pagina.por_id("detalhe").texto)

    def test_com_local_continua_igual(self):
        cliente, _ = criar([vaga(1, local="Rio de Janeiro")])

        pagina = Pagina(cliente.get("/tela").text)

        sec = next(e for e in pagina.elementos if "card-sec" in (e.attrs.get("class") or ""))
        self.assertEqual(sec.texto.strip(), "Empresa Teste - Rio de Janeiro")
        chips = [e.texto.strip() for e in pagina.tags("span") if e.attrs.get("class") == "chip-info"]
        self.assertIn("Rio de Janeiro", chips)


if __name__ == "__main__":
    unittest.main()
