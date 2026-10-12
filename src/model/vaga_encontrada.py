from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class VagaEncontrada:
    """Vaga guardada pela coleta: a última versão vista no provider e quando foi achada."""

    url: str
    titulo: str
    empresa: str
    plataforma: str
    local: str | None = None
    remoto: bool | None = None
    publicada_em: date | None = None
    termos: list[str] = field(default_factory=list)
    primeira_vez_em: datetime | None = None
    ultima_vez_em: datetime | None = None
    vista_em: datetime | None = None
