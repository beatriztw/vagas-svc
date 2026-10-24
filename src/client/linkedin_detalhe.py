"""Descrição de uma vaga do LinkedIn, sob demanda, pela página pública da vaga (sem login).

`GET https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id}` devolve HTML; a descrição fica em
`<div class="description__text description__text--rich">` > `<section class="show-more-less-html">` >
`<div class="show-more-less-html__markup ...">`, que pode ter divs aninhadas. Conferido do Pi em
13/10/2026: pede User-Agent de navegador; o LinkedIn limita forte (3 requisições seguidas deram 200 e a
4ª, 8 s depois, 429 sem Retry-After); id inexistente dá 404.

Qualquer falha é `ErroProvider`, inclusive 404/410: 404 ou 429 do LinkedIn não prova que a vaga fechou,
então nada aqui marca a vaga como expirada.
"""

import http.client
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from html.parser import HTMLParser

from src.client.remotar import ErroProvider
from src.client.sanitizar import MAX_CARACTERES_DESCRICAO, html_para_texto
from src.model.detalhe_vaga import DetalheVaga
from src.model.situacao_vaga import SituacaoNaFonte

DETALHE_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id}"
# A página pública responde a navegador; com o User-Agent do `base` (vagas-svc/0.1) não foi conferida.
USER_AGENT_NAVEGADOR = (
    "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
# Abrir o detalhe espera o provider: mais que isso e a tela mostra o aviso no lugar das seções.
TIMEOUT_DETALHE_SEGUNDOS = 5
MAX_BYTES_DETALHE = 2_000_000
ID_DA_VAGA = re.compile(r"[0-9]{1,20}")
CLASSE_DESCRICAO = "show-more-less-html__markup"
# A vaga encerrada mostra `<figcaption class="closed-job__flavor--closed">Não aceita mais candidaturas`
# (conferido em 24/10/2026 em vagas fechadas da página pública; as abertas não têm a classe).
_FECHADA = re.compile(r"""class=["'][^"']*\bclosed-job__flavor--closed\b""")

HttpGetTexto = Callable[[str], str]


class LimiteDeRequisicoes(ErroProvider):
    """O LinkedIn respondeu 429: a conferência para de consultar o LinkedIn até a próxima."""


def get_html_detalhe(url: str) -> str:
    """GET com tempo de 5 s; qualquer falha (rede, tempo, 404, 429, 5xx, corpo grande) é `ErroProvider`."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT_NAVEGADOR,
            "Accept": "text/html",
            "Accept-Language": "pt-BR",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_DETALHE_SEGUNDOS) as resp:
            corpo = resp.read(MAX_BYTES_DETALHE + 1)
            charset = resp.headers.get_content_charset() if resp.headers is not None else None
    except urllib.error.HTTPError as erro:
        # 404/410 e 429 também: o LinkedIn não diz com isso que a vaga fechou.
        if erro.code == 429:
            raise LimiteDeRequisicoes("HTTP 429") from erro
        raise ErroProvider(f"HTTP {erro.code}") from erro
    except (OSError, http.client.HTTPException, ValueError) as erro:
        raise ErroProvider(type(erro).__name__) from erro
    if not isinstance(corpo, bytes):
        raise ErroProvider("resposta sem corpo")
    if len(corpo) > MAX_BYTES_DETALHE:
        raise ErroProvider("resposta grande demais")
    try:
        return corpo.decode(charset or "utf-8", errors="replace")
    except LookupError:
        return corpo.decode("utf-8", errors="replace")


class _BlocoDescricao(HTMLParser):
    """O HTML de dentro do primeiro `div.show-more-less-html__markup`, contando a profundidade das divs.

    O conteúdo é remontado como veio (tags com os atributos originais, entidades sem decodificar), para o
    `html_para_texto` decodificar uma vez só: `&lt;b&gt;` continua sendo texto, nunca vira tag.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.partes: list[str] = []
        self.achou = False
        self._profundidade = 0  # divs abertas dentro do bloco; 0 = fora dele
        self._terminou = False

    @property
    def _dentro(self) -> bool:
        return self._profundidade > 0 and not self._terminou

    def handle_starttag(self, tag: str, attrs) -> None:
        if self._terminou:
            return
        if self._dentro:
            if tag == "div":
                self._profundidade += 1
            self.partes.append(self.get_starttag_text() or "")
            return
        if tag == "div" and not self.achou:
            classes = next((v for k, v in attrs if k == "class"), None) or ""
            if CLASSE_DESCRICAO in classes.split():
                self.achou = True
                self._profundidade = 1

    def handle_startendtag(self, tag: str, attrs) -> None:
        if self._dentro:
            self.partes.append(self.get_starttag_text() or "")

    def handle_endtag(self, tag: str) -> None:
        if not self._dentro:
            return
        if tag == "div":
            self._profundidade -= 1
            if self._profundidade == 0:
                self._terminou = True
                return
        self.partes.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._dentro:
            self.partes.append(data)

    def handle_entityref(self, name: str) -> None:
        if self._dentro:
            self.partes.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self._dentro:
            self.partes.append(f"&#{name};")


def extrair_descricao(html: object) -> str:
    """O HTML de dentro do bloco da descrição; `ErroProvider` se a página não tem o bloco."""
    if not isinstance(html, str):
        raise ErroProvider("resposta não é texto")
    parser = _BlocoDescricao()
    try:
        parser.feed(html)
        parser.close()
    except (AssertionError, ValueError, RecursionError) as erro:
        raise ErroProvider(f"HTML ilegível: {type(erro).__name__}") from erro
    if not parser.achou:
        raise ErroProvider("página sem o bloco da descrição")
    return "".join(parser.partes)


class LinkedInDetalheClient:
    """Detalhe sob demanda de vaga do LinkedIn: só a descrição (a página pública não separa requisitos,
    salário nem benefícios). Uma requisição por vaga aberta, só quando falta a descrição."""

    nome = "LinkedIn"

    def __init__(self, http: HttpGetTexto = get_html_detalhe):
        self._http = http

    def detalhar(self, id: str) -> DetalheVaga:
        """Descrição da vaga `id` (só dígitos, até 20). `ErroProvider` em qualquer falha, nunca
        `VagaNaoEncontrada`: nada que o LinkedIn mande marca a vaga como expirada."""
        if not isinstance(id, str) or not ID_DA_VAGA.fullmatch(id):
            raise ValueError("id da vaga do LinkedIn tem que ter só dígitos (até 20)")
        descricao = html_para_texto(extrair_descricao(self._http(DETALHE_URL.format(id=id))), MAX_CARACTERES_DESCRICAO)
        if not descricao:
            raise ErroProvider("descrição vazia")
        return DetalheVaga(descricao=descricao)

    def situacao(self, id: str) -> SituacaoNaFonte:
        """Se a vaga `id` ainda aceita candidaturas, para a conferência diária. Só a página dizendo que
        encerrou é FECHADA (404 do LinkedIn não prova nada); a página normal, com a descrição, é ABERTA;
        429 é BLOQUEADA; qualquer outra coisa é DESCONHECIDA."""
        if not isinstance(id, str) or not ID_DA_VAGA.fullmatch(id):
            raise ValueError("id da vaga do LinkedIn tem que ter só dígitos (até 20)")
        try:
            pagina = self._http(DETALHE_URL.format(id=id))
        except LimiteDeRequisicoes:
            return SituacaoNaFonte.BLOQUEADA
        except ErroProvider:
            return SituacaoNaFonte.DESCONHECIDA
        if not isinstance(pagina, str):
            return SituacaoNaFonte.DESCONHECIDA
        if _FECHADA.search(pagina):
            return SituacaoNaFonte.FECHADA
        try:
            # Página sem o bloco da descrição (login, captcha) levanta: não dá para saber.
            return SituacaoNaFonte.ABERTA if extrair_descricao(pagina).strip() else SituacaoNaFonte.DESCONHECIDA
        except ErroProvider:
            return SituacaoNaFonte.DESCONHECIDA
