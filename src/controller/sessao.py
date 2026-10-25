"""A sessão de login nas rotas: o cookie, a dependência que exige sessão em TODA rota e a resposta de "sem sessão".

`exigir_sessao` é uma dependência do app inteiro (como a de CSRF em `seguranca_json.py`): rota nova, de /tela ou
da API JSON, já nasce protegida. O que fica de fora é a lista explícita `ISENTAS` mais as duas rotas do worker:

- `/pedidos-ia/*` tem o Bearer do worker (`exigir_worker_ia`); a fila é de todas as pessoas.
- `POST /curriculos/adaptados` aceita a sessão OU o Bearer do worker (ver `exigir_sessao`).

Quem precisa da pessoa usa `Depends(pessoa_da_requisicao)`: ela vem do cookie, nunca de parâmetro da requisição.
"""

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from src.controller.pedidos_ia import conferir_bearer
from src.controller.tela_seguranca import caminho_da_tela, eh_htmx
from src.repository.sessao import PessoaLogada

COOKIE = "vagas_sessao"
URL_LOGIN = "/login"

# Caminhos exatos sem sessão: saúde, login e logout, o redirect de `/` e a documentação gerada pelo FastAPI (só o
# esquema da API, sem dado de ninguém).
ISENTAS: frozenset[str] = frozenset({"/", "/saude", "/login", "/logout", "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"})
# Prefixos com autenticação própria (Bearer do worker) ou sem dado nenhum (os estáticos).
PREFIXOS_ISENTOS = ("/static", "/pedidos-ia")
# Sessão OU Bearer do worker.
ROTA_DO_WORKER = ("POST", "/curriculos/adaptados")


class SemSessao(Exception):
    """Sem cookie, cookie desconhecido ou sessão expirada."""


def _sob(caminho: str, prefixo: str) -> bool:
    return caminho == prefixo or caminho.startswith(prefixo + "/")


def caminho_isento(caminho: str) -> bool:
    return caminho in ISENTAS or any(_sob(caminho, p) for p in PREFIXOS_ISENTOS)


def exigir_sessao(request: Request) -> None:
    """Dependência do app. Sem sessão válida: `SemSessao` (303 para /login na tela, `HX-Redirect` no HTMX, 401 na API).

    `POST /curriculos/adaptados`: com `Authorization` na requisição vale SÓ o Bearer do worker (token errado é 401,
    e o cookie não é olhado); sem ele, a sessão. Quem vem com Bearer não tem pessoa: a rota a tira do `pedido_id`.
    """
    caminho = request.url.path
    if caminho_isento(caminho):
        return
    if (request.method, caminho) == ROTA_DO_WORKER and "authorization" in request.headers:
        conferir_bearer(request)
        request.state.worker = True
        return
    pessoa = request.app.state.autenticacao.pessoa_da_sessao(request.cookies.get(COOKIE))
    if pessoa is None:
        raise SemSessao()
    request.state.pessoa = pessoa


def pessoa_logada(request: Request) -> PessoaLogada:
    pessoa = getattr(request.state, "pessoa", None)
    if pessoa is None:
        # Chegou numa rota sem passar por `exigir_sessao` (ou por uma isenta): falha fechada.
        raise SemSessao()
    return pessoa


def pessoa_da_requisicao(request: Request) -> int:
    """O `pessoa_id` da sessão. É o ÚNICO jeito de uma rota saber de quem são os dados."""
    return pessoa_logada(request).id


def veio_do_worker(request: Request) -> bool:
    return getattr(request.state, "worker", False) is True


def resposta_sem_sessao(request: Request, erro: SemSessao) -> Response:
    if caminho_da_tela(request.url.path):
        if eh_htmx(request):
            # O htmx navega a janela inteira (o 401 não é trocado no lugar de nada).
            resposta: Response = Response(status_code=401, headers={"HX-Redirect": URL_LOGIN})
        else:
            resposta = RedirectResponse(URL_LOGIN, status_code=303)
    else:
        resposta = JSONResponse(status_code=401, content={"detail": "Faça login"})
    resposta.headers["Cache-Control"] = "no-store"
    return resposta
