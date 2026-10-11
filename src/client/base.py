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


# Cargos que aparecem nos dois idiomas nos títulos; cada grupo vale nos dois sentidos.
_MESMO_CARGO = [
    {"dado", "data"},
    {"analist", "analyst"},
    {"desenvolvedor", "developer"},
    {"engenheir", "engineer"},
    {"cientist", "scientist"},
    {"cienci", "science"},
]


def _equivalentes(raiz: str) -> set[str]:
    for grupo in _MESMO_CARGO:
        if raiz in grupo:
            return grupo
    return {raiz}


def _formas_do_titulo(titulo: str) -> list[str]:
    # Radicais das palavras e de cada par vizinho junto: "Front End" também vira "frontend",
    # para casar com "front-end"/"frontend" no termo.
    raizes = [radical(p) for p in palavras(titulo)]
    return raizes + [a + b for a, b in zip(raizes, raizes[1:])]


def relevancia_termo(titulo: str, termo: str) -> float:
    """Fração das palavras do termo (fora as de ligação) que aparecem no título, de 0 a 1.

    Compara por radical (gênero e plural), ignora hífen, junta palavras vizinhas ("front
    end" = "front-end" = "frontend") e aceita o cargo no outro idioma (dados/data,
    engenheiro/engineer...). Termo sem palavra útil vale 1: não há o que comparar.
    """
    formas = _formas_do_titulo(titulo)
    exigidas = [radical(p) for p in palavras(termo) if p not in _LIGACAO]
    if not exigidas:
        return 1.0
    casadas = 0
    i = 0
    while i < len(exigidas):
        # Par vizinho do termo junto ("front" + "end") conta pelas duas palavras.
        if i + 1 < len(exigidas) and any(f.startswith(exigidas[i] + exigidas[i + 1]) for f in formas):
            casadas += 2
            i += 2
            continue
        if any(f.startswith(alt) for alt in _equivalentes(exigidas[i]) for f in formas):
            casadas += 1
        i += 1
    return casadas / len(exigidas)


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
