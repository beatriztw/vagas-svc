"""Conferência diária na fonte: as vagas que o histórico mostra no ar, uma a uma, no Remotar e no LinkedIn.

A vaga encerrada some da busca da fonte, então a coleta nunca fica sabendo que ela fechou: sem isto, ela
fica na lista para sempre (em 24/10/2026, 117 das 450 vagas "no ar" já tinham fechado). A conferência
roda depois de cada coleta agendada e uma vez quando a app sobe, numa thread à parte, uma por vez.

Vaga FECHADA ganha `expirada_em` (`marcar_expirada`): some da lista, aparece "encerrada" nas salvas e
"A vaga saiu do ar" nas candidaturas, onde o status só muda quando a pessoa decide. Himalayas (bloqueia a
consulta) e as publicações do LinkedIn (não são vagas) ficam de fora. O log só tem contagens.
"""

import logging
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from src.model.situacao_vaga import SituacaoNaFonte
from src.model.vaga_encontrada import VagaEncontrada
from src.repository.base import VagaEncontradaRepository
from src.service.detalhe_service import PLATAFORMA_LINKEDIN, PLATAFORMA_REMOTAR, id_linkedin, id_remotar
from src.service.historico_service import TETO_VARREDURA_HISTORICO

log = logging.getLogger(__name__)

# Entre uma consulta e a próxima da mesma fonte: o LinkedIn bloqueia (429) quem pergunta rápido demais.
PAUSA_S = {PLATAFORMA_REMOTAR: 0.2, PLATAFORMA_LINKEDIN: 2.0}


class ConsultaDeSituacao(Protocol):
    def situacao(self, id: str) -> SituacaoNaFonte: ...


@dataclass
class ResultadoConferencia:
    # Contagem por situação (os valores de `SituacaoNaFonte`) das vagas que chegaram a ser consultadas.
    situacoes: Counter = field(default_factory=Counter)
    # Plataformas que pediram para parar (429) nesta conferência.
    bloqueadas: set[str] = field(default_factory=set)

    @property
    def fechadas(self) -> int:
        return self.situacoes[SituacaoNaFonte.FECHADA.value]


class ConferenciaService:
    def __init__(
        self,
        vagas: VagaEncontradaRepository,
        remotar: ConsultaDeSituacao | None = None,
        linkedin: ConsultaDeSituacao | None = None,
        dormir: Callable[[float], None] = time.sleep,
        pausa_s: dict[str, float] | None = None,
    ):
        self.vagas = vagas
        self._fontes = {PLATAFORMA_REMOTAR: (remotar, id_remotar), PLATAFORMA_LINKEDIN: (linkedin, id_linkedin)}
        self._dormir = dormir
        self._pausa_s = PAUSA_S if pausa_s is None else pausa_s
        # Uma conferência por vez: a da subida e a da coleta não se sobrepõem.
        self._trava = threading.Lock()

    def _fonte(self, vaga: VagaEncontrada) -> tuple[ConsultaDeSituacao, str] | None:
        cliente, extrair_id = self._fontes.get(vaga.plataforma, (None, None))
        id = extrair_id(vaga.url) if cliente is not None else None
        return (cliente, id) if id is not None else None

    def conferir(self) -> ResultadoConferencia | None:
        """Consulta cada vaga no ar (até o teto da varredura) na sua fonte e marca as fechadas. None se outra
        conferência já está rodando. Falha numa vaga (inclusive do banco ao marcar) não para as outras."""
        if not self._trava.acquire(blocking=False):
            return None
        try:
            resultado = ResultadoConferencia()
            for vaga in self.vagas.listar_recentes(somente_novas=False, teto=TETO_VARREDURA_HISTORICO):
                if vaga.plataforma in resultado.bloqueadas:
                    continue
                fonte = self._fonte(vaga)
                if fonte is None:
                    continue
                cliente, id = fonte
                try:
                    situacao = cliente.situacao(id)
                    if situacao == SituacaoNaFonte.FECHADA:
                        self.vagas.marcar_expirada(vaga.url)
                except Exception as e:  # noqa: BLE001 - uma vaga ruim não para a conferência
                    log.warning("conferência: falha numa vaga (%s)", type(e).__name__)
                    situacao = SituacaoNaFonte.DESCONHECIDA
                resultado.situacoes[situacao.value] += 1
                if situacao == SituacaoNaFonte.BLOQUEADA:
                    resultado.bloqueadas.add(vaga.plataforma)
                self._dormir(self._pausa_s.get(vaga.plataforma, 0.0))
            log.info(
                "conferência %s bloqueadas=%s",
                " ".join(f"{s.value}={resultado.situacoes[s.value]}" for s in SituacaoNaFonte),
                ",".join(sorted(resultado.bloqueadas)) or "-",
            )
            return resultado
        finally:
            self._trava.release()

    def conferir_em_segundo_plano(self) -> threading.Thread:
        """A conferência leva minutos (pausas do LinkedIn): numa thread daemon, para não segurar quem chamou."""

        def rodar():
            try:
                self.conferir()
            except Exception as e:  # noqa: BLE001 - banco fora ao listar: fica para a próxima
                log.warning("conferência falhou (%s)", type(e).__name__)

        thread = threading.Thread(target=rodar, name="conferencia", daemon=True)
        thread.start()
        return thread
