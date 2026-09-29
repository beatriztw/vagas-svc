from dataclasses import dataclass
from enum import Enum


class Plataforma(str, Enum):
    SITE_DA_EMPRESA = "Site da empresa"
    LINKEDIN = "LinkedIn"
    GUPY = "Gupy"
    INDICACAO = "Indicação"
    EMAIL_DIRETO = "E-mail direto"


class Modalidade(str, Enum):
    REMOTO = "Remoto"
    HIBRIDO = "Híbrido"
    PRESENCIAL = "Presencial"


@dataclass
class Vaga:
    empresa: str
    nome: str
    plataforma: Plataforma
    modalidade: Modalidade

    @property
    def remota(self) -> bool:
        return self.modalidade == Modalidade.REMOTO
