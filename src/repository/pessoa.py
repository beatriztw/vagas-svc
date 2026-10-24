"""As pessoas que o app atende. Só o id sai daqui: o nome fica no banco e não passa por log nem por saída."""

from abc import ABC, abstractmethod
from collections.abc import Iterable


class PessoaRepository(ABC):
    @abstractmethod
    def listar(self) -> list[int]:
        """Os ids de todas as pessoas, em ordem crescente."""


class PessoaRepositoryMemoria(PessoaRepository):
    def __init__(self, ids: Iterable[int] = ()) -> None:
        self._ids = set(ids)

    def adicionar(self, pessoa_id: int) -> None:
        self._ids.add(pessoa_id)

    def listar(self) -> list[int]:
        return sorted(self._ids)
