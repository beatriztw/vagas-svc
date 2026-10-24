"""Métricas de acurácia da compatibilidade por IA. Funções puras: recebem pedidos e rótulos, devolvem números.

Lê-se assim (ver `calcular`): a faixa que a pessoa deu à vaga (o rótulo) é a referência; a IA acerta quando a faixa dela coincide.
As métricas são de uma pessoa: os pedidos e rótulos que chegam já são só dela.
Os cortes 70/40 da faixa são provisórios e estas métricas servem para calibrá-los.
"""

import math
import re
from collections.abc import Iterable, Mapping, Sequence

from src.model.pedido_ia import PedidoIA

FAIXAS = ("baixa", "média", "alta")
# Até 6 dígitos, como o PUT do worker aceita: o int() da versão nunca estoura.
PADRAO_VERSAO = re.compile(r"^compatibilidade-v([0-9]{1,6})$")
CASAS = 4


def ordinal(faixa: str) -> int:
    """baixa=0, média=1, alta=2. Faixa desconhecida é erro de programação (KeyError... ValueError)."""
    valor = getattr(faixa, "value", faixa)
    try:
        return FAIXAS.index(valor)
    except ValueError:
        raise ValueError(f"faixa desconhecida: {valor!r}") from None


def postos(valores: Sequence[float]) -> list[float]:
    """Postos de 1 a n; empates recebem a média dos postos que ocupariam."""
    ordem = sorted(range(len(valores)), key=lambda i: valores[i])
    resultado = [0.0] * len(valores)
    i = 0
    while i < len(ordem):
        j = i
        while j + 1 < len(ordem) and valores[ordem[j + 1]] == valores[ordem[i]]:
            j += 1
        medio = (i + j) / 2 + 1
        for k in range(i, j + 1):
            resultado[ordem[k]] = medio
        i = j + 1
    return resultado


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Pearson dos postos. None com n < 3 ou variância zero em algum dos lados."""
    n = len(xs)
    if n != len(ys):
        raise ValueError("xs e ys precisam ter o mesmo tamanho")
    if n < 3:
        return None
    px, py = postos(xs), postos(ys)
    mx, my = sum(px) / n, sum(py) / n
    sxx = sum((a - mx) ** 2 for a in px)
    syy = sum((b - my) ** 2 for b in py)
    if sxx == 0 or syy == 0:
        return None
    sxy = sum((a - mx) * (b - my) for a, b in zip(px, py))
    return round(max(-1.0, min(1.0, sxy / math.sqrt(sxx * syy))), CASAS)


def concordancia(pares: Sequence[tuple[str, str]]) -> dict:
    """`pares` = (faixa_pessoa, faixa_ia). Matriz 3x3 completa `matriz[faixa_pessoa][faixa_ia]`; taxa = diagonal / n."""
    matriz = {a: {b: 0 for b in FAIXAS} for a in FAIXAS}
    for faixa_pessoa, ia in pares:
        matriz[getattr(faixa_pessoa, "value", faixa_pessoa)][getattr(ia, "value", ia)] += 1
    n = len(pares)
    acertos = sum(matriz[f][f] for f in FAIXAS)
    return {
        "n": n,
        "taxa": round(acertos / n, CASAS) if n else None,
        "matriz": {a: {b: matriz[a][b] for b in ("alta", "média", "baixa")} for a in ("alta", "média", "baixa")},
    }


def estabilidade(prontos_por_vaga: Mapping[str, Sequence[PedidoIA]]) -> dict:
    """Só vagas com 2 ou mais prontos. Desvio-padrão populacional médio, maior amplitude (max-min) e fração
    de vagas cuja faixa foi sempre a mesma. Tudo None sem vagas."""
    desvios: list[float] = []
    amplitudes: list[int] = []
    iguais = 0
    for prontos in prontos_por_vaga.values():
        if len(prontos) < 2:
            continue
        notas = [p.nota for p in prontos]
        media = sum(notas) / len(notas)
        desvios.append(math.sqrt(sum((x - media) ** 2 for x in notas) / len(notas)))
        amplitudes.append(max(notas) - min(notas))
        if len({getattr(p.faixa, "value", p.faixa) for p in prontos}) == 1:
            iguais += 1
    vagas = len(desvios)
    return {
        "vagas": vagas,
        "desvio_nota_medio": round(sum(desvios) / vagas, CASAS) if vagas else None,
        "amplitude_nota_max": max(amplitudes) if vagas else None,
        "faixa_igual_taxa": round(iguais / vagas, CASAS) if vagas else None,
    }


def maior_versao(versoes: Iterable[str | None]) -> str | None:
    """A maior `compatibilidade-vN` (comparada como número: v10 > v9); fora do padrão é ignorada."""
    numeros = {}
    for versao in versoes:
        achado = PADRAO_VERSAO.match(versao or "")
        if achado:
            numeros[int(achado.group(1))] = versao
    return numeros[max(numeros)] if numeros else None


def versao_mais_recente(prontos: Sequence[PedidoIA]) -> str | None:
    """A maior `compatibilidade-vN` (comparada como número: v10 > v9) entre os prontos."""
    return maior_versao(p.prompt_versao for p in prontos)


def calcular(prontos: Sequence[PedidoIA], rotulos: Mapping[str, str], prompt_versao: str | None) -> dict:
    """Formato do contrato §11. Só entram prontos de compatibilidade da `prompt_versao`; a nota da vaga é a do
    pronto mais recente (maior id).

    - concordancia_faixa: quantas vezes a faixa da IA bateu com a da pessoa (taxa) e onde errou (matriz).
    - correlacao_ordem: Spearman entre a faixa da pessoa e a nota da IA; 1 = mesma ordem das vagas.
    - estabilidade: quanto a nota oscila quando o mesmo pedido é refeito (desvio e amplitude menores = melhor).
    """
    doversao = [p for p in prontos if p.prompt_versao == prompt_versao and p.nota is not None and p.faixa is not None]
    por_vaga: dict[str, list[PedidoIA]] = {}
    for p in sorted(doversao, key=lambda p: p.id):
        por_vaga.setdefault(p.vaga_url, []).append(p)
    ultimos = {url: lista[-1] for url, lista in por_vaga.items()}
    rotuladas = [(url, rotulos[url]) for url in ultimos if url in rotulos]
    pares = [(r, ultimos[url].faixa.value) for url, r in rotuladas]
    corr_n = len(rotuladas)
    return {
        "prompt_versao": prompt_versao if doversao else None,
        "pedidos_prontos": len(doversao),
        "vagas_com_resultado": len(ultimos),
        "rotuladas": corr_n,
        "concordancia_faixa": concordancia(pares),
        "correlacao_ordem": {
            "n": corr_n,
            "spearman": spearman([ordinal(r) for _, r in rotuladas], [ultimos[url].nota for url, _ in rotuladas]),
        },
        "estabilidade": estabilidade(por_vaga),
    }
