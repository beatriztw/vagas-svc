"""Rotas HTML de "Vagas aplicadas" (`/tela/candidaturas`): lista, mudar status e desfazer."""

from collections.abc import Callable
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import RedirectResponse

from src.controller.contexto_pessoa import ContextoPessoa
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


def contexto_linha() -> dict:
    """O que `tela/_linha_candidatura.html` precisa além da candidatura: as opções do menu (em dois grupos)
    e o agora (a vaga que saiu do ar ganha o aviso)."""
    return {
        "status_andamento": [s for s in StatusCandidatura if not s.encerrada],
        "status_encerradas": [s for s in StatusCandidatura if s.encerrada],
        "agora": datetime.now(timezone.utc),
    }


def contexto_aplicadas(servico: CandidaturaService, encerradas_aberta: bool = False) -> dict:
    """Variáveis de `tela/_aplicadas.html`; `GET /tela` e as rotas daqui usam a mesma. As em andamento ficam
    na tabela; as encerradas, na seção recolhida embaixo (cada grupo na ordem do repositório, a mais recente
    primeiro). `encerradas_aberta` devolve a seção aberta quando a mudança veio de dentro dela."""
    lista = servico.listar()
    return {
        "em_andamento": [c for c in lista if not c.status.encerrada],
        "encerradas": [c for c in lista if c.status.encerrada],
        "total_candidaturas": len(lista),
        "encerradas_aberta": encerradas_aberta,
        **contexto_linha(),
    }


def id_da_rota(valor: str) -> int:
    """O id vem como texto para o erro sair como aviso HTML (DadoInvalido), não como 422 JSON do FastAPI."""
    if not (valor.isascii() and valor.isdigit()):
        raise DadoInvalido("Identificador de candidatura inválido")
    return int(valor)


def criar_router_tela_candidaturas(contexto_da_pessoa: Callable[..., ContextoPessoa]) -> APIRouter:
    router = APIRouter(prefix=PREFIXO_TELA + "/candidaturas", include_in_schema=False)

    def bloco(request: Request, servico: CandidaturaService, status_code: int = 200):
        return templates.TemplateResponse(
            request, "tela/_aplicadas.html", contexto_aplicadas(servico), status_code=status_code
        )

    @router.get("")
    def listar(request: Request, ctx: ContextoPessoa = Depends(contexto_da_pessoa)):
        # A página inteira é a de `GET /tela`; este caminho só entrega o partial.
        if not eh_htmx(request):
            return RedirectResponse(PREFIXO_TELA, status_code=303)
        return bloco(request, ctx.candidaturas)

    def bloco_com_acao(request: Request, servico: CandidaturaService, vaga_url: str, candidatura):
        """O bloco #aplicadas e, por OOB, a área do botão no detalhe (que já aberto é desta vaga)."""
        contexto = {**contexto_aplicadas(servico), "oob_vaga_url": vaga_url, "candidatura": candidatura}
        if candidatura is not None:
            # A vaga aplicada saiu das salvas: a resposta leva a bandeirinha e o número da aba (OOB).
            contexto["total_salvas"] = servico.contar_salvas()
        return templates.TemplateResponse(request, "tela/_aplicadas_e_acao.html", contexto)

    @router.post("", dependencies=[Depends(exigir_mutacao_da_tela)])
    def marcar_aplicada(request: Request, ctx: ContextoPessoa = Depends(contexto_da_pessoa), url: str = Form("")):
        # Vaga fora do histórico (ou url vazia): DadoInvalido, que em /tela vira o aviso 422.
        candidatura, _ = ctx.candidaturas.marcar_aplicada(url)
        return bloco_com_acao(request, ctx.candidaturas, url, candidatura)

    @router.patch("/{id}", dependencies=[Depends(exigir_mutacao_da_tela)])
    def mudar_status(
        id: str,
        request: Request,
        ctx: ContextoPessoa = Depends(contexto_da_pessoa),
        status: str = Form(""),
        encerradas_aberta: str = Form(""),
    ):
        # Status fora do enum: DadoInvalido, que em /tela vira o partial _erro.html com 422.
        candidatura = ctx.candidaturas.mudar_status(id_da_rota(id), status)
        if candidatura is None:
            return resposta_de_erro(request, "Candidatura não encontrada", 404)
        # Devolve o bloco inteiro: a linha pode trocar de grupo (encerrou ou voltou a andar).
        return templates.TemplateResponse(
            request,
            "tela/_aplicadas.html",
            contexto_aplicadas(ctx.candidaturas, encerradas_aberta=bool(encerradas_aberta)),
        )

    @router.delete("/{id}", dependencies=[Depends(exigir_mutacao_da_tela)])
    def desfazer(
        id: str,
        request: Request,
        ctx: ContextoPessoa = Depends(contexto_da_pessoa),
        detalhe_url: str | None = Query(None),
    ):
        servico = ctx.candidaturas
        id = id_da_rota(id)
        candidatura = servico.obter(id)
        if candidatura is None or not servico.desfazer(id):
            return resposta_de_erro(request, "Candidatura não encontrada", 404)
        # Devolve o bloco inteiro: some a linha, o contador acompanha e o estado vazio aparece sozinho.
        # Se o detalhe aberto é desta vaga, o botão "Marcar como aplicada" volta por OOB (D5).
        if detalhe_url == candidatura.vaga_url:
            return bloco_com_acao(request, servico, candidatura.vaga_url, None)
        return bloco(request, servico)

    return router
