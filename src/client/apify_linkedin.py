import json
import os
import urllib.request
from collections.abc import Callable
from typing import Any

from src.client.base import USER_AGENT, ProviderClient, parse_data
from src.model.vaga_externa import VagaExterna

ACTOR = "curious_coder~linkedin-jobs-scraper"
RUN_URL = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
VAGA_URL = "https://www.linkedin.com/jobs/view/{id}"
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
    try:
        return parse_data(valor)
    except (TypeError, ValueError):
        return None


def _remoto(item: dict) -> bool | None:
    if isinstance(item.get("workRemoteAllowed"), bool):
        return item["workRemoteAllowed"]
    tipos = item.get("workplaceTypes")
    if isinstance(tipos, list) and tipos:
        return any("remote" in str(t).casefold() for t in tipos)
    return None


class ApifyLinkedInClient(ProviderClient):
    """Vagas do LinkedIn pelo actor curious_coder/linkedin-jobs-scraper do Apify.

    Usa só a busca pública de vagas, sem login no LinkedIn. O Apify cobra por vaga
    devolvida (US$ 0,001 a 0,002 em out/2026), então `limite` controla o custo de cada
    busca. O token vem de APIFY_TOKEN; sem ele a busca falha e o BuscaService registra
    o erro sem derrubar os outros providers.
    """

    nome = "LinkedIn (Apify)"

    def __init__(
        self,
        local: str = "Brasil",
        limite: int = 50,
        token: str | None = None,
        http_post: HttpPost = post_json,
    ):
        super().__init__()
        self.local = local
        self.limite = limite
        self._token = token
        self._http_post = http_post

    def buscar(self, termo: str) -> list[VagaExterna]:
        token = self._token or os.environ.get("APIFY_TOKEN")
        if not token:
            raise RuntimeError("APIFY_TOKEN não definido")
        corpo = {
            "keywords": termo,
            "location": self.local,
            "limitPerSource": self.limite,
            # Dados da empresa custam um request extra por vaga e não são usados aqui.
            "scrapeCompany": False,
        }
        itens = self._http_post(RUN_URL.format(actor=ACTOR), corpo, token)
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
                    remoto=_remoto(item),
                    publicada_em=_data(item.get("postedAt")),
                )
            )
        return vagas
