"""Registro dos horários da coleta agendada (migration 008): quem reserva o horário é quem coleta."""

import threading
from abc import ABC, abstractmethod
from datetime import datetime

MAX_RESULTADO = 500


class RegistroColetaAgendada(ABC):
    @abstractmethod
    def reservar(self, horario: datetime) -> bool:
        """Marca o horário como tentado. True só para quem marcou primeiro (esse coleta)."""

    @abstractmethod
    def concluir(self, horario: datetime, resultado: str) -> None:
        """Grava o fim e o resumo (contagens ou o tipo do erro) do horário reservado."""


class RegistroColetaAgendadaMemoria(RegistroColetaAgendada):
    """Vale dentro do processo (testes)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.horarios: dict[datetime, str | None] = {}

    def reservar(self, horario: datetime) -> bool:
        with self._lock:
            if horario in self.horarios:
                return False
            self.horarios[horario] = None
            return True

    def concluir(self, horario: datetime, resultado: str) -> None:
        with self._lock:
            self.horarios[horario] = resultado[:MAX_RESULTADO]


class RegistroColetaAgendadaPostgres(RegistroColetaAgendada):
    def __init__(self, conn) -> None:
        self._conn = conn

    def reservar(self, horario: datetime) -> bool:
        linha = self._conn.execute(
            "INSERT INTO coleta_agendada (horario) VALUES (%s) ON CONFLICT (horario) DO NOTHING RETURNING horario",
            (horario,),
        ).fetchone()
        return linha is not None

    def concluir(self, horario: datetime, resultado: str) -> None:
        self._conn.execute(
            "UPDATE coleta_agendada SET terminada_em = now(), resultado = %s WHERE horario = %s",
            (resultado[:MAX_RESULTADO], horario),
        )
