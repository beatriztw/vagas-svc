"""Detalhe de uma vaga (descrição, requisitos, salário, benefícios), já limpo e com tamanho limitado."""

from dataclasses import dataclass, field
from datetime import datetime

MAX_REQUISITOS = 50
MAX_BENEFICIOS = 30
MAX_TEXTO_ITEM = 500
MAX_TEXTO_SUBTITULO = 300

_PERIODOS = {"hourly": "por hora", "daily": "por dia", "weekly": "por semana", "monthly": "por mês", "yearly": "por ano"}


@dataclass(frozen=True)
class Requisito:
    texto: str
    obrigatorio: bool = False


@dataclass(frozen=True)
class Salario:
    """Faixa em CENTAVOS da moeda (o Remotar manda 350000 para R$ 3.500)."""

    minimo: int | None = None
    maximo: int | None = None
    moeda: str = "BRL"
    periodo: str | None = None

    def texto(self) -> str:
        """"R$ 3.500 a R$ 4.500 por mês"; só um lado vira "a partir de" / "até"."""
        minimo, maximo = self.minimo or None, self.maximo or None
        if minimo and maximo and minimo != maximo:
            faixa = f"{_dinheiro(minimo, self.moeda)} a {_dinheiro(maximo, self.moeda)}"
        elif minimo and maximo:
            faixa = _dinheiro(minimo, self.moeda)
        elif minimo:
            faixa = f"a partir de {_dinheiro(minimo, self.moeda)}"
        elif maximo:
            faixa = f"até {_dinheiro(maximo, self.moeda)}"
        else:
            return ""
        periodo = _PERIODOS.get(self.periodo or "")
        return f"{faixa} {periodo}" if periodo else faixa


def _dinheiro(centavos: int, moeda: str) -> str:
    reais, resto = divmod(centavos, 100)
    texto = f"{reais:,}".replace(",", ".")
    if resto:
        texto += f",{resto:02d}"
    return f"R$ {texto}" if moeda == "BRL" else f"{moeda} {texto}"


@dataclass
class DetalheVaga:
    """O detalhe de uma vaga (vindo da listagem da coleta ou do detalhe sob demanda), já limpo.

    `expirada_em`: o `expiresAt` do provider (mesmo futuro: a vaga some quando o prazo passa) ou, se o
    provider diz `expired` sem prazo, o instante da leitura. None = sem expiração conhecida.

    `tem_prazo`: False = o provider disse que a vaga não tem prazo (limpa um prazo futuro gravado
    antes); True = tem prazo ou expirou; None = não disse ou veio torto (fica o que estava).
    """

    descricao: str | None = None
    requisitos: list[Requisito] = field(default_factory=list)
    salario: Salario | None = None
    beneficios: list[str] = field(default_factory=list)
    subtitulo: str | None = None
    expirada_em: datetime | None = None
    tem_prazo: bool | None = None


def requisitos_para_json(requisitos: list[Requisito]) -> list[dict]:
    return [{"texto": r.texto, "obrigatorio": r.obrigatorio} for r in requisitos]


def requisitos_de_json(valor) -> list[Requisito]:
    """Item que não é objeto ou sem texto é descartado sozinho; o resto fica."""
    if not isinstance(valor, list):
        return []
    itens = []
    for item in valor:
        if isinstance(item, dict) and isinstance(item.get("texto"), str) and item["texto"].strip():
            itens.append(Requisito(item["texto"], item.get("obrigatorio") is True))
    return itens[:MAX_REQUISITOS]


def beneficios_de_json(valor) -> list[str]:
    if not isinstance(valor, list):
        return []
    return [b for b in valor if isinstance(b, str) and b.strip()][:MAX_BENEFICIOS]


def salario_para_json(salario: Salario | None) -> dict | None:
    if salario is None:
        return None
    return {"minimo": salario.minimo, "maximo": salario.maximo, "moeda": salario.moeda, "periodo": salario.periodo}


def _centavos(valor) -> int | None:
    # bool é int em Python; negativo ou enorme não é salário.
    if isinstance(valor, bool) or not isinstance(valor, int) or not 0 < valor <= 10**11:
        return None
    return valor


def salario_de_json(valor) -> Salario | None:
    """None se não é objeto ou sem nenhum valor positivo; "uninformed"/zeros também viram None."""
    if not isinstance(valor, dict):
        return None
    minimo, maximo = _centavos(valor.get("minimo")), _centavos(valor.get("maximo"))
    if minimo is None and maximo is None:
        return None
    moeda = valor.get("moeda")
    if not (isinstance(moeda, str) and moeda.isascii() and moeda.isalpha() and 2 <= len(moeda) <= 4):
        moeda = "BRL"
    periodo = valor.get("periodo")
    return Salario(minimo, maximo, moeda.upper(), periodo if periodo in _PERIODOS else None)
