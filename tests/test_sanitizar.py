"""HTML de provider vira texto puro. Entradas escritas à mão (ataques), mais a descrição de uma vaga
gravada de verdade em 2026-10-12 (tests/fixtures/remotar_job_166020.json)."""

import json
import unittest
from pathlib import Path

from src.client.sanitizar import MAX_CARACTERES_DESCRICAO, html_para_linha, html_para_texto

FIXTURES = Path(__file__).parent / "fixtures"


class TextoPuroTest(unittest.TestCase):
    def test_script_perde_tag_e_conteudo(self):
        texto = html_para_texto("<p>Oi</p><script>alert(1)</script><p>Tchau</p>")
        self.assertEqual(texto, "Oi\n\nTchau")

    def test_style_e_iframe_perdem_o_conteudo(self):
        texto = html_para_texto("a<style>body{color:red}</style><iframe>x</iframe>b")
        self.assertEqual(texto, "ab")

    def test_img_com_onerror_some(self):
        texto = html_para_texto('antes<img src=x onerror="alert(1)">depois')
        self.assertEqual(texto, "antesdepois")
        self.assertNotIn("onerror", texto)

    def test_link_javascript_fica_so_com_o_texto(self):
        texto = html_para_texto('<a href="javascript:alert(1)">clique aqui</a>')
        self.assertEqual(texto, "clique aqui")
        self.assertNotIn("javascript", texto)

    def test_script_dentro_de_atributo_e_tag_quebrada_nao_sobrevivem(self):
        for entrada in ('<a title="<script>x</script>">t</a>', "<scr<script>ipt>x</script>", "<p onclick=x>y"):
            with self.subTest(entrada=entrada):
                texto = html_para_texto(entrada)
                self.assertNotIn("<script", texto.lower())
                self.assertNotIn("onclick", texto)

    def test_script_sem_fechar_descarta_o_resto(self):
        self.assertEqual(html_para_texto("ok<script>alert(1)"), "ok")

    def test_entidades_viram_o_caractere(self):
        self.assertEqual(html_para_texto("<p>Fran&ccedil;a &amp; Brasil &#233; &nbsp;bom</p>"), "França & Brasil é bom")

    def test_entidade_de_tag_vira_texto_literal_nao_tag(self):
        # &lt;script&gt; é texto: aqui sai como "<script>" e é a tela que escapa (autoescape) ao renderizar.
        self.assertEqual(html_para_texto("&lt;script&gt;alert(1)&lt;/script&gt;"), "<script>alert(1)</script>")

    def test_blocos_viram_quebra_e_li_vira_marcador(self):
        html = "<h2>Responsabilidades</h2><ul><li>Um</li><li>Dois<br>três</li></ul><div>fim</div>"
        self.assertEqual(html_para_texto(html), "Responsabilidades\n\n• Um\n• Dois\ntrês\n\nfim")

    def test_espacos_e_linhas_em_branco_colapsam(self):
        self.assertEqual(html_para_texto("<p>  a   b </p><p></p><p></p><p>c</p>"), "a b\n\nc")

    def test_item_de_lista_vazio_some(self):
        self.assertEqual(html_para_texto("<ul><li></li><li>x</li></ul>"), "• x")

    def test_controle_e_marcas_de_direcao_viram_espaco(self):
        texto = html_para_texto("a\x00b‮c​d")
        self.assertNotRegex(texto, r"[\x00‮​]")

    def test_nao_texto_e_vazio_viram_string_vazia(self):
        for entrada in (None, 5, [], {}, "", "   ", "<p></p>", "<script>x</script>"):
            with self.subTest(entrada=entrada):
                self.assertEqual(html_para_texto(entrada), "")

    def test_teto_de_caracteres_com_reticencias(self):
        texto = html_para_texto("<p>" + "a" * 50_000 + "</p>")
        self.assertEqual(len(texto), MAX_CARACTERES_DESCRICAO)
        self.assertTrue(texto.endswith("…"))

    def test_html_gigante_nao_trava(self):
        texto = html_para_texto("<p>x</p>" * 100_000)
        self.assertLessEqual(len(texto), MAX_CARACTERES_DESCRICAO)

    def test_linha_unica_sem_marcador(self):
        self.assertEqual(html_para_linha("<li>um</li><li>dois</li>\n tres", 100), "um dois tres")
        self.assertEqual(html_para_linha("a" * 600, 500), "a" * 499 + "…")

    def test_descricao_gravada_de_verdade(self):
        job = json.loads((FIXTURES / "remotar_job_166020.json").read_text(encoding="utf-8"))
        texto = html_para_texto(job["description"])
        self.assertIn("Responsabilidades", texto)
        self.assertIn("• Modelar repositórios de dados", texto)
        self.assertNotIn("<", texto)
        self.assertNotRegex(texto, r"&[a-z]+;")
