import http.client
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from src.client.base import USER_AGENT

# O Gupy não tem API pública por vaga: o estado vem no `__NEXT_DATA__` da própria página da vaga.
TIMEOUT_SEGUNDOS = 5
MAX_BYTES = 2_000_000
# As datas do Gupy são só dia ("2026-10-05"), no horário de Brasília.
FUSO = ZoneInfo("America/Sao_Paulo")
_HOST = re.compile(r"[a-z0-9-]{1,63}\.gupy\.io")
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)

HttpTexto = Callable[[str], str | None]


class ErroGupy(Exception):
    """Página que não deu para ler (rede, tempo, status, forma): a vaga fica como o Remotar disse."""


def link_gupy(link: object) -> str | None:
    """O link, se for a página de uma vaga no Gupy (https, `<empresa>.gupy.io/job/...` ou `/jobs/...`)."""
    if not isinstance(link, str):
        return None
    try:
        partes = urllib.parse.urlsplit(link.strip())
    except ValueError:
        return None
    if partes.scheme != "https" or not _HOST.fullmatch(partes.hostname or ""):
        return None
    if not re.fullmatch(r"/jobs?/[A-Za-z0-9=_%-]{1,200}", partes.path):
        return None
    return link.strip()


def get_html(url: str) -> str | None:
    """GET da página; None em 404/410 (a vaga saiu do ar), `ErroGupy` em qualquer outra falha."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEGUNDOS) as resp:
            corpo = resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as erro:
        if erro.code in (404, 410):
            return None
        raise ErroGupy(f"HTTP {erro.code}") from erro
    except (OSError, http.client.HTTPException) as erro:
        raise ErroGupy(type(erro).__name__) from erro
    if len(corpo) > MAX_BYTES:
        raise ErroGupy("resposta grande demais")
    return corpo.decode("utf-8", "replace")


def _dia(valor: object) -> date | None:
    if not isinstance(valor, str):
        return None
    try:
        return date.fromisoformat(valor[:10])
    except ValueError:
        return None


def _fim_do_dia(dia: date) -> datetime:
    """A vaga aceita inscrição no dia todo: fecha à meia-noite seguinte, em Brasília."""
    return datetime.combine(dia + timedelta(days=1), time(), FUSO).astimezone(timezone.utc)


def encerramento_do_job(job: dict, agora: datetime) -> datetime | None:
    """Quando a vaga fecha para inscrição, pelo objeto `job` da página.

    Status diferente de `published` (closed, canceled, frozen...) fecha agora. Senão vale o
    primeiro prazo entre `registerEndDate` (fim das inscrições) e `expiresAt` (fim do anúncio):
    a página continua `published` depois que as inscrições acabam. Sem prazo nenhum, None.
    """
    status = job.get("status")
    if isinstance(status, str) and status != "published":
        return agora
    prazos = [_fim_do_dia(d) for d in (_dia(job.get("registerEndDate")), _dia(job.get("expiresAt"))) if d]
    return min(prazos, default=None)


def job_da_pagina(html: str) -> dict:
    m = _NEXT_DATA.search(html)
    if m is None:
        raise ErroGupy("página sem __NEXT_DATA__")
    try:
        job = json.loads(m.group(1))["props"]["pageProps"]["job"]
    except (ValueError, RecursionError, KeyError, TypeError) as erro:
        raise ErroGupy("__NEXT_DATA__ com forma inesperada") from erro
    if not isinstance(job, dict):
        raise ErroGupy("job não é um objeto")
    return job


class GupyClient:
    """Diz se uma vaga do Gupy já fechou para inscrição, lendo a página pública dela."""

    def __init__(
        self,
        http: HttpTexto = get_html,
        relogio: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self._http = http
        self._relogio = relogio

    def encerramento(self, link: str) -> datetime | None:
        """Quando fecha (pode ser futuro); agora se a página sumiu; None sem prazo. `ErroGupy` se não deu para ler."""
        agora = self._relogio()
        html = self._http(link)
        if html is None:
            return agora
        return encerramento_do_job(job_da_pagina(html), agora)
