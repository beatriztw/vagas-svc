from dataclasses import dataclass
from datetime import datetime


@dataclass
class Curriculo:
    """Currículo em markdown. `tipo` é 'base' (versão vigente = a mais recente) ou 'adaptado' (para uma vaga)."""

    tipo: str
    titulo: str
    conteudo_md: str
    id: int | None = None
    base_id: int | None = None
    vaga_url: str | None = None
    arquivo_s3: str | None = None
    criado_em: datetime | None = None
