"""Rotas HTML da IA da vaga (`/tela/vagas/ia`): pedir, acompanhar por polling, baixar o currículo e rotular."""

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import RedirectResponse

from src.controller.tela_seguranca import PREFIXO_TELA, eh_htmx, exigir_mutacao_da_tela, resposta_de_erro, templates
from src.model.erros import DadoInvalido
from src.model.pedido_ia import EstadoPedidoIA, PedidoIA, TipoPedidoIA
from src.repository.pedido_ia import id_possivel
from src.service.pedido_ia_service import MAX_URL, PedidoIAService

# Código do htmx que troca o conteúdo e PARA o polling.
PARAR_POLLING = 286
SEM_CACHE = {"Cache-Control": "no-store"}


def id_da_rota(valor: str) -> int:
    """O id vem como texto para o erro sair como aviso HTML (DadoInvalido), não como 422 JSON do FastAPI."""
    if not (valor.isascii() and valor.isdigit()):
        raise DadoInvalido("Identificador de pedido inválido")
    return int(valor)


def criar_router_tela_ia(servico: PedidoIAService) -> APIRouter:
    router = APIRouter(prefix=PREFIXO_TELA + "/vagas/ia", include_in_schema=False)

    def vaga_existe(vaga_url: str | None) -> bool:
        return bool(vaga_url) and len(vaga_url) <= MAX_URL and servico.encontradas.obter(vaga_url) is not None

    def pedido(request: Request, p: PedidoIA, status_code: int = 200):
        return templates.TemplateResponse(request, "tela/_ia_pedido.html", {"p": p}, status_code=status_code, headers=SEM_CACHE)

    @router.get("")
    def painel(request: Request, vaga_url: str | None = None):
        # A página inteira é a de `GET /tela`; este caminho só entrega o partial.
        if not eh_htmx(request):
            return RedirectResponse(PREFIXO_TELA, status_code=303)
        if not vaga_existe(vaga_url):
            return resposta_de_erro(request, "Vaga não encontrada", 404)
        contexto = {"vaga_url": vaga_url, "ultimos": servico.ultimos(vaga_url), "rotulo": servico.rotulo(vaga_url)}
        return templates.TemplateResponse(request, "tela/_ia_painel.html", contexto, headers=SEM_CACHE)

    @router.post("", dependencies=[Depends(exigir_mutacao_da_tela)])
    def pedir(request: Request, vaga_url: str = Form(""), tipo: str = Form("")):
        # Tipo inválido, vaga fora do histórico e falta de base são DadoInvalido: o aviso 422 da página.
        p, _ = servico.pedir(vaga_url, tipo)
        return pedido(request, p, 202)

    @router.post("/rotulo", dependencies=[Depends(exigir_mutacao_da_tela)])
    def rotular(request: Request, vaga_url: str = Form(""), faixa: str = Form("")):
        gravada = servico.rotular(vaga_url, faixa or None)
        return templates.TemplateResponse(
            request, "tela/_ia_rotulo.html", {"vaga_url": vaga_url, "rotulo": gravada}, headers=SEM_CACHE
        )

    def buscar(id: str) -> PedidoIA | None:
        numero = id_da_rota(id)
        return servico.obter(numero) if id_possivel(numero) else None

    @router.get("/{id}")
    def acompanhar(id: str, request: Request):
        if not eh_htmx(request):
            return RedirectResponse(PREFIXO_TELA, status_code=303)
        p = buscar(id)
        if p is None:
            # Sem hx-get: a troca do elemento que fazia polling encerra o polling.
            return templates.TemplateResponse(
                request, "tela/_ia_pedido.html", {"p": None}, status_code=404, headers=SEM_CACHE
            )
        return pedido(request, p, 200 if p.ativo else PARAR_POLLING)

    @router.get("/{id}/curriculo.md")
    def baixar(id: str):
        p = buscar(id)
        adaptado = None
        if p is not None and p.tipo == TipoPedidoIA.CURRICULO and p.estado == EstadoPedidoIA.PRONTO and p.curriculo_id:
            adaptado = servico.curriculos.obter(p.curriculo_id)
        if adaptado is None:
            return Response("Currículo não encontrado", status_code=404, media_type="text/plain; charset=utf-8", headers=SEM_CACHE)
        return Response(
            adaptado.conteudo_md,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="curriculo-{p.id}.md"', **SEM_CACHE},
        )

    return router
