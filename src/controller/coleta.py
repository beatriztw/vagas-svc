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


def rodar(
    criar_servico: Callable[[], ColetaService],
    saida: TextIO,
    erro: TextIO,
    pos_coleta: Callable[[], int] | None = None,
) -> int:
    """Roda uma coleta e imprime o resumo; devolve o código de saída.

    `pos_coleta` (o lote da IA) roda só depois de uma coleta bem-sucedida, DEPOIS das linhas dos
    termos, e imprime em `saida` quantas vagas foram para a fila. Falha nele vai para `erro` e não
    muda o código de saída: a coleta já gravou."""
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
    if pos_coleta is not None:
        try:
            print(f"ia: {pos_coleta()} vagas na fila", file=saida)
        except Exception as e:  # noqa: BLE001 - o lote nunca derruba a coleta
            print(f"ia: lote falhou: {type(e).__name__}", file=erro)
    return 0


def main() -> int:
    from src.controller.api import PESSOA_PADRAO, providers_padrao
    from src.repository.conexao import conectar
    from src.repository.trava import CHAVE_TRAVA_LOTE_IA, TravaColetaPostgres
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
    lote = []

    def criar_servico() -> ColetaService:
        # Processo de uma thread só, uma chamada de repositório por vez: uma conexão basta.
        # Cada `registrar` roda na própria transação (autocommit fora dela).
        conn = conectar()
        conexoes.append(conn)
        perfis = PerfilBuscaRepositoryPostgres(conn, PESSOA_PADRAO)
        vagas = VagaEncontradaRepositoryPostgres(conn, PESSOA_PADRAO)
        ia = PedidoIAService(
            PedidoIARepositoryPostgres(conn, PESSOA_PADRAO),
            vagas,
            perfis,
            CurriculoRepositoryPostgres(conn, PESSOA_PADRAO),
            trava_lote=TravaColetaPostgres(chave=CHAVE_TRAVA_LOTE_IA),
        )
        lote.append(lambda: enfileirar_lote(ia, HistoricoService(vagas, perfis)))
        return ColetaService(RecomendacaoService(BuscaService(providers_padrao())), perfis, vagas, TravaColetaPostgres())

    try:
        return rodar(criar_servico, sys.stdout, sys.stderr, pos_coleta=lambda: lote[-1]())
    finally:
        for conn in conexoes:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
