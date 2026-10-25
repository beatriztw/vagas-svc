import logging
from collections.abc import Callable, Hashable
from dataclasses import dataclass, field
from datetime import date

from src.client.base import palavras, radical
from src.model.erros import DadoInvalido
from src.repository.base import CurriculoRepository, PerfilBuscaRepository, VagaEncontradaRepository
from src.repository.candidatura import CandidaturaRepository
from src.repository.pedido_ia import PedidoIARepository
from src.repository.pessoa import PessoaRepository, PessoaRepositoryMemoria
from src.repository.trava import TravaColeta, TravaColetaMemoria
from src.service.busca_service import BuscaService, ResultadoBusca
from src.service.providers_pessoa import PROVIDERS_DO_AMBIENTE, ProvidersDaPessoa
from src.service.recomendacao_service import RecomendacaoService

log = logging.getLogger(__name__)


@dataclass
class ColetaDoTermo:
    termo: str
    encontradas: int
    novas: int
    erros: dict[str, str] = field(default_factory=dict)


@dataclass
class ColetaDaPessoa:
    """O que a coleta fez por uma pessoa. Só o id: o nome nunca sai do banco."""

    pessoa_id: int
    termos: list[ColetaDoTermo]
    # Quantos providers a busca consulta: com todos em `erros` o termo não foi buscado de fato.
    providers: int

    @property
    def novas(self) -> int:
        return sum(t.novas for t in self.termos)

    @property
    def falhou(self) -> bool:
        """Todos os providers falharam em todos os termos: nada foi buscado de verdade.

        Erro de alguns providers, com outro respondendo (mesmo sem vaga), não é falha.
        """
        return bool(self.termos) and all(len(t.erros) >= self.providers for t in self.termos)


@dataclass
class ResultadoColeta:
    pessoas: list[ColetaDaPessoa]
    # Quantos providers a busca consulta: com todos em `erros` o termo não foi buscado de fato.
    providers: int
    # Pessoas sem perfil ou sem termos: puladas, a coleta das outras segue.
    puladas: list[int] = field(default_factory=list)
    # Pessoas cuja coleta deu erro (ex.: banco no meio): as outras seguem; o que já gravou fica.
    com_erro: list[int] = field(default_factory=list)
    # O erro de cada pessoa de `com_erro`: quem pede o resultado dela (a API e a tela) recebe o erro, não um vazio.
    erros: dict[int, Exception] = field(default_factory=dict, repr=False)

    @property
    def termos(self) -> list[ColetaDoTermo]:
        """Os termos de todas as pessoas coletadas, na ordem das pessoas (para contagens somadas)."""
        return [t for p in self.pessoas for t in p.termos]

    @property
    def novas(self) -> int:
        return sum(p.novas for p in self.pessoas)

    @property
    def falhou(self) -> bool:
        """Nenhuma pessoa teve termo buscado de verdade (ver `ColetaDaPessoa.falhou`)."""
        return bool(self.pessoas) and all(p.falhou for p in self.pessoas)

    def da_pessoa(self, pessoa_id: int) -> ColetaDaPessoa:
        """A coleta da pessoa; vazia (sem termos, sem falha) se ela foi pulada. Levanta o erro dela se a coleta dela
        deu erro (as outras pessoas já coletaram e tiveram o lote enfileirado)."""
        if pessoa_id in self.erros:
            raise self.erros[pessoa_id]
        for p in self.pessoas:
            if p.pessoa_id == pessoa_id:
                return p
        return ColetaDaPessoa(pessoa_id, [], self.providers)


@dataclass
class RepositoriosDaPessoa:
    """Os repositórios de UMA pessoa. Na produção são `_PorChamada`: cada chamada pega a própria conexão do pool.
    `curriculos` e `pedidos_ia` só servem a quem enfileira o lote da IA; a coleta usa `perfis` e `vagas`;
    `candidaturas` serve às rotas da pessoa da sessão."""

    perfis: PerfilBuscaRepository
    vagas: VagaEncontradaRepository
    curriculos: CurriculoRepository | None = None
    pedidos_ia: PedidoIARepository | None = None
    candidaturas: CandidaturaRepository | None = None


def chave_do_termo(termo: str) -> str:
    """Chave de comparação do termo: radical de cada palavra, tudo junto. "front end", "Front-end" e
    "frontend" dão a mesma chave; vazia = o termo não diz nada ("", "—")."""
    return "".join(radical(p) for p in palavras(termo))


def termos_da_coleta(termos: list[str]) -> list[str]:
    """Termos do perfil sem repetidos pela comparação por palavra.

    Acento, caixa, hífen, gênero e plural não contam: "Front-end", "front end" e "Frontend"
    são o mesmo termo, "Analista" e "analistas" também. Termo que fica vazio depois de
    normalizar ("", "—") é ignorado: buscaria qualquer coisa. Fica a primeira grafia.
    """
    vistos: dict[str, str] = {}
    for termo in termos:
        chave = chave_do_termo(termo)
        if chave:
            vistos.setdefault(chave, termo.strip())
    return list(vistos.values())


class ColetaService:
    """Roda a recomendação para cada termo do perfil de cada pessoa e guarda as vagas no histórico dela."""

    def __init__(
        self,
        recomendacao: RecomendacaoService,
        pessoas: PessoaRepository,
        repositorios: Callable[[int], RepositoriosDaPessoa],
        trava: TravaColeta | None = None,
        ao_terminar: Callable[[ColetaDaPessoa], object] | None = None,
        buscas: ProvidersDaPessoa | None = None,
    ):
        self.recomendacao = recomendacao
        # Os providers de cada pessoa. Sem isto (testes), todas usam os da `recomendacao` (o ambiente).
        self.buscas = buscas
        self.pessoas = pessoas
        # Os repositórios de cada pessoa saem de uma função, não de um objeto: na produção cada chamada pega a
        # própria conexão do pool (uma conexão compartilhada misturaria transações).
        self.repositorios = repositorios
        # A trava mora no serviço: toda porta de entrada (API, tela, CLI) passa por `coletar`.
        # Sem uma trava compartilhada, vale só dentro deste serviço (testes).
        self.trava = trava if trava is not None else TravaColetaMemoria()
        # Roda depois da coleta, uma vez por pessoa coletada sem falha (ex.: enfileirar o lote da IA dela).
        # Falha aqui só vai para o log, com o id: a coleta já gravou e não pode virar erro por causa disso.
        self.ao_terminar = ao_terminar

    @classmethod
    def de_uma_pessoa(
        cls,
        recomendacao: RecomendacaoService,
        perfis: PerfilBuscaRepository,
        vagas: VagaEncontradaRepository,
        trava: TravaColeta | None = None,
        ao_terminar: Callable[[ColetaDaPessoa], object] | None = None,
        pessoa_id: int = 1,
    ) -> "ColetaService":
        """Um app com uma pessoa só (testes e `criar_app` sem Postgres)."""
        return cls(
            recomendacao,
            PessoaRepositoryMemoria([pessoa_id]),
            lambda _: RepositoriosDaPessoa(perfis, vagas),
            trava,
            ao_terminar,
        )

    def coletar(self, hoje: date | None = None) -> ResultadoColeta:
        """Uma coleta por vez: com outra em andamento levanta `ColetaEmAndamento` sem esperar.

        Todas as pessoas rodam dentro da MESMA trava, uma depois da outra."""
        with self.trava.segurar():
            resultado = self._coletar(hoje)
        if self.ao_terminar is not None:
            for da_pessoa in resultado.pessoas:
                if da_pessoa.falhou:
                    continue
                try:
                    self.ao_terminar(da_pessoa)
                except Exception as e:  # noqa: BLE001 - o pós-coleta nunca derruba a coleta
                    log.warning("pós-coleta da pessoa %s falhou: %s", da_pessoa.pessoa_id, type(e).__name__)
        return resultado

    def _busca_da_pessoa(self, pessoa_id: int) -> tuple[Hashable, BuscaService]:
        """Os providers da pessoa e a chave que os identifica no cache: `PROVIDERS_DO_AMBIENTE` quando vale o
        ambiente, senão a config dela (igual para configs iguais)."""
        if self.buscas is None:
            return PROVIDERS_DO_AMBIENTE, self.recomendacao.busca
        return self.buscas.busca(pessoa_id)

    @staticmethod
    def _buscar(
        cache: dict[tuple[Hashable, str], ResultadoBusca], providers: Hashable, busca: BuscaService, termo: str
    ) -> ResultadoBusca:
        """Resultado CRU dos providers para o termo, buscado uma vez por `(providers, termo normalizado)` durante
        a coleta (o Apify é pago). Vem sem filtro de perfil: o filtro é de cada pessoa e se aplica depois."""
        chave = (providers, chave_do_termo(termo))
        if chave not in cache:
            cache[chave] = busca.buscar(termo)
        cru = cache[chave]
        # Cópia: quem recebe filtra e ordena à vontade sem mexer no que as outras pessoas vão ler.
        return ResultadoBusca(vagas=list(cru.vagas), erros=dict(cru.erros))

    def _coletar(self, hoje: date | None) -> ResultadoColeta:
        cache: dict[tuple[Hashable, str], ResultadoBusca] = {}
        coletadas: list[ColetaDaPessoa] = []
        puladas: list[int] = []
        com_erro: list[int] = []
        erros: dict[int, Exception] = {}
        ultimo_erro: Exception | None = None
        algum_perfil = False
        for pessoa_id in self.pessoas.listar():
            try:
                repos = self.repositorios(pessoa_id)
                perfil = repos.perfis.obter()
                termos = termos_da_coleta(perfil.termos) if perfil is not None else []
                algum_perfil = algum_perfil or perfil is not None
                if not termos:
                    # Sem perfil ou sem termos: pula, e as outras pessoas seguem.
                    puladas.append(pessoa_id)
                    continue
                # Uma vez por pessoa: a config não muda no meio da coleta dela (e um erro ao lê-la é só dela).
                providers, busca_da_pessoa = self._busca_da_pessoa(pessoa_id)
                resultado = []
                for termo in termos:
                    # A falha de um provider já vem em `erros` (BuscaService); a coleta segue.
                    busca = self.recomendacao.refinar(
                        self._buscar(cache, providers, busca_da_pessoa, termo), termo, perfil, hoje
                    )
                    novas = repos.vagas.registrar(busca.vagas, termo)
                    resultado.append(ColetaDoTermo(termo, len(busca.vagas), novas, dict(busca.erros)))
            except Exception as e:  # noqa: BLE001 - erro de uma pessoa (ex.: banco) não derruba as outras
                # Só o id e o tipo: a mensagem pode trazer termo ou dado do perfil.
                log.warning("coleta da pessoa %s falhou: %s", pessoa_id, type(e).__name__)
                com_erro.append(pessoa_id)
                erros[pessoa_id] = e
                ultimo_erro = e
                continue
            coletadas.append(ColetaDaPessoa(pessoa_id, resultado, providers=len(busca_da_pessoa.clients)))
        if not coletadas:
            # Ninguém coletou e alguém deu erro (ex.: banco fora para todos): a coleta inteira falhou.
            if ultimo_erro is not None:
                raise ultimo_erro
            if algum_perfil:
                raise DadoInvalido("O perfil salvo não tem termos de busca: inclua pelo menos um em `termos`")
            raise DadoInvalido("Nenhum perfil salvo: salve um perfil com termos (PUT /perfil) antes de coletar")
        return ResultadoColeta(
            coletadas, providers=len(self.recomendacao.busca.clients), puladas=puladas, com_erro=com_erro, erros=erros
        )
