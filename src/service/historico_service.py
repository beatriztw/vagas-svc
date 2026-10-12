"""Leitura do histórico da coleta com o perfil salvo aplicado na hora da leitura."""

from dataclasses import dataclass
from datetime import date, datetime, timezone

from src.model.perfil import Perfil
from src.model.vaga_encontrada import VagaEncontrada
from src.repository.base import (
    PerfilBuscaRepository,
    VagaEncontradaRepository,
    validar_deslocamento,
    validar_limite,
)
from src.service.recomendacao_service import filtrar_pelo_perfil, pontuar, relevancia_perfil

# Janela de varredura com perfil: as vagas coletadas mais recentemente, lidas do banco por
# leitura e filtradas e ordenadas em Python. O histórico é pessoal (centenas, no máximo poucos
# milhares); o que passar disso fica fora da lista, do contador e da busca até sair da janela.
TETO_VARREDURA_HISTORICO = 5000

_SEM_DATA = datetime.min.replace(tzinfo=timezone.utc)


@dataclass
class PaginaHistorico:
    vagas: list[VagaEncontrada]
    # Quantas a lista inteira tem com o mesmo perfil, filtro e busca: o "[N] encontradas".
    total: int


def ordenar_por_match(vagas: list[VagaEncontrada], perfil: Perfil) -> list[VagaEncontrada]:
    """Título mais parecido com algum termo do perfil primeiro, depois mais skills no título,
    depois `publicada_em` mais nova (sem data por último), depois `primeira_vez_em` mais nova.

    A URL desempata o resto: a ordem não depende da ordem de leitura, e a paginação não repete
    nem pula vaga entre duas leituras do mesmo histórico.
    """

    def chave(v: VagaEncontrada):
        return (
            relevancia_perfil(v.titulo, perfil.termos),
            pontuar(v, perfil.skills),
            v.publicada_em is not None,
            v.publicada_em or date.min,
            v.primeira_vez_em or _SEM_DATA,
        )

    # sorted é estável: primeiro a URL crescente, depois a chave decrescente por cima.
    return sorted(sorted(vagas, key=lambda v: v.url), key=chave, reverse=True)


class HistoricoService:
    """O histórico obedece ao perfil ATUAL: mudar o perfil muda o que já foi coletado, sem migration.

    Com perfil salvo: lê a janela de varredura (`TETO_VARREDURA_HISTORICO` mais recentes, com o
    filtro novas e o `q`), aplica os MESMOS filtros da busca ao vivo (`filtrar_pelo_perfil`),
    ordena por match e só então pagina e conta, então contador, páginas e "carregar mais" batem.
    Sem perfil salvo: o repositório direto, como antes (mais recentes primeiro, paginação no SQL).
    """

    def __init__(self, vagas: VagaEncontradaRepository, perfis: PerfilBuscaRepository):
        self.vagas = vagas
        self.perfis = perfis

    def pagina(
        self, somente_novas: bool, limite: int, q: str | None = None, deslocamento: int = 0, hoje: date | None = None
    ) -> PaginaHistorico:
        limite = validar_limite(limite)
        deslocamento = validar_deslocamento(deslocamento)
        # Um perfil lido uma vez por leitura: a página e o total saem do mesmo perfil.
        perfil = self.perfis.obter()
        if perfil is None:
            return PaginaHistorico(
                vagas=self.vagas.listar(somente_novas=somente_novas, limite=limite, q=q, deslocamento=deslocamento),
                total=self.vagas.contar(somente_novas=somente_novas, q=q),
            )
        janela = self.vagas.listar_recentes(somente_novas=somente_novas, teto=TETO_VARREDURA_HISTORICO, q=q)
        visiveis = ordenar_por_match(filtrar_pelo_perfil(janela, perfil, hoje or date.today()), perfil)
        return PaginaHistorico(vagas=visiveis[deslocamento : deslocamento + limite], total=len(visiveis))

    def visiveis(self, hoje: date | None = None) -> list[VagaEncontrada]:
        """A lista inteira, sem busca e sem o filtro de novas, na ordem da tela: com perfil, as que
        passam nele, por match; sem perfil, as mais recentes. Nunca as expiradas. Teto da varredura."""
        janela = self.vagas.listar_recentes(somente_novas=False, teto=TETO_VARREDURA_HISTORICO, q=None)
        perfil = self.perfis.obter()
        if perfil is None:
            return janela
        return ordenar_por_match(filtrar_pelo_perfil(janela, perfil, hoje or date.today()), perfil)

    def listar(
        self, somente_novas: bool, limite: int, q: str | None = None, deslocamento: int = 0, hoje: date | None = None
    ) -> list[VagaEncontrada]:
        return self.pagina(somente_novas, limite, q, deslocamento, hoje).vagas

    def contar(self, somente_novas: bool, q: str | None = None, hoje: date | None = None) -> int:
        return self.pagina(somente_novas, 1, q, 0, hoje).total
