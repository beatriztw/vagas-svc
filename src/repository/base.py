from abc import ABC, abstractmethod
from dataclasses import replace
from datetime import datetime, timezone

from src.model.curriculo import Curriculo
from src.model.perfil import Perfil
from src.model.vaga import Modalidade, Nivel


class PerfilBuscaRepository(ABC):
    """Perfil de busca único (linha id=1)."""

    @abstractmethod
    def obter(self) -> Perfil | None: ...

    @abstractmethod
    def salvar(self, perfil: Perfil) -> None:
        """Grava por cima do perfil existente (upsert da linha id=1)."""


class CurriculoRepository(ABC):
    @abstractmethod
    def obter_base(self) -> Curriculo | None:
        """Base vigente: o currículo tipo='base' mais recente."""

    @abstractmethod
    def salvar(self, curriculo: Curriculo) -> Curriculo:
        """Insere uma nova linha e devolve o currículo com id e criado_em preenchidos."""

    @abstractmethod
    def obter(self, id: int) -> Curriculo | None: ...

    @abstractmethod
    def listar_adaptados(self) -> list[Curriculo]:
        """Adaptados do mais recente para o mais antigo."""


def normalizar_perfil(perfil: Perfil) -> Perfil:
    """Cópia do perfil com enums de verdade; valor fora de Modalidade/Nivel levanta ValueError."""
    return replace(
        perfil,
        termos=list(perfil.termos),
        excluir=list(perfil.excluir),
        modalidades=[Modalidade(m) for m in perfil.modalidades],
        localidades=list(perfil.localidades),
        plataformas=list(perfil.plataformas),
        niveis=[Nivel(n) for n in perfil.niveis],
        skills=list(perfil.skills),
    )


class PerfilBuscaRepositoryMemoria(PerfilBuscaRepository):
    def __init__(self) -> None:
        self._perfil: Perfil | None = None

    def obter(self) -> Perfil | None:
        return normalizar_perfil(self._perfil) if self._perfil else None

    def salvar(self, perfil: Perfil) -> None:
        if perfil.publicada_desde_dias is not None and perfil.publicada_desde_dias <= 0:
            raise ValueError("publicada_desde_dias tem que ser positivo")
        self._perfil = normalizar_perfil(perfil)


class CurriculoRepositoryMemoria(CurriculoRepository):
    def __init__(self) -> None:
        self._linhas: dict[int, Curriculo] = {}
        self._proximo_id = 1

    def obter_base(self) -> Curriculo | None:
        bases = [c for c in self._linhas.values() if c.tipo == "base"]
        return replace(max(bases, key=lambda c: (c.criado_em, c.id))) if bases else None

    def salvar(self, curriculo: Curriculo) -> Curriculo:
        curriculo.validar()
        if curriculo.base_id is not None and curriculo.base_id not in self._linhas:
            raise ValueError(f"base_id {curriculo.base_id} não existe")
        salvo = replace(curriculo, id=self._proximo_id, criado_em=datetime.now(timezone.utc))
        self._linhas[salvo.id] = salvo
        self._proximo_id += 1
        return replace(salvo)

    def obter(self, id: int) -> Curriculo | None:
        curriculo = self._linhas.get(id)
        return replace(curriculo) if curriculo else None

    def listar_adaptados(self) -> list[Curriculo]:
        adaptados = [c for c in self._linhas.values() if c.tipo == "adaptado"]
        return [replace(c) for c in sorted(adaptados, key=lambda c: (c.criado_em, c.id), reverse=True)]
