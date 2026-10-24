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
    return TestClient(criar_app(PerfilBuscaRepositoryMemoria(1), CurriculoRepositoryMemoria(1), BuscaService([])))


class TelaMobileTest(unittest.TestCase):
    def test_css_e_js_sao_servidos_com_a_csp(self):
        c = cliente()
        for nome in ("tela-mobile.css", "tela-mobile.js"):
            resposta = c.get(f"/static/{nome}")
            self.assertEqual(resposta.status_code, 200, nome)
            self.assertIn("default-src 'self'", resposta.headers["content-security-policy"])

    def test_base_carrega_o_css_e_o_js_novos_depois_dos_antigos(self):
        html = cliente().get("/tela").text

        self.assertRegex(html, r'<link rel="stylesheet" href="/static/tela-mobile\.css\?v=[0-9a-f]{10}">')
        self.assertRegex(html, r'<script src="/static/tela-mobile\.js\?v=[0-9a-f]{10}" defer></script>')
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

    def test_tabela_aplicadas_nao_alarga_a_pagina_no_celular(self):
        # O "Ações" (.visualmente-oculto, absolute) da tabela escapava do overflow-x da .tabela-rolavel e dava
        # scroll horizontal na página inteira (scrollWidth 527 > 390). Provado no navegador; aqui só a regra.
        css = (STATIC / "tela-mobile.css").read_text()
        dentro_da_media = css.split("@media", 1)[1]

        self.assertRegex(dentro_da_media, r"\.tabela-rolavel\s*\{\s*position:\s*relative;\s*\}")

    def test_aviso_fica_acima_do_detalhe_aberto(self):
        # A camada do detalhe aberto é fixed com z-index 100; o #aviso (erros de ações feitas nele) tem de
        # ficar por cima dela, na mesma condição. Provado no navegador (390x844); aqui só a regra.
        dentro_da_media = (STATIC / "tela-mobile.css").read_text().split("@media", 1)[1]
        regras = dict(
            (seletor.strip(), corpo)
            for seletor, corpo in re.findall(r"([^{}]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", dentro_da_media, flags=re.S))
        )
        aberto = "#detalhe[data-aberto]:not([data-fechado])"
        [camada] = [c for s, c in regras.items() if s.startswith(".col-detalhe:has(") and aberto in s]
        [aviso] = [c for s, c in regras.items() if s.endswith("#aviso") and aberto in s]

        def z(corpo):
            return int(re.search(r"z-index:\s*(\d+)", corpo).group(1))

        self.assertRegex(aviso, r"position:\s*fixed")
        self.assertGreater(z(aviso), z(camada))

    def test_botao_voltar_tem_rotulo_acessivel_e_o_detalhe_recebe_foco(self):
        js = (STATIC / "tela-mobile.js").read_text()

        self.assertIn('"aria-label", "Voltar para a lista de vagas"', js)
        self.assertIn('"tabindex", "-1"', js)
        self.assertIn("history.go(-passos)", js)
        self.assertIn("htmx:historyRestore", js)

    def test_voltar_so_volta_no_historico_pelas_entradas_que_um_card_abriu(self):
        # Era um contador global de `htmx:pushedIntoHistory`: contava o push da busca e de Todas/Novas e não via o
        # voltar/avançar do navegador, então o "Voltar" saía do site ou deixava duas /tela seguidas. Agora a entrada
        # aberta por card é marcada pela chave da Navigation API (o htmx reescreve o history.state ao sair e voltar).
        # Os três cenários do review foram conferidos no navegador (Playwright 900x500 -> 390x844); aqui só a regra.
        js = (STATIC / "tela-mobile.js").read_text()
        sem_comentario = re.sub(r"//[^\n]*", "", js)

        self.assertNotRegex(sem_comentario, r"\w+\s*\+=\s*1;\s*\}\s*\)", "nada de contador por push")
        self.assertNotIn("history.back()", sem_comentario)
        # Quem fez o request é o requestConfig.elt (o detail.elt do evento é o body) e só card marca a entrada.
        self.assertRegex(sem_comentario, r"htmx:beforeHistoryUpdate[\s\S]*requestConfig\.elt[\s\S]*contains\(\"card\"\)")
        self.assertRegex(sem_comentario, r"htmx:pushedIntoHistory[\s\S]*currentEntry\.key\]\s*=\s*true")
        # Sem Navigation API não volta no histórico: fecha no lugar (não sai do site).
        self.assertRegex(sem_comentario, r"if \(!n\) \{ return 0; \}")

    def test_aviso_visivel_nao_cobre_o_rodape_do_detalhe(self):
        # O #aviso fixo embaixo (até 40vh) cobria "Marcar como aplicada" no fim do detalhe e não dava para tentar de
        # novo. Com o aviso visível, a camada reserva embaixo pelo menos a altura máxima dele. Provado no navegador
        # (390x844, rodapé no fim: botão em 474 px, aviso a partir de 734 px; sem a regra, o aviso ficava por cima).
        dentro_da_media = re.sub(r"/\*.*?\*/", "", (STATIC / "tela-mobile.css").read_text().split("@media", 1)[1], flags=re.S)
        regras = dict((s.strip(), c) for s, c in re.findall(r"([^{}]+)\{([^{}]*)\}", dentro_da_media))
        aberto = "#detalhe[data-aberto]:not([data-fechado])"
        [aviso] = [c for s, c in regras.items() if s.endswith("#aviso") and aberto in s]
        [reserva] = [c for s, c in regras.items() if s.startswith("body:has(#aviso:not([hidden]))") and aberto in s]

        altura_maxima = re.search(r"max-height:\s*(\d+)vh", aviso).group(1)
        fundo = re.search(r"bottom:\s*(\d+)px", aviso).group(1)
        m = re.search(r"padding-bottom:\s*calc\((\d+)vh \+ (\d+)px\)", reserva)
        self.assertIsNotNone(m)
        self.assertGreaterEqual(int(m.group(1)), int(altura_maxima))
        self.assertGreater(int(m.group(2)), int(fundo))


if __name__ == "__main__":
    unittest.main()
