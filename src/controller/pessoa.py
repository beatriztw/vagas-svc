"""Cadastro de pessoa pela linha de comando (não existe tela de cadastro):

    python -m src.controller.pessoa criar --nome "<nome>"   # pede a senha duas vezes (getpass)
    python -m src.controller.pessoa senha --id N            # define ou troca a senha da pessoa N

A senha tem de 10 a 128 caracteres. NUNCA é impressa, nem o hash: a saída é só o id. Trocar a senha encerra as
sessões abertas da pessoa. Depois do deploy da migration 016 a pessoa 1 está sem senha (e portanto não entra):
rode `senha --id 1`. Sai com 0; sai com 1 e uma mensagem no stderr em qualquer recusa.
"""

import argparse
import getpass
import sys
from collections.abc import Callable
from typing import TextIO

from src.model.erros import DadoInvalido
from src.repository.pessoa import PessoaRepository
from src.repository.sessao import SessaoRepository
from src.service import senha as senhas


def validar_senha(senha: str) -> str:
    if not (senhas.MIN_SENHA <= len(senha) <= senhas.MAX_SENHA):
        raise DadoInvalido(f"senha: de {senhas.MIN_SENHA} a {senhas.MAX_SENHA} caracteres")
    return senha


def pedir_senha(ler: Callable[[str], str]) -> str:
    primeira = validar_senha(ler("Senha: "))
    if ler("Repita a senha: ") != primeira:
        raise DadoInvalido("as senhas não são iguais")
    return primeira


def criar(pessoas: PessoaRepository, nome: str, ler: Callable[[str], str]) -> int:
    """Cria a pessoa já com a senha. O nome é validado ANTES de pedir a senha."""
    from src.repository.pessoa import validar_nome

    nome = validar_nome(nome)
    return pessoas.criar(nome, senhas.hash_senha(pedir_senha(ler)))


def trocar_senha(pessoas: PessoaRepository, sessoes: SessaoRepository, pessoa_id: int, ler: Callable[[str], str]) -> None:
    senha = pedir_senha(ler)
    if not pessoas.definir_senha(pessoa_id, senhas.hash_senha(senha)):
        raise DadoInvalido(f"pessoa {pessoa_id} não existe")
    # Quem tinha a senha antiga (ou a sessão roubada) sai.
    sessoes.remover_da_pessoa(pessoa_id)


def rodar(
    argv: list[str],
    pessoas: PessoaRepository,
    sessoes: SessaoRepository,
    saida: TextIO,
    erro: TextIO,
    ler: Callable[[str], str] = getpass.getpass,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.controller.pessoa", description="Cadastro de pessoas")
    sub = parser.add_subparsers(dest="comando", required=True)
    p_criar = sub.add_parser("criar", help="cria uma pessoa e pede a senha")
    p_criar.add_argument("--nome", required=True)
    p_senha = sub.add_parser("senha", help="define ou troca a senha de uma pessoa")
    p_senha.add_argument("--id", required=True, type=int)
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return 1 if e.code else 0
    try:
        if args.comando == "criar":
            pessoa_id = criar(pessoas, args.nome, ler)
            print(f"pessoa {pessoa_id} criada", file=saida)
        else:
            trocar_senha(pessoas, sessoes, args.id, ler)
            print(f"senha da pessoa {args.id} definida", file=saida)
    except DadoInvalido as e:
        print(f"erro: {e}", file=erro)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("erro: cancelado", file=erro)
        return 1
    return 0


def main() -> int:
    from src.repository.conexao import conectar
    from src.repository.pessoa_postgres import PessoaRepositoryPostgres
    from src.repository.sessao_postgres import SessaoRepositoryPostgres

    conn = conectar()
    try:
        return rodar(sys.argv[1:], PessoaRepositoryPostgres(conn), SessaoRepositoryPostgres(conn), sys.stdout, sys.stderr)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
