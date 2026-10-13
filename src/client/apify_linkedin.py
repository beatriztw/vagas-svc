import json
import os
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from src.client.base import USER_AGENT, ProviderClient, normalizar, parse_data
from src.model.vaga_externa import VagaExterna

# Input conferido contra o schema gravado em tests/fixtures/apify_linkedin_input_schema.json
# (build 1.7.18): urls/limitPerSource; o schema não tem `count`. Vai a URL de busca pronta, e não
# keywords/location, porque só pela URL dá para pedir o filtro de remoto do LinkedIn (`f_WT=2`).
ACTOR = "curious_coder~linkedin-jobs-scraper"
RUN_URL = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
VAGA_URL = "https://www.linkedin.com/jobs/view/{id}"
BUSCA_URL = "https://www.linkedin.com/jobs/search/?"
# O LinkedIn só entende o local em inglês: "Brasil" cai numa busca sem local e traz vagas dos EUA
# (conferido em 12/10/2026). "Latin America" traz as vagas abertas para a região (México, Chile, Brasil...).
LOCAIS_PADRAO = ("Brazil", "Latin America")
# Filtros da busca do LinkedIn: só remoto (f_WT=2), da última semana (f_TPR), mais novas primeiro.
_FILTROS = {"f_WT": "2", "f_TPR": "r604800", "sortBy": "DD"}
# O run síncrono espera o actor terminar; o Apify corta em 300 s.
TIMEOUT_SEGUNDOS = 300

HttpPost = Callable[[str, dict, str], Any]


def post_json(url: str, corpo: dict, token: str) -> Any:
    req = urllib.request.Request(
        url,
        data=json.dumps(corpo).encode(),
        method="POST",
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_SEGUNDOS) as resp:
        return json.load(resp)


def _data(valor: str | int | None):
    # parse_data trata todo int como milissegundos; abaixo de 10^11 é um epoch em segundos.
    if isinstance(valor, int) and 0 < valor < 10**11:
        valor *= 1000
    try:
        return parse_data(valor)
    # OverflowError/OSError: epoch absurdo, fora do que o datetime aceita.
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _remoto(item: dict) -> bool | None:
    if isinstance(item.get("workRemoteAllowed"), bool):
        return item["workRemoteAllowed"]
    tipos = item.get("workplaceTypes")
    if isinstance(tipos, list) and tipos:
        # O actor devolve o rótulo no idioma do LinkedIn: "Remote", "Remoto", "Remota".
        return any("remot" in normalizar(str(t)) for t in tipos)
    return None


def _local_do_linkedin(local: str) -> str:
    """"Brasil" (o padrão antigo da APIFY_LOCAL, com qualquer acento ou caixa) vira "Brazil"."""
    return "Brazil" if normalizar(local).strip() == "brasil" else local


def url_de_busca(termo: str, local: str) -> str:
    return BUSCA_URL + urllib.parse.urlencode({"keywords": termo, "location": local, **_FILTROS})


class ApifyLinkedInClient(ProviderClient):
    """Vagas remotas do LinkedIn pelo actor curious_coder/linkedin-jobs-scraper do Apify.

    Usa só a busca pública de vagas, sem login no LinkedIn: uma URL de busca por local, só
    remotas e da última semana. O Apify cobra por vaga devolvida (US$ 0,002 em out/2026), então
    `limite` (por local) controla o custo de cada busca. O token vem de APIFY_TOKEN; sem ele a
    busca falha e o BuscaService registra o erro sem derrubar os outros providers.
    """

    nome = "LinkedIn (Apify)"

    def __init__(
        self,
        locais: tuple[str, ...] | list[str] = LOCAIS_PADRAO,
        limite: int = 50,
        token: str | None = None,
        http_post: HttpPost = post_json,
    ):
        super().__init__()
        # Sem repetidos (pelo texto normalizado): local repetido seria cobrado duas vezes pelas mesmas vagas.
        vistos: dict[str, str] = {}
        for local in locais:
            vistos.setdefault(normalizar(_local_do_linkedin(local)).strip(), _local_do_linkedin(local).strip())
        self.locais = tuple(v for k, v in vistos.items() if k) or LOCAIS_PADRAO
        self.limite = limite
        self._token = token
        self._http_post = http_post

    def buscar(self, termo: str) -> list[VagaExterna]:
        token = self._token or os.environ.get("APIFY_TOKEN")
        if not token:
            raise RuntimeError("APIFY_TOKEN não definido")
        corpo = {
            "urls": [url_de_busca(termo, local) for local in self.locais],
            # Por URL: o total da busca fica em limite x locais.
            "limitPerSource": self.limite,
            # Dados da empresa custam um request extra por vaga e não são usados aqui.
            "scrapeCompany": False,
        }
        # maxItems é o teto de cobrança do próprio Apify, valha o input do actor ou não.
        url = f"{RUN_URL.format(actor=ACTOR)}?maxItems={self.limite * len(self.locais)}"
        itens = self._http_post(url, corpo, token)
        if not isinstance(itens, list):
            raise ValueError(f"resposta do Apify não é uma lista: {str(itens)[:200]}")

        vagas = []
        for item in itens:
            # Um item quebrado no dataset não pode derrubar as vagas válidas da mesma busca.
            if not isinstance(item, dict):
                continue
            titulo = (item.get("title") or "").strip()
            link = item.get("link") or item.get("applyUrl")
            if not titulo or not link:
                continue
            vagas.append(
                VagaExterna(
                    titulo=titulo,
                    empresa=item.get("companyName") or "",
                    plataforma=self.nome,
                    # O link do actor traz parâmetros de rastreio; pelo id a URL é estável
                    # e a deduplicação do BuscaService funciona entre buscas.
                    url=VAGA_URL.format(id=item["id"]) if item.get("id") else link,
                    local=item.get("location") or None,
                    # A busca já filtrou remoto (f_WT=2); o item quase nunca traz a modalidade.
                    remoto=r if (r := _remoto(item)) is not None else True,
                    publicada_em=_data(item.get("postedAt")),
                )
            )
        return vagas
