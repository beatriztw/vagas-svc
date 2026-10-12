"""Leitura do histórico da coleta com o perfil salvo aplicado na hora da leitura."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timezone

from src.client.base import _LIGACAO, palavras, radical
from src.model.erros import DadoInvalido
from src.model.perfil import Perfil
from src.model.vaga_encontrada import VagaEncontrada
from src.repository.base import (
    PerfilBuscaRepository,
    VagaEncontradaRepository,
    validar_ate,
    validar_deslocamento,
    validar_limite,
)
from src.service.recomendacao_service import filtrar_pelo_perfil, pontuar, relevancia_perfil

# Janela de varredura com perfil: as vagas coletadas mais recentemente, lidas do banco por
# leitura e filtradas e ordenadas em Python. O histórico é pessoal (centenas, no máximo poucos
# milhares); o que passar disso fica fora da lista, do contador e da busca até sair da janela.
TETO_VARREDURA_HISTORICO = 5000

_SEM_DATA = datetime.min.replace(tzinfo=timezone.utc)

MAX_TAMANHO_ATE = 64


def ler_ate(texto: str | None) -> datetime | None:
    """`ate` da query string: instante ISO 8601 COM fuso (`2026-10-12T03:20:00.123456+00:00` ou `...Z`).

    Vazio ou ausente é None (sem foto: o serviço tira uma agora). Qualquer outra coisa, inclusive sem
    fuso ou com o `+` do fuso virado espaço por uma url sem codificar, é `DadoInvalido` (422).
    Devolve o instante em UTC.
    """
    texto = (texto or "").strip()
    if not texto:
        return None
    if len(texto) > MAX_TAMANHO_ATE:
        raise DadoInvalido("ate tem que ser um instante ISO 8601 com fuso, como 2026-10-12T03:20:00+00:00")
    try:
        instante = datetime.fromisoformat(texto)
        if instante.tzinfo is None or instante.utcoffset() is None:
            raise ValueError("sem fuso")
        return instante.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise DadoInvalido("ate tem que ser um instante ISO 8601 com fuso, como 2026-10-12T03:20:00+00:00") from None


@dataclass
class PaginaHistorico:
    vagas: list[VagaEncontrada]
    # Quantas a lista inteira tem com o mesmo perfil, filtro e busca: o "[N] encontradas".
    total: int
    # O instante da foto: o "carregar mais" volta com ele para a janela não mudar entre as páginas.
    ate: datetime


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


def chave_do_grupo(vaga: VagaEncontrada) -> tuple[str, str] | None:
    """Título e empresa por palavra (sem acento, caixa, hífen, gênero, plural nem palavra de ligação).

    O mesmo anúncio republicado com outra URL tem a mesma chave. Sem palavra útil no título ou na
    empresa a chave é None e a vaga não agrupa: duas "Analista de Dados" sem empresa são, com boa
    chance, de empresas diferentes. Nível e outras palavras do título contam: "Sênior" não agrupa com "Pleno".
    """
    # Radicais colados: "front end" = "front-end" = "frontend", como na busca (CLAUDE.md).
    titulo = "".join(radical(p) for p in palavras(vaga.titulo) if p not in _LIGACAO)
    empresa = "".join(radical(p) for p in palavras(vaga.empresa))
    return (titulo, empresa) if titulo and empresa else None


def agrupar(ordenadas: list[VagaEncontrada]) -> list[VagaEncontrada]:
    """Uma vaga por grupo de `chave_do_grupo`, na posição do membro mais bem ranqueado (a lista já vem
    ordenada) e mostrando o anúncio mais recente (`primeira_vez_em`; a URL desempata)."""
    grupos: dict[object, list[VagaEncontrada]] = {}
    for vaga in ordenadas:
        # Sem chave, a própria URL: cada vaga é um grupo de um.
        grupos.setdefault(chave_do_grupo(vaga) or ("url", vaga.url), []).append(vaga)
    return [max(sorted(g, key=lambda v: v.url), key=lambda v: v.primeira_vez_em or _SEM_DATA) for g in grupos.values()]


class HistoricoService:
    """O histórico obedece ao perfil ATUAL: mudar o perfil muda o que já foi coletado, sem migration.

    Com perfil salvo: lê a janela de varredura (`TETO_VARREDURA_HISTORICO` mais recentes, com o
    filtro novas e o `q`), aplica os MESMOS filtros da busca ao vivo (`filtrar_pelo_perfil`),
    ordena por match e só então pagina e conta, então contador, páginas e "carregar mais" batem.
    Sem perfil salvo: a mesma janela, na ordem do repositório (mais recentes primeiro).

    A janela é uma foto do instante (`ate`): a primeira página a tira, o "carregar mais" a repete.
    Vaga coletada depois dela não entra, e vaga vista depois dela continua na janela de Novas, então
    as páginas não repetem nem pulam item.

    Anúncios repetidos (mesmo título e empresa, URLs diferentes) viram UMA vaga na lista e no
    contador: o agrupamento vem depois de filtrar e ordenar e antes de paginar e contar. O grupo
    fica na posição do membro mais bem ranqueado e mostra o anúncio mais recente; os outros são
    `outros_anuncios` da vaga.
    """

    def __init__(
        self,
        vagas: VagaEncontradaRepository,
        perfis: PerfilBuscaRepository,
        candidatadas: Callable[[], Iterable] | None = None,
    ):
        # `candidatadas` devolve as candidaturas (com `vaga_url`, `titulo` e `empresa`), lidas a cada
        # leitura; sem ele, `ocultar_candidatadas` não tem o que esconder.
        self.vagas = vagas
        self.perfis = perfis
        self.candidatadas = candidatadas

    def _sem_candidatadas(self, vagas: list[VagaEncontrada], perfil: Perfil) -> list[VagaEncontrada]:
        """Com `ocultar_candidatadas`, tira as vagas em que a pessoa aplicou E os outros anúncios do mesmo
        grupo (mesmo título e empresa): senão a duplicata da vaga aplicada voltaria como nova."""
        if not perfil.ocultar_candidatadas or self.candidatadas is None:
            return vagas
        candidaturas = list(self.candidatadas())
        urls = {c.vaga_url for c in candidaturas}
        chaves = {k for c in candidaturas if (k := chave_do_grupo(c)) is not None}
        return [v for v in vagas if v.url not in urls and chave_do_grupo(v) not in chaves]

    def pagina(
        self,
        somente_novas: bool,
        limite: int,
        q: str | None = None,
        deslocamento: int = 0,
        hoje: date | None = None,
        ate: datetime | None = None,
    ) -> PaginaHistorico:
        """Sem `ate` (primeira página) a foto é tirada agora; com `ate` (as seguintes), a mesma da primeira."""
        limite = validar_limite(limite)
        deslocamento = validar_deslocamento(deslocamento)
        ate = validar_ate(ate) or datetime.now(timezone.utc)
        # Um perfil lido uma vez por leitura: a página e o total saem do mesmo perfil.
        perfil = self.perfis.obter()
        janela = self.vagas.listar_recentes(
            somente_novas=somente_novas, teto=TETO_VARREDURA_HISTORICO, q=q, ate=ate
        )
        if perfil is not None:
            janela = filtrar_pelo_perfil(janela, perfil, hoje or date.today())
            janela = ordenar_por_match(self._sem_candidatadas(janela, perfil), perfil)
        visiveis = agrupar(janela)
        return PaginaHistorico(vagas=visiveis[deslocamento : deslocamento + limite], total=len(visiveis), ate=ate)

    def outros_anuncios(self, vaga: VagaEncontrada, hoje: date | None = None) -> list[VagaEncontrada]:
        """Os outros anúncios do mesmo grupo da vaga (sem ela), o mais recente primeiro.

        Lê o histórico inteiro, sem Novas nem busca, com o perfil aplicado: o que o detalhe lista é o que
        o agrupamento da lista juntou. A própria vaga não precisa passar no perfil (link direto abre).
        """
        chave = chave_do_grupo(vaga)
        if chave is None:
            return []
        todas = self.vagas.listar_recentes(somente_novas=False, teto=TETO_VARREDURA_HISTORICO, q=None)
        perfil = self.perfis.obter()
        if perfil is not None:
            todas = filtrar_pelo_perfil(todas, perfil, hoje or date.today())
        iguais = [v for v in todas if v.url != vaga.url and chave_do_grupo(v) == chave]
        return sorted(sorted(iguais, key=lambda v: v.url), key=lambda v: v.primeira_vez_em or _SEM_DATA, reverse=True)

    def urls_do_grupo(self, vaga: VagaEncontrada, hoje: date | None = None) -> list[str]:
        """A URL da vaga e as dos outros anúncios do grupo: marcar vista marca tudo isso."""
        return [vaga.url, *(v.url for v in self.outros_anuncios(vaga, hoje))]

    def listar(
        self,
        somente_novas: bool,
        limite: int,
        q: str | None = None,
        deslocamento: int = 0,
        hoje: date | None = None,
        ate: datetime | None = None,
    ) -> list[VagaEncontrada]:
        return self.pagina(somente_novas, limite, q, deslocamento, hoje, ate).vagas

    def contar(
        self, somente_novas: bool, q: str | None = None, hoje: date | None = None, ate: datetime | None = None
    ) -> int:
        return self.pagina(somente_novas, 1, q, 0, hoje, ate).total
