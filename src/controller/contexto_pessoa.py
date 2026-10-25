"""Tudo o que uma requisição usa, montado para a pessoa da sessão.

Os repositórios e serviços NÃO são guardados nas rotas: cada requisição recebe um `ContextoPessoa` novo pela
dependência `contexto_da_requisicao` (criada em `criar_app`), com a pessoa que veio do cookie. Na produção os
repositórios são `_PorChamada`: criar o contexto não segura conexão nenhuma, e cada chamada usa uma do pool.
"""

from dataclasses import dataclass

from src.repository.base import CurriculoRepository, PerfilBuscaRepository, VagaEncontradaRepository
from src.repository.pedido_ia import PedidoIARepository
from src.repository.pessoa_provider import PessoaProviderRepository
from src.service.candidatura_service import CandidaturaService
from src.service.detalhe_service import DetalheService
from src.service.historico_service import HistoricoService
from src.service.pedido_ia_service import PedidoIAService


@dataclass
class ContextoPessoa:
    pessoa_id: int
    nome_exibicao: str
    perfis: PerfilBuscaRepository
    curriculos: CurriculoRepository
    encontradas: VagaEncontradaRepository
    pedidos_ia: PedidoIARepository
    providers: PessoaProviderRepository
    historico: HistoricoService
    candidaturas: CandidaturaService
    detalhe: DetalheService
    ia: PedidoIAService
