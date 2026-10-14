"""Validação do resultado que o worker devolve (schemas do §7 do contrato).

Todo erro é `DadoInvalido` com motivo curto, sem ecoar o conteúdo recebido. A regra da evidência
literal no currículo NÃO está aqui: o servidor não tem o currículo da época, ela é só do worker.
"""

import re

from src.model.erros import DadoInvalido
from src.model.pedido_ia import Faixa, TipoPedidoIA

_VERSAO = re.compile(r"^[a-z]+-v[0-9]{1,6}$")
_FAIXAS = {f.value for f in Faixa}
# Cortes provisórios, a calibrar pelas métricas (mudar exige um prompt -v2).
_CORTES = ((70, Faixa.ALTA.value), (40, Faixa.MEDIA.value), (0, Faixa.BAIXA.value))

_CAMPOS_COMPATIBILIDADE = {"nota", "faixa", "atende", "falta", "eliminatorios", "resumo"}
_CAMPOS_CURRICULO = {"curriculo_id", "titulo", "o_que_mudou", "lacunas"}
_MAX_ID = 2**63 - 1


def faixa_da_nota(nota: int) -> str:
    return next(faixa for minimo, faixa in _CORTES if nota >= minimo)


def _objeto(valor, onde: str, campos: set[str]) -> dict:
    if not isinstance(valor, dict):
        raise DadoInvalido(f"{onde}: tem que ser um objeto")
    ausentes = campos - valor.keys()
    if ausentes:
        raise DadoInvalido(f"{onde}: campo ausente: {sorted(ausentes)[0]}")
    if valor.keys() - campos:
        # Não nomeia a chave: ela veio de fora.
        raise DadoInvalido(f"{onde}: chave que o schema não tem")
    return valor


def _texto(valor, onde: str, maximo: int) -> str:
    if not isinstance(valor, str):
        raise DadoInvalido(f"{onde}: tem que ser um texto")
    if not valor.strip():
        raise DadoInvalido(f"{onde}: não pode ser vazio")
    if valor != valor.strip():
        raise DadoInvalido(f"{onde}: não pode ter espaço nas pontas")
    if len(valor) > maximo:
        raise DadoInvalido(f"{onde}: passa de {maximo} caracteres")
    if "\x00" in valor:
        # O Postgres não grava NUL em JSONB.
        raise DadoInvalido(f"{onde}: caractere inválido")
    return valor


def _lista(valor, onde: str, minimo: int, maximo: int) -> list:
    if not isinstance(valor, list):
        raise DadoInvalido(f"{onde}: tem que ser uma lista")
    if not minimo <= len(valor) <= maximo:
        raise DadoInvalido(f"{onde}: tem que ter de {minimo} a {maximo} itens")
    return valor


def _inteiro(valor, onde: str) -> int:
    # bool é int em Python: True não é um número.
    if type(valor) is not int:
        raise DadoInvalido(f"{onde}: tem que ser um inteiro")
    return valor


def validar_resultado_compatibilidade(resultado: dict) -> dict:
    """Schema `compatibilidade-v1`: nota, faixa coerente com a nota, atende, falta, eliminatórios e resumo."""
    r = _objeto(resultado, "resultado", _CAMPOS_COMPATIBILIDADE)
    nota = _inteiro(r["nota"], "nota")
    if not 0 <= nota <= 100:
        raise DadoInvalido("nota: tem que estar entre 0 e 100")
    faixa = r["faixa"]
    if not isinstance(faixa, str) or faixa not in _FAIXAS:
        raise DadoInvalido("faixa: tem que ser alta, média ou baixa")
    if faixa != faixa_da_nota(nota):
        raise DadoInvalido("faixa: não bate com a nota (alta 70-100, média 40-69, baixa 0-39)")
    for item in _lista(r["atende"], "atende", 0, 30):
        item = _objeto(item, "atende[]", {"requisito", "evidencia_no_curriculo"})
        _texto(item["requisito"], "atende[].requisito", 300)
        _texto(item["evidencia_no_curriculo"], "atende[].evidencia_no_curriculo", 500)
    for item in _lista(r["falta"], "falta", 0, 30):
        item = _objeto(item, "falta[]", {"requisito", "obrigatorio"})
        _texto(item["requisito"], "falta[].requisito", 300)
        if not isinstance(item["obrigatorio"], bool):
            raise DadoInvalido("falta[].obrigatorio: tem que ser verdadeiro ou falso")
    for item in _lista(r["eliminatorios"], "eliminatorios", 0, 20):
        _texto(item, "eliminatorios[]", 300)
    _texto(r["resumo"], "resumo", 1500)
    return r


def validar_resultado_curriculo(resultado: dict) -> dict:
    """Resultado do PUT do currículo: `{curriculo_id, titulo, o_que_mudou, lacunas}` (sem o conteudo_md)."""
    r = _objeto(resultado, "resultado", _CAMPOS_CURRICULO)
    curriculo_id = _inteiro(r["curriculo_id"], "curriculo_id")
    if not 0 < curriculo_id <= _MAX_ID:
        raise DadoInvalido("curriculo_id: tem que ser positivo")
    _texto(r["titulo"], "titulo", 200)
    for item in _lista(r["o_que_mudou"], "o_que_mudou", 1, 30):
        _texto(item, "o_que_mudou[]", 500)
    for item in _lista(r["lacunas"], "lacunas", 0, 30):
        _texto(item, "lacunas[]", 500)
    return r


def validar_prompt_versao(tipo: TipoPedidoIA, valor: str) -> str:
    """`<tipo>-v<N>`: a versão do prompt começa com o tipo do pedido."""
    if not isinstance(valor, str) or not _VERSAO.fullmatch(valor) or not valor.startswith(f"{TipoPedidoIA(tipo).value}-v"):
        raise DadoInvalido(f"prompt_versao: tem que ser {TipoPedidoIA(tipo).value}-vN")
    return valor
