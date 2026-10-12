from abc import ABC, abstractmethod
from dataclasses import replace
from datetime import date, datetime, timezone

from src.client.base import _LIGACAO, _equivalentes, _formas_do_titulo, palavras, radical
from src.model.curriculo import Curriculo
from src.model.erros import DadoInvalido
from src.model.perfil import Perfil
from src.model.vaga import Modalidade, Nivel
from src.model.vaga_encontrada import VagaEncontrada
from src.model.vaga_externa import VagaExterna


class PerfilBuscaRepository(ABC):
    """Perfil de busca único (linha id=1)."""

    @abstractmethod
    def obter(self) -> Perfil | None: ...

    @abstractmethod
    def salvar(self, perfil: Perfil) -> None:
        """Grava por cima do perfil existente (upsert da linha id=1)."""


class CurriculoRepository(ABC):
    @abstractmethod
    def obter_base(self) -> Curriculo | None:
        """Base vigente: o currículo tipo='base' mais recente."""

    @abstractmethod
    def salvar(self, curriculo: Curriculo) -> Curriculo:
        """Insere uma nova linha e devolve o currículo com id e criado_em preenchidos."""

    @abstractmethod
    def obter(self, id: int) -> Curriculo | None: ...

    @abstractmethod
    def listar_adaptados(self) -> list[Curriculo]:
        """Adaptados do mais recente para o mais antigo."""


# Teto de GET /vagas/encontradas: a lista inteira de uma vez não serve para ler.
MAX_LIMITE_VAGAS = 500
# Teto do "carregar mais": ninguém rola dez mil vagas, e o OFFSET enorme só custa.
MAX_DESLOCAMENTO_VAGAS = 10_000
# Teto do texto da busca: mais que isso não é uma busca digitada.
MAX_TAMANHO_BUSCA = 200


class VagaEncontradaRepository(ABC):
    """Histórico da coleta: uma linha por URL de vaga."""

    @abstractmethod
    def registrar(self, vagas: list[VagaExterna], termo: str) -> int:
        """Upsert por URL numa transação só; devolve quantas vagas são novas.

        Vaga que já existia atualiza `ultima_vez_em`, os campos que vieram preenchidos e
        ganha o termo em `termos` (sem repetir); `primeira_vez_em` e `vista_em` ficam.
        """

    @abstractmethod
    def listar(
        self, somente_novas: bool, limite: int, q: str | None = None, deslocamento: int = 0
    ) -> list[VagaEncontrada]:
        """Mais recentes (`primeira_vez_em`) primeiro; `somente_novas` deixa só as não vistas.

        `q` filtra o histórico por palavra (ver `casa_busca`); `deslocamento` pula as primeiras
        já filtradas, para o "carregar mais".
        """

    @abstractmethod
    def contar(self, somente_novas: bool, q: str | None = None) -> int:
        """Quantas vagas `listar` devolveria sem limite nem deslocamento."""

    @abstractmethod
    def listar_recentes(
        self, somente_novas: bool, teto: int, q: str | None = None, ate: datetime | None = None
    ) -> list[VagaEncontrada]:
        """A janela de varredura do histórico: as `teto` vagas mais recentes na ordem de `listar`
        (já com o filtro `somente_novas`) e, dentro delas, as que casam com `q`.

        O teto vale antes do `q`, para o custo ficar limitado pelo teto nos dois repositórios.

        `ate` (datetime com fuso) é a foto do instante: só entra o que já existia nele
        (`primeira_vez_em <= ate`) e, em Novas, o que ainda não tinha sido visto nele
        (`vista_em IS NULL OR vista_em > ate`). Assim uma coleta ou uma vista depois de `ate` não muda
        a janela e as páginas do "carregar mais" não pulam nem repetem. Sem `ate`, sem foto: o filtro
        de `listar` (Novas = `vista_em IS NULL`), sem limite de data.
        """

    @abstractmethod
    def obter(self, url: str) -> VagaEncontrada | None:
        """A vaga pela URL exatamente como foi gravada (é a chave: não se normaliza); None se não existe."""

    @abstractmethod
    def marcar_vistas(self, urls: list[str]) -> int:
        """Marca como vistas as que ainda não estavam; devolve quantas mudaram."""


def validar_termo(termo: str) -> str:
    """Termo da coleta sem espaços nas pontas; vazio é recusado antes de gravar."""
    if not isinstance(termo, str) or not termo.strip():
        raise DadoInvalido("termo da coleta não pode ser vazio")
    return termo.strip()


def validar_limite(limite: int) -> int:
    # bool é int em Python: True não é um limite.
    if isinstance(limite, bool) or not isinstance(limite, int) or not 1 <= limite <= MAX_LIMITE_VAGAS:
        raise DadoInvalido(f"limite tem que estar entre 1 e {MAX_LIMITE_VAGAS}")
    return limite


def validar_deslocamento(deslocamento: int) -> int:
    if (
        isinstance(deslocamento, bool)
        or not isinstance(deslocamento, int)
        or not 0 <= deslocamento <= MAX_DESLOCAMENTO_VAGAS
    ):
        raise DadoInvalido(f"deslocamento tem que estar entre 0 e {MAX_DESLOCAMENTO_VAGAS}")
    return deslocamento


def exigidas_da_busca(q: str | None) -> list[str]:
    """Radicais das palavras úteis de `q`; vazio quando não há filtro (None, "", só pontuação ou só ligação)."""
    if q is None:
        return []
    if not isinstance(q, str) or len(q) > MAX_TAMANHO_BUSCA:
        raise DadoInvalido(f"q tem que ser um texto de até {MAX_TAMANHO_BUSCA} caracteres")
    return [radical(p) for p in palavras(q) if p not in _LIGACAO]


def casa_busca(vaga: VagaEncontrada, exigidas: list[str]) -> bool:
    """Toda palavra de `q` casa com uma palavra de título, empresa ou local (E entre elas).

    Mesma comparação da relevância da recomendação: palavra inteira por radical, nunca por
    pedaço (`java` não casa com JavaScript, `sql` não casa com NoSQL), sem acento, caixa,
    hífen, gênero e plural; "front end" = "front-end" = "frontend". Sem palavras, não filtra.
    """
    formas: set[str] = set()
    for campo in (vaga.titulo, vaga.empresa, vaga.local):
        # Por campo: o par vizinho não atravessa do fim do título para o começo da empresa.
        formas.update(_formas_do_titulo(campo or ""))
    i = 0
    while i < len(exigidas):
        if i + 1 < len(exigidas) and (exigidas[i] + exigidas[i + 1]) in formas:
            i += 2
            continue
        if not _equivalentes(exigidas[i]) & formas:
            return False
        i += 1
    return True


def filtrar_e_ordenar(
    linhas: list[VagaEncontrada], somente_novas: bool, q: str | None
) -> list[VagaEncontrada]:
    """O filtro e a ordem do contrato, em Python (a memória usa direto)."""
    exigidas = exigidas_da_busca(q)
    linhas = [
        v for v in linhas if (not somente_novas or v.vista_em is None) and casa_busca(v, exigidas)
    ]
    # Mesma ordem do Postgres: primeira_vez_em desc, publicada_em desc (sem data por último), url.
    return sorted(sorted(linhas, key=lambda v: v.url), key=_chave_listagem, reverse=True)


def validar_teto(teto: int) -> int:
    if isinstance(teto, bool) or not isinstance(teto, int) or teto < 1:
        raise DadoInvalido("teto da varredura tem que ser um inteiro positivo")
    return teto


def validar_ate(ate: datetime | None) -> datetime | None:
    """A foto do instante tem que ter fuso: sem ele a comparação com `timestamptz` dependeria do servidor."""
    if ate is not None and (not isinstance(ate, datetime) or ate.tzinfo is None or ate.utcoffset() is None):
        raise DadoInvalido("ate tem que ser um instante com fuso")
    return ate


def na_janela(vaga: VagaEncontrada, somente_novas: bool, ate: datetime) -> bool:
    """A regra da foto do instante (ver `listar_recentes`), em Python, para a memória."""
    if vaga.primeira_vez_em is None or vaga.primeira_vez_em > ate:
        return False
    return not somente_novas or vaga.vista_em is None or vaga.vista_em > ate


def validar_urls(urls: list[str]) -> list[str]:
    if not isinstance(urls, list) or not all(isinstance(u, str) for u in urls):
        raise DadoInvalido("urls tem que ser uma lista de textos")
    return _sem_repetidos(u for u in urls if u)


def _texto(valor) -> bool:
    # O Postgres não aceita NUL em TEXT: um título assim derrubaria o registro do termo inteiro.
    return isinstance(valor, str) and "\x00" not in valor


def vagas_validas(vagas: list[VagaExterna]) -> list[VagaExterna]:
    """Vagas que dá para gravar, uma por URL (a primeira).

    Dado de provider: o item sem URL ou com campo obrigatório que não é texto é descartado
    sozinho, sem derrubar o registro das outras. A URL vale como veio (é a chave).
    """
    validas: dict[str, VagaExterna] = {}
    for vaga in vagas:
        if not isinstance(vaga, VagaExterna) or not _texto(vaga.url) or not vaga.url.strip():
            continue
        if not (_texto(vaga.titulo) and _texto(vaga.empresa) and _texto(vaga.plataforma)):
            continue
        if vaga.local is not None and not _texto(vaga.local):
            continue
        if vaga.remoto is not None and not isinstance(vaga.remoto, bool):
            continue
        # datetime é subclasse de date e iria para a coluna DATE com hora.
        if vaga.publicada_em is not None and (
            not isinstance(vaga.publicada_em, date) or isinstance(vaga.publicada_em, datetime)
        ):
            continue
        validas.setdefault(vaga.url, vaga)
    return list(validas.values())


def _sem_repetidos(valores) -> list:
    # Mantém a ordem; ["Remoto", "Remoto"] deixaria de ser igual a [Remoto] nos filtros.
    return list(dict.fromkeys(valores))


def normalizar_perfil(perfil: Perfil) -> Perfil:
    """Cópia do perfil com enums de verdade e sem repetidos; valor fora de Modalidade/Nivel levanta ValueError."""
    return replace(
        perfil,
        termos=_sem_repetidos(perfil.termos),
        excluir=_sem_repetidos(perfil.excluir),
        modalidades=_sem_repetidos(Modalidade(m) for m in perfil.modalidades),
        localidades=_sem_repetidos(perfil.localidades),
        plataformas=_sem_repetidos(perfil.plataformas),
        niveis=_sem_repetidos(Nivel(n) for n in perfil.niveis),
        skills=_sem_repetidos(perfil.skills),
    )


# Dez anos: acima disso o filtro não serve para nada, e um valor enorme estoura o
# `hoje - timedelta(days=...)` da recomendação e o INT da coluna.
MAX_PUBLICADA_DESDE_DIAS = 3650


def validar_perfil(perfil: Perfil) -> Perfil:
    """Perfil normalizado e conferido antes de gravar; inválido levanta ValueError."""
    dias = perfil.publicada_desde_dias
    if dias is not None and not 0 < dias <= MAX_PUBLICADA_DESDE_DIAS:
        raise DadoInvalido(f"publicada_desde_dias tem que estar entre 1 e {MAX_PUBLICADA_DESDE_DIAS}")
    try:
        return normalizar_perfil(perfil)
    except ValueError as erro:
        # Enum fora de Modalidade/Nivel: dado do usuário, não falha do servidor.
        raise DadoInvalido(str(erro)) from erro


class PerfilBuscaRepositoryMemoria(PerfilBuscaRepository):
    def __init__(self) -> None:
        self._perfil: Perfil | None = None

    def obter(self) -> Perfil | None:
        return normalizar_perfil(self._perfil) if self._perfil else None

    def salvar(self, perfil: Perfil) -> None:
        self._perfil = validar_perfil(perfil)


class CurriculoRepositoryMemoria(CurriculoRepository):
    def __init__(self) -> None:
        self._linhas: dict[int, Curriculo] = {}
        self._proximo_id = 1

    def obter_base(self) -> Curriculo | None:
        bases = [c for c in self._linhas.values() if c.tipo == "base"]
        return replace(max(bases, key=lambda c: (c.criado_em, c.id))) if bases else None

    def salvar(self, curriculo: Curriculo) -> Curriculo:
        curriculo.validar()
        if curriculo.base_id is not None:
            base = self._linhas.get(curriculo.base_id)
            if base is None:
                raise DadoInvalido(f"base_id {curriculo.base_id} não existe")
            if base.tipo != "base":
                raise DadoInvalido(f"base_id {curriculo.base_id} é um currículo adaptado, não uma base")
        salvo = replace(curriculo, id=self._proximo_id, criado_em=datetime.now(timezone.utc))
        self._linhas[salvo.id] = salvo
        self._proximo_id += 1
        return replace(salvo)

    def obter(self, id: int) -> Curriculo | None:
        curriculo = self._linhas.get(id)
        return replace(curriculo) if curriculo else None

    def listar_adaptados(self) -> list[Curriculo]:
        adaptados = [c for c in self._linhas.values() if c.tipo == "adaptado"]
        return [replace(c) for c in sorted(adaptados, key=lambda c: (c.criado_em, c.id), reverse=True)]


def _chave_listagem(vaga: VagaEncontrada):
    return (vaga.primeira_vez_em, vaga.publicada_em or date.min)


class VagaEncontradaRepositoryMemoria(VagaEncontradaRepository):
    def __init__(self) -> None:
        self._linhas: dict[str, VagaEncontrada] = {}

    def registrar(self, vagas: list[VagaExterna], termo: str) -> int:
        termo = validar_termo(termo)
        validas = vagas_validas(vagas)
        # Um instante só por registro, como o now() da transação do Postgres.
        agora = datetime.now(timezone.utc)
        novas = 0
        for vaga in validas:
            atual = self._linhas.get(vaga.url)
            if atual is None:
                self._linhas[vaga.url] = VagaEncontrada(
                    **vars(vaga), termos=[termo], primeira_vez_em=agora, ultima_vez_em=agora
                )
                novas += 1
                continue
            self._linhas[vaga.url] = replace(
                atual,
                titulo=vaga.titulo or atual.titulo,
                empresa=vaga.empresa or atual.empresa,
                plataforma=vaga.plataforma or atual.plataforma,
                local=vaga.local if vaga.local is not None else atual.local,
                remoto=vaga.remoto if vaga.remoto is not None else atual.remoto,
                publicada_em=vaga.publicada_em if vaga.publicada_em is not None else atual.publicada_em,
                termos=atual.termos if termo in atual.termos else [*atual.termos, termo],
                ultima_vez_em=agora,
            )
        return novas

    def listar(
        self, somente_novas: bool, limite: int, q: str | None = None, deslocamento: int = 0
    ) -> list[VagaEncontrada]:
        limite = validar_limite(limite)
        deslocamento = validar_deslocamento(deslocamento)
        linhas = filtrar_e_ordenar(list(self._linhas.values()), somente_novas, q)
        return [replace(v, termos=list(v.termos)) for v in linhas[deslocamento : deslocamento + limite]]

    def contar(self, somente_novas: bool, q: str | None = None) -> int:
        return len(filtrar_e_ordenar(list(self._linhas.values()), somente_novas, q))

    def listar_recentes(
        self, somente_novas: bool, teto: int, q: str | None = None, ate: datetime | None = None
    ) -> list[VagaEncontrada]:
        teto = validar_teto(teto)
        ate = validar_ate(ate)
        exigidas = exigidas_da_busca(q)
        linhas = list(self._linhas.values())
        if ate is None:
            janela = filtrar_e_ordenar(linhas, somente_novas, None)[:teto]
        else:
            # A foto decide Novas pela vista_em no instante; a ordem é a de sempre.
            janela = filtrar_e_ordenar([v for v in linhas if na_janela(v, somente_novas, ate)], False, None)[:teto]
        return [replace(v, termos=list(v.termos)) for v in janela if casa_busca(v, exigidas)]

    def obter(self, url: str) -> VagaEncontrada | None:
        vaga = self._linhas.get(url) if isinstance(url, str) else None
        return replace(vaga, termos=list(vaga.termos)) if vaga else None

    def marcar_vistas(self, urls: list[str]) -> int:
        urls = validar_urls(urls)
        agora = datetime.now(timezone.utc)
        marcadas = 0
        for url in urls:
            vaga = self._linhas.get(url)
            if vaga is not None and vaga.vista_em is None:
                self._linhas[url] = replace(vaga, vista_em=agora)
                marcadas += 1
        return marcadas
