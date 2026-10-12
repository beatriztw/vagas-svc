"""Rotas HTML de "Vagas aplicadas" (`/tela/candidaturas`): lista, mudar status e desfazer."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import RedirectResponse

from src.controller.tela_seguranca import PREFIXO_TELA, eh_htmx, exigir_mutacao_da_tela, resposta_de_erro, templates
from src.model.candidatura import StatusCandidatura
from src.model.erros import DadoInvalido
from src.service.candidatura_service import CandidaturaService

FUSO = ZoneInfo("America/Sao_Paulo")


def data_curta(valor: datetime | None) -> str:
    """dd/mm no fuso de São Paulo (01:00Z ainda é o dia anterior lá)."""
    if not isinstance(valor, datetime):
        return "—"
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=timezone.utc)
    return valor.astimezone(FUSO).strftime("%d/%m")


templates.env.filters["data_curta"] = data_curta


def contexto_aplicadas(servico: CandidaturaService) -> dict:
    """Variáveis de `tela/_aplicadas.html`; `GET /tela` e as rotas daqui usam a mesma."""
    lista = servico.listar()
    return {"candidaturas": lista, "total_candidaturas": len(lista), "status_opcoes": list(StatusCandidatura)}


def id_da_rota(valor: str) -> int:
    """O id vem como texto para o erro sair como aviso HTML (DadoInvalido), não como 422 JSON do FastAPI."""
    if not (valor.isascii() and valor.isdigit()):
        raise DadoInvalido("Identificador de candidatura inválido")
    return int(valor)


def criar_router_tela_candidaturas(servico: CandidaturaService) -> APIRouter:
    router = APIRouter(prefix=PREFIXO_TELA + "/candidaturas", include_in_schema=False)

    def bloco(request: Request, status_code: int = 200):
        return templates.TemplateResponse(
            request, "tela/_aplicadas.html", contexto_aplicadas(servico), status_code=status_code
        )

    @router.get("")
    def listar(request: Request):
        # A página inteira é a de `GET /tela`; este caminho só entrega o partial.
        if not eh_htmx(request):
            return RedirectResponse(PREFIXO_TELA, status_code=303)
        return bloco(request)

    def bloco_com_acao(request: Request, vaga_url: str, candidatura):
        """O bloco #aplicadas e, por OOB, a área do botão no detalhe (que já aberto é desta vaga)."""
        contexto = {**contexto_aplicadas(servico), "oob_vaga_url": vaga_url, "candidatura": candidatura}
        return templates.TemplateResponse(request, "tela/_aplicadas_e_acao.html", contexto)

    @router.post("", dependencies=[Depends(exigir_mutacao_da_tela)])
    def marcar_aplicada(request: Request, url: str = Form("")):
        # Vaga fora do histórico (ou url vazia): DadoInvalido, que em /tela vira o aviso 422.
        candidatura, _ = servico.marcar_aplicada(url)
        return bloco_com_acao(request, url, candidatura)

    @router.patch("/{id}", dependencies=[Depends(exigir_mutacao_da_tela)])
    def mudar_status(id: str, request: Request, status: str = Form("")):
        # Status fora do enum: DadoInvalido, que em /tela vira o partial _erro.html com 422.
        candidatura = servico.mudar_status(id_da_rota(id), status)
        if candidatura is None:
            return resposta_de_erro(request, "Candidatura não encontrada", 404)
        return templates.TemplateResponse(
            request,
            "tela/_linha_candidatura.html",
            {"c": candidatura, "status_opcoes": list(StatusCandidatura)},
        )

    @router.delete("/{id}", dependencies=[Depends(exigir_mutacao_da_tela)])
    def desfazer(id: str, request: Request, detalhe_url: str | None = Query(None)):
        id = id_da_rota(id)
        candidatura = servico.obter(id)
        if candidatura is None or not servico.desfazer(id):
            return resposta_de_erro(request, "Candidatura não encontrada", 404)
        # Devolve o bloco inteiro: some a linha, o contador acompanha e o estado vazio aparece sozinho.
        # Se o detalhe aberto é desta vaga, o botão "Marcar como aplicada" volta por OOB (D5).
        if detalhe_url == candidatura.vaga_url:
            return bloco_com_acao(request, candidatura.vaga_url, None)
        return bloco(request)

    return router
