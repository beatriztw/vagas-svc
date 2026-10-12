"""PDF do currículo adaptado (formato ATS). Currículo e pessoa são inventados, escritos à mão."""

import unittest

from src.service.curriculo_pdf import (
    _blocos,
    gerar_pdf,
    nome_do_arquivo,
    nome_utf8_do_arquivo,
)

MD = """# Fulana de Tal
São Paulo, SP | fulana@example.com | (11) 90000-0000

## Resumo
Analista fiscal com **5 anos** em apuração de ICMS — e “conciliação”.

## Experiência
### Analista Fiscal Pleno — Empresa Fictícia
01/2022 – atual · São Paulo
- Apurei ICMS e PIS/COFINS de 3 filiais…
* Conciliei contas no [SAP](https://example.com)

## Competências
SQL, Excel, Power BI, `SPED`
"""


def texto_do_pdf(dados: bytes) -> str:
    """Extrai o texto como um ATS faria. O pypdf vem de requirements-dev.txt (só testes)."""
    import io

    from pypdf import PdfReader

    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(dados)).pages)


class BlocosTest(unittest.TestCase):
    def test_estrutura_do_markdown_ats(self):
        blocos = _blocos(MD)
        self.assertEqual(
            [t for t, _ in blocos],
            ["nome", "contato", "secao", "paragrafo", "secao", "subtitulo", "paragrafo", "topico", "topico", "secao", "paragrafo"],
        )
        self.assertEqual(blocos[0][1], "Fulana de Tal")

    def test_marcacao_inline_vira_texto_puro_e_pontuacao_vira_latin1(self):
        textos = dict(enumerate(t for _, t in _blocos(MD)))
        self.assertEqual(textos[3], 'Analista fiscal com 5 anos em apuração de ICMS - e "conciliação".')
        self.assertEqual(textos[5], "Analista Fiscal Pleno - Empresa Fictícia")
        self.assertEqual(textos[8], "Conciliei contas no SAP (https://example.com)")
        self.assertEqual(textos[10], "SQL, Excel, Power BI, SPED")
        self.assertTrue(textos[7].endswith("filiais..."))

    def test_link_guarda_a_url_sem_mailto_e_sem_repetir(self):
        linha = (
            "[LinkedIn](https://linkedin.com/in/fulana) | [fulana@example.com](mailto:fulana@example.com)"
            " | [https://site.example](https://site.example) | [](https://vazio.example)"
        )
        self.assertEqual(
            _blocos("# Nome\n" + linha)[1][1],
            "LinkedIn (https://linkedin.com/in/fulana) | fulana@example.com | https://site.example | https://vazio.example",
        )

    def test_url_codigo_e_nome_tecnico_passam_intactos(self):
        casos = {
            "- [GitHub](https://github.com/fulana/dados__2024)": "GitHub (https://github.com/fulana/dados__2024)",
            "- veja https://exemplo.com/a__b*c_d": "veja https://exemplo.com/a__b*c_d",
            "- Python (`__init__`, `*args`)": "Python (__init__, *args)",
            "- tabelas snake_case e dados__2024": "tabelas snake_case e dados__2024",
            "- **Negrito**, *itálico*, __forte__ e _leve_": "Negrito, itálico, forte e leve",
            "- [**SAP** FI](https://sap.example/x_y)": "SAP FI (https://sap.example/x_y)",
            "- 3 * 4 = 12 e 2*3": "3 * 4 = 12 e 2*3",
        }
        for md, esperado in casos.items():
            self.assertEqual(_blocos(md)[0][1], esperado, md)

    def test_fora_do_latin1_vira_interrogacao_sem_quebrar(self):
        self.assertEqual(_blocos("# Nome\n\nPython 🐍 e 中文")[1][1], "Python ? e ??")

    def test_regua_e_linha_vazia_somem(self):
        self.assertEqual(_blocos("# Nome\n---\n\n***\n## Resumo"), [("nome", "Nome"), ("secao", "Resumo")])


class GerarPdfTest(unittest.TestCase):
    def test_pdf_valido_com_texto_selecionavel_na_ordem(self):
        dados = gerar_pdf(MD, "Analista Fiscal Pl - Empresa Fictícia")
        self.assertTrue(dados.startswith(b"%PDF-"))
        texto = texto_do_pdf(dados)
        ordem = ["Fulana de Tal", "fulana@example.com", "RESUMO", "EXPERIÊNCIA", "Analista Fiscal Pleno", "Apurei", "COMPETÊNCIAS", "Power BI"]
        posicoes = [texto.find(t) for t in ordem]
        self.assertNotIn(-1, posicoes, texto)
        self.assertEqual(posicoes, sorted(posicoes))
        self.assertIn("apuração", texto)

    def test_sem_imagem_nem_objeto_externo(self):
        import io

        from pypdf import PdfReader

        for pagina in PdfReader(io.BytesIO(gerar_pdf(MD, "x"))).pages:
            self.assertNotIn("/XObject", pagina["/Resources"])

    def test_metadados_titulo_e_autor(self):
        import io

        from pypdf import PdfReader

        meta = PdfReader(io.BytesIO(gerar_pdf(MD, "Analista Fiscal Pl - Empresa Fictícia"))).metadata
        self.assertEqual(meta.title, "Analista Fiscal Pl - Empresa Fictícia")
        self.assertEqual(meta.author, "Fulana de Tal")

    def test_currículo_longo_quebra_pagina(self):
        md = "# Nome\n\n## Experiência\n" + "\n".join(f"- tópico {i} " + "palavra " * 30 for i in range(80))
        import io

        from pypdf import PdfReader

        self.assertGreater(len(PdfReader(io.BytesIO(gerar_pdf(md, "x"))).pages), 1)

    def test_maiusculo_da_secao_que_sai_do_latin1_nao_quebra(self):
        # "µ".upper() é o mu grego e "ÿ".upper() é U+0178: fora da fonte padrão do PDF.
        texto = texto_do_pdf(gerar_pdf("# Nome\n\n## Latência em µs e ÿ\n- x", "x"))
        self.assertIn("LATÊNCIA EM ?S E ?", texto)

    def test_markdown_vazio_ou_so_marcacao_gera_pdf(self):
        for md in ("", "   \n\n", "---"):
            self.assertTrue(gerar_pdf(md, "x").startswith(b"%PDF-"), repr(md))


class NomeDoArquivoTest(unittest.TestCase):
    def test_nome_utf8_guarda_os_acentos_sem_controle_nem_barra(self):
        self.assertEqual(
            nome_utf8_do_arquivo("Analista Fiscal Pl - Albiéri & Associados"),
            "Curriculo - Analista Fiscal Pl - Albiéri & Associados.pdf",
        )
        self.assertEqual(nome_utf8_do_arquivo('a\r\nb/c\\d"e\x00f'), "Curriculo - a b c d e f.pdf")
        self.assertEqual(nome_utf8_do_arquivo(" \n "), "Curriculo.pdf")

    def test_ascii_seguro(self):
        self.assertEqual(
            nome_do_arquivo("Analista Fiscal Pl - Albiéri & Associados"), "Curriculo - Analista Fiscal Pl - Albieri Associados.pdf"
        )
        self.assertEqual(nome_do_arquivo('a"b\r\nc/../d;e'), "Curriculo - a b c .. d e.pdf")
        self.assertEqual(nome_do_arquivo("???"), "Curriculo.pdf")
        self.assertLessEqual(len(nome_do_arquivo("x" * 500)), len("Curriculo - .pdf") + 80)


if __name__ == "__main__":
    unittest.main()
