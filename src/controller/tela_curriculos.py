"""Currículo adaptado na tela: o bloco do detalhe da vaga e o download do `.md`.

Só o tipo `adaptado` sai por aqui. O currículo base é o documento pessoal inteiro e não tem
vaga: a tela não tem motivo para entregá-lo (o `GET /curriculos/base` JSON, também só da
pessoa logada, continua existindo). Os dois caminhos só leem.
"""

import re
import unicodedata
from urllib.parse import quote

from collections.abc import Callable

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from src.controller.contexto_pessoa import ContextoPessoa
from src.controller.tela_seguranca import PREFIXO_TELA, resposta_de_erro, templates

TAMANHO_MAX_NOME = 60


def nome_do_arquivo(titulo: str, id: int) -> str:
    """Nome seguro para o `Content-Disposition`: só [a-z0-9_-], sem acento, com teto, sempre `.md`."""
    sem_acento = unicodedata.normalize("NFKD", titulo).encode("ascii", "ignore").decode("ascii")
    base = re.sub(r"[^a-z0-9_-]+", "-", sem_acento.lower()).strip("-_")[:TAMANHO_MAX_NOME].strip("-_")
    return f"{base or f'curriculo-{id}'}.md"


def cabecalho_de_download(titulo: str, id: int) -> str:
    """`filename` ASCII sanitizado e `filename*` com o título (acento) em percent-encoding."""
    ascii_ = nome_do_arquivo(titulo, id)
    limpo = "".join(c for c in titulo if c.isprintable() and c not in '/\\"')[:100].strip()
    if not limpo or limpo.strip(".") == "":
        return f'attachment; filename="{ascii_}"'
    return f"attachment; filename=\"{ascii_}\"; filename*=UTF-8''{quote(limpo + '.md', safe='')}"


def data_curriculo(valor) -> str:
    return valor.strftime("%d/%m/%Y") if hasattr(valor, "strftime") else "—"


templates.env.filters["data_curriculo"] = data_curriculo


def criar_router_tela_curriculos(contexto_da_pessoa: Callable[..., ContextoPessoa]) -> APIRouter:
    router = APIRouter(prefix=PREFIXO_TELA + "/curriculos", include_in_schema=False)

    @router.get("/da-vaga")
    def da_vaga(request: Request, ctx: ContextoPessoa = Depends(contexto_da_pessoa), url: str = Query("", max_length=2000)):
        """Partial do detalhe: o adaptado mais recente da vaga, ou corpo vazio (a frente G põe o "Gerar")."""
        adaptados = ctx.curriculos.listar_adaptados(url) if url else []
        return templates.TemplateResponse(
            request, "tela/_curriculo_conteudo.html", {"curriculo": adaptados[0] if adaptados else None}
        )

    # Registrada depois de `/da-vaga`; o id é texto para o inválido virar 404, não 422 JSON.
    @router.get("/{arquivo}")
    def baixar(arquivo: str, request: Request, ctx: ContextoPessoa = Depends(contexto_da_pessoa)):
        achado = re.fullmatch(r"([0-9]{1,18})\.md", arquivo)
        curriculo = ctx.curriculos.obter(int(achado.group(1))) if achado else None
        if curriculo is None or curriculo.tipo != "adaptado":
            return resposta_de_erro(request, "Currículo não encontrado", 404)
        return Response(
            curriculo.conteudo_md.encode("utf-8"),
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": cabecalho_de_download(curriculo.titulo, curriculo.id),
                "Cache-Control": "no-store",
            },
        )

    return router
