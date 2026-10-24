"""Coleta agendada pela própria app: às 08:00 e às 18:00 de Brasília (`COLETA_HORARIOS`).

Uma thread daemon, iniciada no lifespan da app de produção depois das migrations, chama a MESMA
coleta do `POST /coletas` (trava da coleta, e no fim o lote da IA). Cada horário é reservado no
banco (`coleta_agendada`, migration 008) ANTES de coletar: só quem reserva coleta, então um
reinício ou uma segunda réplica nunca rodam o mesmo horário. Ao subir e a cada volta, o horário
mais recente que passou há até `JANELA_RECUPERACAO` e não foi reservado roda (Pi desligado ou a
app reiniciando no horário). A thread nunca morre por uma coleta ruim; o log só tem horário,
duração e contagens.
"""

import logging
import os
import threading
import time
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from src.model.erros import DadoInvalido
from src.repository.coleta_agendada import RegistroColetaAgendada
from src.repository.trava import ColetaEmAndamento

log = logging.getLogger(__name__)

PADRAO_HORARIOS = "08:00,18:00"
FUSO = ZoneInfo("America/Sao_Paulo")
# Horário perdido há mais que isso fica para o próximo (o intervalo mais curto é de 10 h).
JANELA_RECUPERACAO = timedelta(hours=6)
# Banco fora ao reservar: tenta de novo nesse intervalo, sem marcar o horário.
ESPERA_BANCO_S = 60.0
# Dorme no máximo isso por volta: relógio ajustado (NTP) ou config nova não esperam horas.
MAX_SONO_S = 300.0


def ler_horarios(texto: str) -> list[tuple[int, int]]:
    """`"08:00,18:00"` -> [(8, 0), (18, 0)], sem repetidos e em ordem. Vazio -> []. Inválido: DadoInvalido."""
    horarios = set()
    for parte in (p.strip() for p in texto.split(",")):
        if not parte:
            continue
        hora, separador, minuto = parte.partition(":")
        if not (separador and hora.isdigit() and minuto.isdigit() and len(minuto) == 2):
            raise DadoInvalido(f"COLETA_HORARIOS: use HH:MM separados por vírgula, recebi {parte!r}")
        h, m = int(hora), int(minuto)
        if not (0 <= h < 24 and 0 <= m < 60):
            raise DadoInvalido(f"COLETA_HORARIOS: horário fora do dia: {parte!r}")
        horarios.add((h, m))
    return sorted(horarios)


def horarios_da_config(env: Mapping[str, str]) -> list[tuple[int, int]]:
    """Sem a variável: o padrão. Vazia: desliga. Inválida: aviso no log e desliga (nunca derruba a app)."""
    texto = env.get("COLETA_HORARIOS")
    if texto is None:
        texto = PADRAO_HORARIOS
    try:
        return ler_horarios(texto)
    except DadoInvalido as e:
        log.warning("coleta agendada desligada: %s", e)
        return []


def _no_dia(dia: date, h: int, m: int, fuso: ZoneInfo) -> datetime:
    return datetime(dia.year, dia.month, dia.day, h, m, tzinfo=fuso)


def ultimo_horario(agora: datetime, horarios: list[tuple[int, int]], fuso: ZoneInfo = FUSO) -> datetime:
    """O horário agendado mais recente <= `agora`, em UTC."""
    hoje = agora.astimezone(fuso).date()
    candidatos = [_no_dia(hoje - timedelta(days=d), h, m, fuso) for d in (0, 1) for h, m in horarios]
    return max(c for c in candidatos if c <= agora).astimezone(timezone.utc)


def proximo_horario(agora: datetime, horarios: list[tuple[int, int]], fuso: ZoneInfo = FUSO) -> datetime:
    """O próximo horário agendado > `agora`, em UTC."""
    hoje = agora.astimezone(fuso).date()
    candidatos = [_no_dia(hoje + timedelta(days=d), h, m, fuso) for d in (0, 1) for h, m in horarios]
    return min(c for c in candidatos if c > agora).astimezone(timezone.utc)


def resumir(resultado) -> str:
    """Só contagens: nada de vaga, termo ou erro do provider (que pode trazer o termo)."""
    termos = list(resultado.termos)
    com_erro = sum(1 for t in termos if t.erros)
    estado = "falhou" if resultado.falhou else "ok"
    resumo = f"{estado} termos={len(termos)} novas={resultado.novas} termos_com_erro={com_erro}"
    pessoas_com_erro = len(getattr(resultado, "com_erro", []))
    return f"{resumo} pessoas_com_erro={pessoas_com_erro}" if pessoas_com_erro else resumo


class AgendadorColeta:
    def __init__(
        self,
        coletar: Callable[[], object],
        registro: RegistroColetaAgendada,
        horarios: list[tuple[int, int]],
        fuso: ZoneInfo = FUSO,
        relogio: Callable[[], datetime] | None = None,
    ):
        self.coletar = coletar
        self.registro = registro
        self.horarios = horarios
        self.fuso = fuso
        self._relogio = relogio or (lambda: datetime.now(timezone.utc))
        self._parar = threading.Event()
        self._thread: threading.Thread | None = None

    @classmethod
    def da_config(
        cls, coletar: Callable[[], object], registro: RegistroColetaAgendada, env: Mapping[str, str] | None = None
    ) -> "AgendadorColeta | None":
        """None com a coleta agendada desligada (`COLETA_HORARIOS` vazia ou inválida)."""
        horarios = horarios_da_config(os.environ if env is None else env)
        return cls(coletar, registro, horarios) if horarios else None

    def passo(self) -> float:
        """Uma volta: coleta o horário devido, se houver; devolve quantos segundos dormir."""
        agora = self._relogio()
        horario = ultimo_horario(agora, self.horarios, self.fuso)
        if agora - horario <= JANELA_RECUPERACAO:
            try:
                reservado = self.registro.reservar(horario)
            except Exception as e:  # noqa: BLE001 - banco fora: tenta de novo sem marcar o horário
                log.warning("coleta agendada: não consegui reservar %s (%s)", horario.isoformat(), type(e).__name__)
                return ESPERA_BANCO_S
            if reservado:
                self._coletar(horario)
        restante = (proximo_horario(agora, self.horarios, self.fuso) - agora).total_seconds()
        return max(1.0, min(restante, MAX_SONO_S))

    def _coletar(self, horario: datetime) -> None:
        inicio = time.monotonic()
        try:
            resumo = resumir(self.coletar())
        except ColetaEmAndamento:
            resumo = "em andamento"  # outra coleta (botão, API) já está buscando
        except DadoInvalido:
            resumo = "sem perfil ou sem termos"
        except Exception as e:  # noqa: BLE001 - a thread nunca morre por uma coleta ruim
            resumo = f"erro {type(e).__name__}"
        duracao = round(time.monotonic() - inicio)
        log.info("coleta agendada horario=%s duracao_s=%s %s", horario.isoformat(), duracao, resumo)
        try:
            self.registro.concluir(horario, resumo)
        except Exception as e:  # noqa: BLE001 - o horário já está reservado; só o resumo se perde
            log.warning("coleta agendada: não consegui gravar o fim de %s (%s)", horario.isoformat(), type(e).__name__)

    def _rodar(self) -> None:
        log.info("coleta agendada ligada: %s (%s)", ",".join(f"{h:02d}:{m:02d}" for h, m in self.horarios), self.fuso)
        while not self._parar.is_set():
            try:
                espera = self.passo()
            except Exception as e:  # noqa: BLE001 - uma volta ruim não derruba o agendamento
                log.warning("coleta agendada: volta falhou (%s)", type(e).__name__)
                espera = ESPERA_BANCO_S
            self._parar.wait(espera)

    def iniciar(self) -> None:
        if self._thread is not None:
            return
        self._parar.clear()
        self._thread = threading.Thread(target=self._rodar, name="coleta-agendada", daemon=True)
        self._thread.start()

    def parar(self, timeout: float = 5.0) -> None:
        """Para de agendar. Uma coleta no meio segue até o fim na thread daemon (o processo está saindo)."""
        self._parar.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None
