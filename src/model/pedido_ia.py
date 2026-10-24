from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

# Rodando há mais que isso = preso: o worker morreu e o pedido volta à fila.
PRAZO_RODANDO = timedelta(minutes=15)
# Depois de tantas reivindicações sem terminar, o pedido vira erro.
MAX_TENTATIVAS = 3
# O servidor corta a mensagem de erro do worker (não recusa).
MAX_ERRO = 500
# Resultado maior que isso (JSON em UTF-8) é recusado com 422.
MAX_RESULTADO_BYTES = 64 * 1024

ERRO_SEM_BASE = "Não há currículo base"

# Prioridade na fila: o clique da pessoa passa na frente do lote.
PRIORIDADE_PESSOA = 0
PRIORIDADE_LOTE = 1
ERRO_PRESO = "O worker não terminou depois de 3 tentativas"


class TipoPedidoIA(str, Enum):
    COMPATIBILIDADE = "compatibilidade"
    CURRICULO = "curriculo"


class EstadoPedidoIA(str, Enum):
    PENDENTE = "pendente"
    RODANDO = "rodando"
    PRONTO = "pronto"
    ERRO = "erro"


class Faixa(str, Enum):
    """Grafia com acento em todo lugar; só o worker normaliza `media`."""

    ALTA = "alta"
    MEDIA = "média"
    BAIXA = "baixa"


@dataclass
class PedidoIA:
    """Linha de `pedido_ia`: um pedido de IA para uma vaga e em que pé ele está."""

    id: int
    tipo: TipoPedidoIA
    vaga_url: str
    estado: EstadoPedidoIA = EstadoPedidoIA.PENDENTE
    tentativas: int = 0
    resultado: dict | None = None
    erro: str | None = None
    prompt_versao: str | None = None
    modelo: str | None = None
    nota: int | None = None
    faixa: Faixa | None = None
    curriculo_id: int | None = None
    prioridade: int = 0
    # Currículo base vigente quando o pedido foi criado: a nota só vale para essa base.
    curriculo_base_id: int | None = None
    criado_em: datetime | None = None
    reivindicado_em: datetime | None = None
    terminado_em: datetime | None = None
    # Dona do pedido: quem lê a fila de todas as pessoas (o worker) monta o payload com os dados dela.
    pessoa_id: int | None = None

    @property
    def ativo(self) -> bool:
        """Pendente ou rodando: ainda não terminou."""
        return self.estado in (EstadoPedidoIA.PENDENTE, EstadoPedidoIA.RODANDO)


@dataclass(frozen=True)
class NotaIA:
    """Nota e faixa do pronto de compatibilidade mais recente de uma vaga."""

    nota: int
    faixa: Faixa


class PedidoInexistente(LookupError):
    """O id do pedido não existe (404)."""


class PedidoEmConflito(RuntimeError):
    """O pedido não está rodando ou a tentativa não é a atual (409)."""
