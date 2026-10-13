"""Trava de uma coleta por vez.

A coleta custa (Apify pago) e demora (~10 s por termo): dois cliques, duas abas ou a API junto
com o cron (`python -m src.controller.coleta`, outro processo) não podem rodar duas ao mesmo
tempo. Quem não pega a trava recebe `ColetaEmAndamento` na hora, sem esperar.
"""

import logging
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from contextlib import contextmanager

log = logging.getLogger(__name__)

# Chave fixa do `pg_try_advisory_lock` (bigint): vale entre processos e é só desta coleta.
# Outro uso de advisory lock neste banco tem que escolher outra chave.
CHAVE_TRAVA_COLETA = 7_650_001_001
# Enfileirar o lote da IA (teto diário, quando configurado): dois disparos ao mesmo tempo leriam a mesma contagem.
CHAVE_TRAVA_LOTE_IA = 7_650_001_002


class ColetaEmAndamento(Exception):
    """Já existe uma coleta rodando. Não é DadoInvalido (422) nem falha do servidor (500): a API responde 409."""

    def __init__(self) -> None:
        super().__init__("coleta em andamento")


class TravaColeta(ABC):
    @abstractmethod
    def _tentar(self) -> Callable[[], None] | None:
        """Pega a trava sem esperar e devolve a função que a libera; None se já estava presa."""

    @contextmanager
    def segurar(
        self, esperar_s: float = 0, intervalo_s: float = 0.2, dormir: Callable[[float], None] = time.sleep
    ) -> Iterator[None]:
        """Segura a trava durante o bloco (inclusive se ele levantar). Presa: tenta de novo a cada
        `intervalo_s` por até `esperar_s` (0 = não espera, o caso da coleta) e então `ColetaEmAndamento`."""
        liberar = self._tentar()
        tentativas = int(esperar_s / intervalo_s) if esperar_s > 0 else 0
        while liberar is None and tentativas > 0:
            dormir(intervalo_s)
            tentativas -= 1
            liberar = self._tentar()
        if liberar is None:
            raise ColetaEmAndamento
        try:
            yield
        finally:
            liberar()


class TravaColetaMemoria(TravaColeta):
    """Vale dentro do processo (testes e `criar_app` sem Postgres)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def _tentar(self) -> Callable[[], None] | None:
        return self._lock.release if self._lock.acquire(blocking=False) else None


class TravaColetaPostgres(TravaColeta):
    """`pg_try_advisory_lock` de sessão: vale entre processos que apontam para o mesmo banco.

    Cada coleta abre uma conexão DIRETA, só da trava, e a fecha no fim (fechar também solta o
    lock se o processo morrer no meio). Não sai do pool da API: ele tem `max_size=4` e a trava
    ficaria com uma das conexões durante minutos, disputando com os repositories da própria
    coleta e das outras requisições. A trava é por sessão, então cada coleta tem a sua conexão.
    """

    def __init__(self, conectar: Callable[[], object] | None = None, chave: int = CHAVE_TRAVA_COLETA) -> None:
        self._conectar = conectar
        self._chave = chave

    def _tentar(self) -> Callable[[], None] | None:
        if self._conectar is None:
            from src.repository.conexao import conectar

            self._conectar = conectar
        conn = self._conectar()
        try:
            pegou = conn.execute("SELECT pg_try_advisory_lock(%s)", (self._chave,)).fetchone()[0]
        except BaseException:
            conn.close()
            raise
        if not pegou:
            conn.close()
            return None

        def liberar() -> None:
            # A coleta já terminou (ou falhou por outro motivo): erro ao soltar não pode mudar o
            # resultado. A sessão que caiu já perdeu o lock sozinha; fechar também o solta.
            try:
                conn.execute("SELECT pg_advisory_unlock(%s)", (self._chave,))
            except Exception:
                log.warning("não consegui soltar a trava da coleta; ela some com a sessão", exc_info=True)
            finally:
                try:
                    conn.close()
                except Exception:
                    log.warning("não consegui fechar a conexão da trava da coleta", exc_info=True)

        return liberar
