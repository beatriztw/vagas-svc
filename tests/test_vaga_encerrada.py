"""O botão "Encerrada" do detalhe: a pessoa marca à mão a vaga que a conferência não vê (post do LinkedIn,
vaga que só aparece fechada para quem está logado). Tela e repositório na memória; vagas escritas à mão."""

import unittest

from tests.test_tela_lista import Pagina
from tests.test_vagas_salvas import HX, Tela, titulos, url, vaga


class EncerradaTest(unittest.TestCase):
    def encerrar(self, t: Tela, i: int):
        return t.cliente.post("/tela/vagas/encerrada", data={"url": url(i)}, headers=HX)

    def test_marca_expirada_e_a_vaga_sai_da_lista(self):
        t = Tela([vaga(1), vaga(2)])

        resposta = self.encerrar(t, 1)

        self.assertEqual(resposta.status_code, 200)
        self.assertIsNotNone(t.repo.obter(url(1)).expirada_em)
        self.assertIsNone(t.repo.obter(url(2)).expirada_em)
        self.assertEqual(titulos(t.lista()), ["Analista de Dados 2"])

    def test_resposta_e_o_detalhe_com_o_aviso_e_sem_o_botao(self):
        t = Tela([vaga(1)])

        resposta = self.encerrar(t, 1)

        pagina = Pagina(resposta.text)
        self.assertEqual(pagina.por_id("detalhe").attrs["data-url"], url(1))
        self.assertIn("Vaga expirada", resposta.text)
        self.assertNotIn("/tela/vagas/encerrada", resposta.text)

    def test_detalhe_da_vaga_no_ar_tem_o_botao_com_confirmacao(self):
        t = Tela([vaga(1)])

        html = t.cliente.get("/tela/vagas/detalhe", params={"url": url(1)}, headers=HX).text

        form = next(e for e in Pagina(html).tags("form") if e.attrs.get("hx-post") == "/tela/vagas/encerrada")
        self.assertIn("hx-confirm", form.attrs)
        self.assertIn('name="url" value="' + url(1) + '"', html)

    def test_salva_encerrada_continua_na_aba_salvas(self):
        t = Tela([vaga(1)])
        t.salvar(1)

        self.encerrar(t, 1)

        salvas = t.lista("?salvas=1")
        self.assertEqual(titulos(salvas), ["Analista de Dados 1"])
        self.assertIn("encerrada", salvas.por_id("vagas").texto)

    def test_encerrar_de_novo_guarda_a_primeira_data(self):
        t = Tela([vaga(1)])
        self.encerrar(t, 1)
        primeira = t.repo.obter(url(1)).expirada_em

        self.assertEqual(self.encerrar(t, 1).status_code, 200)
        self.assertEqual(t.repo.obter(url(1)).expirada_em, primeira)

    def test_vaga_inexistente_404_e_url_vazia_422(self):
        t = Tela([vaga(1)])
        self.assertEqual(self.encerrar(t, 9).status_code, 404)
        self.assertEqual(t.cliente.post("/tela/vagas/encerrada", data={"url": ""}, headers=HX).status_code, 422)
        self.assertIsNone(t.repo.obter(url(1)).expirada_em)

    def test_sem_htmx_e_origem_da_tela_e_recusado(self):
        t = Tela([vaga(1)])
        resposta = t.cliente.post("/tela/vagas/encerrada", data={"url": url(1)})
        self.assertIn(resposta.status_code, (403, 415))
        self.assertIsNone(t.repo.obter(url(1)).expirada_em)


if __name__ == "__main__":
    unittest.main()
