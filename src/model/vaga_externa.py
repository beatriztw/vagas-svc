from dataclasses import dataclass
from datetime import date


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
