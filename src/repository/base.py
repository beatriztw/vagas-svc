from abc import ABC, abstractmethod
from dataclasses import replace
from datetime import datetime, timezone

from src.model.curriculo import Curriculo
from src.model.perfil import Perfil


class PerfilBuscaRepository(ABC):
    @abstractmethod
    def obter(self) -> Perfil | None: ...

    @abstractmethod
    def salvar(self, perfil: Perfil) -> None:
        """Upsert da única linha (id=1)."""


class CurriculoRepository(ABC):
    @abstractmethod
    def obter_base(self) -> Curriculo | None:
        """A base vigente: o currículo tipo 'base' mais recente."""

    @abstractmethod
    def salvar(self, curriculo: Curriculo) -> Curriculo:
        """Insere uma nova linha e devolve o currículo com `id` e `criado_em`."""

    @abstractmethod
    def obter(self, id: int) -> Curriculo | None: ...

    @abstractmethod
    def listar_adaptados(self) -> list[Curriculo]: ...


class PerfilBuscaRepositoryMemoria(PerfilBuscaRepository):
    def __init__(self):
        self._perfil: Perfil | None = None

    def obter(self) -> Perfil | None:
        return self._perfil

    def salvar(self, perfil: Perfil) -> None:
        self._perfil = perfil


class CurriculoRepositoryMemoria(CurriculoRepository):
    def __init__(self):
        self._curriculos: list[Curriculo] = []

    def obter_base(self) -> Curriculo | None:
        bases = [c for c in self._curriculos if c.tipo == "base"]
        return bases[-1] if bases else None

    def salvar(self, curriculo: Curriculo) -> Curriculo:
        salvo = replace(curriculo, id=len(self._curriculos) + 1, criado_em=datetime.now(timezone.utc))
        self._curriculos.append(salvo)
        return salvo

    def obter(self, id: int) -> Curriculo | None:
        return next((c for c in self._curriculos if c.id == id), None)

    def listar_adaptados(self) -> list[Curriculo]:
        return [c for c in self._curriculos if c.tipo == "adaptado"]
