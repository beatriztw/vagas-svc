"""CSRF das rotas JSON que mudam estado (tudo fora de `/tela` e `/interno`)."""

from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from src.controller.tela_seguranca import caminho_da_tela

PREFIXO_INTERNO = "/interno"
METODOS_SEGUROS = frozenset({"GET", "HEAD", "OPTIONS"})


def caminho_interno(caminho: str) -> bool:
    return caminho == PREFIXO_INTERNO or caminho.startswith(PREFIXO_INTERNO + "/")


def exigir_json(request: Request) -> None:
    """A regra completa, sem isenção de método nem de caminho (rotas que a aplicam à mão, como
    `Depends(exigir_json)`). Reexportada por `candidaturas.py`.

    1. O tipo do `Content-Type` (sem parâmetros) tem que ser `application/json`, mesmo sem corpo
       (inclusive no DELETE): senão 415. Um formulário ou um `fetch` no-cors de outro site só
       consegue `text/plain`, `application/x-www-form-urlencoded` ou `multipart/form-data`; JSON
       exige preflight de CORS, e a API não responde preflight.
    2. `Sec-Fetch-Site`, se vier, tem que ser `same-origin` ou `none`: senão 403.
    3. `Origin`, se vier, tem que ter o mesmo host (netloc) do `Host`; `Origin: null` é 403.
    4. Sem `Origin` e sem `Sec-Fetch-Site` (curl, o timer do Pi) passa, cumprida a regra 1.
    """
    tipo = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if tipo != "application/json":
        raise HTTPException(415, "Content-Type tem que ser application/json")
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None and fetch_site.strip().lower() not in ("same-origin", "none"):
        raise HTTPException(403, "Origem não permitida")
    origem = request.headers.get("origin")
    if origem is not None:
        host = request.headers.get("host", "")
        try:
            mesma = bool(host) and urlsplit(origem).netloc.lower() == host.lower()
        except ValueError:
            mesma = False
        if not mesma:
            raise HTTPException(403, "Origem não permitida")


def exigir_json_mesma_origem(request: Request) -> None:
    """Dependência do app inteiro: aplica `exigir_json` a toda rota que muda estado, fora de
    `/tela` (proteção própria do HTMX) e `/interno` (Bearer)."""
    if request.method in METODOS_SEGUROS:
        return
    caminho = request.url.path
    if caminho_da_tela(caminho) or caminho_interno(caminho):
        return
    exigir_json(request)
