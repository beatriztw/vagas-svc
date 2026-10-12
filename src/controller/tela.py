"""Rotas HTML da tela (`/tela`): renderizadas no servidor, com partials trocados pelo HTMX."""

from fastapi import APIRouter, Request

from src.controller.tela_candidaturas import contexto_aplicadas
from src.controller.tela_seguranca import PREFIXO_TELA, templates
from src.repository.base import VagaEncontradaRepository


def criar_router_tela(encontradas: VagaEncontradaRepository) -> APIRouter:
    """Recebe os repositories que `criar_app` já tem (na produção, os `_PorChamada`)."""
    router = APIRouter(prefix=PREFIXO_TELA, include_in_schema=False)

    @router.get("")
    def pagina(request: Request):
        # Esqueleto: só precisa saber se há histórico para escolher o estado vazio. A lista é do PR2.
        tem_vagas = bool(encontradas.listar(somente_novas=False, limite=1))
        # A página inteira sempre: `GET /tela` não é um partial (as rotas de partial usam `renderizar`).
        return templates.TemplateResponse(
            request,
            "tela/index.html",
            {"tem_vagas": tem_vagas, "total": None if tem_vagas else 0, **contexto_aplicadas(request.app.state.candidaturas)},
        )

    return router
