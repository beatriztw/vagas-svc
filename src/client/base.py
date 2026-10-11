import json
import re
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


def palavras(texto: str | None) -> list[str]:
    """Palavras normalizadas, na ordem; hífen entre letras junta ("Back-end" vira "backend")."""
    sem_hifen = re.sub(r"(?<=[a-z0-9])[-‐‑](?=[a-z0-9])", "", normalizar(texto or ""))
    return re.findall(r"[a-z0-9]+", sem_hifen)


# Palavras que não ajudam a dizer se o título é da vaga procurada ("analista DE dados").
_LIGACAO = {"a", "o", "as", "os", "de", "da", "do", "das", "dos", "e", "em", "para", "com", "na", "no"}


def radical(palavra: str) -> str:
    """Tira plural e gênero do fim: "engenheira" e "engenheiros" viram "engenheir"."""
    for sufixo in ("as", "os", "es", "a", "o", "s"):
        if palavra.endswith(sufixo) and len(palavra) - len(sufixo) >= 4:
            return palavra[: -len(sufixo)]
    return palavra


# Cargos em português que aparecem em inglês nos títulos (LinkedIn, empresas gringas).
_EM_INGLES = {
    "dado": {"data"},
    "analist": {"analyst"},
    "desenvolvedor": {"developer"},
    "engenheir": {"engineer"},
    "cientist": {"scientist"},
    "cienci": {"science"},
}


def combina_termo(titulo: str, termo: str) -> bool:
    """Cada palavra do termo (fora as de ligação) aparece no título, por radical ou em inglês.

    "engenheiro de dados" casa com "Engenheira de Dados" e com "Data Engineer"; "backend"
    casa com "Back-end"; "analista de dados" não casa com "Analista de QA".
    """
    no_titulo = [radical(p) for p in palavras(titulo)]
    for palavra in palavras(termo):
        if palavra in _LIGACAO:
            continue
        alternativas = {radical(palavra)} | _EM_INGLES.get(radical(palavra), set())
        if not any(p.startswith(alt) for alt in alternativas for p in no_titulo):
            return False
    return True


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
