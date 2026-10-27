"""Evidência conservadora de salário base em USD; não infere moeda, período ou bônus."""
import re
import unicodedata
from decimal import Decimal

from src.model.detalhe_vaga import Salario

_NUMERO = r"(?:\d{1,3}(?:[,\.]\d{3})+|\d{1,12}(?:\.\d{1,2})?)\s*k?"
_FAIXA = re.compile(
    r"(?P<prefixo>\b(?:salary|base pay|base compensation|salario|remuneracao)\b[^\n.!?]{0,60}?)"
    r"(?:\bUSD\b\s*\$?|US\$)\s*"
    rf"(?P<minimo>{_NUMERO})(?:\s*(?:[-–—]|\bto\b|\ba\b|\bate\b)\s*(?:USD\s*)?\$?\s*(?P<maximo>{_NUMERO}))?"
    r"(?P<sufixo>[^\n!?]{0,55})", re.IGNORECASE,
)
_ANO = re.compile(r"\b(?:annual|annually|year|yearly|anual|ano|annum)\b|/yr\b")
_MES = re.compile(r"\b(?:month|monthly|mensal|mes)\b|/mo\b")
_PERIODO_APOS_VALOR = re.compile(
    r"^\s*(?:(?:/\s*|per\s+|por\s+)(?:year|month|ano|mes|annum|yr|mo)|annual|annually|yearly|anual|monthly|mensal)\b"
)


def _centavos(texto: str) -> int:
    texto = texto.strip().lower()
    multiplicador = 100_000 if texto.endswith("k") else 100
    texto = texto.removesuffix("k").strip()
    if re.fullmatch(r"\d{1,3}(?:[,\.]\d{3})+", texto):
        texto = texto.replace(",", "").replace(".", "")
    return int(Decimal(texto) * multiplicador)


def salario_usd_do_texto(descricao: object) -> Salario | None:
    """Só salário explicitamente em USD, com valor e período. Ausente/ambíguo = não confirmado.

    Não converte '$' em USD nem promessa genérica em faixa. O texto original fica disponível
    para conferência das formas não reconhecidas.
    """
    if not isinstance(descricao, str):
        return None
    descricao = descricao[:20_000].replace("–", "-").replace("—", "-")
    texto = unicodedata.normalize("NFKD", descricao).encode("ascii", "ignore").decode().lower()
    for match in _FAIXA.finditer(texto):
        antes = texto[max(0, match.start() - 20):match.start()]
        if re.search(r"\b(?:equity|bonus|total)\s*$", antes) or re.search(r"\b(?:not|nao|sem|bonus|equity|revenue|receita|arr|benchmark)\b", match["prefixo"]):
            continue
        if re.match(r"[.,]\d", match["sufixo"]):
            continue  # formato numérico não reconhecido: não trunca centavos como se fosse outro valor
        # Período precisa qualificar o VALOR; 'paid monthly' pode ser só a frequência de um salário anual.
        # Uma frase seguinte sobre benefício mensal também não define o período do salário.
        periodo_apos = _PERIODO_APOS_VALOR.match(match["sufixo"].split(".", 1)[0])
        periodo_texto = match["prefixo"] + " " + (periodo_apos[0] if periodo_apos else "")
        anual, mensal = bool(_ANO.search(periodo_texto)), bool(_MES.search(periodo_texto))
        if anual == mensal:
            continue
        periodo_match = (_ANO if anual else _MES).search(match["sufixo"])
        antes_periodo = match["sufixo"][:periodo_match.start()] if periodo_match else match["sufixo"]
        if re.search(r"\b(?:equity|stock|bonus)\b", antes_periodo):
            continue
        minimo = _centavos(match["minimo"])
        maximo = _centavos(match["maximo"]) if match["maximo"] else None
        if not 0 < minimo <= 10**11 or (maximo is not None and not minimo <= maximo <= 10**11):
            continue
        return Salario(minimo, maximo, "USD", "yearly" if anual else "monthly")
    return None


def atinge_piso_usd(vaga, piso_mensal: int) -> bool:
    detalhe = getattr(vaga, "detalhe", None)
    salario = getattr(vaga, "salario", None) or getattr(detalhe, "salario", None)
    if salario is None:
        salario = salario_usd_do_texto(getattr(vaga, "descricao", None) or getattr(detalhe, "descricao", None))
    if not isinstance(salario, Salario) or salario.moeda != "USD" or salario.periodo not in ("monthly", "yearly"):
        return False
    valores = [v for v in (salario.minimo, salario.maximo) if v is not None]
    if any(isinstance(v, bool) or not isinstance(v, int) or not 0 < v <= 10**11 for v in valores):
        return False
    if salario.minimo is not None and salario.maximo is not None and salario.minimo > salario.maximo:
        return False
    valor = salario.maximo if salario.maximo is not None else salario.minimo
    piso = piso_mensal * 100 * (12 if salario.periodo == "yearly" else 1)
    return isinstance(valor, int) and not isinstance(valor, bool) and valor >= piso
