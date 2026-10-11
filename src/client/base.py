import json
import unicodedata
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from src.model.vaga_externa import VagaExterna

USER_AGENT = "vagas-svc/0.1"
TIMEOUT_SEGUNDOS = 20

HttpGet = Callable[[str], Any]


def get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SEGUNDOS) as resp:
        return json.load(resp)


def montar_url(base: str, **params: Any) -> str:
    params = {k: v for k, v in params.items() if v is not None}
    return f"{base}?{urllib.parse.urlencode(params)}" if params else base


def normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return sem_acento.casefold()


def contem(texto: str | None, termo: str) -> bool:
    return normalizar(termo) in normalizar(texto or "")


def parse_data(valor: str | int | None) -> date | None:
    """Aceita ISO 8601 ou epoch em milissegundos."""
    if valor is None or valor == "":
        return None
    if isinstance(valor, int):
        return datetime.fromtimestamp(valor / 1000).date()
    return datetime.fromisoformat(valor).date()


class ProviderClient(ABC):
    nome: str

    def __init__(self, http: HttpGet = get_json):
        self._http = http

    @abstractmethod
    def buscar(self, termo: str) -> list[VagaExterna]: ...
