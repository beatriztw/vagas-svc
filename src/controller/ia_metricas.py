"""`GET /ia/metricas`: agregados da acurácia da compatibilidade (sem vaga, currículo nem texto da IA)."""

from fastapi import APIRouter, Query, Response

from src.model.erros import DadoInvalido
from src.repository.pedido_ia import PedidoIARepository
from src.service import metricas_ia


def criar_router_ia_metricas(pedidos: PedidoIARepository) -> APIRouter:
    router = APIRouter()

    @router.get("/ia/metricas")
    def metricas(response: Response, prompt_versao: str | None = Query(default=None)) -> dict:
        response.headers["Cache-Control"] = "no-store"
        if prompt_versao is not None and not metricas_ia.PADRAO_VERSAO.fullmatch(prompt_versao):
            raise DadoInvalido("prompt_versao: use o formato compatibilidade-vN")
        if prompt_versao is None:
            prompt_versao = metricas_ia.versao_mais_recente(pedidos.listar_prontos_compatibilidade())
        prontos = pedidos.listar_prontos_compatibilidade(prompt_versao) if prompt_versao else []
        return metricas_ia.calcular(prontos, pedidos.listar_rotulos(), prompt_versao)

    return router
