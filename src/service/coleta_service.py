from dataclasses import dataclass, field
from datetime import date

from src.client.base import palavras, radical
from src.model.erros import DadoInvalido
from src.repository.base import PerfilBuscaRepository, VagaEncontradaRepository
from src.repository.trava import TravaColeta, TravaColetaMemoria
from src.service.busca_service import plataformas_uteis
from src.service.recomendacao_service import RecomendacaoService


@dataclass
class ColetaDoTermo:
    termo: str
    encontradas: int
    novas: int
    erros: dict[str, str] = field(default_factory=dict)


@dataclass
class ResultadoColeta:
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
        return all(len(t.erros) >= self.providers for t in self.termos)


def termos_da_coleta(termos: list[str]) -> list[str]:
    """Termos do perfil sem repetidos pela comparação por palavra.

    Acento, caixa, hífen, gênero e plural não contam: "Front-end", "front end" e "Frontend"
    são o mesmo termo, "Analista" e "analistas" também. Termo que fica vazio depois de
    normalizar ("", "—") é ignorado: buscaria qualquer coisa. Fica a primeira grafia.
    """
    vistos: dict[str, str] = {}
    for termo in termos:
        # Radical de cada palavra, tudo junto: "front end" e "frontend" dão a mesma chave.
        chave = "".join(radical(p) for p in palavras(termo))
        if chave:
            vistos.setdefault(chave, termo.strip())
    return list(vistos.values())


class ColetaService:
    """Roda a recomendação para cada termo do perfil salvo e guarda as vagas no histórico."""

    def __init__(
        self,
        recomendacao: RecomendacaoService,
        perfis: PerfilBuscaRepository,
        vagas: VagaEncontradaRepository,
        trava: TravaColeta | None = None,
    ):
        self.recomendacao = recomendacao
        self.perfis = perfis
        self.vagas = vagas
        # A trava mora no serviço: toda porta de entrada (API, tela, CLI) passa por `coletar`.
        # Sem uma trava compartilhada, vale só dentro deste serviço (testes).
        self.trava = trava if trava is not None else TravaColetaMemoria()

    def coletar(self, hoje: date | None = None) -> ResultadoColeta:
        """Uma coleta por vez: com outra em andamento levanta `ColetaEmAndamento` sem esperar."""
        with self.trava.segurar():
            return self._coletar(hoje)

    def _coletar(self, hoje: date | None) -> ResultadoColeta:
        perfil = self.perfis.obter()
        if perfil is None:
            raise DadoInvalido("Nenhum perfil salvo: salve um perfil com termos (PUT /perfil) antes de coletar")
        termos = termos_da_coleta(perfil.termos)
        if not termos:
            raise DadoInvalido("O perfil salvo não tem termos de busca: inclua pelo menos um em `termos`")
        # Só os providers que as `plataformas` do perfil deixam: os outros nem são chamados.
        consultados = self.recomendacao.busca.clients_para(perfil.plataformas)
        if not consultados and plataformas_uteis(perfil.plataformas):
            raise DadoInvalido(
                "Nenhum provider configurado casa com `plataformas` do perfil: ajuste a lista ou deixe vazia"
            )
        resultado = []
        for termo in termos:
            # A falha de um provider já vem em `erros` (BuscaService); a coleta segue.
            busca = self.recomendacao.recomendar(termo, perfil, hoje)
            novas = self.vagas.registrar(busca.vagas, termo)
            resultado.append(ColetaDoTermo(termo, len(busca.vagas), novas, dict(busca.erros)))
        return ResultadoColeta(resultado, providers=len(consultados))
