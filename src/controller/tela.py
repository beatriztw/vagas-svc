"""Rotas HTML da tela (`/tela`): renderizadas no servidor, com partials trocados pelo HTMX."""

from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Form, Request, Response

from src.controller.tela_candidaturas import contexto_aplicadas
from src.controller.tela_seguranca import PREFIXO_TELA, eh_htmx, exigir_mutacao_da_tela, templates
from src.model.erros import DadoInvalido
from src.repository.base import MAX_DESLOCAMENTO_VAGAS, MAX_TAMANHO_BUSCA, VagaEncontradaRepository, validar_deslocamento
from src.service.coleta_service import ColetaService

# D11: 50 por vez; o resto vem pelo "carregar mais".
TAMANHO_PAGINA = 50
# Teto do `?url=`: bem acima de qualquer url de vaga real, para não consultar o banco com lixo.
MAX_TAMANHO_URL = 2048


def _deslocamento(texto: str) -> int:
    """Texto da query como inteiro; qualquer outra coisa é DadoInvalido (422 em HTML, não o JSON do FastAPI)."""
    try:
        return int(texto)
    except ValueError:
        raise DadoInvalido("deslocamento tem que ser um número inteiro") from None


def _busca(q: str | None) -> str | None:
    q = (q or "").strip() or None
    if q is not None and len(q) > MAX_TAMANHO_BUSCA:
        raise DadoInvalido(f"busca tem no máximo {MAX_TAMANHO_BUSCA} caracteres")
    return q


def _novas(texto: str | None) -> bool:
    """`novas` da query: "1"/"true" só as não vistas; ausente, "0" ou "false" todas (o padrão do PR2)."""
    valor = (texto or "").strip().lower()
    if valor in ("", "0", "false"):
        return False
    if valor in ("1", "true"):
        return True
    raise DadoInvalido("novas tem que ser 0 ou 1")


def _query(**parametros) -> str:
    """Query já codificada, só com o que tem valor; `novas` vai como 1. Os templates só imprimem."""
    limpos = {n: v for n, v in parametros.items() if v}
    if "novas" in limpos:
        limpos["novas"] = "1"
    return urlencode(limpos, quote_via=quote)


def criar_router_tela(encontradas: VagaEncontradaRepository, coleta: ColetaService) -> APIRouter:
    """Recebe o que `criar_app` já tem: os repositories (na produção, os `_PorChamada`) e o MESMO
    `ColetaService`, com a mesma trava de uma coleta por vez da API e do cron."""
    router = APIRouter(prefix=PREFIXO_TELA, include_in_schema=False)

    def obter(url: str | None):
        if not url or len(url) > MAX_TAMANHO_URL:
            return None
        return encontradas.obter(url)

    def contexto_lista(q: str | None, novas: bool, deslocamento: int) -> dict:
        """O que `_lista.html` e `_cards.html` precisam; a contagem vem de `contar`, nunca do tamanho da página."""
        total = encontradas.contar(somente_novas=novas, q=q)
        vagas = encontradas.listar(somente_novas=novas, limite=TAMANHO_PAGINA, q=q, deslocamento=deslocamento)
        proximo = deslocamento + len(vagas)
        query_proximo = None
        if vagas and proximo < total and proximo <= MAX_DESLOCAMENTO_VAGAS:
            # Codificado aqui, uma vez: o template só imprime (e escapa para atributo).
            query_proximo = _query(deslocamento=proximo, q=q, novas=novas)
        return {
            "vagas": vagas,
            "total": total,
            "q": q,
            "novas": novas,
            "query_proximo": query_proximo,
            # O filtro viaja em toda url gerada (card, alternância); sem filtro fica vazio.
            "filtro": _query(q=q, novas=novas),
            "query_todas": _query(q=q),
            "query_novas": _query(q=q, novas=True),
            "max_busca": MAX_TAMANHO_BUSCA,
        }

    def pagina(request: Request, q: str | None, novas: bool, vaga, pedida: bool):
        """A página inteira. `vaga` é a selecionada; `pedida` sem vaga é um link para o que não existe."""
        contexto = contexto_lista(q, novas, 0)
        nao_encontrada = pedida and vaga is None
        if not pedida and contexto["vagas"]:
            vaga = contexto["vagas"][0]
        contexto.update(selecionada=vaga, nao_encontrada=nao_encontrada)
        # "Vagas aplicadas" entra na página inteira com o mesmo contexto das rotas de /tela/candidaturas.
        contexto.update(contexto_aplicadas(request.app.state.candidaturas))
        # 404 também na página inteira: o recurso pedido não existe, e o aviso aparece no detalhe
        # junto com a lista (o link de um favorito antigo continua útil).
        return templates.TemplateResponse(
            request, "tela/index.html", contexto, status_code=404 if nao_encontrada else 200
        )

    def detalhe_partial(request: Request, vaga, nao_encontrada: bool):
        return templates.TemplateResponse(
            request,
            "tela/_detalhe.html",
            {"selecionada": vaga, "nao_encontrada": nao_encontrada},
            status_code=404 if nao_encontrada else 200,
        )

    @router.get("")
    def tela(request: Request, url: str | None = None, q: str | None = None, novas: str | None = None):
        # A página inteira sempre, mesmo com HX-Request: as rotas de partial são as de baixo.
        return pagina(request, _busca(q), _novas(novas), obter(url), bool(url))

    @router.get("/vagas")
    def vagas(request: Request, q: str | None = None, novas: str | None = None, deslocamento: str = "0"):
        q = _busca(q)
        somente_novas = _novas(novas)
        # Validado também na página inteira: um deslocamento ruim é 422, não é ignorado.
        inicio = validar_deslocamento(_deslocamento(deslocamento))
        if not eh_htmx(request):
            return pagina(request, q, somente_novas, None, False)
        contexto = contexto_lista(q, somente_novas, inicio)
        # Do 2º bloco em diante só entram os cards e o novo sentinela (substitui o anterior).
        partial = "tela/_cards.html" if inicio > 0 else "tela/_lista.html"
        return templates.TemplateResponse(request, partial, contexto)

    @router.get("/vagas/detalhe")
    def detalhe(request: Request, url: str | None = None, q: str | None = None, novas: str | None = None):
        vaga = obter(url)
        busca, somente_novas = _busca(q), _novas(novas)  # validados também no partial
        if not eh_htmx(request):
            return pagina(request, busca, somente_novas, vaga, True)
        return detalhe_partial(request, vaga, vaga is None)

    @router.post("/vagas/vista", dependencies=[Depends(exigir_mutacao_da_tela)])
    def vista(url: str = Form("")):
        """Marca a vaga como vista: só aqui, nunca no GET do detalhe (D8). Sem corpo; o tela.js tira o selo."""
        if not url or len(url) > MAX_TAMANHO_URL:
            raise DadoInvalido("url da vaga inválida")
        encontradas.marcar_vistas([url])
        return Response(status_code=204)

    @router.post("/coletas", dependencies=[Depends(exigir_mutacao_da_tela)])
    def coletar(request: Request, q: str = Form(""), novas: str = Form("")):
        """Roda a coleta (a trava de uma por vez é do ColetaService: 409 em HTML se já há uma) e devolve a lista."""
        busca, somente_novas = _busca(q), _novas(novas)
        resultado = coleta.coletar()
        contexto = contexto_lista(busca, somente_novas, 0)
        contexto["resumo"] = {
            "novas": resultado.novas,
            "falhou": resultado.falhou,
            "erros": [f"{t.termo}: {nome}" for t in resultado.termos for nome in t.erros],
        }
        return templates.TemplateResponse(request, "tela/_lista.html", contexto)

    return router
