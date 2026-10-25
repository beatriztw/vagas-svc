"""De onde sai a pessoa de uma requisição, sem login.

Ordem: o cabeçalho `X-Person-Id` (API e scripts; inválido é 422), depois o cookie `vagas_pessoa` (gravado pelo
seletor da tela; inválido ou de pessoa que não existe é ignorado) e, por fim, a pessoa padrão. Isto NÃO é
autenticação: qualquer um que alcance a app escolhe a pessoa que quiser.
"""

from collections.abc import Callable

from fastapi import Request

from src.model.erros import DadoInvalido
from src.repository.pessoa import PessoaListada, PessoaRepository

CABECALHO_PESSOA = "X-Person-Id"
COOKIE_PESSOA = "vagas_pessoa"
UM_ANO = 365 * 24 * 60 * 60
# Um id de pessoa cabe em 18 dígitos; mais que isso nem vira inteiro (o Python recusa números gigantes em texto).
MAX_DIGITOS_ID = 18


def interpretar_id(texto: object) -> int | None:
    """Inteiro positivo em dígitos ASCII (sem sinal, espaço no meio nem dígito de outro alfabeto); senão None."""
    if not isinstance(texto, str):
        return None
    texto = texto.strip()
    if not texto.isascii() or not texto.isdigit() or len(texto) > MAX_DIGITOS_ID:
        return None
    numero = int(texto)
    return numero if numero > 0 else None


def pessoa_existente(texto: object, pessoas: list[PessoaListada]) -> int | None:
    """O id interpretado se é de uma pessoa da lista; senão None."""
    numero = interpretar_id(texto)
    return numero if numero is not None and any(p.id == numero for p in pessoas) else None


def criar_pessoa_da_requisicao(pessoas: PessoaRepository, padrao: int) -> Callable[[Request], int]:
    """A dependência `pessoa_da_requisicao`: o id da pessoa desta requisição.

    Também deixa em `request.state` o que o seletor do cabeçalho precisa (as pessoas e a escolhida), com a mesma
    leitura que valida o id: uma consulta só. O nome da pessoa só vai para a página, nunca para o log."""

    def pessoa_da_requisicao(request: Request) -> int:
        listadas = pessoas.listar_com_nome()
        cabecalho = request.headers.get(CABECALHO_PESSOA)
        if cabecalho is not None:
            escolhida = pessoa_existente(cabecalho, listadas)
            if escolhida is None:
                raise DadoInvalido(f"{CABECALHO_PESSOA}: use o id de uma pessoa existente")
        else:
            escolhida = pessoa_existente(request.cookies.get(COOKIE_PESSOA), listadas) or padrao
        request.state.pessoas_do_seletor = listadas
        request.state.pessoa_id = escolhida
        return escolhida

    return pessoa_da_requisicao
