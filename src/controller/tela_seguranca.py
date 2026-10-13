"""Peças de segurança e renderização compartilhadas pelas rotas HTML de `/tela`."""

import re
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader

# Caminhos pelo arquivo, nunca pelo cwd: a app sobe de qualquer diretório (uvicorn no Docker, testes).
RAIZ_SRC = Path(__file__).resolve().parent.parent
DIRETORIO_TEMPLATES = RAIZ_SRC / "templates"
DIRETORIO_STATIC = RAIZ_SRC / "static"

PREFIXO_TELA = "/tela"


def url_segura(valor: object) -> str | None:
    """A url só vira `href` se for http(s) com host; qualquer outra coisa devolve None.

    Recusa `javascript:`, `data:`, `vbscript:`, relativos, e tudo que tem espaço nas pontas ou
    caractere de controle (o navegador ignora tab e quebra de linha dentro do esquema, então
    `java\\tscript:` seria executado).
    """
    if not isinstance(valor, str) or not valor:
        return None
    if valor != valor.strip() or any(ord(c) < 32 or ord(c) == 127 for c in valor):
        return None
    try:
        partes = urlsplit(valor)
        host = partes.hostname
    except ValueError:
        return None
    if partes.scheme.lower() not in ("http", "https") or not host:
        return None
    return valor


def plataforma_curta(valor: object) -> str:
    """O nome da plataforma sem o sufixo entre parênteses: "LinkedIn (Apify)" vira "LinkedIn"."""
    if not isinstance(valor, str):
        return ""
    return re.sub(r"\s*\([^()]*\)\s*$", "", valor).strip()


# Autoescape ligado de forma explícita: dado de provider nunca é confiável.
templates = Jinja2Templates(env=Environment(loader=FileSystemLoader(DIRETORIO_TEMPLATES), autoescape=True))
templates.env.filters["url_segura"] = url_segura
templates.env.filters["plataforma_curta"] = plataforma_curta


def caminho_da_tela(caminho: str) -> bool:
    return caminho == PREFIXO_TELA or caminho.startswith(PREFIXO_TELA + "/")


def eh_htmx(request: Request) -> bool:
    # Restaurar o histórico do navegador (voltar) pede a página inteira, mesmo com HX-Request.
    if request.headers.get("HX-History-Restore-Request", "").lower() == "true":
        return False
    return request.headers.get("HX-Request", "").lower() == "true"


def renderizar(
    request: Request,
    pagina: str,
    partial: str,
    contexto: dict | None = None,
    status_code: int = 200,
):
    """Com `HX-Request: true` devolve só o partial; sem ele, a página inteira (F5 e deep link)."""
    return templates.TemplateResponse(
        request,
        partial if eh_htmx(request) else pagina,
        contexto or {},
        status_code=status_code,
    )


def exigir_mutacao_da_tela(request: Request) -> None:
    """Dependência das rotas que mudam estado em `/tela`: só chamadas do HTMX da mesma origem.

    Exige `HX-Request: true` (um formulário de outro site não consegue mandar o cabeçalho sem
    preflight de CORS) e `Origin` igual ao host da requisição ou, sem `Origin`, `Sec-Fetch-Site:
    same-origin`. Qualquer outra combinação é 403.
    """
    if not eh_htmx(request):
        raise HTTPException(403, "Requisição não permitida")
    origem = request.headers.get("Origin")
    if origem is not None:
        host = request.headers.get("Host", "")
        try:
            mesma = bool(host) and urlsplit(origem).netloc.lower() == host.lower()
        except ValueError:
            mesma = False
        if not mesma:
            raise HTTPException(403, "Origem não permitida")
        return
    if request.headers.get("Sec-Fetch-Site", "").lower() != "same-origin":
        raise HTTPException(403, "Origem não permitida")


def resposta_de_erro(request: Request, mensagem: str, status_code: int):
    """Erro de /tela como HTML. Com HTMX, o aviso da página é o alvo da troca (HX-Retarget), não o
    elemento de quem chamou: uma linha de tabela ou um bloco não pode ser trocado por ele."""
    contexto = {"mensagem": mensagem}
    if eh_htmx(request):
        return templates.TemplateResponse(
            request,
            "tela/_erro.html",
            contexto,
            status_code=status_code,
            headers={"HX-Retarget": "#aviso", "HX-Reswap": "outerHTML"},
        )
    return templates.TemplateResponse(request, "tela/erro.html", contexto, status_code=status_code)
