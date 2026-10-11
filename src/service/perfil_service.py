import json
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import asdict
from enum import Enum

from src.client.perfil_ia import CAMPOS, PerfilExtractor, validar_bruto
from src.model.perfil import Perfil, SugestaoPerfil

ProfileStore = Callable[[Perfil], None]


class PerfilRepository(ABC):
    @abstractmethod
    def salvar(self, perfil: Perfil) -> None: ...

    @abstractmethod
    def obter(self) -> Perfil | None: ...


class PerfilRepositoryMemoria(PerfilRepository):
    def __init__(self):
        self._perfil: Perfil | None = None

    def salvar(self, perfil: Perfil) -> None:
        self._perfil = perfil

    def obter(self) -> Perfil | None:
        return self._perfil


class PerfilRepositorySQLite(PerfilRepository):
    """Uma linha só, com o perfil em JSON. Serve para SQLite e para o MySQL do schema.sql."""

    def __init__(self, conn):
        self._conn = conn

    def salvar(self, perfil: Perfil) -> None:
        self._conn.execute("INSERT INTO perfil (dados) VALUES (?)", [json_dumps(perfil)])
        self._conn.commit()

    def obter(self) -> Perfil | None:
        row = self._conn.execute("SELECT dados FROM perfil ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            return None
        return from_json(row[0])


def json_dumps(perfil: Perfil) -> str:
    data = asdict(perfil)
    for campo in CAMPOS:
        data[campo] = [v.value if isinstance(v, Enum) else str(v) for v in data[campo]]
    return json.dumps(data, ensure_ascii=False)


def from_json(texto: str) -> Perfil:
    bruto = json.loads(texto)
    bruto = {campo: bruto.get(campo, []) for campo in CAMPOS}
    bruto["ocultar_candidatadas"] = bool(bruto.get("ocultar_candidatadas", True))
    bruto["publicada_desde_dias"] = bruto.get("publicada_desde_dias")
    sugestao = validar_bruto(bruto)
    return sugestao.perfil


class PerfilIAService:
    """Lê o currículo e devolve a sugestão. Nada é salvo: a revisão é da pessoa."""

    def __init__(self, extractor: PerfilExtractor, repository: PerfilRepository):
        self._extractor = extractor
        self._repository = repository

    def importar(self, pdf) -> SugestaoPerfil:
        bruto = self._extractor.extrair(pdf)
        return bruto if isinstance(bruto, SugestaoPerfil) else validar_bruto(bruto)

    def salvar(self, perfil: Perfil) -> None:
        self._repository.salvar(perfil)

    def obter(self) -> Perfil | None:
        return self._repository.obter()
