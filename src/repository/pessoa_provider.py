"""Providers de vaga por pessoa. Lista vazia = a pessoa usa os providers do ambiente (`providers_padrao`)."""

import copy
from abc import ABC, abstractmethod

from src.model.provider_pessoa import ProviderPessoa, validar_providers


class PessoaProviderRepository(ABC):
    @abstractmethod
    def listar(self) -> list[ProviderPessoa]:
        """Os providers da pessoa, na ordem de `NOMES`; vazio quando vale o ambiente."""

    @abstractmethod
    def salvar(self, providers: list[ProviderPessoa]) -> None:
        """Substitui TODOS os providers da pessoa de uma vez (lista vazia volta ao ambiente).

        Valida antes (`DadoInvalido`): inválido não grava nada, nem parte da lista."""


class PessoaProviderRepositoryMemoria(PessoaProviderRepository):
    """`compartilhado`: outra instância cujo armazenamento esta usa, para duas pessoas no mesmo "banco"."""

    def __init__(self, pessoa_id: int, compartilhado: "PessoaProviderRepositoryMemoria | None" = None) -> None:
        self._pessoa_id = pessoa_id
        self._linhas: dict[int, list[ProviderPessoa]] = compartilhado._linhas if compartilhado is not None else {}

    def listar(self) -> list[ProviderPessoa]:
        return copy.deepcopy(self._linhas.get(self._pessoa_id, []))

    def salvar(self, providers: list[ProviderPessoa]) -> None:
        self._linhas[self._pessoa_id] = copy.deepcopy(validar_providers(providers))
