"""HTML de provider -> TEXTO PURO. Nada de HTML sai daqui: a tela escapa o texto e usa `white-space: pre-line`.

Só stdlib (`html.parser`). Tag nenhuma sobrevive: `<script>`/`<style>` perdem também o conteúdo, `<a>`
fica só com o texto (o `href` some, então `javascript:` não chega a lugar nenhum), `<img>` desaparece.
Entidades viram o caractere (`&lt;b&gt;` vira o texto `<b>`, que a tela escapa de novo ao renderizar).
"""

import re
from html.parser import HTMLParser

# Teto do texto guardado; o corte põe "…" no fim.
MAX_CARACTERES_DESCRICAO = 20_000
# Teto do HTML lido: o que passar disso nem entra no parser (custo limitado).
MAX_HTML = 200_000

_QUEBRA = {
    "p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "tr", "table",
    "section", "article", "header", "footer", "blockquote", "pre", "hr",
}
_DESCARTA_CONTEUDO = {"script", "style", "template", "noscript", "iframe", "object", "svg", "head", "title"}
# Controles, zero-width e marcas de direção (o texto não pode inverter a leitura da página).
_CONTROLE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f​-‏  ‪-‮⁦-⁩﻿]")


class _Texto(HTMLParser):
    def __init__(self, marcadores: bool) -> None:
        super().__init__(convert_charrefs=True)
        self.partes: list[str] = []
        self._descartando = 0
        self._marcadores = marcadores

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _DESCARTA_CONTEUDO:
            self._descartando += 1
        elif tag == "li":
            self.partes.append("\n• " if self._marcadores else "\n")
        elif tag in _QUEBRA:
            self.partes.append("\n")

    def handle_startendtag(self, tag: str, attrs) -> None:
        # `<br/>` e `<script/>`: sem conteúdo para descartar, só a quebra.
        if tag in _QUEBRA and tag != "li":
            self.partes.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _DESCARTA_CONTEUDO:
            self._descartando = max(0, self._descartando - 1)
        elif tag in _QUEBRA and tag != "li":
            self.partes.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._descartando:
            self.partes.append(data)


def _limpar(html: object, marcadores: bool) -> str:
    if not isinstance(html, str) or not html.strip():
        return ""
    parser = _Texto(marcadores)
    parser.feed(html[:MAX_HTML])
    parser.close()
    texto = _CONTROLE.sub(" ", "".join(parser.partes).replace("\xa0", " ").replace("\r", "\n"))
    linhas = [re.sub(r"[ \t\f\v]+", " ", linha).strip() for linha in texto.split("\n")]
    # No máximo uma linha em branco seguida; item de lista vazio ("•" sozinho) some.
    saida: list[str] = []
    for linha in linhas:
        if linha == "•":
            continue
        if linha or (saida and saida[-1]):
            saida.append(linha)
    return "\n".join(saida).strip()


def html_para_texto(html: object, limite: int = MAX_CARACTERES_DESCRICAO) -> str:
    """Texto puro com quebras de linha e "• " nos itens de lista; "" se não é texto ou não sobra nada."""
    texto = _limpar(html, marcadores=True)
    return texto[: limite - 1].rstrip() + "…" if len(texto) > limite else texto


def html_para_linha(html: object, limite: int) -> str:
    """Uma linha só (requisito, benefício, subtítulo): quebras viram espaço, sem marcadores."""
    texto = re.sub(r"\s+", " ", _limpar(html, marcadores=False)).strip()
    return texto[: limite - 1].rstrip() + "…" if len(texto) > limite else texto
