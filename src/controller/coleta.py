"""Coleta pela linha de comando, sem a API (para rodar no cron): `python -m src.controller.coleta`.

Lê o Postgres pelas mesmas variáveis PG* da API e busca nos providers de produção
(`providers_padrao`). Imprime uma linha por termo (e, no fim, quantas vagas foram para a fila
da IA em lote) e sai com 0; sai com 1 se a coleta
inteira falhar (sem perfil ou sem termos, banco fora do ar, todos os providers com erro
em todos os termos, ou outra coleta em andamento: mensagem "coleta em andamento" no
stderr, mesmo código 1 e nada é buscado). O erro de um provider só aparece na linha do termo e não é falha.
"""

import sys
from collections.abc import Callable
from typing import TextIO

from src.repository.trava import ColetaEmAndamento
from src.service.coleta_service import ColetaDoTermo, ColetaService


def linha_do_termo(termo: ColetaDoTermo) -> str:
    linha = f"{termo.termo}: {termo.encontradas} encontradas, {termo.novas} novas"
    if termo.erros:
        linha += " | erros: " + "; ".join(f"{nome}: {erro}" for nome, erro in termo.erros.items())
    return linha


def enfileirar_lote(ia, historico) -> int:
    """Depois da coleta: as vagas novas (e as que ainda não têm nota) vão para a fila da IA em lote."""
    return ia.enfileirar_lote(historico.visiveis())


def rodar(criar_servico: Callable[[], ColetaService], saida: TextIO, erro: TextIO) -> int:
    """Roda uma coleta e imprime o resumo; devolve o código de saída."""
    try:
        resultado = criar_servico().coletar()
    except ColetaEmAndamento as e:
        print(str(e), file=erro)
        return 1
    except Exception as e:
        print(f"coleta falhou: {type(e).__name__}: {e}", file=erro)
        return 1
    for termo in resultado.termos:
        print(linha_do_termo(termo), file=saida)
    if resultado.falhou:
        print("coleta falhou: todos os providers deram erro em todos os termos", file=erro)
        return 1
    return 0


def main() -> int:
    from src.controller.api import providers_padrao
    from src.repository.conexao import conectar
    from src.repository.trava import TravaColetaPostgres
    from src.repository.pedido_ia_postgres import PedidoIARepositoryPostgres
    from src.repository.postgres import (
        CurriculoRepositoryPostgres,
        PerfilBuscaRepositoryPostgres,
        VagaEncontradaRepositoryPostgres,
    )
    from src.service.busca_service import BuscaService
    from src.service.historico_service import HistoricoService
    from src.service.pedido_ia_service import PedidoIAService
    from src.service.recomendacao_service import RecomendacaoService

    conexoes = []

    def criar_servico() -> ColetaService:
        # Processo de uma thread só, uma chamada de repositório por vez: uma conexão basta.
        # Cada `registrar` roda na própria transação (autocommit fora dela).
        conn = conectar()
        conexoes.append(conn)
        perfis, vagas = PerfilBuscaRepositoryPostgres(conn), VagaEncontradaRepositoryPostgres(conn)
        ia = PedidoIAService(PedidoIARepositoryPostgres(conn), vagas, perfis, CurriculoRepositoryPostgres(conn))
        return ColetaService(
            RecomendacaoService(BuscaService(providers_padrao())),
            perfis,
            vagas,
            TravaColetaPostgres(),
            ao_terminar=lambda _: print(
                f"ia: {enfileirar_lote(ia, HistoricoService(vagas, perfis))} vagas na fila", file=sys.stdout
            ),
        )

    try:
        return rodar(criar_servico, sys.stdout, sys.stderr)
    finally:
        for conn in conexoes:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
