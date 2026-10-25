"""Helper dos testes: cliente HTTP já logado.

`ClienteLogado(app)` é um `TestClient` com o cookie de uma sessão válida da pessoa (a 1 por padrão), aberta direto no
repositório de sessões do app (o login pela senha é testado em `test_login.py`). Sem sessão, use `TestClient`.
"""

import secrets
from datetime import timedelta

from fastapi.testclient import TestClient

from src.controller.sessao import COOKIE
from src.service.autenticacao import hash_do_token


def abrir_sessao(app, pessoa_id: int = 1, dias: int = 1) -> str:
    """Cria a sessão da pessoa no repositório do app e devolve o TOKEN (o valor do cookie)."""
    autenticacao = app.state.autenticacao
    token = secrets.token_urlsafe(32)
    autenticacao.sessoes.criar(hash_do_token(token), pessoa_id, autenticacao.agora() + timedelta(days=dias))
    return token


class ClienteLogado(TestClient):
    def __init__(self, app, *args, pessoa_id: int = 1, **kwargs):
        super().__init__(app, *args, **kwargs)
        self.pessoa_id = pessoa_id
        self.cookies.set(COOKIE, abrir_sessao(app, pessoa_id))
