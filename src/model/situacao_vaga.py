from enum import Enum


class SituacaoNaFonte(str, Enum):
    """O que a fonte diz de uma vaga numa conferência (`ConferenciaService`). Só FECHADA marca a vaga
    como encerrada; as outras deixam a vaga como está."""

    ABERTA = "aberta"
    FECHADA = "fechada"
    # Falha de rede, resposta torta ou página sem os sinais esperados: fica para a próxima conferência.
    DESCONHECIDA = "desconhecida"
    # A fonte pediu para parar (429): o resto das vagas dela fica para a próxima conferência.
    BLOQUEADA = "bloqueada"
