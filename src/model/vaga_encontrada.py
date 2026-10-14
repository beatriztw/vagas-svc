from dataclasses import dataclass, field
from datetime import date, datetime

from src.model.detalhe_vaga import Requisito, Salario


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
    # Detalhe (frente C): buscado sob demanda ao abrir a vaga; `detalhada_em` é o cache de 24 h.
    descricao: str | None = None
    requisitos: list[Requisito] = field(default_factory=list)
    salario: Salario | None = None
    beneficios: list[str] = field(default_factory=list)
    subtitulo: str | None = None
    # Preenchida = o provider disse que a vaga expirou: some da lista, mas o link direto ainda abre.
    expirada_em: datetime | None = None
    detalhada_em: datetime | None = None
    # Quando a pessoa salvou a vaga para ver depois (a bandeirinha); None = não salva.
    salva_em: datetime | None = None
