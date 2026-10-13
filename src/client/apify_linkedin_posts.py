"""Vagas divulgadas em PUBLICAÇÕES (posts) do LinkedIn, pelo actor harvestapi/linkedin-post-search do Apify.

Input conferido contra o schema gravado em tests/fixtures/apify_linkedin_posts_input_schema.json (build
0.0.114): `searchQueries`, `maxPosts` (por consulta), `postedLimit`, `sortBy`, `profileScraperMode`,
`scrapeReactions`, `scrapeComments`. O actor cobra por evento: US$ 0,002 por post no plano Free, US$ 0,001
por consulta sem resultado e um centavo de dólar mínimo por run (`minimalMaxTotalChargeUsd`); reações,
comentários e o perfil "main" do autor são eventos pagos à parte e ficam desligados.
"""

import html
import os
import re
import unicodedata
from datetime import date

from src.client.apify_linkedin import RUN_URL, HttpPost, _data, post_json
from src.client.base import ProviderClient, palavras
from src.client.sanitizar import MAX_CARACTERES_DESCRICAO, html_para_texto
from src.model.detalhe_vaga import DetalheVaga
from src.model.vaga_externa import VagaExterna

ACTOR = "harvestapi~linkedin-post-search"
# Pelo id do post a URL é estável e não leva o identificador (nome) do autor, que o `linkedinUrl` traz.
POST_URL = "https://www.linkedin.com/feed/update/urn:li:activity:{id}/"
PLATAFORMA = "LinkedIn (posts)"
PRECO_POR_POST_USD = 0.002
# O que o run cobra além dos posts: início do actor (US$ 0,00005) e consulta sem resultado (US$ 0,001).
FOLGA_USD = 0.002
# O Apify recusa um teto de cobrança abaixo do mínimo do actor (minimalMaxTotalChargeUsd).
TETO_MINIMO_USD = 0.01
# Limites da busca booleana do LinkedIn.
MAX_OPERADORES = 5
MAX_CARACTERES_CONSULTA = 500
MAX_CARACTERES_TITULO = 120

# 5 operadores (AND, OR, OR, AND, OR): "100% remoto" e "estamos contratando" já casam com "remoto" e "contratando".
_MODELO_CONSULTA = '"{termo}" AND ("vaga" OR "contratando" OR "contratamos") AND ("remoto" OR "home office")'

# Frases comparadas por palavra normalizada (sem acento e caixa; hífen entre letras junta: "home-office" = "homeoffice").
# "Estou em busca de <cargo>" sozinho não entra: é a abertura mais comum de recrutador. Só a busca DE
# oportunidade/emprego/recolocação, que é de quem procura.
_PROCURA_EMPREGO = (("opentowork",), ("open", "to", "work"), ("recolocacao",), ("procuro", "oportunidade"),
                    ("procuro", "oportunidades"), ("procuro", "emprego"), ("estou", "disponivel"),
                    ("busca", "de", "oportunidade"), ("busca", "de", "oportunidades"), ("busca", "de", "emprego"),
                    ("busca", "de", "uma", "oportunidade"), ("busca", "de", "nova", "oportunidade"),
                    ("busca", "de", "uma", "nova", "oportunidade"), ("busca", "de", "novas", "oportunidades"))
# "curso" sozinho não entra: "curso superior completo" é requisito comum em vaga de verdade. Nem "inscrições"
# sozinho: "inscrições abertas para a vaga de estágio" é vaga. Só a inscrição em curso ou mentoria.
_CURSO = (("mentoria",), ("mentorias",), ("link", "na", "bio"), ("curso", "gratuito"), ("cursos", "gratuitos"),
          ("curso", "online"), ("meu", "curso"), ("nosso", "curso"), ("novo", "curso"), ("inscricoes", "para", "o", "curso"),
          ("inscricoes", "abertas", "para", "o", "curso"), ("inscricoes", "para", "a", "mentoria"),
          ("inscricoes", "abertas", "para", "a", "mentoria"))
_CONTRATACAO = {"contratando", "contratamos", "vaga", "vagas", "oportunidade", "oportunidades", "hiring", "buscamos",
                "procuramos"}
_REMOTO = (("remoto",), ("remota",), ("remote",), ("home", "office"), ("homeoffice",))
_NAO_REMOTO = (("hibrido",), ("hibrida",), ("hybrid",), ("presencial",), ("onsite",))

# Motivos de descarte (contados em `descartes`).
TORTO = "torto"
PROCURA_EMPREGO = "procura_emprego"
CURSO = "curso"
SEM_CONTRATACAO = "sem_contratacao"


def _tem(ws: list[str], frases) -> bool:
    for frase in frases:
        n = len(frase)
        if any(tuple(ws[i : i + n]) == frase for i in range(len(ws) - n + 1)):
            return True
    return False


def motivo_de_descarte(texto: str) -> str | None:
    """Por que o post não é vaga (procura emprego, curso, sem verbo de contratação), ou None se é."""
    ws = palavras(texto)
    if _tem(ws, _PROCURA_EMPREGO):
        return PROCURA_EMPREGO
    if _tem(ws, _CURSO):
        return CURSO
    if not _CONTRATACAO & set(ws):
        return SEM_CONTRATACAO
    return None


def remoto_do_texto(texto: str) -> bool | None:
    """False se fala em híbrido/presencial; True se fala em remoto/home office; senão None (não informado)."""
    ws = palavras(texto)
    if _tem(ws, _NAO_REMOTO):
        return False
    return True if _tem(ws, _REMOTO) else None


def consultas(termo: str) -> list[str]:
    """A consulta booleana do termo (vaga remota), [] se o termo fica vazio. Aspas e parênteses do termo saem
    (quebrariam a consulta) e ele é cortado para a consulta caber em MAX_CARACTERES_CONSULTA."""
    limpo = re.sub(r"\s+", " ", re.sub(r'["()]', " ", termo)).strip()
    if not palavras(limpo):
        return []
    espaco = MAX_CARACTERES_CONSULTA - len(_MODELO_CONSULTA.format(termo=""))
    return [_MODELO_CONSULTA.format(termo=limpo[:espaco].strip())]


def operadores(consulta: str) -> int:
    """AND/OR/NOT fora das aspas: o que o LinkedIn conta no limite da busca booleana."""
    fora = re.sub(r'"[^"]*"', " ", consulta)
    return len(re.findall(r"\b(?:AND|OR|NOT)\b", fora))


def _sem_emoji(linha: str) -> str:
    # Emoji e símbolos gráficos (So/Sk/Cs, seletores de variação) saem; "#" sai e a palavra da hashtag fica.
    linha = unicodedata.normalize("NFC", linha)
    limpa = "".join(c for c in linha if unicodedata.category(c) not in ("So", "Sk", "Cs", "Mn", "Cf") or c.isalnum())
    return re.sub(r"\s+", " ", limpa.replace("#", " ")).strip(" -–—:|•*·")


def titulo_do_texto(texto: str, empresa: str) -> str:
    """Primeira linha com pelo menos duas palavras que não seja só hashtag, sem emoji, cortada em
    MAX_CARACTERES_TITULO; sem nenhuma, "Vaga em publicação de <empresa>" (ou "do LinkedIn")."""
    for linha in texto.split("\n"):
        sem_hashtag = re.sub(r"#\w+", " ", linha)
        if len(palavras(sem_hashtag)) < 2:
            continue
        titulo = _sem_emoji(linha)
        if len(titulo) > MAX_CARACTERES_TITULO:
            titulo = titulo[: MAX_CARACTERES_TITULO - 1].rstrip() + "…"
        return titulo
    return f"Vaga em publicação de {empresa}" if empresa else "Vaga em publicação do LinkedIn"


def _empresa(autor) -> str:
    """Só o autor que é página de empresa vira empresa: nome de pessoa nunca."""
    if isinstance(autor, dict) and autor.get("type") == "company" and isinstance(autor.get("name"), str):
        return autor["name"].strip()
    return ""


def _publicada_em(valor) -> date | None:
    if isinstance(valor, dict):
        if isinstance(valor.get("timestamp"), int) and not isinstance(valor["timestamp"], bool):
            if (d := _data(valor["timestamp"])) is not None:
                return d
        valor = valor.get("date")
    return _data(valor) if isinstance(valor, str) else None


def _url(item: dict) -> str | None:
    id_ = item.get("id")
    if isinstance(id_, str) and id_.isascii() and id_.isdigit():
        return POST_URL.format(id=id_)
    return None


class ApifyLinkedInPostsClient(ProviderClient):
    """Vagas anunciadas em posts do LinkedIn (sem login), pelo actor harvestapi/linkedin-post-search.

    Um run por termo, com uma consulta booleana focada em vaga remota, dos posts das últimas 24 h, mais
    novos primeiro. `limite` posts por termo vai como `maxPosts` e como `maxItems` do run, e o mesmo teto em
    dólar vai como `maxTotalChargeUsd`: o Apify nunca cobra além disso. Post de quem procura emprego, de
    curso/mentoria ou sem verbo de contratação é descartado (`descartes` conta os motivos da última busca).
    """

    nome = PLATAFORMA

    def __init__(self, limite: int = 1, token: str | None = None, http_post: HttpPost = post_json):
        super().__init__()
        self.limite = limite
        self._token = token
        self._http_post = http_post
        self.descartes: dict[str, int] = {}

    def buscar(self, termo: str) -> list[VagaExterna]:
        self.descartes = {}
        lista = consultas(termo)
        # Termo vazio não vira run: o Apify cobraria a consulta sem resultado.
        if not lista or self.limite < 1:
            return []
        token = self._token or os.environ.get("APIFY_TOKEN")
        if not token:
            raise RuntimeError("APIFY_TOKEN não definido")
        corpo = {
            "searchQueries": lista,
            "maxPosts": self.limite,
            "postedLimit": "24h",
            "sortBy": "date",
            # Perfil curto do autor vem de graça; o "main" é cobrado por post.
            "profileScraperMode": "short",
            "scrapeReactions": False,
            "scrapeComments": False,
        }
        teto_usd = max(TETO_MINIMO_USD, round(self.limite * PRECO_POR_POST_USD + FOLGA_USD, 4))
        url = f"{RUN_URL.format(actor=ACTOR)}?maxItems={self.limite}&maxTotalChargeUsd={teto_usd}"
        itens = self._http_post(url, corpo, token)
        if not isinstance(itens, list):
            raise ValueError(f"resposta do Apify não é uma lista: {str(itens)[:200]}")

        vagas: list[VagaExterna] = []
        vistas: set[str] = set()
        for item in itens:
            vaga, motivo = self._converter(item)
            if motivo:
                self.descartes[motivo] = self.descartes.get(motivo, 0) + 1
            elif vaga.url not in vistas:
                vistas.add(vaga.url)
                vagas.append(vaga)
        return vagas

    def _converter(self, item) -> tuple[VagaExterna | None, str | None]:
        # Item quebrado (não é objeto, sem id numérico, sem texto) descarta só ele.
        if not isinstance(item, dict) or item.get("type", "post") != "post":
            return None, TORTO
        url = _url(item)
        conteudo = item.get("content")
        if not url or not isinstance(conteudo, str):
            return None, TORTO
        # Texto puro passa pelo sanitizador: limpa controles e espaços e corta no teto.
        texto = html_para_texto(html.escape(conteudo), MAX_CARACTERES_DESCRICAO)
        if not texto:
            return None, TORTO
        if motivo := motivo_de_descarte(texto):
            return None, motivo
        empresa = _empresa(item.get("author"))
        return VagaExterna(
            titulo=titulo_do_texto(texto, empresa),
            empresa=empresa,
            plataforma=self.nome,
            url=url,
            remoto=remoto_do_texto(texto),
            publicada_em=_publicada_em(item.get("postedAt")),
            detalhe=DetalheVaga(descricao=texto),
        ), None
