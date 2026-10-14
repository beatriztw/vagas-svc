from dataclasses import dataclass, field

from src.model.vaga import Modalidade, Nivel


@dataclass
class Perfil:
    """Parâmetros de recomendação. Filtros eliminam a vaga, preferências só mudam a ordem."""

    termos: list[str] = field(default_factory=list)
    excluir: list[str] = field(default_factory=list)
    modalidades: list[Modalidade] = field(default_factory=list)
    localidades: list[str] = field(default_factory=list)
    plataformas: list[str] = field(default_factory=list)
    niveis: list[Nivel] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    publicada_desde_dias: int | None = None
    ocultar_candidatadas: bool = True
    # Em reais por mês. Não filtra a coleta: vai para a IA, que limita a nota de vaga que paga isso ou menos.
    salario_minimo: int | None = None


@dataclass
class SugestaoPerfil:
    """Perfil sugerido pela leitura do currículo, com uma justificativa por campo. Nada é salvo."""

    perfil: Perfil
    justificativas: dict[str, str] = field(default_factory=dict)
