"""`GET/POST /login` e `POST /logout`: formulários HTML normais (funcionam sem JavaScript).

CSRF: o login e o logout NÃO exigem `HX-Request` (são formulários comuns, não chamadas do HTMX), mas exigem a
outra metade da proteção da tela: `Origin` igual ao host ou, sem `Origin`, `Sec-Fetch-Site: same-origin`
(`exigir_mesma_origem`), senão 403. Sem isso um site qualquer poderia fazer um formulário entrar com a conta
de quem ataca (login CSRF); o `SameSite=Strict` do cookie não cobre esse caso, porque o login não depende de
cookie nenhum. O logout fica com a mesma regra.

A mensagem de erro é a mesma para nome inexistente, pessoa sem senha e senha errada. Passou de 10 tentativas
para o nome em 15 minutos: 429, sem conferir a senha.
"""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from src.controller.sessao import COOKIE, URL_LOGIN
from src.controller.tela_seguranca import PREFIXO_TELA, exigir_mesma_origem, templates
from src.service.autenticacao import CredenciaisInvalidas, JANELA_FALHAS, MuitasTentativas, cookie_secure, sessao_dias

SEM_CACHE = {"Cache-Control": "no-store"}
MSG_INVALIDAS = "Nome ou senha incorretos."
MSG_MUITAS = "Muitas tentativas. Tente de novo em alguns minutos."


def _formulario(request: Request, mensagem: str | None = None, nome: str = "", status_code: int = 200, headers=None):
    return templates.TemplateResponse(
        request, "login.html", {"mensagem": mensagem, "nome": nome[:60]}, status_code=status_code, headers={**SEM_CACHE, **(headers or {})}
    )


def criar_router_login() -> APIRouter:
    router = APIRouter(include_in_schema=False)

    @router.get(URL_LOGIN)
    def formulario(request: Request):
        autenticacao = request.app.state.autenticacao
        if autenticacao.pessoa_da_sessao(request.cookies.get(COOKIE)) is not None:
            return RedirectResponse(PREFIXO_TELA, status_code=303, headers=SEM_CACHE)
        return _formulario(request)

    @router.post(URL_LOGIN, dependencies=[Depends(exigir_mesma_origem)])
    def entrar(request: Request, nome: str = Form(""), senha: str = Form("")):
        autenticacao = request.app.state.autenticacao
        try:
            nova = autenticacao.entrar(nome, senha)
        except MuitasTentativas:
            retry = {"Retry-After": str(int(JANELA_FALHAS.total_seconds()))}
            return _formulario(request, MSG_MUITAS, nome, 429, retry)
        except CredenciaisInvalidas:
            return _formulario(request, MSG_INVALIDAS, nome, 401)
        # Trocar de pessoa é sair e entrar: a sessão que este navegador já tinha acaba aqui.
        autenticacao.sair(request.cookies.get(COOKIE))
        resposta = RedirectResponse(PREFIXO_TELA, status_code=303, headers=SEM_CACHE)
        resposta.set_cookie(
            COOKIE,
            nova.token,
            max_age=sessao_dias() * 86400,
            path="/",
            secure=cookie_secure(),
            httponly=True,
            samesite="strict",
        )
        return resposta

    @router.post("/logout", dependencies=[Depends(exigir_mesma_origem)])
    def sair(request: Request):
        request.app.state.autenticacao.sair(request.cookies.get(COOKIE))
        resposta = RedirectResponse(URL_LOGIN, status_code=303, headers=SEM_CACHE)
        resposta.delete_cookie(COOKIE, path="/", secure=cookie_secure(), httponly=True, samesite="strict")
        return resposta

    return router
