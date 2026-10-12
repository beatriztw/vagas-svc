"""Rotas HTML de "Meu perfil" (`/tela/perfil`): o mesmo perfil, validação e repositório da rota JSON `PUT /perfil`."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request

from src.controller.tela_seguranca import PREFIXO_TELA, exigir_mutacao_da_tela, renderizar, resposta_de_erro, templates
from src.model.erros import DadoInvalido
from src.model.perfil import Perfil
from src.model.vaga import Modalidade, Nivel
from src.repository.base import MAX_PUBLICADA_DESDE_DIAS, PerfilBuscaRepository, validar_perfil

def _linhas(texto: str) -> list[str]:
    """Uma entrada por linha; linha em branco é ignorada. Teto, repetidos e normalização ficam com `validar_perfil`."""
    return [linha.strip() for linha in texto.splitlines() if linha.strip()]


def _dias(texto: str) -> int | None:
    texto = texto.strip()
    if not texto:
        return None
    if not (texto.isascii() and texto.isdigit()):
        raise DadoInvalido(f"publicada_desde_dias tem que ser um número inteiro entre 1 e {MAX_PUBLICADA_DESDE_DIAS}")
    # `int()` recusa texto com mais de 4300 dígitos com ValueError (500): com mais dígitos que o teto,
    # já está fora do intervalo. Zeros à esquerda não contam ("0014" é 14).
    if len(texto.lstrip("0")) > len(str(MAX_PUBLICADA_DESDE_DIAS)):
        raise DadoInvalido(f"publicada_desde_dias tem que estar entre 1 e {MAX_PUBLICADA_DESDE_DIAS}")
    return int(texto)


def contexto_do_formulario(perfil: Perfil | None, salvo: bool = False) -> dict:
    """Variáveis de `tela/_perfil_form.html`. Sem perfil salvo, o formulário vem vazio (e `ocultar_candidatadas`
    no padrão do modelo)."""
    perfil = perfil or Perfil()
    return {
        "perfil": perfil,
        "dias": perfil.publicada_desde_dias if perfil.publicada_desde_dias is not None else "",
        "marcadas_modalidades": {Modalidade(m).value for m in perfil.modalidades},
        "marcados_niveis": {Nivel(n).value for n in perfil.niveis},
        "modalidades": list(Modalidade),
        "niveis": list(Nivel),
        "max_dias": MAX_PUBLICADA_DESDE_DIAS,
        "salvo": salvo,
    }


def criar_router_tela_perfil(perfis: PerfilBuscaRepository) -> APIRouter:
    router = APIRouter(prefix=PREFIXO_TELA + "/perfil", include_in_schema=False)

    @router.get("")
    def formulario(request: Request):
        return renderizar(request, "tela/perfil.html", "tela/_perfil_form.html", contexto_do_formulario(perfis.obter()))

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
        )
        perfis.salvar(validar_perfil(perfil))
        # Relê do repositório: o formulário mostra o que ficou gravado (normalizado, sem repetidos).
        salvo = perfis.obter()
        if salvo is None:
            return resposta_de_erro(request, "O perfil não foi salvo", 500)
        return templates.TemplateResponse(request, "tela/_perfil_form.html", contexto_do_formulario(salvo, salvo=True))

    return router
