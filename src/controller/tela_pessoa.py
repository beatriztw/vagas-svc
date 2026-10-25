"""`POST /tela/pessoa`: o seletor de pessoa do cabeçalho. Sem senha: grava o cookie e volta para a lista."""

from fastapi import APIRouter, Depends, Form, Response

from src.controller.pessoa_selecionada import COOKIE_PESSOA, UM_ANO, pessoa_existente
from src.controller.tela_seguranca import PREFIXO_TELA, exigir_mutacao_da_tela
from src.model.erros import DadoInvalido
from src.repository.pessoa import PessoaRepository


def criar_router_tela_pessoa(pessoas: PessoaRepository) -> APIRouter:
    router = APIRouter(prefix=PREFIXO_TELA, include_in_schema=False)

    @router.post("/pessoa", dependencies=[Depends(exigir_mutacao_da_tela)])
    def trocar(pessoa_id: str = Form("")) -> Response:
        # Pessoa inexistente ou id torto: DadoInvalido, que em /tela vira o aviso 422 e não grava cookie.
        escolhida = pessoa_existente(pessoa_id, pessoas.listar_com_nome())
        if escolhida is None:
            raise DadoInvalido("pessoa_id: use o id de uma pessoa existente")
        # Só HTMX chega aqui (`exigir_mutacao_da_tela`): o redirecionamento é o do HTMX.
        resposta = Response(status_code=200, headers={"HX-Redirect": PREFIXO_TELA})
        resposta.set_cookie(
            COOKIE_PESSOA, str(escolhida), max_age=UM_ANO, path="/", httponly=True, samesite="lax"
        )
        return resposta

    return router
