"""Rotas HTML da tela (`/tela`): renderizadas no servidor, com partials trocados pelo HTMX."""

import hashlib
from datetime import datetime, timezone
from urllib.parse import quote, urlencode

from collections.abc import Callable

from fastapi import APIRouter, Depends, Form, Request, Response

from src.controller.contexto_pessoa import ContextoPessoa
from src.controller.tela_candidaturas import FUSO, contexto_aplicadas
from src.controller.tela_seguranca import PREFIXO_TELA, eh_htmx, exigir_mutacao_da_tela, resposta_de_erro, templates
from src.model.erros import DadoInvalido
from src.repository.base import MAX_DESLOCAMENTO_VAGAS, MAX_TAMANHO_BUSCA, validar_deslocamento
from src.service.coleta_service import ColetaService
from src.service.detalhe_service import VagaAberta
from src.service.historico_service import (
    FaixaFiltro,
    HistoricoService,
    Ordem,
    validar_faixa,
    validar_ordem,
)

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


def _sim_ou_nao(texto: str | None, nome: str) -> bool:
    valor = (texto or "").strip().lower()
    if valor in ("", "0", "false"):
        return False
    if valor in ("1", "true"):
        return True
    raise DadoInvalido(f"{nome} tem que ser 0 ou 1")


def _novas(texto: str | None) -> bool:
    """`novas` da query: "1"/"true" só as não vistas; ausente, "0" ou "false" todas (o padrão do PR2)."""
    return _sim_ou_nao(texto, "novas")


def _salvas(texto: str | None) -> bool:
    """`salvas` da query: "1"/"true" só as salvas (a aba "Salvas"); vale mais que `novas`."""
    return _sim_ou_nao(texto, "salvas")


def _query(**parametros) -> str:
    """Query já codificada, só com o que tem valor; `novas` e `salvas` vão como 1 e os enums pelo
    valor. Os templates só imprimem."""
    limpos = {n: getattr(v, "value", v) for n, v in parametros.items() if v}
    for marca in ("novas", "salvas"):
        if marca in limpos:
            limpos[marca] = "1"
    return urlencode(limpos, quote_via=quote)


def _apos(em: str | None, url: str | None) -> tuple[datetime, str] | None:
    """O cursor do "carregar mais" da aba "Salvas": `salva_em` (ISO 8601) e url da última vaga mostrada."""
    if not em and not url:
        return None
    try:
        instante = datetime.fromisoformat(em or "")
    except ValueError:
        raise DadoInvalido("apos_em tem que ser uma data ISO 8601") from None
    if instante.tzinfo is None or not url or len(url) > MAX_TAMANHO_URL:
        raise DadoInvalido("cursor da aba Salvas inválido")
    return instante, url


def quando(valor: datetime | None, agora: datetime | None = None) -> str | None:
    """"hoje 18:02", "ontem 08:01" ou "12/10 08:01", no fuso de São Paulo; None sem data."""
    if valor is None:
        return None
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=timezone.utc)
    local = valor.astimezone(FUSO)
    dias = ((agora or datetime.now(timezone.utc)).astimezone(FUSO).date() - local.date()).days
    dia = "hoje" if dias == 0 else "ontem" if dias == 1 else local.strftime("%d/%m")
    return f"{dia} {local:%H:%M}"


def id_salvar(url: str) -> str:
    """Pedaço de id HTML estável para a bandeirinha da vaga: a URL inteira não serve de id."""
    return hashlib.sha256(url.encode("utf-8", "surrogatepass")).hexdigest()[:16]


templates.env.filters["id_salvar"] = id_salvar


def criar_router_tela(coleta: ColetaService, contexto_da_pessoa: Callable[..., ContextoPessoa]) -> APIRouter:
    """`coleta` é o MESMO `ColetaService` da API e do cron (com a mesma trava de uma coleta por vez): a coleta roda
    para todas as pessoas, mas o resumo mostrado é o da pessoa da sessão. Todo o resto vem do `ContextoPessoa` da
    requisição (`contexto_da_pessoa`, uma dependência): os repositórios e serviços da pessoa da sessão, nunca os de outra."""
    router = APIRouter(prefix=PREFIXO_TELA, include_in_schema=False)

    def obter(ctx: ContextoPessoa, url: str | None) -> VagaAberta | None:
        """A vaga aberta como está guardada, SEM falar com o provider (a página não espera ninguém); a
        expirada também abre."""
        if not url or len(url) > MAX_TAMANHO_URL:
            return None
        return ctx.detalhe.abrir(url)

    def contexto_lote(ctx: ContextoPessoa, criados: int | None = None, notas: dict | None = None) -> dict:
        """O indicador da IA em lote (avaliadas / na fila, entre as vagas da lista)."""
        ia = ctx.ia
        avaliadas, na_fila, sem_nota = ia.progresso_lote(ctx.historico.visiveis(), notas)
        return {
            "avaliadas": avaliadas,
            "na_fila": na_fila,
            # O botão "Avaliar N sem nota" só aparece com N > 0 (ex.: depois de trocar o currículo base).
            "sem_nota": sem_nota,
            "criados": criados,
            "sem_base": ia.curriculos.obter_base() is None,
            # Teto em 24 h (None = sem teto; 0 = lote desligado) e quanto ainda cabe nele.
            "teto": ia.max_lote_por_dia,
            "restante": ia.restante_lote(),
            "desligado": ia.max_lote_por_dia == 0,
            "pausado": ia.lote_pausado(),
        }

    def candidatura_da(ctx: ContextoPessoa, vaga):
        """A candidatura da vaga aberta, para o detalhe mostrar "Aplicada em dd/mm" em vez do botão."""
        return ctx.candidaturas.obter_por_url(vaga.url) if vaga is not None else None

    def contexto_lista(
        ctx: ContextoPessoa,
        q: str | None,
        novas: bool,
        deslocamento: int,
        ordem: Ordem | None = None,
        faixa: FaixaFiltro | None = None,
        salvas: bool = False,
        apos: tuple[datetime, str] | None = None,
    ) -> dict:
        """O que `_lista.html` e `_cards.html` precisam. Página e total saem da mesma leitura do
        histórico com o perfil aplicado: o contador é o da lista inteira, nunca o tamanho da página.
        As notas da IA saem de UMA consulta e servem à ordem, ao corte por faixa e aos chips.
        Na aba "Salvas" a leitura é outra (`historico.salvas`): sem perfil, sem ordem nem faixa da IA."""
        ia, historico, encontradas = ctx.ia, ctx.historico, ctx.encontradas
        notas = ia.notas_validas()
        if salvas:
            novas, ordem, faixa = False, None, None
            lida = historico.salvas(limite=TAMANHO_PAGINA, q=q, apos=apos)
        else:
            lida = historico.pagina(
                somente_novas=novas,
                limite=TAMANHO_PAGINA,
                q=q,
                deslocamento=deslocamento,
                notas=notas,
                ordem=ordem,
                faixa=faixa,
            )
        vagas, total = lida.vagas, lida.total
        proximo = deslocamento + len(vagas)
        # O que a pessoa escolheu viaja nas urls (a ordem padrão não: ela muda sozinha com as notas).
        escolha = {"q": q, "ordem": ordem, "faixa": faixa}
        query_proximo = None
        if salvas:
            # A aba "Salvas" continua depois da última vaga mostrada (ver `HistoricoService.salvas`).
            if len(vagas) == TAMANHO_PAGINA:
                ultima = vagas[-1]
                query_proximo = _query(salvas=True, q=q, apos_em=ultima.salva_em.isoformat(), apos_url=ultima.url)
        elif vagas and proximo < total and proximo <= MAX_DESLOCAMENTO_VAGAS:
            # Codificado aqui, uma vez: o template só imprime (e escapa para atributo).
            query_proximo = _query(deslocamento=proximo, novas=novas, **escolha)
        com_novas = {**escolha, "novas": novas, "salvas": salvas}
        agora = datetime.now(timezone.utc)
        primeiro_bloco = deslocamento == 0 and apos is None
        return {
            "vagas": vagas,
            "total": total,
            "salario_minimo_usd": lida.salario_minimo_usd,
            "q": q,
            "novas": novas,
            "salvas": salvas,
            "total_salvas": encontradas.contar_salvas(),
            # Salva que expirou continua na aba "Salvas", com a etiqueta "encerrada".
            "encerradas": {v.url for v in vagas if v.expirada_em is not None and v.expirada_em <= agora},
            "ordem": lida.ordem,
            "ordem_pedida": ordem,
            "faixa": faixa,
            "notas": notas,
            "na_fila": ia.pedidos.urls_ativas("compatibilidade"),
            "tem_ia": True,
            "query_proximo": query_proximo,
            # O filtro viaja em toda url gerada (card, alternância); sem filtro fica vazio.
            "filtro": _query(**com_novas),
            "query_todas": _query(**escolha),
            "query_novas": _query(**escolha, novas=True),
            "query_salvas": _query(q=q, salvas=True),
            "query_ordem_ia": _query(**{**com_novas, "ordem": Ordem.IA}),
            "query_ordem_perfil": _query(**{**com_novas, "ordem": Ordem.PERFIL}),
            "query_faixa_todas": _query(**{**com_novas, "faixa": None}),
            "query_faixa_media": _query(**{**com_novas, "faixa": FaixaFiltro.MEDIA}),
            "query_faixa_alta": _query(**{**com_novas, "faixa": FaixaFiltro.ALTA}),
            "query_faixa_baixa": _query(**{**com_novas, "faixa": FaixaFiltro.BAIXA}),
            "max_busca": MAX_TAMANHO_BUSCA,
            # Só no 1º bloco (a barra da lista); os blocos do "carregar mais" não têm barra.
            "lote": contexto_lote(ctx, notas=notas) if primeiro_bloco else None,
            "ultima_coleta": quando(encontradas.ultima_coleta(), agora) if primeiro_bloco else None,
        }

    def pagina(
        ctx: ContextoPessoa,
        request: Request,
        q: str | None,
        novas: bool,
        aberta: VagaAberta | None,
        pedida: bool,
        ordem: Ordem | None = None,
        faixa: FaixaFiltro | None = None,
        salvas: bool = False,
    ):
        """A página inteira. `aberta` é a selecionada; `pedida` sem vaga é um link para o que não existe."""
        contexto_da_tela = contexto_lista(ctx, q, novas, 0, ordem, faixa, salvas)
        nao_encontrada = pedida and aberta is None
        if not pedida and contexto_da_tela["vagas"]:
            aberta = obter(ctx, contexto_da_tela["vagas"][0].url)
        vaga = aberta.vaga if aberta else None
        contexto_da_tela.update(
            selecionada=vaga,
            aberta=aberta,
            nao_encontrada=nao_encontrada,
            candidatura=candidatura_da(ctx, vaga),
        )
        # "Vagas aplicadas" entra na página inteira com o mesmo contexto das rotas de /tela/candidaturas.
        contexto_da_tela.update(contexto_aplicadas(ctx.candidaturas))
        # 404 também na página inteira: o recurso pedido não existe, e o aviso aparece no detalhe
        # junto com a lista (o link de um favorito antigo continua útil).
        return templates.TemplateResponse(
            request, "tela/index.html", contexto_da_tela, status_code=404 if nao_encontrada else 200
        )

    def detalhe_partial(ctx: ContextoPessoa, request: Request, aberta: VagaAberta | None):
        vaga = aberta.vaga if aberta else None
        return templates.TemplateResponse(
            request,
            "tela/_detalhe.html",
            # Só o detalhe pedido por clique (este partial) traz o POST de vista; a página inteira
            # (abrir, F5, deep link, 1ª vaga automática) não marca nada.
            {
                "selecionada": vaga,
                "aberta": aberta,
                "nao_encontrada": aberta is None,
                "candidatura": candidatura_da(ctx, vaga),
                "marcar_vista": True,
            },
            status_code=404 if aberta is None else 200,
        )

    @router.get("")
    def tela(
        request: Request,
        ctx: ContextoPessoa = Depends(contexto_da_pessoa),
        url: str | None = None,
        q: str | None = None,
        novas: str | None = None,
        ordem: str | None = None,
        faixa: str | None = None,
        salvas: str | None = None,
    ):
        # A página inteira sempre, mesmo com HX-Request: as rotas de partial são as de baixo.
        busca, somente_novas, so_salvas = _busca(q), _novas(novas), _salvas(salvas)
        ordem_, faixa_ = validar_ordem(ordem), validar_faixa(faixa)
        return pagina(ctx, request, busca, somente_novas, obter(ctx, url), bool(url), ordem_, faixa_, so_salvas)

    @router.get("/vagas")
    def vagas(
        request: Request,
        ctx: ContextoPessoa = Depends(contexto_da_pessoa),
        q: str | None = None,
        novas: str | None = None,
        deslocamento: str = "0",
        detalhe_url: str | None = None,
        ordem: str | None = None,
        faixa: str | None = None,
        salvas: str | None = None,
        apos_em: str | None = None,
        apos_url: str | None = None,
    ):
        q = _busca(q)
        somente_novas, so_salvas = _novas(novas), _salvas(salvas)
        ordem_, faixa_ = validar_ordem(ordem), validar_faixa(faixa)
        # Validado também na página inteira: um deslocamento ruim é 422, não é ignorado.
        inicio = validar_deslocamento(_deslocamento(deslocamento))
        apos = _apos(apos_em, apos_url)
        if not eh_htmx(request):
            return pagina(ctx, request, q, somente_novas, None, False, ordem_, faixa_, so_salvas)
        da_lista = contexto_lista(ctx, q, somente_novas, inicio, ordem_, faixa_, so_salvas, apos if so_salvas else None)
        continuacao = inicio > 0 or (so_salvas and apos is not None)
        # Do 2º bloco em diante só entram os cards e o novo sentinela (substitui o anterior).
        partial = "tela/_cards.html" if continuacao else "tela/_lista.html"
        resposta = templates.TemplateResponse(request, partial, da_lista)
        if not continuacao:
            # A barra mostra a PÁGINA (`/tela?...`), não o partial, e mantém a vaga aberta (`url`):
            # F5 e link copiado reabrem a mesma tela. `detalhe_url` vem do #detalhe-url, se houver detalhe.
            aberta = detalhe_url if detalhe_url and len(detalhe_url) <= MAX_TAMANHO_URL else None
            query = _query(
                q=q, novas=da_lista["novas"], salvas=so_salvas, ordem=da_lista["ordem_pedida"], faixa=da_lista["faixa"], url=aberta
            )
            resposta.headers["HX-Push-Url"] = f"{PREFIXO_TELA}?{query}".rstrip("?")
        return resposta

    @router.get("/vagas/detalhe")
    def detalhe(
        request: Request,
        ctx: ContextoPessoa = Depends(contexto_da_pessoa),
        url: str | None = None,
        q: str | None = None,
        novas: str | None = None,
        ordem: str | None = None,
        faixa: str | None = None,
        salvas: str | None = None,
    ):
        busca, somente_novas, so_salvas = _busca(q), _novas(novas), _salvas(salvas)  # validados antes do provider
        ordem_, faixa_ = validar_ordem(ordem), validar_faixa(faixa)
        aberta = obter(ctx, url)
        if not eh_htmx(request):
            return pagina(ctx, request, busca, somente_novas, aberta, True, ordem_, faixa_, so_salvas)
        return detalhe_partial(ctx, request, aberta)

    @router.get("/vagas/secoes")
    def secoes(request: Request, ctx: ContextoPessoa = Depends(contexto_da_pessoa), url: str | None = None):
        """As seções do detalhe, para a vaga que ainda não tem o detalhe gravado: aqui (e só aqui) pode
        esperar o provider (5 s). Falha vira aviso no lugar das seções, 404 do provider marca expirada."""
        aberta = ctx.detalhe.carregar(url) if url and len(url) <= MAX_TAMANHO_URL else None
        return templates.TemplateResponse(
            request,
            "tela/_detalhe_conteudo.html",
            {"vaga": aberta.vaga if aberta else None, "aberta": aberta, "recarregar": False},
            status_code=404 if aberta is None else 200,
        )

    @router.post("/vagas/salva", dependencies=[Depends(exigir_mutacao_da_tela)])
    def salvar(
        request: Request, ctx: ContextoPessoa = Depends(contexto_da_pessoa), url: str = Form(""), salvar: str = Form("")
    ):
        """A bandeirinha: salva (`salvar=1`) ou tira dos salvos (`salvar=0`). A resposta só tem trocas
        OOB: a bandeirinha do card e a do detalhe dessa vaga (a que não estiver na tela é ignorada) e
        o número da aba "Salvas". A lista não é redesenhada: na aba "Salvas", o card de uma vaga tirada
        dos salvos fica até a próxima leitura, para um clique errado ter volta."""
        salva = _sim_ou_nao(salvar, "salvar")
        if not url or len(url) > MAX_TAMANHO_URL:
            raise DadoInvalido("url da vaga inválida")
        encontradas = ctx.encontradas
        if not encontradas.marcar_salva(url, salva):
            return resposta_de_erro(request, "Vaga não encontrada", 404)
        return templates.TemplateResponse(
            request, "tela/_salvar_oob.html", {"vaga_url": url, "salva": salva, "total_salvas": encontradas.contar_salvas()}
        )

    @router.post("/vagas/vista", dependencies=[Depends(exigir_mutacao_da_tela)])
    def vista(ctx: ContextoPessoa = Depends(contexto_da_pessoa), url: str = Form("")):
        """Marca a vaga como vista: só aqui, nunca no GET do detalhe (D8). Sem corpo; o tela.js tira o selo."""
        if not url or len(url) > MAX_TAMANHO_URL:
            raise DadoInvalido("url da vaga inválida")
        ctx.encontradas.marcar_vistas([url])
        return Response(status_code=204)

    @router.post("/ia/lote", dependencies=[Depends(exigir_mutacao_da_tela)])
    def lote_ia(request: Request, ctx: ContextoPessoa = Depends(contexto_da_pessoa)):
        """Manda as vagas da lista sem nota válida para a fila da IA (prioridade do lote, teto diário se houver)."""
        criados = ctx.ia.enfileirar_lote(ctx.historico.visiveis())
        return templates.TemplateResponse(request, "tela/_lote_ia.html", {"lote": contexto_lote(ctx, criados)})

    @router.post("/ia/lote/pausar", dependencies=[Depends(exigir_mutacao_da_tela)])
    def pausar_lote_ia(request: Request, ctx: ContextoPessoa = Depends(contexto_da_pessoa)):
        """Tira da fila o lote que ainda não começou e para o lote automático da coleta. O clique numa
        vaga ("Compatibilidade") e o currículo seguem funcionando."""
        removidos = ctx.ia.pausar_lote()
        lote = contexto_lote(ctx)
        lote["removidos"] = removidos
        return templates.TemplateResponse(request, "tela/_lote_ia.html", {"lote": lote})

    @router.post("/ia/lote/retomar", dependencies=[Depends(exigir_mutacao_da_tela)])
    def retomar_lote_ia(request: Request, ctx: ContextoPessoa = Depends(contexto_da_pessoa)):
        """Tira a pausa e já manda para a fila as vagas da lista sem nota."""
        criados = ctx.ia.retomar_lote(ctx.historico.visiveis())
        return templates.TemplateResponse(request, "tela/_lote_ia.html", {"lote": contexto_lote(ctx, criados)})

    @router.post("/coletas", dependencies=[Depends(exigir_mutacao_da_tela)])
    def coletar(
        request: Request,
        ctx: ContextoPessoa = Depends(contexto_da_pessoa),
        q: str = Form(""),
        novas: str = Form(""),
        ordem: str = Form(""),
        faixa: str = Form(""),
        salvas: str = Form(""),
    ):
        """Roda a coleta (a trava de uma por vez é do ColetaService: 409 em HTML se já há uma) e devolve a lista."""
        busca, somente_novas, so_salvas = _busca(q), _novas(novas), _salvas(salvas)
        ordem_, faixa_ = validar_ordem(ordem), validar_faixa(faixa)
        resultado = coleta.coletar().da_pessoa(ctx.pessoa_id)
        da_lista = contexto_lista(ctx, busca, somente_novas, 0, ordem_, faixa_, so_salvas)
        da_lista["resumo"] = {
            "novas": resultado.novas,
            "falhou": resultado.falhou,
            "erros": [f"{t.termo}: {nome}" for t in resultado.termos for nome in t.erros],
        }
        return templates.TemplateResponse(request, "tela/_lista.html", da_lista)

    return router
