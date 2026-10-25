"""`GET /ia/metricas`: agregados da acurácia da compatibilidade (sem vaga, currículo nem texto da IA)."""

from collections.abc import Callable

from fastapi import APIRouter, Depends, Query, Response

from src.controller.contexto_pessoa import ContextoPessoa
from src.model.erros import DadoInvalido
from src.service import metricas_ia


def criar_router_ia_metricas(contexto_da_pessoa: Callable[..., ContextoPessoa]) -> APIRouter:
    router = APIRouter()

    @router.get("/ia/metricas")
    def metricas(
        response: Response, ctx: ContextoPessoa = Depends(contexto_da_pessoa), prompt_versao: str | None = Query(default=None)
    ) -> dict:
        pedidos = ctx.pedidos_ia
        response.headers["Cache-Control"] = "no-store"
        if prompt_versao is not None and not metricas_ia.PADRAO_VERSAO.fullmatch(prompt_versao):
            raise DadoInvalido("prompt_versao: use o formato compatibilidade-vN")
        if prompt_versao is None:
            prompt_versao = metricas_ia.versao_mais_recente(pedidos.listar_prontos_compatibilidade())
        prontos = pedidos.listar_prontos_compatibilidade(prompt_versao) if prompt_versao else []
        return metricas_ia.calcular(prontos, pedidos.listar_rotulos(), prompt_versao)

    return router
