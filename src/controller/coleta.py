"""Coleta pela linha de comando, sem a API (para rodar no cron): `python -m src.controller.coleta`.

Lê o Postgres pelas mesmas variáveis PG* da API e busca nos providers de produção
(`providers_padrao`, ou os de `pessoa_provider` da pessoa que os configurou), para todas as pessoas com perfil e termos. Imprime uma linha por pessoa e termo
(`pessoa 2 | python: 20 encontradas, 3 novas | erros: ...`, sempre o id, nunca o nome) e, por pessoa,
quantas vagas foram para a fila da IA em lote; pessoa sem perfil ou sem termos sai como `pulada`. Sai com
0; sai com 1 se a coleta inteira falhar (nenhuma pessoa com perfil e termos, banco fora do ar, todos os
providers com erro em todos os termos, ou outra coleta em andamento: mensagem "coleta em andamento" no
stderr, mesmo código 1 e nada é buscado). O erro de um provider só aparece na linha do termo e não é falha.
"""

import sys
from collections.abc import Callable
from typing import TextIO

from src.repository.trava import ColetaEmAndamento
from src.service.coleta_service import ColetaDoTermo, ColetaService


def linha_do_termo(pessoa_id: int, termo: ColetaDoTermo) -> str:
    linha = f"pessoa {pessoa_id} | {termo.termo}: {termo.encontradas} encontradas, {termo.novas} novas"
    if termo.erros:
        linha += " | erros: " + "; ".join(f"{nome}: {erro}" for nome, erro in termo.erros.items())
    return linha


def enfileirar_lote(ia, historico) -> int:
    """Depois da coleta: as vagas novas (e as que ainda não têm nota) vão para a fila da IA em lote."""
    return ia.enfileirar_lote(historico.visiveis())


def rodar(
    criar_servico: Callable[[], ColetaService],
    saida: TextIO,
    erro: TextIO,
    pos_coleta: Callable[[int], int] | None = None,
) -> int:
    """Roda uma coleta e imprime o resumo; devolve o código de saída.

    `pos_coleta` (o lote da IA, recebe o id da pessoa) roda para cada pessoa cuja coleta não falhou, DEPOIS
    das linhas dos termos, e imprime em `saida` quantas vagas foram para a fila dela. Falha nele vai para
    `erro`, só com o id da pessoa, e não muda o código de saída (nem impede as outras pessoas): a coleta
    já gravou."""
    try:
        resultado = criar_servico().coletar()
    except ColetaEmAndamento as e:
        print(str(e), file=erro)
        return 1
    except Exception as e:
        print(f"coleta falhou: {type(e).__name__}: {e}", file=erro)
        return 1
    for da_pessoa in resultado.pessoas:
        for termo in da_pessoa.termos:
            print(linha_do_termo(da_pessoa.pessoa_id, termo), file=saida)
    for pessoa_id in resultado.puladas:
        print(f"pessoa {pessoa_id} | pulada: sem perfil ou sem termos", file=saida)
    for pessoa_id in resultado.com_erro:
        print(f"pessoa {pessoa_id} | coleta falhou", file=erro)
    if resultado.falhou:
        print("coleta falhou: todos os providers deram erro em todos os termos", file=erro)
        return 1
    if pos_coleta is not None:
        for da_pessoa in resultado.pessoas:
            if da_pessoa.falhou:
                continue
            try:
                print(f"pessoa {da_pessoa.pessoa_id} | ia: {pos_coleta(da_pessoa.pessoa_id)} vagas na fila", file=saida)
            except Exception as e:  # noqa: BLE001 - o lote nunca derruba a coleta
                print(f"pessoa {da_pessoa.pessoa_id} | ia: lote falhou: {type(e).__name__}", file=erro)
    return 0


def main() -> int:
    from src.controller.api import providers_padrao
    from src.repository.conexao import conectar
    from src.repository.trava import CHAVE_TRAVA_LOTE_IA, TravaColetaPostgres
    from src.repository.pedido_ia_postgres import PedidoIARepositoryPostgres
    from src.repository.pessoa_postgres import PessoaRepositoryPostgres
    from src.repository.pessoa_provider_postgres import PessoaProviderRepositoryPostgres
    from src.repository.postgres import (
        CurriculoRepositoryPostgres,
        PerfilBuscaRepositoryPostgres,
        VagaEncontradaRepositoryPostgres,
    )
    from src.service.busca_service import BuscaService
    from src.service.coleta_service import RepositoriosDaPessoa
    from src.service.historico_service import HistoricoService
    from src.service.pedido_ia_service import PedidoIAService
    from src.service.providers_pessoa import ProvidersDaPessoa
    from src.service.recomendacao_service import RecomendacaoService

    # Processo de uma thread só, uma chamada de repositório por vez: uma conexão basta para todas as pessoas.
    # Cada `registrar` roda na própria transação (autocommit fora dela).
    conn = conectar()

    def repositorios(pessoa_id: int) -> RepositoriosDaPessoa:
        return RepositoriosDaPessoa(
            perfis=PerfilBuscaRepositoryPostgres(conn, pessoa_id),
            vagas=VagaEncontradaRepositoryPostgres(conn, pessoa_id),
            curriculos=CurriculoRepositoryPostgres(conn, pessoa_id),
            pedidos_ia=PedidoIARepositoryPostgres(conn, pessoa_id),
        )

    def lote_da_pessoa(pessoa_id: int) -> int:
        repos = repositorios(pessoa_id)
        ia = PedidoIAService(
            repos.pedidos_ia,
            repos.vagas,
            repos.perfis,
            repos.curriculos,
            trava_lote=TravaColetaPostgres(chave=CHAVE_TRAVA_LOTE_IA),
        )
        return enfileirar_lote(ia, HistoricoService(repos.vagas, repos.perfis))

    ambiente = BuscaService(providers_padrao())
    servico = ColetaService(
        RecomendacaoService(ambiente),
        PessoaRepositoryPostgres(conn),
        repositorios,
        TravaColetaPostgres(),
        # Os providers de cada pessoa (tabela pessoa_provider); sem eles, os do ambiente.
        buscas=ProvidersDaPessoa(ambiente, lambda pessoa_id: PessoaProviderRepositoryPostgres(conn, pessoa_id)),
    )
    try:
        return rodar(lambda: servico, sys.stdout, sys.stderr, pos_coleta=lote_da_pessoa)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
