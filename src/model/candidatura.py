from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class StatusCandidatura(str, Enum):
    """Andamento da candidatura. O valor é o que vai para o banco e para a API; `rotulo` é o da tela."""

    ENVIADA = "enviada"
    EM_ANALISE = "em_analise"
    ENTREVISTA = "entrevista"
    RECUSADA = "recusada"
    OFERTA = "oferta"

    @property
    def rotulo(self) -> str:
        return _ROTULOS[self]


_ROTULOS = {
    StatusCandidatura.ENVIADA: "Enviada",
    StatusCandidatura.EM_ANALISE: "Em análise",
    StatusCandidatura.ENTREVISTA: "Entrevista",
    StatusCandidatura.RECUSADA: "Recusada",
    StatusCandidatura.OFERTA: "Oferta",
}

STATUS_PADRAO = StatusCandidatura.ENVIADA


@dataclass
class Candidatura:
    """Linha de `candidatura`: a vaga (pela URL) em que a pessoa aplicou e em que pé está."""

    id: int
    vaga_url: str
    status: StatusCandidatura
    aplicada_em: datetime
    atualizada_em: datetime


@dataclass
class CandidaturaComVaga(Candidatura):
    """Tipo de LEITURA do repositório: a candidatura com título e empresa da vaga (JOIN com vaga_encontrada).

    É o que a tabela "Vagas aplicadas" precisa, sem uma segunda consulta por linha. Toda leitura do
    `CandidaturaRepository` (criar, obter, listar, atualizar_status) devolve este tipo; ele é uma
    `Candidatura`, então quem só quer a candidatura usa os mesmos campos.
    """

    titulo: str = ""
    empresa: str = ""
