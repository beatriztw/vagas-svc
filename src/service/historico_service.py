"""Leitura do histórico da coleta com o perfil salvo aplicado na hora da leitura."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum

from src.model.erros import DadoInvalido
from src.model.pedido_ia import Faixa, NotaIA
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


class Ordem(str, Enum):
    """Ordem da lista: pela nota da IA (as avaliadas primeiro) ou pelo match com o perfil."""

    IA = "ia"
    PERFIL = "perfil"


class FaixaFiltro(str, Enum):
    """Filtro pela faixa da IA: só as vagas DAQUELA faixa (alta 70-100, média 40-69, baixa 0-39), não
    "aquela ou acima" (com o corte acumulado, "média" abria com as altas no topo e parecia não filtrar).
    Vaga sem nota não entra em nenhuma."""

    ALTA = "alta"
    MEDIA = "media"
    BAIXA = "baixa"


_FAIXA_DO_FILTRO = {FaixaFiltro.ALTA: Faixa.ALTA, FaixaFiltro.MEDIA: Faixa.MEDIA, FaixaFiltro.BAIXA: Faixa.BAIXA}


def validar_ordem(valor: str | None) -> Ordem | None:
    if valor is None or not valor.strip():
        return None
    try:
        return Ordem(valor.strip().lower())
    except ValueError:
        raise DadoInvalido("ordem tem que ser ia ou perfil") from None


def validar_faixa(valor: str | None) -> FaixaFiltro | None:
    if valor is None or not valor.strip() or valor.strip().lower() == "todas":
        return None
    try:
        return FaixaFiltro(valor.strip().lower())
    except ValueError:
        raise DadoInvalido("faixa tem que ser alta, media, baixa ou todas") from None


def ordenar_por_nota(vagas: list[VagaEncontrada], notas: Mapping[str, NotaIA]) -> list[VagaEncontrada]:
    """As avaliadas pela IA primeiro, da maior nota para a menor; depois as sem nota. Dentro de cada
    nota (e entre as sem nota) fica a ordem que veio, que já é determinística (match ou recentes).

    A paginação por deslocamento continua sem repetir nem pular entre duas leituras com as mesmas
    notas; uma nota que chega entre duas páginas muda a ordem (a vaga sobe), como uma coleta no meio.
    """
    return sorted(vagas, key=lambda v: -notas[v.url].nota if v.url in notas else 1)


@dataclass
class PaginaHistorico:
    vagas: list[VagaEncontrada]
    # Quantas a lista inteira tem com o mesmo perfil, filtro e busca: o "[N] encontradas".
    total: int
    # A ordem aplicada (a pedida, ou a padrão: IA quando há alguma nota).
    ordem: Ordem = Ordem.PERFIL
    # Quantas de cada faixa ("alta", "media", "baixa") e "todas", com o mesmo perfil, filtro e busca, ANTES do
    # filtro de faixa: o número de cada botão. None no caminho sem perfil e sem IA (a contagem fica no SQL).
    por_faixa: dict[str, int] | None = None


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
    Sem perfil salvo: o repositório direto, como antes (mais recentes primeiro, paginação no SQL),
    mesmo com notas da IA; só a ordem "Compatibilidade (IA)" ou o filtro de faixa pedidos de propósito
    leem a janela de varredura.
    """

    def __init__(self, vagas: VagaEncontradaRepository, perfis: PerfilBuscaRepository):
        self.vagas = vagas
        self.perfis = perfis

    def pagina(
        self,
        somente_novas: bool,
        limite: int,
        q: str | None = None,
        deslocamento: int = 0,
        hoje: date | None = None,
        notas: Mapping[str, NotaIA] | None = None,
        ordem: Ordem | None = None,
        faixa: FaixaFiltro | None = None,
    ) -> PaginaHistorico:
        """`notas` (vaga_url -> nota válida da IA) liga a ordem por IA, que vira a padrão quando há
        alguma nota E perfil salvo; `ordem` força uma das duas. `faixa` deixa só as avaliadas
        naquela faixa. Sem perfil, o padrão continua a paginação no SQL: uma nota avaliada não pode
        cortar o histórico e o contador na janela de varredura."""
        limite = validar_limite(limite)
        deslocamento = validar_deslocamento(deslocamento)
        notas = notas or {}
        # Um perfil lido uma vez por leitura: a página e o total saem do mesmo perfil.
        perfil = self.perfis.obter()
        ordem = ordem or (Ordem.IA if notas and perfil is not None else Ordem.PERFIL)
        if perfil is None and ordem == Ordem.PERFIL and faixa is None:
            return PaginaHistorico(
                vagas=self.vagas.listar(somente_novas=somente_novas, limite=limite, q=q, deslocamento=deslocamento),
                total=self.vagas.contar(somente_novas=somente_novas, q=q),
            )
        janela = self.vagas.listar_recentes(somente_novas=somente_novas, teto=TETO_VARREDURA_HISTORICO, q=q)
        if perfil is None:
            visiveis = janela  # mais recentes primeiro, como o repositório lista
        else:
            visiveis = ordenar_por_match(filtrar_pelo_perfil(janela, perfil, hoje or date.today()), perfil)
        por_faixa = {"todas": len(visiveis), **{f.value: 0 for f in FaixaFiltro}}
        for v in visiveis:
            nota = notas.get(v.url)
            for filtro, da_ia in _FAIXA_DO_FILTRO.items():
                if nota is not None and nota.faixa == da_ia:
                    por_faixa[filtro.value] += 1
        if faixa is not None:
            visiveis = [v for v in visiveis if v.url in notas and notas[v.url].faixa == _FAIXA_DO_FILTRO[faixa]]
        if ordem == Ordem.IA:
            visiveis = ordenar_por_nota(visiveis, notas)
        return PaginaHistorico(
            vagas=visiveis[deslocamento : deslocamento + limite], total=len(visiveis), ordem=ordem, por_faixa=por_faixa
        )

    def salvas(
        self, limite: int, q: str | None = None, apos: tuple[datetime, str] | None = None
    ) -> PaginaHistorico:
        """A aba "Salvas": só as salvas, da salva mais recente para a mais antiga. Sem os filtros do
        perfil (a pessoa salvou de propósito, e mudar o perfil não esconde o que ela guardou), sem a
        ordem nem o corte da IA, e com as expiradas (a tela as marca como encerradas).

        O "carregar mais" continua DEPOIS da última vaga mostrada (`apos` = seu `salva_em` e sua url),
        não por deslocamento: tirar uma vaga dos salvos com a página aberta encolhe a lista, e um
        deslocamento pularia a vaga seguinte. `total` é o da aba inteira."""
        limite = validar_limite(limite)
        salvas = self.vagas.listar_salvas(teto=TETO_VARREDURA_HISTORICO, q=q)
        restantes = salvas
        if apos is not None:
            em, url = apos
            restantes = [v for v in salvas if v.salva_em < em or (v.salva_em == em and v.url > url)]
        return PaginaHistorico(vagas=restantes[:limite], total=len(salvas))

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
