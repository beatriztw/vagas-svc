"""Providers de vaga de uma pessoa: quais fontes ela usa e a config de cada uma.

Validado ANTES de gravar (`DadoInvalido`, 422, nada é gravado). Segredos (APIFY_TOKEN) nunca entram na config:
ficam no ambiente. O custo do Apify tem teto: `limite` por pessoa, no máximo `APIFY_LIMITE_MAX` (padrão =
`APIFY_LIMITE_PADRAO`) e `APIFY_POSTS_LIMITE_MAX` (padrão 5) para os posts.
"""

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from src.client.base import normalizar
from src.model.erros import DadoInvalido

APIFY_LIMITE_PADRAO = 50
APIFY_POSTS_LIMITE_MAX_PADRAO = 5
MAX_EMPRESAS = 200
MAX_TAMANHO_SLUG = 100
# O LinkedIn cobra `limite` por local: sem teto de locais, o teto de `limite` seria contornável.
MAX_LOCAIS = 5
MAX_TAMANHO_LOCAL = 100
# Himalayas devolve uma página (20); o Remotive, até 100 por chamada: o padrão de cada client é o máximo.
MAX_LIMITE_HIMALAYAS = 20
MAX_LIMITE_REMOTIVE = 100

PROVIDERS_SEM_CONFIG = ("remotar", "trampos")
PROVIDERS_DE_EMPRESAS = ("greenhouse", "lever", "ashby")
NOMES = (
    "remotar",
    "trampos",
    "himalayas",
    "remotive",
    "greenhouse",
    "lever",
    "ashby",
    "linkedin",
    "linkedin_posts",
)

# O slug vai no caminho da URL do board: só o que um slug de verdade tem (nada de "/", "?" ou espaço).
_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


def _inteiro_do_ambiente(nome: str, padrao: int) -> int:
    """Variável inválida ou não positiva de algo opcional cai no padrão, sem derrubar a app."""
    try:
        valor = int(os.environ.get(nome, padrao))
    except ValueError:
        return padrao
    return valor if valor > 0 else padrao


def limite_apify_max() -> int:
    """Teto global do `limite` do LinkedIn (vagas por local) de qualquer pessoa: APIFY_LIMITE_MAX."""
    return _inteiro_do_ambiente("APIFY_LIMITE_MAX", APIFY_LIMITE_PADRAO)


def limite_posts_max() -> int:
    """Teto global do `limite` dos posts do LinkedIn (posts por termo): APIFY_POSTS_LIMITE_MAX."""
    return _inteiro_do_ambiente("APIFY_POSTS_LIMITE_MAX", APIFY_POSTS_LIMITE_MAX_PADRAO)


@dataclass(frozen=True)
class ProviderPessoa:
    provider: str
    config: dict[str, Any] = field(default_factory=dict)


def _limite(config: dict, provider: str, teto: int) -> int | None:
    if "limite" not in config:
        return None
    limite = config["limite"]
    # bool é int em Python: True não é um limite.
    if isinstance(limite, bool) or not isinstance(limite, int) or not 1 <= limite <= teto:
        raise DadoInvalido(f"{provider}: limite tem que ser um inteiro entre 1 e {teto}")
    return limite


def _lista_de_texto(config: dict, chave: str, provider: str) -> list[str]:
    valor = config.get(chave)
    if not isinstance(valor, list) or not all(isinstance(i, str) for i in valor):
        raise DadoInvalido(f"{provider}: {chave} tem que ser uma lista de textos")
    return valor


def _chaves_conhecidas(config: dict, provider: str, permitidas: tuple[str, ...]) -> None:
    desconhecidas = sorted(str(k) for k in config if k not in permitidas)
    if desconhecidas:
        aceitas = ", ".join(permitidas) or "nenhuma"
        raise DadoInvalido(f"{provider}: chave desconhecida na config ({', '.join(desconhecidas)}); aceitas: {aceitas}")


def validar_provider(
    item: ProviderPessoa, teto_apify: int | None = None, teto_posts: int | None = None
) -> ProviderPessoa:
    """O provider com a config normalizada (sem repetidos, sem espaços); inválido levanta `DadoInvalido`."""
    teto_apify = limite_apify_max() if teto_apify is None else teto_apify
    teto_posts = limite_posts_max() if teto_posts is None else teto_posts
    nome, config = item.provider, item.config
    if nome not in NOMES:
        raise DadoInvalido(f"provider desconhecido: {str(nome)[:40]!r}; aceitos: {', '.join(NOMES)}")
    if not isinstance(config, dict):
        raise DadoInvalido(f"{nome}: config tem que ser um objeto")

    if nome in PROVIDERS_SEM_CONFIG:
        _chaves_conhecidas(config, nome, ())
        return ProviderPessoa(nome, {})

    if nome in PROVIDERS_DE_EMPRESAS:
        _chaves_conhecidas(config, nome, ("empresas",))
        brutas = _lista_de_texto(config, "empresas", nome)
        vistos: dict[str, str] = {}
        for bruta in brutas:
            slug = bruta.strip()
            if not slug:
                raise DadoInvalido(f"{nome}: empresas não aceita item vazio")
            if len(slug) > MAX_TAMANHO_SLUG or not _SLUG.fullmatch(slug):
                raise DadoInvalido(f"{nome}: empresa inválida {slug[:40]!r} (só letras, números, '-', '_' e '.')")
            vistos.setdefault(slug.casefold(), slug)
        if not vistos:
            raise DadoInvalido(f"{nome}: empresas precisa de pelo menos uma empresa")
        if len(vistos) > MAX_EMPRESAS:
            raise DadoInvalido(f"{nome}: empresas aceita até {MAX_EMPRESAS} itens")
        return ProviderPessoa(nome, {"empresas": list(vistos.values())})

    if nome == "linkedin":
        _chaves_conhecidas(config, nome, ("locais", "limite"))
        saida: dict[str, Any] = {}
        if "locais" in config:
            brutos = _lista_de_texto(config, "locais", nome)
            locais: dict[str, str] = {}
            for bruto in brutos:
                local = bruto.strip()
                if not normalizar(local).strip():
                    raise DadoInvalido("linkedin: locais não aceita item vazio")
                if len(local) > MAX_TAMANHO_LOCAL:
                    raise DadoInvalido(f"linkedin: cada local tem até {MAX_TAMANHO_LOCAL} caracteres")
                locais.setdefault(normalizar(local).strip(), local)
            if not locais:
                raise DadoInvalido("linkedin: locais precisa de pelo menos um local")
            if len(locais) > MAX_LOCAIS:
                raise DadoInvalido(f"linkedin: locais aceita até {MAX_LOCAIS} itens (o Apify cobra por local)")
            saida["locais"] = list(locais.values())
        if (limite := _limite(config, nome, teto_apify)) is not None:
            saida["limite"] = limite
        return ProviderPessoa(nome, saida)

    # Os três restantes só têm `limite`.
    teto = {"linkedin_posts": teto_posts, "himalayas": MAX_LIMITE_HIMALAYAS, "remotive": MAX_LIMITE_REMOTIVE}[nome]
    _chaves_conhecidas(config, nome, ("limite",))
    limite = _limite(config, nome, teto)
    return ProviderPessoa(nome, {} if limite is None else {"limite": limite})


def validar_providers(
    itens: list[ProviderPessoa], teto_apify: int | None = None, teto_posts: int | None = None
) -> list[ProviderPessoa]:
    """A lista inteira validada, em ordem de nome (a gravação é determinística); um provider por nome."""
    if not isinstance(itens, list):
        raise DadoInvalido("providers tem que ser uma lista")
    validos = [validar_provider(i, teto_apify, teto_posts) for i in itens]
    nomes = [v.provider for v in validos]
    repetidos = sorted({n for n in nomes if nomes.count(n) > 1})
    if repetidos:
        raise DadoInvalido(f"provider repetido: {', '.join(repetidos)}")
    return sorted(validos, key=lambda v: NOMES.index(v.provider))


def chave_da_config(efetivas: list[tuple[str, dict]]) -> tuple:
    """Chave hashable e igual para configs iguais (ordem das entradas e das chaves não conta)."""
    return tuple(sorted((nome, json.dumps(config, sort_keys=True, ensure_ascii=False)) for nome, config in efetivas))
