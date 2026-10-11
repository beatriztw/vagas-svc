from abc import ABC, abstractmethod
from dataclasses import replace
from datetime import datetime, timezone

from src.model.curriculo import Curriculo
from src.model.erros import DadoInvalido
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


def _sem_repetidos(valores) -> list:
    # Mantém a ordem; ["Remoto", "Remoto"] deixaria de ser igual a [Remoto] nos filtros.
    return list(dict.fromkeys(valores))


def normalizar_perfil(perfil: Perfil) -> Perfil:
    """Cópia do perfil com enums de verdade e sem repetidos; valor fora de Modalidade/Nivel levanta ValueError."""
    return replace(
        perfil,
        termos=_sem_repetidos(perfil.termos),
        excluir=_sem_repetidos(perfil.excluir),
        modalidades=_sem_repetidos(Modalidade(m) for m in perfil.modalidades),
        localidades=_sem_repetidos(perfil.localidades),
        plataformas=_sem_repetidos(perfil.plataformas),
        niveis=_sem_repetidos(Nivel(n) for n in perfil.niveis),
        skills=_sem_repetidos(perfil.skills),
    )


# Dez anos: acima disso o filtro não serve para nada, e um valor enorme estoura o
# `hoje - timedelta(days=...)` da recomendação e o INT da coluna.
MAX_PUBLICADA_DESDE_DIAS = 3650


def validar_perfil(perfil: Perfil) -> Perfil:
    """Perfil normalizado e conferido antes de gravar; inválido levanta ValueError."""
    dias = perfil.publicada_desde_dias
    if dias is not None and not 0 < dias <= MAX_PUBLICADA_DESDE_DIAS:
        raise DadoInvalido(f"publicada_desde_dias tem que estar entre 1 e {MAX_PUBLICADA_DESDE_DIAS}")
    try:
        return normalizar_perfil(perfil)
    except ValueError as erro:
        # Enum fora de Modalidade/Nivel: dado do usuário, não falha do servidor.
        raise DadoInvalido(str(erro)) from erro


class PerfilBuscaRepositoryMemoria(PerfilBuscaRepository):
    def __init__(self) -> None:
        self._perfil: Perfil | None = None

    def obter(self) -> Perfil | None:
        return normalizar_perfil(self._perfil) if self._perfil else None

    def salvar(self, perfil: Perfil) -> None:
        self._perfil = validar_perfil(perfil)


class CurriculoRepositoryMemoria(CurriculoRepository):
    def __init__(self) -> None:
        self._linhas: dict[int, Curriculo] = {}
        self._proximo_id = 1

    def obter_base(self) -> Curriculo | None:
        bases = [c for c in self._linhas.values() if c.tipo == "base"]
        return replace(max(bases, key=lambda c: (c.criado_em, c.id))) if bases else None

    def salvar(self, curriculo: Curriculo) -> Curriculo:
        curriculo.validar()
        if curriculo.base_id is not None:
            base = self._linhas.get(curriculo.base_id)
            if base is None:
                raise DadoInvalido(f"base_id {curriculo.base_id} não existe")
            if base.tipo != "base":
                raise DadoInvalido(f"base_id {curriculo.base_id} é um currículo adaptado, não uma base")
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
