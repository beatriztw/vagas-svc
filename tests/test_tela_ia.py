"""Tela da IA da vaga: botões, polling, resultado, download do currículo e rótulo.

Vagas, currículos e resultados inventados e escritos à mão (não são respostas gravadas).
"""

import unittest

from tests.sessao import ClienteLogado

from src.controller.api import criar_app
from src.model.curriculo import Curriculo
from src.model.vaga_externa import VagaExterna
from src.repository.base import CurriculoRepositoryMemoria, PerfilBuscaRepositoryMemoria, VagaEncontradaRepositoryMemoria
from src.service.busca_service import BuscaService
from tests.test_tela_lista import Pagina

HX = {"HX-Request": "true", "Origin": "http://testserver"}
URL = "https://exemplo.test/vaga/1"
URL_DIFICIL = "https://exemplo.test/vaga ç/1?a=1&b=2#c"

COMPATIBILIDADE = {
    "nota": 82,
    "faixa": "alta",
    "atende": [{"requisito": "SQL avançado", "evidencia_no_curriculo": "Consultas SQL em bases grandes"}],
    "falta": [{"requisito": "Airflow", "obrigatorio": False}, {"requisito": "Python", "obrigatorio": True}],
    "eliminatorios": ["Inglês fluente"],
    "resumo": "Boa aderência.\nFalta orquestração.",
}


def montar(com_base: bool = True, *urls: str):
    encontradas = VagaEncontradaRepositoryMemoria(1)
    encontradas.registrar([VagaExterna("Analista de Dados", "Empresa Teste", "Remotar", u) for u in (urls or (URL,))], "dados")
    curriculos = CurriculoRepositoryMemoria(1)
    base = curriculos.salvar(Curriculo(tipo="base", titulo="Base", conteudo_md="# Base")) if com_base else None
    app = criar_app(PerfilBuscaRepositoryMemoria(1), curriculos, BuscaService([]), encontradas=encontradas)
    return ClienteLogado(app, raise_server_exceptions=False), app.state.pedido_ia_service, curriculos, base


def pedir(api, tipo="compatibilidade", url=URL):
    return api.post("/tela/vagas/ia", data={"vaga_url": url, "tipo": tipo}, headers=HX)


def id_do(resposta) -> int:
    return int(next(e for e in Pagina(resposta.text).elementos if e.attrs.get("id", "").startswith("ia-pedido-")).attrs["id"].split("-")[-1])


def terminar_compat(servico, resultado=None, versao="compatibilidade-v1"):
    pedido = servico.proximo()
    servico.concluir(pedido["id"], pedido["tentativa"], resultado or COMPATIBILIDADE, versao, "modelo-teste")
    return pedido["id"]


class DetalheTest(unittest.TestCase):
    def test_detalhe_troca_o_botao_desabilitado_pelo_painel_com_a_url_codificada(self):
        api, *_ = montar(True, URL_DIFICIL)

        resposta = api.get("/tela/vagas/detalhe", params={"url": URL_DIFICIL}, headers=HX)

        pagina = Pagina(resposta.text)
        self.assertNotIn("em breve", resposta.text)
        self.assertEqual([b for b in pagina.tags("button") if b.texto.strip() == "Gerar"], [])
        ia = pagina.por_id("ia")
        self.assertEqual(ia.attrs["hx-trigger"], "load")
        self.assertEqual(ia.attrs["hx-get"], "/tela/vagas/ia?vaga_url=https%3A//exemplo.test/vaga%20%C3%A7/1%3Fa%3D1%26b%3D2%23c")
        # O navegador desfaz o %-encoding: o servidor recebe a url exata.
        painel = api.get("/tela/vagas/ia", params={"vaga_url": URL_DIFICIL}, headers=HX)
        self.assertEqual(painel.status_code, 200)
        self.assertIn(URL_DIFICIL.replace("&", "&amp;"), painel.text)

    def test_css_proprio_carregado_pelo_base(self):
        api, *_ = montar()

        self.assertIn('href="/static/tela-ia.css?v=', api.get("/tela").text)
        css = api.get("/static/tela-ia.css")
        self.assertEqual(css.status_code, 200)
        self.assertIn("pre-line", css.text)


class PainelTest(unittest.TestCase):
    def test_painel_sem_htmx_redireciona_para_a_tela(self):
        api, *_ = montar()

        resposta = api.get("/tela/vagas/ia", params={"vaga_url": URL}, follow_redirects=False)

        self.assertEqual((resposta.status_code, resposta.headers["location"]), (303, "/tela"))

    def test_painel_tem_os_dois_botoes_e_vaga_desconhecida_e_404(self):
        api, *_ = montar()

        pagina = Pagina(api.get("/tela/vagas/ia", params={"vaga_url": URL}, headers=HX).text)

        forms = [f for f in pagina.tags("form") if f.attrs.get("hx-post") == "/tela/vagas/ia"]
        self.assertEqual(len(forms), 2)
        self.assertEqual({b.texto.strip() for b in pagina.tags("button") if "botao-ai" in b.attrs.get("class", "")}, {"Compatibilidade", "Gerar currículo"})
        self.assertEqual(api.get("/tela/vagas/ia", params={"vaga_url": "https://nao.existe/x"}, headers=HX).status_code, 404)
        self.assertEqual(api.get("/tela/vagas/ia", headers=HX).status_code, 404)

    def test_painel_mostra_o_ultimo_pedido_de_cada_tipo(self):
        api, servico, *_ = montar()
        pedir(api)
        terminar_compat(servico)

        texto = api.get("/tela/vagas/ia", params={"vaga_url": URL}, headers=HX).text

        self.assertIn("82/100", texto)


class PedirTest(unittest.TestCase):
    def test_post_sem_htmx_ou_origem_e_403(self):
        api, servico, *_ = montar()

        sem_hx = api.post("/tela/vagas/ia", data={"vaga_url": URL, "tipo": "compatibilidade"})
        outra_origem = api.post(
            "/tela/vagas/ia", data={"vaga_url": URL, "tipo": "compatibilidade"}, headers={"HX-Request": "true", "Origin": "http://outro.site"}
        )
        rotulo = api.post("/tela/vagas/ia/rotulo", data={"vaga_url": URL, "faixa": "alta"})

        self.assertEqual((sem_hx.status_code, outra_origem.status_code, rotulo.status_code), (403, 403, 403))
        self.assertIsNone(servico.ultimos(URL)["compatibilidade"])
        self.assertIsNone(servico.rotulo(URL))

    def test_post_cria_o_pedido_com_polling_e_o_segundo_devolve_o_mesmo_id(self):
        api, *_ = montar()

        primeiro = pedir(api)
        segundo = pedir(api)

        self.assertEqual(primeiro.status_code, 202)
        pedido = next(e for e in Pagina(primeiro.text).elementos if e.attrs.get("id", "").startswith("ia-pedido-"))
        self.assertEqual(pedido.attrs["hx-trigger"], "every 3s")
        self.assertEqual(pedido.attrs["hx-swap"], "outerHTML")
        self.assertEqual(pedido.attrs["hx-get"], f"/tela/vagas/ia/{id_do(primeiro)}")
        self.assertIn("Na fila", pedido.texto)
        self.assertEqual(id_do(primeiro), id_do(segundo))

    def test_sem_base_e_422_com_aviso_html(self):
        api, servico, *_ = montar(False)

        resposta = pedir(api)

        self.assertEqual(resposta.status_code, 422)
        self.assertEqual(resposta.headers["HX-Retarget"], "#aviso")
        self.assertIn("Salve um currículo base antes de usar a IA", resposta.text)
        self.assertIsNone(servico.ultimos(URL)["compatibilidade"])

    def test_tipo_invalido_e_vaga_fora_do_historico_sao_422(self):
        api, *_ = montar()

        self.assertEqual(pedir(api, tipo="outro").status_code, 422)
        self.assertEqual(pedir(api, url="https://nao.existe/x").status_code, 422)


class AcompanharTest(unittest.TestCase):
    def test_pendente_e_rodando_seguem_com_polling_200(self):
        api, servico, *_ = montar()
        id = id_do(pedir(api))

        pendente = api.get(f"/tela/vagas/ia/{id}", headers=HX)
        servico.proximo()
        rodando = api.get(f"/tela/vagas/ia/{id}", headers=HX)

        self.assertEqual((pendente.status_code, rodando.status_code), (200, 200))
        self.assertIn("Na fila", pendente.text)
        self.assertIn("Rodando", rodando.text)
        self.assertIn('hx-trigger="every 3s"', rodando.text)

    def test_compatibilidade_pronta_responde_286_sem_polling_e_mostra_o_resultado(self):
        api, servico, *_ = montar()
        id = id_do(pedir(api))
        terminar_compat(servico)

        resposta = api.get(f"/tela/vagas/ia/{id}", headers=HX)

        self.assertEqual(resposta.status_code, 286)
        self.assertNotIn("hx-trigger", resposta.text)
        self.assertNotIn("hx-get", resposta.text)
        texto = " ".join(Pagina(resposta.text).por_id(f"ia-pedido-{id}").texto.split())
        for esperado in ("82/100", "alta", "Boa aderência.", "SQL avançado", "“Consultas SQL em bases grandes”", "Airflow desejável", "Python obrigatório", "Inglês fluente", "compatibilidade-v1"):
            self.assertIn(esperado, texto)

    def test_erro_responde_286_com_a_mensagem_escapada(self):
        api, servico, *_ = montar()
        id = id_do(pedir(api))
        pedido = servico.proximo()
        servico.falhar(pedido["id"], pedido["tentativa"], "tempo <b>esgotado</b>", "compatibilidade-v1", "m")

        resposta = api.get(f"/tela/vagas/ia/{id}", headers=HX)

        self.assertEqual(resposta.status_code, 286)
        self.assertIn("Não foi possível: tempo &lt;b&gt;esgotado&lt;/b&gt;", resposta.text)
        self.assertNotIn("<b>", resposta.text)
        self.assertNotIn("hx-trigger", resposta.text)

    def test_script_na_evidencia_sai_escapado(self):
        api, servico, *_ = montar()
        id = id_do(pedir(api))
        atende = [{"requisito": "<img src=x>", "evidencia_no_curriculo": "<script>alert(1)</script>"}]
        terminar_compat(servico, {**COMPATIBILIDADE, "atende": atende})

        texto = api.get(f"/tela/vagas/ia/{id}", headers=HX).text

        self.assertNotIn("<script>", texto)
        self.assertNotIn("<img", texto)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", texto)

    def test_id_nao_numerico_e_422_e_desconhecido_e_404_que_encerra_o_polling(self):
        api, *_ = montar()

        self.assertEqual(api.get("/tela/vagas/ia/abc", headers=HX).status_code, 422)
        self.assertEqual(api.get("/tela/vagas/ia/١٢", headers=HX).status_code, 422)
        # Milhares de dígitos: int() levantaria ValueError e a rota daria 500. É só um pedido que não existe.
        self.assertEqual(api.get("/tela/vagas/ia/" + "9" * 5000, headers=HX).status_code, 404)
        self.assertEqual(api.get("/tela/vagas/ia/" + "9" * 5000 + "/curriculo.md").status_code, 404)
        desconhecido = api.get("/tela/vagas/ia/999", headers=HX)
        enorme = api.get(f"/tela/vagas/ia/{10**30}", headers=HX)
        self.assertEqual((desconhecido.status_code, enorme.status_code), (404, 404))
        self.assertIn("Pedido não encontrado", desconhecido.text)
        self.assertNotIn("hx-trigger", desconhecido.text)


class CurriculoTest(unittest.TestCase):
    def pronto(self, api, servico, curriculos, base, titulo="Analista - Empresa Teste"):
        id = id_do(pedir(api, "curriculo"))
        pedido = servico.proximo()
        adaptado = curriculos.salvar(
            Curriculo(tipo="adaptado", titulo=titulo, conteudo_md="# Adaptado\nTexto", base_id=base.id, vaga_url=URL)
        )
        resultado = {"curriculo_id": adaptado.id, "titulo": adaptado.titulo, "o_que_mudou": ["Reordenei <i>SQL</i>"], "lacunas": ["Airflow"]}
        servico.concluir(pedido["id"], pedido["tentativa"], resultado, "curriculo-v1", "m")
        return id

    def test_curriculo_pronto_mostra_link_mudancas_e_lacunas(self):
        api, servico, curriculos, base = montar()
        id = self.pronto(api, servico, curriculos, base)

        resposta = api.get(f"/tela/vagas/ia/{id}", headers=HX)

        self.assertEqual(resposta.status_code, 286)
        pagina = Pagina(resposta.text)
        pdf = next(a for a in pagina.tags("a") if a.texto.strip() == "Baixar currículo (PDF)")
        self.assertEqual(pdf.attrs["href"], f"/tela/vagas/ia/{id}/curriculo.pdf")
        md = next(a for a in pagina.tags("a") if a.texto.strip() == ".md")
        self.assertEqual(md.attrs["href"], f"/tela/vagas/ia/{id}/curriculo.md")
        self.assertIn("Analista - Empresa Teste", resposta.text)
        self.assertIn("Reordenei &lt;i&gt;SQL&lt;/i&gt;", resposta.text)
        self.assertIn("Airflow", resposta.text)

    def test_download_do_markdown(self):
        api, servico, curriculos, base = montar()
        id = self.pronto(api, servico, curriculos, base)

        resposta = api.get(f"/tela/vagas/ia/{id}/curriculo.md")

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.text, "# Adaptado\nTexto")
        self.assertEqual(resposta.headers["content-type"], "text/markdown; charset=utf-8")
        self.assertEqual(resposta.headers["content-disposition"], f'attachment; filename="curriculo-{id}.md"')
        self.assertEqual(resposta.headers["cache-control"], "no-store")

    def test_download_do_pdf(self):
        import io

        from pypdf import PdfReader

        api, servico, curriculos, base = montar()
        id = self.pronto(api, servico, curriculos, base)

        resposta = api.get(f"/tela/vagas/ia/{id}/curriculo.pdf")

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.headers["content-type"], "application/pdf")
        self.assertEqual(
            resposta.headers["content-disposition"],
            'attachment; filename="Curriculo - Analista - Empresa Teste.pdf"; '
            "filename*=UTF-8''Curriculo%20-%20Analista%20-%20Empresa%20Teste.pdf",
        )
        self.assertEqual(resposta.headers["cache-control"], "no-store")
        texto = PdfReader(io.BytesIO(resposta.content)).pages[0].extract_text()
        self.assertIn("Adaptado", texto)
        self.assertIn("Texto", texto)

    def test_pdf_com_titulo_acentuado_leva_o_acento_no_filename_estrela(self):
        from urllib.parse import unquote

        api, servico, curriculos, base = montar()
        id = self.pronto(api, servico, curriculos, base, titulo="Analista Fiscal Pl - Albiéri & Associados")

        cabecalho = api.get(f"/tela/vagas/ia/{id}/curriculo.pdf").headers["content-disposition"]

        ascii_, utf8 = cabecalho.split("; filename*=UTF-8''")
        self.assertEqual(ascii_, 'attachment; filename="Curriculo - Analista Fiscal Pl - Albieri Associados.pdf"')
        self.assertEqual(unquote(utf8), "Curriculo - Analista Fiscal Pl - Albiéri & Associados.pdf")
        self.assertNotIn("&", utf8)  # tudo fora do conjunto seguro vai codificado

    def test_download_e_404_para_compatibilidade_pendente_e_inexistente(self):
        api, servico, *_ = montar()
        compat = id_do(pedir(api))
        curriculo_pendente = id_do(pedir(api, "curriculo"))
        terminar_compat(servico)

        for formato in ("md", "pdf"):
            for caminho in (compat, curriculo_pendente, 999):
                self.assertEqual(api.get(f"/tela/vagas/ia/{caminho}/curriculo.{formato}").status_code, 404, (formato, caminho))
            self.assertEqual(api.get(f"/tela/vagas/ia/abc/curriculo.{formato}").status_code, 422, formato)


class RotuloTest(unittest.TestCase):
    def post(self, api, faixa, url=URL):
        return api.post("/tela/vagas/ia/rotulo", data={"vaga_url": url, "faixa": faixa}, headers=HX)

    def test_grava_troca_e_limpa(self):
        api, servico, *_ = montar()

        gravou = self.post(api, "alta")
        self.assertEqual(servico.rotulo(URL).value, "alta")
        self.assertIn("Sua avaliação", gravou.text)
        ativo = [b for b in Pagina(gravou.text).tags("button") if b.attrs.get("aria-pressed") == "true"]
        self.assertEqual([b.attrs["value"] for b in ativo], ["alta"])

        self.post(api, "média")
        self.assertEqual(servico.rotulo(URL).value, "média")

        limpou = self.post(api, "")
        self.assertEqual(limpou.status_code, 200)
        self.assertIsNone(servico.rotulo(URL))

    def test_faixa_invalida_e_422_e_nao_muda_nada(self):
        api, servico, *_ = montar()
        self.post(api, "baixa")

        self.assertEqual(self.post(api, "media").status_code, 422)
        self.assertEqual(self.post(api, "ótima").status_code, 422)
        self.assertEqual(servico.rotulo(URL).value, "baixa")


if __name__ == "__main__":
    unittest.main()
