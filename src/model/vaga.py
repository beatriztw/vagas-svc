from dataclasses import dataclass
from datetime import date
from enum import Enum


class Nivel(str, Enum):
    JUNIOR = "Júnior"
    PLENO = "Pleno"
    SENIOR = "Sênior"


class Modalidade(str, Enum):
    REMOTO = "Remoto"
    HIBRIDO = "Híbrido"


@dataclass
class Vaga:
    nome: str
    empresa: str
    plataforma: str
    nivel: Nivel
    modalidade: Modalidade
    data_candidatura: date

    @property
    def remota(self) -> bool:
        return self.modalidade == Modalidade.REMOTO
