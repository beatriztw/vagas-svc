import unittest
from urllib.parse import quote

from src.model.vaga_externa import VagaExterna
from tests.test_tela_lista import HX, Pagina, criar, sem_selecao, vaga

# q com espaço, acento e todos os caracteres que quebram uma query montada na mão.
Q_DIFICIL = "conciliação contábil & #? =+"


def vagas_de_conciliacao(n: int) -> list[VagaExterna]:
    # Fixtures escritas à mão.
    return [vaga(i, f"Analista de conciliação contábil {i}") for i in range(n)]


def sentinela(pagina: Pagina):
    achados = [e for e in pagina.tags("li") if "mais" in (e.attrs.get("class") or "")]
    return achados[0] if achados else None


def urls(pagina: Pagina) -> list[str]:
    return [c.attrs["data-url"] for c in pagina.cards()]


class CampoDeBuscaTest(unittest.TestCase):
    def setUp(self):
        cliente, _ = criar([vaga(1)])
        self.pagina = Pagina(cliente.get("/tela").text)
        self.campo = self.pagina.por_id("busca")

    def test_input_tem_rotulo_acessivel_e_atributos_do_htmx(self):
        rotulo = next(e for e in self.pagina.tags("label") if e.attrs.get("for") == "busca")

        self.assertEqual(rotulo.texto.strip(), "Buscar")
        self.assertEqual(self.campo.tag, "input")
        self.assertEqual(self.campo.attrs["type"], "search")
        self.assertEqual(self.campo.attrs["name"], "q")
        self.assertEqual(self.campo.attrs["hx-get"], "/tela/vagas")
        self.assertEqual(self.campo.attrs["hx-trigger"], "input changed delay:300ms, search")
        self.assertEqual(self.campo.attrs["hx-target"], "#vagas")
        self.assertEqual(self.campo.attrs["hx-swap"], "outerHTML")
        self.assertEqual(self.campo.attrs["hx-push-url"], "true")

    def test_campo_fica_fora_do_bloco_trocado_e_o_detalhe_tambem(self):
        cliente, _ = criar([vaga(1), vaga(2)])
        texto = cliente.get("/tela").text
        inicio = texto.index('<div id="vagas">')
        # O bloco termina onde a lista termina; o campo e o detalhe não estão dentro dele.
        fim = texto.index("</ul>", inicio)

        self.assertLess(texto.index('id="busca"'), inicio)
        self.assertGreater(texto.index('id="detalhe"'), fim)

    def test_busca_pelo_htmx_devolve_so_o_bloco_sem_campo_nem_detalhe(self):
        cliente, _ = criar([vaga(1, "Dev Java"), vaga(2, "Dev JavaScript")])

        parcial = cliente.get("/tela/vagas", params={"q": "java"}, headers=HX).text

        self.assertNotIn("<html", parcial)
        self.assertNotIn('id="busca"', parcial)
        self.assertNotIn('id="detalhe"', parcial)
        self.assertIn('<div id="vagas">', parcial)


class BuscaTest(unittest.TestCase):
    def test_q_java_nao_traz_javascript_na_tela(self):
        cliente, _ = criar([vaga(1, "Dev Java"), vaga(2, "Dev JavaScript"), vaga(3, "Engenheiro Java Sênior")])

        for resposta in (cliente.get("/tela", params={"q": "java"}), cliente.get("/tela/vagas", params={"q": "java"}, headers=HX)):
            pagina = Pagina(resposta.text)
            textos = [c.texto for c in pagina.cards()]
            self.assertEqual(len(textos), 2)
            self.assertFalse(any("JavaScript" in t for t in textos))
            self.assertIn("2 encontradas", pagina.por_id("vagas").texto)

    def test_deep_link_da_url_empurrada_devolve_a_pagina_com_campo_e_lista_filtrados(self):
        cliente, _ = criar([vaga(1, "Dev Java"), vaga(2, "Dev Python")])

        resposta = cliente.get("/tela/vagas?q=java")
        pagina = Pagina(resposta.text)

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(len(pagina.tags("html")), 1)
        self.assertEqual(pagina.por_id("busca").attrs["value"], "java")
        self.assertEqual(urls(pagina), ["https://exemplo.test/vaga/1"])

    def test_q_vazio_ou_so_espacos_lista_tudo_e_campo_vazio(self):
        cliente, _ = criar([vaga(1), vaga(2)])

        for q in ("", "   "):
            pagina = Pagina(cliente.get("/tela/vagas", params={"q": q}, headers=HX).text)
            self.assertEqual(len(pagina.cards()), 2, repr(q))
        self.assertEqual(Pagina(cliente.get("/tela", params={"q": "   "}).text).por_id("busca").attrs["value"], "")

    def test_sem_resultado_mostra_aviso_sem_botao_de_coleta(self):
        cliente, _ = criar([vaga(1, "Dev Python")])

        texto = cliente.get("/tela/vagas", params={"q": "cobol"}, headers=HX).text

        self.assertIn("Nenhuma vaga encontrada para a busca", texto)
        # Sem o botão no estado vazio (só o da barra da lista, do PR4): a busca sem resultado não pede coleta.
        self.assertEqual(texto.count("Coletar agora"), 1)
        self.assertNotIn("rode uma coleta", texto)
        self.assertIn("0 encontradas", texto)

    def test_partial_da_busca_e_pedaco_da_pagina_inteira_com_o_mesmo_q(self):
        cliente, _ = criar([vaga(1, "Dev Java"), vaga(2, "Dev Python")])

        parcial = cliente.get("/tela/vagas", params={"q": "java"}, headers=HX).text
        inteira = cliente.get("/tela/vagas", params={"q": "java"}).text

        self.assertIn(parcial.strip(), sem_selecao(inteira))

    def test_busca_grande_demais_e_422_com_htmx_retarget(self):
        cliente, _ = criar([vaga(1)])

        resposta = cliente.get("/tela/vagas", params={"q": "a" * 500}, headers=HX)

        self.assertEqual(resposta.status_code, 422)
        self.assertEqual(resposta.headers["hx-retarget"], "#aviso")


class IdaEVoltaDoQTest(unittest.TestCase):
    def test_q_dificil_vai_ao_campo_ao_sentinela_e_volta_na_mesma_lista(self):
        cliente, repo = criar(vagas_de_conciliacao(60) + [vaga("x", "Dev Java")])
        esperado = [v.url for v in repo.listar(somente_novas=False, limite=500, q=Q_DIFICIL)]
        self.assertEqual(len(esperado), 60)

        for rota in ("/tela", "/tela/vagas"):
            with self.subTest(rota=rota):
                pagina = Pagina(cliente.get(rota, params={"q": Q_DIFICIL}).text)

                # O valor volta do HTML (desescapado) exatamente como foi enviado.
                self.assertEqual(pagina.por_id("busca").attrs["value"], Q_DIFICIL.strip())
                self.assertIn("60 encontradas", pagina.por_id("vagas").texto)
                self.assertEqual(urls(pagina), esperado[:50])
                proximo = sentinela(pagina).attrs["hx-get"]
                self.assertEqual(proximo, "/tela/vagas?deslocamento=50&q=" + quote(Q_DIFICIL))
                # Nenhum caractere de query solto no valor.
                valor = proximo.split("&q=", 1)[1]
                for solto in " &#?=+":
                    self.assertNotIn(solto, valor)

                segunda = Pagina(cliente.get(proximo, headers=HX).text)
                self.assertEqual(urls(pagina) + urls(segunda), esperado)
                self.assertIsNone(sentinela(segunda))

    def test_q_com_html_e_aspas_e_escapado_no_campo_e_no_sentinela(self):
        q = "\"><script>alert(1)</script> conciliação"
        cliente, _ = criar(vagas_de_conciliacao(60))

        texto = cliente.get("/tela", params={"q": q}).text
        pagina = Pagina(texto)

        self.assertNotIn("<script>alert", texto)
        self.assertEqual([s for s in pagina.tags("script") if "src" not in s.attrs], [])
        self.assertEqual(pagina.por_id("busca").attrs["value"], q)
        for elemento in pagina.elementos:
            self.assertFalse(any(nome.startswith("on") for nome in elemento.attrs))


if __name__ == "__main__":
    unittest.main()
