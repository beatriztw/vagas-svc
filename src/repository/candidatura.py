import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone

from src.model.candidatura import STATUS_PADRAO, CandidaturaComVaga, StatusCandidatura
from src.model.erros import DadoInvalido
from src.model.vaga_encontrada import VagaEncontrada
from src.repository.base import VagaEncontradaRepository


class CandidaturaRepository(ABC):
    @abstractmethod
    def criar(self, vaga_url: str) -> tuple[CandidaturaComVaga, bool]:
        """Marca a vaga como aplicada (status `enviada`). Idempotente: a repetida devolve a existente.

        Devolve (candidatura, criada). Vaga que não está em `vaga_encontrada` é `DadoInvalido`.
        """

    @abstractmethod
    def listar(self) -> list[CandidaturaComVaga]:
        """Mais recentes primeiro (aplicada_em desc, desempate id desc)."""

    @abstractmethod
    def contar(self) -> int: ...

    @abstractmethod
    def obter(self, id: int) -> CandidaturaComVaga | None: ...

    @abstractmethod
    def obter_por_url(self, vaga_url: str) -> CandidaturaComVaga | None: ...

    @abstractmethod
    def atualizar_status(self, id: int, status: StatusCandidatura | str) -> CandidaturaComVaga | None:
        """None se a candidatura não existe; status fora do enum é `DadoInvalido`."""

    @abstractmethod
    def remover(self, id: int) -> bool:
        """Desfaz a candidatura; False se não existia."""


def validar_vaga_url(vaga_url: str) -> str:
    """URL da vaga como veio (é a chave de vaga_encontrada); só recusa o que não dá para gravar."""
    # O Postgres não aceita NUL em TEXT.
    if not isinstance(vaga_url, str) or not vaga_url.strip() or "\x00" in vaga_url:
        raise DadoInvalido("vaga_url tem que ser um texto não vazio")
    return vaga_url


def validar_status(status) -> StatusCandidatura:
    """Aceita o enum ou o valor gravado ("em_analise"); qualquer outra coisa é DadoInvalido."""
    try:
        return StatusCandidatura(status)
    except ValueError:
        validos = ", ".join(s.value for s in StatusCandidatura)
        raise DadoInvalido(f"status inválido: use um destes: {validos}") from None


def validar_id(id) -> int:
    # bool é int em Python: True não é um id.
    if isinstance(id, bool) or not isinstance(id, int):
        raise DadoInvalido("id da candidatura tem que ser um inteiro")
    return id


def id_possivel(id: int) -> bool:
    """O BIGSERIAL só gera de 1 a 2**63-1: fora disso a candidatura não existe (e o Postgres estouraria)."""
    return 0 < id < 2**63


def vaga_por_url_em(encontradas: VagaEncontradaRepository) -> Callable[[str], VagaEncontrada | None]:
    """Busca de vaga por URL para a memória: faz o papel da chave estrangeira do Postgres.

    Usa `VagaEncontradaRepository.obter`, que acha a vaga em qualquer ponto do histórico (não só na
    primeira página da listagem).
    """
    return encontradas.obter


class CandidaturaRepositoryMemoria(CandidaturaRepository):
    """`vaga_por_url` faz o papel da chave estrangeira do Postgres: vaga desconhecida é DadoInvalido."""

    def __init__(self, vaga_por_url: Callable[[str], VagaEncontrada | None]) -> None:
        self._vaga_por_url = vaga_por_url
        self._linhas: dict[int, CandidaturaComVaga] = {}
        self._proximo_id = 1
        self._lock = threading.Lock()

    def _com_vaga(self, linha: CandidaturaComVaga) -> CandidaturaComVaga:
        # Título e empresa lidos na hora (como o JOIN): a vaga pode ter sido atualizada pela coleta.
        vaga = self._vaga_por_url(linha.vaga_url)
        if vaga is None:
            return replace(linha)
        return replace(linha, titulo=vaga.titulo, empresa=vaga.empresa, expirada_em=vaga.expirada_em)

    def criar(self, vaga_url: str) -> tuple[CandidaturaComVaga, bool]:
        vaga_url = validar_vaga_url(vaga_url)
        with self._lock:
            existente = next((c for c in self._linhas.values() if c.vaga_url == vaga_url), None)
            if existente is not None:
                return self._com_vaga(existente), False
            if self._vaga_por_url(vaga_url) is None:
                raise DadoInvalido(f"vaga não encontrada no histórico: {vaga_url}")
            agora = datetime.now(timezone.utc)
            linha = CandidaturaComVaga(self._proximo_id, vaga_url, STATUS_PADRAO, agora, agora)
            self._linhas[linha.id] = linha
            self._proximo_id += 1
            return self._com_vaga(linha), True

    def listar(self) -> list[CandidaturaComVaga]:
        with self._lock:
            linhas = sorted(self._linhas.values(), key=lambda c: (c.aplicada_em, c.id), reverse=True)
            return [self._com_vaga(c) for c in linhas]

    def contar(self) -> int:
        with self._lock:
            return len(self._linhas)

    def obter(self, id: int) -> CandidaturaComVaga | None:
        id = validar_id(id)
        with self._lock:
            linha = self._linhas.get(id)
            return self._com_vaga(linha) if linha else None

    def obter_por_url(self, vaga_url: str) -> CandidaturaComVaga | None:
        vaga_url = validar_vaga_url(vaga_url)
        with self._lock:
            linha = next((c for c in self._linhas.values() if c.vaga_url == vaga_url), None)
            return self._com_vaga(linha) if linha else None

    def atualizar_status(self, id: int, status: StatusCandidatura | str) -> CandidaturaComVaga | None:
        id = validar_id(id)
        status = validar_status(status)
        with self._lock:
            linha = self._linhas.get(id)
            if linha is None:
                return None
            linha = replace(linha, status=status, atualizada_em=datetime.now(timezone.utc))
            self._linhas[id] = linha
            return self._com_vaga(linha)

    def remover(self, id: int) -> bool:
        id = validar_id(id)
        with self._lock:
            return self._linhas.pop(id, None) is not None
