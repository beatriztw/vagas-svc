"""Currículo em markdown simples -> PDF legível por ATS (sistemas de triagem de currículo).

O layout é o que os ATS leem sem errar: uma coluna, fonte padrão (Helvetica), texto de verdade
(selecionável, nada de imagem), títulos de seção em linhas próprias e tópicos com hífen. Sem tabela,
cabeçalho, rodapé, ícone nem cor. Entende o markdown dos prompts `curriculo-v2` e `curriculo-v3` (este no
idioma da vaga, com `### Cargo | Empresa`):

    # Nome                      -> nome, em destaque
    linha logo abaixo do nome   -> contato
    ## Seção                    -> título de seção, com uma linha fina embaixo
    ### Cargo | Empresa         -> subtítulo em negrito
    - tópico                    -> tópico com hífen e recuo
    qualquer outra linha        -> parágrafo

Ênfase em par (`**x**`, `*x*`, `__x__`, `_x_`) e crases (`` `x` ``) viram texto puro, e o link
`[texto](url)` vira `texto (url)`; URL e código passam intactos (`dados__2024`, `__init__`). A fonte padrão do PDF só
tem Latin-1, que cobre o português; travessão, aspas curvas, marcador e reticências viram equivalentes
ASCII, e o que sobrar fora do Latin-1 vira "?".
"""

import re
import unicodedata

from fpdf import FPDF

_TROCAS = str.maketrans(
    {
        "—": "-",
        "–": "-",
        "‐": "-",
        "‑": "-",
        "“": '"',
        "”": '"',
        "„": '"',
        "‘": "'",
        "’": "'",
        "•": "-",
        "·": "-",
        "▪": "-",
        "●": "-",
        "…": "...",
        "\u00a0": " ",
        "\u200b": "",
    }
)
_LINK = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
# Trechos que passam sem tirar ênfase: código entre crases (sai sem as crases), link e URL solta.
_INTOCAVEL = re.compile(r"`(?P<codigo>[^`]*)`|(?P<link>\[[^\]]*\]\([^)]*\))|(?P<url>https?://\S+)")
# Ênfase só em PAR delimitado e fora de palavra: `dados__2024` e `snake_case` ficam como estão.
_ENFASES = (
    re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*"),
    re.compile(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])"),
    re.compile(r"(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)"),
    re.compile(r"(?<!\w)_(?=\S)(.+?)(?<=\S)_(?!\w)"),
)
_TOPICO = re.compile(r"^\s*[-*+•]\s+")
# Título ATX de 1 a 6 `#`, com os `#` de fechamento opcionais (`## Resumo ##`).
_TITULO = re.compile(r"^(#{1,6})\s+(.*?)(?:\s+#+)?\s*$")
_TIPO_DO_TITULO = {1: "nome", 2: "secao"}  # 3 a 6: subtítulo

MARGEM_MM = 18
CORPO_PT = 10.5


def _latin1(texto: str) -> str:
    texto = unicodedata.normalize("NFC", texto).translate(_TROCAS)
    return texto.encode("latin-1", errors="replace").decode("latin-1")


def _sem_enfase(texto: str) -> str:
    for padrao in _ENFASES:
        texto = padrao.sub(r"\1", texto)
    return texto


def _link(achado: re.Match) -> str:
    """`[texto](url)` vira `texto (url)`: o PDF não tem link clicável, e o recrutador e o ATS
    precisam do endereço. `mailto:`/`tel:` saem; texto igual à URL não se repete. A URL passa intacta."""
    texto = _sem_enfase(achado.group(1).strip())
    url = re.sub(r"^(mailto|tel):", "", achado.group(2).strip(), flags=re.IGNORECASE)
    if not url or texto == url or texto == achado.group(2).strip():
        return texto or url
    return f"{texto} ({url})" if texto else url


def _limpar(linha: str) -> str:
    """Tira a marcação inline (o ATS lê o texto, não a ênfase), sem mexer em código, link nem URL.

    Os intocáveis viram marcadores, a ênfase sai da linha INTEIRA (assim `**[LinkedIn](url)**` e
    `**Stack: `Python`**` também perdem os asteriscos) e os trechos voltam no fim. A URL solta não
    engole o `*`/`_` de fechamento que vem colado nela."""
    linha = linha.replace("\x00", "")
    trechos: list[str] = []

    def guardar(achado: re.Match) -> str:
        if achado.group("codigo") is not None:
            trechos.append(achado.group("codigo"))
            return f"\x00{len(trechos) - 1}\x00"
        if achado.group("link"):
            trechos.append(_LINK.sub(_link, achado.group("link")))
            return f"\x00{len(trechos) - 1}\x00"
        url = achado.group("url")
        # O `*`/`_` de fechamento colado na URL, mesmo com pontuação depois (`url**.`, `url_),`).
        sobra = re.search(r"[*_]+[.,;:!?)\]]*$", url)
        fecho = sobra.group(0) if sobra else ""
        trechos.append(url[: len(url) - len(fecho)])
        return f"\x00{len(trechos) - 1}\x00{fecho}"

    texto = _sem_enfase(_INTOCAVEL.sub(guardar, linha))
    texto = re.sub(r"\x00(\d+)\x00", lambda m: trechos[int(m.group(1))], texto)
    return _latin1(texto).strip()


def _blocos(conteudo_md: str) -> list[tuple[str, str]]:
    """(tipo, texto) na ordem do arquivo. A primeira linha comum logo depois do `# ` é o contato."""
    blocos: list[tuple[str, str]] = []
    for bruta in conteudo_md.replace("\r\n", "\n").split("\n"):
        linha = bruta.strip()
        # Linha em branco ou régua, com ou sem espaço entre os traços (`---`, `* * *`, `- - -`).
        if not linha or set(linha.replace(" ", "")) <= {"-", "*", "_"}:
            continue
        titulo = _TITULO.match(linha)
        if titulo:
            tipo = _TIPO_DO_TITULO.get(len(titulo.group(1)), "subtitulo")
            blocos.append((tipo, _limpar(titulo.group(2))))
        elif _TOPICO.match(linha):
            blocos.append(("topico", _limpar(_TOPICO.sub("", linha))))
        elif blocos and blocos[-1][0] == "nome":
            blocos.append(("contato", _limpar(linha)))
        else:
            blocos.append(("paragrafo", _limpar(linha)))
    return [(tipo, texto) for tipo, texto in blocos if texto]


def gerar_pdf(conteudo_md: str, titulo: str) -> bytes:
    """PDF A4 do currículo. `titulo` vai só nos metadados do arquivo."""
    pdf = FPDF(format="A4", unit="mm")
    pdf.set_margins(MARGEM_MM, MARGEM_MM, MARGEM_MM)
    pdf.set_auto_page_break(True, margin=MARGEM_MM)
    pdf.set_title(_latin1(titulo))
    pdf.set_creator("vagas-svc")
    pdf.add_page()
    largura = pdf.epw

    blocos = _blocos(conteudo_md)
    nome = next((texto for tipo, texto in blocos if tipo == "nome"), "")
    if nome:
        pdf.set_author(nome)

    for tipo, texto in blocos:
        if tipo == "nome":
            pdf.set_font("Helvetica", "B", 16)
            pdf.multi_cell(largura, 7.5, texto, new_x="LMARGIN", new_y="NEXT", align="L")
        elif tipo == "contato":
            pdf.set_font("Helvetica", "", 9.5)
            pdf.multi_cell(largura, 5, texto, new_x="LMARGIN", new_y="NEXT", align="L")
        elif tipo == "secao":
            pdf.ln(3)
            pdf.set_font("Helvetica", "B", 12)
            pdf.multi_cell(largura, 6.5, _latin1(texto.upper()), new_x="LMARGIN", new_y="NEXT", align="L")
            y = pdf.get_y()
            pdf.set_line_width(0.2)
            pdf.line(MARGEM_MM, y, MARGEM_MM + largura, y)
            pdf.ln(1.5)
        elif tipo == "subtitulo":
            pdf.ln(1)
            pdf.set_font("Helvetica", "B", CORPO_PT)
            pdf.multi_cell(largura, 5.5, texto, new_x="LMARGIN", new_y="NEXT", align="L")
        elif tipo == "topico":
            pdf.set_font("Helvetica", "", CORPO_PT)
            recuo = 5
            pdf.cell(recuo, 5.2, "-")
            pdf.multi_cell(largura - recuo, 5.2, texto, new_x="LMARGIN", new_y="NEXT", align="L")
        else:
            pdf.set_font("Helvetica", "", CORPO_PT)
            pdf.multi_cell(largura, 5.2, texto, new_x="LMARGIN", new_y="NEXT", align="L")
    return bytes(pdf.output())


def nome_utf8_do_arquivo(titulo: str) -> str:
    """`Curriculo - <titulo>.pdf` com os acentos, para o `filename*` (RFC 6266). Sem caractere de
    controle (quebra de linha viraria outro cabeçalho) nem barra (o navegador trataria como pasta)."""
    base = "".join(" " if unicodedata.category(c).startswith("C") or c in '/\\"' else c for c in titulo)
    base = re.sub(r"\s+", " ", unicodedata.normalize("NFC", base)).strip(" .")[:80].strip()
    return f"Curriculo - {base}.pdf" if base else "Curriculo.pdf"


def nome_do_arquivo(titulo: str) -> str:
    """`Curriculo - <titulo>.pdf` só com ASCII seguro para o Content-Disposition."""
    base = unicodedata.normalize("NFKD", titulo).encode("ascii", "ignore").decode("ascii")
    base = re.sub(r"[^A-Za-z0-9 ._-]+", " ", base)
    base = re.sub(r"\s+", " ", base).strip(" .-_")[:80].strip()
    return f"Curriculo - {base}.pdf" if base else "Curriculo.pdf"
