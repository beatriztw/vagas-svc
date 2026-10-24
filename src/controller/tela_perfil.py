"""Rotas HTML de "Meu perfil" (`/tela/perfil`): o mesmo perfil, validação e repositório da rota JSON `PUT /perfil`."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request

from src.controller.tela_seguranca import PREFIXO_TELA, exigir_mutacao_da_tela, renderizar, resposta_de_erro, templates
from src.model.erros import DadoInvalido
from src.model.perfil import Perfil
from src.model.provider_pessoa import (
    MAX_LIMITE_HIMALAYAS,
    MAX_LIMITE_REMOTIVE,
    NOMES,
    ProviderPessoa,
    limite_apify_max,
    limite_posts_max,
)
from src.model.vaga import Modalidade, Nivel
from src.repository.base import MAX_PUBLICADA_DESDE_DIAS, MAX_SALARIO_MINIMO, PerfilBuscaRepository, validar_perfil
from src.repository.pessoa_provider import PessoaProviderRepository

def _linhas(texto: str) -> list[str]:
    """Uma entrada por linha; linha em branco é ignorada. Teto, repetidos e normalização ficam com `validar_perfil`."""
    return [linha.strip() for linha in texto.splitlines() if linha.strip()]


def _inteiro(texto: str, nome: str, teto: int) -> int | None:
    texto = texto.strip()
    if not texto:
        return None
    if not (texto.isascii() and texto.isdigit()):
        raise DadoInvalido(f"{nome} tem que ser um número inteiro entre 1 e {teto}")
    # `int()` recusa texto com mais de 4300 dígitos com ValueError (500): com mais dígitos que o teto,
    # já está fora do intervalo. Zeros à esquerda não contam ("0014" é 14).
    if len(texto.lstrip("0")) > len(str(teto)):
        raise DadoInvalido(f"{nome} tem que estar entre 1 e {teto}")
    return int(texto)


def _dias(texto: str) -> int | None:
    return _inteiro(texto, "publicada_desde_dias", MAX_PUBLICADA_DESDE_DIAS)


def _salario(texto: str) -> int | None:
    """Reais por mês, só dígitos ("3050"); ponto ou vírgula de milhar não são aceitos."""
    return _inteiro(texto, "salario_minimo", MAX_SALARIO_MINIMO)


def contexto_do_formulario(perfil: Perfil | None, salvo: bool = False) -> dict:
    """Variáveis de `tela/_perfil_form.html`. Sem perfil salvo, o formulário vem vazio (e `ocultar_candidatadas`
    no padrão do modelo)."""
    perfil = perfil or Perfil()
    return {
        "perfil": perfil,
        "dias": perfil.publicada_desde_dias if perfil.publicada_desde_dias is not None else "",
        "salario": perfil.salario_minimo if perfil.salario_minimo is not None else "",
        "max_salario": MAX_SALARIO_MINIMO,
        "marcadas_modalidades": {Modalidade(m).value for m in perfil.modalidades},
        "marcados_niveis": {Nivel(n).value for n in perfil.niveis},
        "modalidades": list(Modalidade),
        "niveis": list(Nivel),
        "max_dias": MAX_PUBLICADA_DESDE_DIAS,
        "salvo": salvo,
    }


_ROTULOS = {
    "remotar": "Remotar",
    "trampos": "Trampos",
    "himalayas": "Himalayas",
    "remotive": "Remotive",
    "greenhouse": "Greenhouse",
    "lever": "Lever",
    "ashby": "Ashby (o PostHog publica aqui: empresa posthog)",
    "linkedin": "LinkedIn (Apify)",
    "linkedin_posts": "LinkedIn, posts (Apify)",
}
_EMPRESAS = ("greenhouse", "lever", "ashby")


def _tetos() -> dict[str, int]:
    return {
        "linkedin": limite_apify_max(),
        "linkedin_posts": limite_posts_max(),
        "himalayas": MAX_LIMITE_HIMALAYAS,
        "remotive": MAX_LIMITE_REMOTIVE,
    }


def contexto_dos_providers(itens: list[ProviderPessoa], salvo: bool = False) -> dict:
    """Variáveis de `tela/_providers_form.html`. Lista vazia = a pessoa usa as fontes padrão do servidor."""
    gravados = {i.provider: i.config for i in itens}
    tetos = _tetos()
    linhas = []
    for nome in NOMES:
        config = gravados.get(nome, {})
        linhas.append(
            {
                "nome": nome,
                "rotulo": _ROTULOS[nome],
                "usar": nome in gravados,
                "empresas": "\n".join(config.get("empresas", [])) if nome in _EMPRESAS else None,
                "locais": "\n".join(config.get("locais", [])) if nome == "linkedin" else None,
                "tem_limite": nome in tetos,
                "limite": config.get("limite", ""),
                "teto": tetos.get(nome),
            }
        )
    return {"linhas": linhas, "usa_padrao": not itens, "providers_salvo": salvo}


def providers_do_formulario(campos: dict[str, str], usar: list[str]) -> list[ProviderPessoa]:
    """Os providers marcados, com a config dos campos deles. Só o que está marcado vale (campo de provider
    desmarcado é ignorado). Nome fora de `NOMES` e config inválida são recusados por `salvar` (DadoInvalido)."""
    tetos = _tetos()
    itens = []
    for nome in usar:
        config: dict = {}
        if nome in _EMPRESAS:
            config["empresas"] = _linhas(campos.get(f"{nome}_empresas", ""))
        if nome == "linkedin":
            if locais := _linhas(campos.get("linkedin_locais", "")):
                config["locais"] = locais
        if nome in tetos:
            limite = _inteiro(campos.get(f"{nome}_limite", ""), f"{nome}: limite", tetos[nome])
            if limite is not None:  # "0" chega aqui e a validação recusa; só o campo vazio fica de fora
                config["limite"] = limite
        itens.append(ProviderPessoa(nome, config))
    return itens


def criar_router_tela_perfil(perfis: PerfilBuscaRepository, providers: PessoaProviderRepository) -> APIRouter:
    router = APIRouter(prefix=PREFIXO_TELA + "/perfil", include_in_schema=False)

    @router.get("")
    def formulario(request: Request):
        contexto = {**contexto_do_formulario(perfis.obter()), **contexto_dos_providers(providers.listar())}
        return renderizar(request, "tela/perfil.html", "tela/_perfil_form.html", contexto)

    @router.put("", dependencies=[Depends(exigir_mutacao_da_tela)])
    def salvar(
        request: Request,
        termos: Annotated[str, Form()] = "",
        excluir: Annotated[str, Form()] = "",
        localidades: Annotated[str, Form()] = "",
        plataformas: Annotated[str, Form()] = "",
        skills: Annotated[str, Form()] = "",
        modalidades: Annotated[list[str] | None, Form()] = None,
        niveis: Annotated[list[str] | None, Form()] = None,
        publicada_desde_dias: Annotated[str, Form()] = "",
        ocultar_candidatadas: Annotated[str | None, Form()] = None,
        salario_minimo: Annotated[str, Form()] = "",
    ):
        # Modalidade/nível fora dos enums, dias fora de 1..teto: DadoInvalido, que em /tela vira o aviso 422
        # (o handler de api.py) e não grava nada. A validação é a da rota JSON.
        perfil = Perfil(
            termos=_linhas(termos),
            excluir=_linhas(excluir),
            modalidades=modalidades or [],
            localidades=_linhas(localidades),
            plataformas=_linhas(plataformas),
            niveis=niveis or [],
            skills=_linhas(skills),
            publicada_desde_dias=_dias(publicada_desde_dias),
            ocultar_candidatadas=ocultar_candidatadas is not None,
            salario_minimo=_salario(salario_minimo),
        )
        perfis.salvar(validar_perfil(perfil))
        # Relê do repositório: o formulário mostra o que ficou gravado (normalizado, sem repetidos).
        salvo = perfis.obter()
        if salvo is None:
            return resposta_de_erro(request, "O perfil não foi salvo", 500)
        return templates.TemplateResponse(request, "tela/_perfil_form.html", contexto_do_formulario(salvo, salvo=True))

    @router.put("/providers", dependencies=[Depends(exigir_mutacao_da_tela)])
    def salvar_providers(
        request: Request,
        usar: Annotated[list[str] | None, Form()] = None,
        greenhouse_empresas: Annotated[str, Form()] = "",
        lever_empresas: Annotated[str, Form()] = "",
        ashby_empresas: Annotated[str, Form()] = "",
        linkedin_locais: Annotated[str, Form()] = "",
        linkedin_limite: Annotated[str, Form()] = "",
        linkedin_posts_limite: Annotated[str, Form()] = "",
        himalayas_limite: Annotated[str, Form()] = "",
        remotive_limite: Annotated[str, Form()] = "",
    ):
        # Nome desconhecido, limite acima do teto ou config torta: DadoInvalido (aviso 422) e nada é gravado.
        # O token do Apify não tem campo aqui: fica só no ambiente.
        campos = {
            "greenhouse_empresas": greenhouse_empresas,
            "lever_empresas": lever_empresas,
            "ashby_empresas": ashby_empresas,
            "linkedin_locais": linkedin_locais,
            "linkedin_limite": linkedin_limite,
            "linkedin_posts_limite": linkedin_posts_limite,
            "himalayas_limite": himalayas_limite,
            "remotive_limite": remotive_limite,
        }
        providers.salvar(providers_do_formulario(campos, usar or []))
        # Relê do repositório: o formulário mostra o que ficou gravado (normalizado, sem repetidos).
        return templates.TemplateResponse(
            request, "tela/_providers_form.html", contexto_dos_providers(providers.listar(), salvo=True)
        )

    return router
