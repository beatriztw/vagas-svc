"""Rotas HTML da tela (`/tela`): renderizadas no servidor, com partials trocados pelo HTMX."""

from urllib.parse import quote, urlencode

from fastapi import APIRouter, Request

from src.controller.tela_candidaturas import contexto_aplicadas
from src.controller.tela_seguranca import PREFIXO_TELA, eh_htmx, templates
from src.model.erros import DadoInvalido
from src.repository.base import MAX_DESLOCAMENTO_VAGAS, MAX_TAMANHO_BUSCA, VagaEncontradaRepository, validar_deslocamento

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


def criar_router_tela(encontradas: VagaEncontradaRepository) -> APIRouter:
    """Recebe os repositories que `criar_app` já tem (na produção, os `_PorChamada`)."""
    router = APIRouter(prefix=PREFIXO_TELA, include_in_schema=False)

    def obter(url: str | None):
        if not url or len(url) > MAX_TAMANHO_URL:
            return None
        return encontradas.obter(url)

    def contexto_lista(q: str | None, deslocamento: int) -> dict:
        """O que `_lista.html` e `_cards.html` precisam; a contagem vem de `contar`, nunca do tamanho da página."""
        total = encontradas.contar(somente_novas=False, q=q)
        vagas = encontradas.listar(somente_novas=False, limite=TAMANHO_PAGINA, q=q, deslocamento=deslocamento)
        proximo = deslocamento + len(vagas)
        query_proximo = None
        if vagas and proximo < total and proximo <= MAX_DESLOCAMENTO_VAGAS:
            parametros = {"deslocamento": proximo, **({"q": q} if q else {})}
            # Codificado aqui, uma vez: o template só imprime (e escapa para atributo).
            query_proximo = urlencode(parametros, quote_via=quote)
        return {"vagas": vagas, "total": total, "q": q, "query_proximo": query_proximo, "max_busca": MAX_TAMANHO_BUSCA}

    def pagina(request: Request, q: str | None, vaga, pedida: bool):
        """A página inteira. `vaga` é a selecionada; `pedida` sem vaga é um link para o que não existe."""
        contexto = contexto_lista(q, 0)
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
    def tela(request: Request, url: str | None = None, q: str | None = None):
        # A página inteira sempre, mesmo com HX-Request: as rotas de partial são as de baixo.
        return pagina(request, _busca(q), obter(url), bool(url))

    @router.get("/vagas")
    def vagas(request: Request, q: str | None = None, deslocamento: str = "0"):
        q = _busca(q)
        # Validado também na página inteira: um deslocamento ruim é 422, não é ignorado.
        inicio = validar_deslocamento(_deslocamento(deslocamento))
        if not eh_htmx(request):
            return pagina(request, q, None, False)
        contexto = contexto_lista(q, inicio)
        # Do 2º bloco em diante só entram os cards e o novo sentinela (substitui o anterior).
        partial = "tela/_cards.html" if inicio > 0 else "tela/_lista.html"
        return templates.TemplateResponse(request, partial, contexto)

    @router.get("/vagas/detalhe")
    def detalhe(request: Request, url: str | None = None):
        vaga = obter(url)
        if not eh_htmx(request):
            return pagina(request, None, vaga, True)
        return detalhe_partial(request, vaga, vaga is None)

    return router
