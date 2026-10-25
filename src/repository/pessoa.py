"""As pessoas que o app atende. O nome só sai daqui para o login e para o cabeçalho da tela: nunca vai para log
nem para saída de CLI."""

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass

from src.model.erros import DadoInvalido

MAX_NOME = 60


@dataclass(frozen=True)
class CredencialPessoa:
    id: int
    nome_exibicao: str
    # None = a pessoa não tem senha, então ninguém entra como ela.
    senha_hash: str | None


def validar_nome(nome: str) -> str:
    """O nome sem as pontas, de 1 a 60 caracteres e sem caractere de controle (o Postgres não grava NUL)."""
    if not isinstance(nome, str):
        raise DadoInvalido("nome: tem que ser texto")
    nome = nome.strip()
    if not nome or len(nome) > MAX_NOME or any(ord(c) < 32 or ord(c) == 127 for c in nome):
        raise DadoInvalido(f"nome: de 1 a {MAX_NOME} caracteres, sem caractere de controle")
    return nome


def chave_do_nome(nome: str) -> str:
    """A comparação do login e da unicidade: sem as pontas e sem diferença de caixa."""
    return nome.strip().lower()


class PessoaRepository(ABC):
    @abstractmethod
    def listar(self) -> list[int]:
        """Os ids de todas as pessoas, em ordem crescente."""

    @abstractmethod
    def credencial_por_nome(self, nome: str) -> CredencialPessoa | None:
        """A pessoa de nome igual (sem diferenciar caixa nem as pontas), ou None."""

    @abstractmethod
    def criar(self, nome: str, senha_hash: str | None) -> int:
        """Cria a pessoa e devolve o id. Nome repetido (sem diferenciar caixa) é `DadoInvalido`."""

    @abstractmethod
    def definir_senha(self, pessoa_id: int, senha_hash: str) -> bool:
        """Troca o hash; False se a pessoa não existe."""


class PessoaRepositoryMemoria(PessoaRepository):
    def __init__(self, ids: Iterable[int] = ()) -> None:
        # id -> (nome, senha_hash). Pessoa só com id ganha um nome inventado.
        self._pessoas: dict[int, tuple[str, str | None]] = {i: (f"Pessoa Teste {i}", None) for i in ids}

    def adicionar(self, pessoa_id: int, nome: str | None = None, senha_hash: str | None = None) -> None:
        self._pessoas[pessoa_id] = (nome or f"Pessoa Teste {pessoa_id}", senha_hash)

    def listar(self) -> list[int]:
        return sorted(self._pessoas)

    def nome(self, pessoa_id: int) -> str | None:
        achada = self._pessoas.get(pessoa_id)
        return achada[0] if achada else None

    def credencial_por_nome(self, nome: str) -> CredencialPessoa | None:
        chave = chave_do_nome(nome)
        for pessoa_id in sorted(self._pessoas):
            nome_, senha_hash = self._pessoas[pessoa_id]
            if chave_do_nome(nome_) == chave:
                return CredencialPessoa(pessoa_id, nome_, senha_hash)
        return None

    def criar(self, nome: str, senha_hash: str | None) -> int:
        nome = validar_nome(nome)
        if self.credencial_por_nome(nome) is not None:
            raise DadoInvalido("já existe uma pessoa com esse nome")
        pessoa_id = max(self._pessoas, default=0) + 1
        self._pessoas[pessoa_id] = (nome, senha_hash)
        return pessoa_id

    def definir_senha(self, pessoa_id: int, senha_hash: str) -> bool:
        if pessoa_id not in self._pessoas:
            return False
        self._pessoas[pessoa_id] = (self._pessoas[pessoa_id][0], senha_hash)
        return True
