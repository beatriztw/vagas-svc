from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class StatusCandidatura(str, Enum):
    """Andamento da candidatura. O valor é o que vai para o banco e para a API; `rotulo` é o da tela.

    A ordem é a do menu: primeiro as que ainda andam, depois as encerradas (`encerrada`)."""

    ENVIADA = "enviada"
    EM_ANALISE = "em_analise"
    TESTE = "teste"
    ENTREVISTA = "entrevista"
    # O valor continua "oferta" (já gravado no banco); na tela é "Proposta recebida", par de "Proposta aceita".
    OFERTA = "oferta"
    VAGA_ENCERRADA = "vaga_encerrada"
    RECUSADA = "recusada"
    SEM_RESPOSTA = "sem_resposta"
    PROPOSTA_ACEITA = "proposta_aceita"

    @property
    def rotulo(self) -> str:
        return _ROTULOS[self]

    @property
    def encerrada(self) -> bool:
        """O processo acabou (por qualquer lado): vai para o fim da tabela e some com "Ocultar encerradas"."""
        return self in ENCERRADAS


_ROTULOS = {
    StatusCandidatura.ENVIADA: "Enviada",
    StatusCandidatura.EM_ANALISE: "Em análise",
    StatusCandidatura.TESTE: "Teste / case",
    StatusCandidatura.ENTREVISTA: "Entrevista",
    StatusCandidatura.OFERTA: "Proposta recebida",
    StatusCandidatura.VAGA_ENCERRADA: "Vaga encerrada",
    StatusCandidatura.RECUSADA: "Recusada",
    StatusCandidatura.SEM_RESPOSTA: "Sem resposta",
    StatusCandidatura.PROPOSTA_ACEITA: "Proposta aceita",
}

ENCERRADAS = frozenset(
    {
        StatusCandidatura.VAGA_ENCERRADA,
        StatusCandidatura.RECUSADA,
        StatusCandidatura.SEM_RESPOSTA,
        StatusCandidatura.PROPOSTA_ACEITA,
    }
)

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
    # Da vaga: quando o provider disse que ela saiu do ar (ou o prazo dela). A tela oferece "Vaga encerrada".
    expirada_em: datetime | None = None
