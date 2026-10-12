"""Detalhe no celular (bug 5). O comportamento de verdade (abrir por cima, voltar, foco, history) foi
conferido no navegador (Playwright 390x844 e 1280x800); aqui só o que dá para provar sem navegador."""
import re
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from src.controller.api import criar_app
from src.repository.base import CurriculoRepositoryMemoria, PerfilBuscaRepositoryMemoria
from src.service.busca_service import BuscaService

STATIC = Path(__file__).resolve().parent.parent / "src" / "static"


def cliente() -> TestClient:
    return TestClient(criar_app(PerfilBuscaRepositoryMemoria(), CurriculoRepositoryMemoria(), BuscaService([])))


class TelaMobileTest(unittest.TestCase):
    def test_css_e_js_sao_servidos_com_a_csp(self):
        c = cliente()
        for nome in ("tela-mobile.css", "tela-mobile.js"):
            resposta = c.get(f"/static/{nome}")
            self.assertEqual(resposta.status_code, 200, nome)
            self.assertIn("default-src 'self'", resposta.headers["content-security-policy"])

    def test_base_carrega_o_css_e_o_js_novos_depois_dos_antigos(self):
        html = cliente().get("/tela").text

        self.assertIn('<link rel="stylesheet" href="/static/tela-mobile.css">', html)
        self.assertIn('<script src="/static/tela-mobile.js" defer></script>', html)
        self.assertLess(html.index("/static/tela.css"), html.index("/static/tela-mobile.css"))
        self.assertNotRegex(html, r"<script(?![^>]*\bsrc=)")
        self.assertNotRegex(html, r"<style")

    def test_js_sem_eval_nem_handler_por_atributo(self):
        js = (STATIC / "tela-mobile.js").read_text()

        self.assertNotRegex(js, r"\beval\s*\(|new\s+Function|setTimeout\s*\(\s*[\"']")
        self.assertNotRegex(js, r"setAttribute\(\s*[\"'](on\w+|style)[\"']|\.innerHTML|insertAdjacentHTML")

    def test_css_so_muda_o_layout_ate_640px(self):
        css = (STATIC / "tela-mobile.css").read_text()
        fora_da_media = css.split("@media", 1)[0]

        # Fora da media query só o botão "voltar" escondido: no desktop nada muda.
        self.assertEqual(re.sub(r"/\*.*?\*/", "", fora_da_media, flags=re.S).split(), ".voltar { display: none; }".split())
        self.assertIn("@media (max-width: 640px)", css)
        self.assertEqual(css.count("@media"), 1)

    def test_botao_voltar_tem_rotulo_acessivel_e_o_detalhe_recebe_foco(self):
        js = (STATIC / "tela-mobile.js").read_text()

        self.assertIn('"aria-label", "Voltar para a lista de vagas"', js)
        self.assertIn('"tabindex", "-1"', js)
        self.assertIn("history.back()", js)
        self.assertIn("htmx:historyRestore", js)


if __name__ == "__main__":
    unittest.main()
