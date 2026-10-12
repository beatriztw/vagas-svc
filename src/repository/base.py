from abc import ABC, abstractmethod
from dataclasses import replace
from datetime import date, datetime, timezone

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


class VagaEncontradaRepository(ABC):
    """Histórico da coleta: uma linha por URL de vaga."""

    @abstractmethod
    def registrar(self, vagas: list[VagaExterna], termo: str) -> int:
        """Upsert por URL numa transação só; devolve quantas vagas são novas.

        Vaga que já existia atualiza `ultima_vez_em`, os campos que vieram preenchidos e
        ganha o termo em `termos` (sem repetir); `primeira_vez_em` e `vista_em` ficam.
        """

    @abstractmethod
    def listar(self, somente_novas: bool, limite: int) -> list[VagaEncontrada]:
        """Mais recentes (`primeira_vez_em`) primeiro; `somente_novas` deixa só as não vistas."""

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

    def listar(self, somente_novas: bool, limite: int) -> list[VagaEncontrada]:
        limite = validar_limite(limite)
        linhas = [v for v in self._linhas.values() if not somente_novas or v.vista_em is None]
        # Mesma ordem do Postgres: primeira_vez_em desc, publicada_em desc (sem data por último), url.
        linhas = sorted(sorted(linhas, key=lambda v: v.url), key=_chave_listagem, reverse=True)
        return [replace(v, termos=list(v.termos)) for v in linhas[:limite]]

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
