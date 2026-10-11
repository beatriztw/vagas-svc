from dataclasses import dataclass
from datetime import datetime

from src.model.erros import DadoInvalido

TIPOS = ("base", "adaptado")


@dataclass
class Curriculo:
    """Currículo em markdown. 'base' é o currículo geral; 'adaptado' nasce de uma base para uma vaga."""

    tipo: str
    titulo: str
    conteudo_md: str
    id: int | None = None
    base_id: int | None = None
    vaga_url: str | None = None
    arquivo_s3: str | None = None
    criado_em: datetime | None = None

    def validar(self) -> None:
        """Mesmas regras dos CHECK da tabela curriculo, para falhar antes de ir ao banco."""
        if self.tipo not in TIPOS:
            raise DadoInvalido(f"tipo inválido: {self.tipo!r}")
        if self.tipo == "base" and self.base_id is not None:
            raise DadoInvalido("currículo base não tem base_id")
        if self.tipo == "adaptado" and self.base_id is None:
            raise DadoInvalido("currículo adaptado exige base_id")
