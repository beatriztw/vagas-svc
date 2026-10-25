"""Sessões de login. O repositório só conhece o HASH do token (sha256 em hex): o token vive só no cookie."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from src.repository.pessoa import PessoaRepositoryMemoria


@dataclass(frozen=True)
class PessoaLogada:
    id: int
    nome_exibicao: str


class SessaoRepository(ABC):
    @abstractmethod
    def criar(self, id_hash: str, pessoa_id: int, expira_em: datetime) -> None: ...

    @abstractmethod
    def obter(self, id_hash: str, agora: datetime) -> PessoaLogada | None:
        """A pessoa da sessão; None se não existe ou se `expira_em <= agora` (sessão expirada = sem sessão)."""

    @abstractmethod
    def remover(self, id_hash: str) -> None: ...

    @abstractmethod
    def remover_da_pessoa(self, pessoa_id: int) -> int:
        """Apaga todas as sessões da pessoa (troca de senha); devolve quantas."""

    @abstractmethod
    def limpar_expiradas(self, agora: datetime) -> int: ...


class SessaoRepositoryMemoria(SessaoRepository):
    def __init__(self, pessoas: PessoaRepositoryMemoria) -> None:
        self._pessoas = pessoas
        # id_hash -> (pessoa_id, expira_em)
        self.linhas: dict[str, tuple[int, datetime]] = {}

    def criar(self, id_hash: str, pessoa_id: int, expira_em: datetime) -> None:
        if id_hash in self.linhas:
            raise ValueError("sessão repetida")
        self.linhas[id_hash] = (pessoa_id, expira_em)

    def obter(self, id_hash: str, agora: datetime) -> PessoaLogada | None:
        linha = self.linhas.get(id_hash)
        if linha is None or linha[1] <= agora:
            return None
        nome = self._pessoas.nome(linha[0])
        return PessoaLogada(linha[0], nome) if nome is not None else None

    def remover(self, id_hash: str) -> None:
        self.linhas.pop(id_hash, None)

    def remover_da_pessoa(self, pessoa_id: int) -> int:
        ids = [i for i, (p, _) in self.linhas.items() if p == pessoa_id]
        for i in ids:
            del self.linhas[i]
        return len(ids)

    def limpar_expiradas(self, agora: datetime) -> int:
        ids = [i for i, (_, expira) in self.linhas.items() if expira <= agora]
        for i in ids:
            del self.linhas[i]
        return len(ids)
