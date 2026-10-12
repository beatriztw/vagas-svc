from dataclasses import dataclass
from datetime import date, datetime

from src.model.detalhe_vaga import DetalheVaga


@dataclass(frozen=True)
class VagaExterna:
    """Vaga anunciada em um provider externo, antes de virar uma candidatura."""

    titulo: str
    empresa: str
    plataforma: str
    url: str
    local: str | None = None
    remoto: bool | None = None
    publicada_em: date | None = None
    # Só o provider que informa expiração na listagem (Remotar) preenche; None = no ar.
    expirada_em: datetime | None = None
    # O que o provider disse sobre o prazo: True = tem prazo (ou já expirou), False = disse que NÃO tem
    # (um prazo futuro gravado antes é removido), None = não disse ou veio torto (fica o que estava).
    tem_prazo: bool | None = None
    # Detalhe que a própria listagem traz (Remotar): descrição, requisitos, salário e benefícios, já sanitizados.
    detalhe: DetalheVaga | None = None
