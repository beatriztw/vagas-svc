"""As pessoas que o app atende. O nome só sai daqui para o seletor do cabeçalho da tela: nunca vai para log nem
para saída de CLI."""

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class PessoaListada:
    id: int
    nome_exibicao: str


class PessoaRepository(ABC):
    @abstractmethod
    def listar(self) -> list[int]:
        """Os ids de todas as pessoas, em ordem crescente."""

    @abstractmethod
    def listar_com_nome(self) -> list[PessoaListada]:
        """Todas as pessoas com o nome de exibição, em ordem crescente de id (seletor da tela)."""


class PessoaRepositoryMemoria(PessoaRepository):
    def __init__(self, ids: Iterable[int] = ()) -> None:
        # id -> nome. Pessoa só com id ganha um nome inventado.
        self._nomes: dict[int, str] = {i: f"Pessoa Teste {i}" for i in ids}

    def adicionar(self, pessoa_id: int, nome: str | None = None) -> None:
        self._nomes[pessoa_id] = nome or f"Pessoa Teste {pessoa_id}"

    def listar(self) -> list[int]:
        return sorted(self._nomes)

    def listar_com_nome(self) -> list[PessoaListada]:
        return [PessoaListada(i, self._nomes[i]) for i in sorted(self._nomes)]
