from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timezone

from src.client.base import _LIGACAO, _equivalentes, _formas_do_titulo, palavras, radical
from src.model.curriculo import Curriculo
from src.model.detalhe_vaga import (
    MAX_BENEFICIOS,
    MAX_REQUISITOS,
    MAX_TEXTO_ITEM,
    MAX_TEXTO_SUBTITULO,
    DetalheVaga,
    Requisito,
    Salario,
)
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
    def listar_adaptados(self, vaga_url: str | None = None) -> list[Curriculo]:
        """Adaptados do mais recente para o mais antigo; com `vaga_url`, só os dessa vaga (url igual)."""


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

        Se a vaga traz `detalhe` (a listagem do Remotar traz), grava/atualiza descrição, requisitos,
        salário, benefícios e `detalhada_em`; `expirada_em` segue `expiracao_resultante` (com `tem_prazo`).
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
    def listar_recentes(self, somente_novas: bool, teto: int, q: str | None = None) -> list[VagaEncontrada]:
        """A janela de varredura do histórico: as `teto` vagas mais recentes na ordem de `listar`
        (já com o filtro `somente_novas`) e, dentro delas, as que casam com `q`.

        O teto vale antes do `q`, para o custo ficar limitado pelo teto nos dois repositórios.
        """

    @abstractmethod
    def obter(self, url: str) -> VagaEncontrada | None:
        """A vaga pela URL exatamente como foi gravada (é a chave: não se normaliza); None se não existe."""

    @abstractmethod
    def marcar_vistas(self, urls: list[str]) -> int:
        """Marca como vistas as que ainda não estavam; devolve quantas mudaram."""

    @abstractmethod
    def gravar_detalhe(self, url: str, detalhe: DetalheVaga) -> bool:
        """Grava o detalhe e `detalhada_em` (agora); `detalhe.expirada_em` e `detalhe.tem_prazo` seguem `expiracao_resultante`.

        Não mexe em `vista_em` nem em `primeira_vez_em`. Devolve False se a URL não existe.
        """

    @abstractmethod
    def marcar_expirada(self, url: str) -> bool:
        """O provider disse que saiu do ar: `expirada_em` = agora, salvo se já expirou antes (essa data
        fica). Um prazo futuro vira agora. False se a URL não existe."""

    @abstractmethod
    def marcar_salva(self, url: str, salva: bool) -> bool:
        """Salva (`salva_em` = agora; já salva fica com a data da primeira vez) ou tira dos salvos.
        False se a URL não existe."""

    @abstractmethod
    def listar_salvas(self, teto: int, q: str | None = None) -> list[VagaEncontrada]:
        """As salvas, da salva mais recente para a mais antiga (a URL desempata), inclusive as
        expiradas: a pessoa salvou de propósito, e a tela mostra "encerrada" em vez de sumir com ela.
        Até `teto` salvas; `q` filtra por palavra, como em `listar`."""

    @abstractmethod
    def contar_salvas(self) -> int:
        """Quantas vagas estão salvas, expiradas inclusive (o número da aba "Salvas")."""


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
    """Toda palavra de `q` casa com uma palavra de título, empresa, local ou plataforma (E entre elas).

    Mesma comparação da relevância da recomendação: palavra inteira por radical, nunca por
    pedaço (`java` não casa com JavaScript, `sql` não casa com NoSQL), sem acento, caixa,
    hífen, gênero e plural; "front end" = "front-end" = "frontend". Sem palavras, não filtra.
    """
    formas: set[str] = set()
    # A plataforma entra para "linkedin" filtrar as vagas que vieram do LinkedIn.
    for campo in (vaga.titulo, vaga.empresa, vaga.local, vaga.plataforma):
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


def no_ar(vaga: VagaEncontrada, agora: datetime) -> bool:
    """Sem expiração ou com o prazo ainda por vir (`expirada_em IS NULL OR expirada_em > now()`)."""
    return vaga.expirada_em is None or vaga.expirada_em > agora


def expiracao_resultante(
    atual: datetime | None, nova: datetime | None, agora: datetime, tem_prazo: bool | None = None
) -> datetime | None:
    """A expiração depois de a listagem ou o detalhe trazer `nova`: quem já expirou continua expirado
    (a data fica), senão vale a nova (o prazo pode mudar). Sem nova, `tem_prazo is False` (o provider
    disse que não há prazo) remove o prazo futuro; qualquer outro caso mantém a que já estava."""
    if atual is not None and atual <= agora:
        return atual
    if nova is not None:
        return nova
    return None if tem_prazo is False else atual


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def filtrar_e_ordenar(
    linhas: list[VagaEncontrada], somente_novas: bool, q: str | None, agora: datetime | None = None
) -> list[VagaEncontrada]:
    """O filtro e a ordem do contrato, em Python (a memória usa direto)."""
    exigidas = exigidas_da_busca(q)
    agora = agora or _agora()
    linhas = [
        v
        for v in linhas
        if no_ar(v, agora) and (not somente_novas or v.vista_em is None) and casa_busca(v, exigidas)
    ]
    # Mesma ordem do Postgres: primeira_vez_em desc, publicada_em desc (sem data por último), url.
    return sorted(sorted(linhas, key=lambda v: v.url), key=_chave_listagem, reverse=True)


def validar_teto(teto: int) -> int:
    if isinstance(teto, bool) or not isinstance(teto, int) or teto < 1:
        raise DadoInvalido("teto da varredura tem que ser um inteiro positivo")
    return teto


def validar_urls(urls: list[str]) -> list[str]:
    if not isinstance(urls, list) or not all(isinstance(u, str) for u in urls):
        raise DadoInvalido("urls tem que ser uma lista de textos")
    return _sem_repetidos(u for u in urls if u)


def _texto(valor) -> bool:
    # O Postgres não aceita NUL em TEXT: um título assim derrubaria o registro do termo inteiro.
    return isinstance(valor, str) and "\x00" not in valor


def _instante_valido(valor) -> bool:
    # Só com fuso, e dentro do que o timestamptz guarda: ano 1 ou 9999 estoura na conversão.
    return isinstance(valor, datetime) and valor.tzinfo is not None and 1900 < valor.year < 3000


def _texto_ate(valor, limite: int) -> bool:
    return isinstance(valor, str) and "\x00" not in valor and len(valor) <= limite


def detalhe_valido(detalhe: object) -> DetalheVaga | None:
    """O detalhe que dá para gravar (a 004 tem CHECK de tamanho e forma); item torto sai sozinho.

    O sanitizador do client já limita tudo; isto é a conferência na hora de gravar, para uma vaga
    nunca derrubar o registro das outras.
    """
    if not isinstance(detalhe, DetalheVaga):
        return None
    descricao = detalhe.descricao if _texto_ate(detalhe.descricao, 20_000) and detalhe.descricao.strip() else None
    subtitulo = detalhe.subtitulo if _texto_ate(detalhe.subtitulo, MAX_TEXTO_SUBTITULO) else None
    requisitos = [
        r
        for r in (detalhe.requisitos if isinstance(detalhe.requisitos, list) else [])
        if isinstance(r, Requisito) and _texto_ate(r.texto, MAX_TEXTO_ITEM) and r.texto.strip()
    ]
    beneficios = [
        b
        for b in (detalhe.beneficios if isinstance(detalhe.beneficios, list) else [])
        if _texto_ate(b, MAX_TEXTO_ITEM) and b.strip()
    ]
    salario = detalhe.salario if isinstance(detalhe.salario, Salario) and _salario_gravavel(detalhe.salario) else None
    expirada_em = detalhe.expirada_em if _instante_valido(detalhe.expirada_em) else None
    return DetalheVaga(
        descricao,
        requisitos[:MAX_REQUISITOS],
        salario,
        beneficios[:MAX_BENEFICIOS],
        subtitulo,
        expirada_em,
        tem_prazo_valido(detalhe.tem_prazo),
    )


def tem_prazo_valido(valor) -> bool | None:
    """Só um bool de verdade conta; qualquer outra coisa é None (mantém a data que estava)."""
    return valor if isinstance(valor, bool) else None


def _salario_gravavel(s: Salario) -> bool:
    return any(isinstance(v, int) and not isinstance(v, bool) and 0 < v <= 10**11 for v in (s.minimo, s.maximo))


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
        # Data de expiração ou detalhe estranho descarta só esse campo: a vaga entra como no ar, sem detalhe.
        if vaga.expirada_em is not None and not _instante_valido(vaga.expirada_em):
            vaga = replace(vaga, expirada_em=None)
        if vaga.tem_prazo is not None and tem_prazo_valido(vaga.tem_prazo) is None:
            vaga = replace(vaga, tem_prazo=None)  # torto: mantém a data que estava
        if vaga.detalhe is not None:
            vaga = replace(vaga, detalhe=detalhe_valido(vaga.detalhe))
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


# Teto de cada lista de texto do perfil, o mesmo no `PUT /perfil` (JSON) e no `PUT /tela/perfil` (formulário):
# um textarea ou corpo colado sem querer não vira gravação gigante, e o que uma rota grava a outra aceita de volta.
MAX_ITENS_POR_LISTA = 200
MAX_TAMANHO_ITEM = 200
_LISTAS_DE_TEXTO = ("termos", "excluir", "localidades", "plataformas", "skills")


def _validar_tamanho_das_listas(perfil: Perfil) -> None:
    for campo in _LISTAS_DE_TEXTO:
        itens = getattr(perfil, campo)
        if len(itens) > MAX_ITENS_POR_LISTA or any(isinstance(i, str) and len(i) > MAX_TAMANHO_ITEM for i in itens):
            raise DadoInvalido(
                f"{campo}: cada lista aceita até {MAX_ITENS_POR_LISTA} itens de até {MAX_TAMANHO_ITEM} caracteres"
            )


def validar_perfil(perfil: Perfil) -> Perfil:
    """Perfil normalizado e conferido antes de gravar; inválido levanta `DadoInvalido`.

    É a única fonte das regras: a rota JSON e a da tela chamam esta função (e os repositórios também).
    """
    _validar_tamanho_das_listas(perfil)
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

    def listar_adaptados(self, vaga_url: str | None = None) -> list[Curriculo]:
        adaptados = [
            c for c in self._linhas.values() if c.tipo == "adaptado" and (vaga_url is None or c.vaga_url == vaga_url)
        ]
        return [replace(c) for c in sorted(adaptados, key=lambda c: (c.criado_em, c.id), reverse=True)]


def _chave_listagem(vaga: VagaEncontrada):
    return (vaga.primeira_vez_em, vaga.publicada_em or date.min)


# O que a listagem do provider preenche direto (o detalhe vai por `com_detalhe`).
_CAMPOS_DA_LISTAGEM = ("url", "titulo", "empresa", "plataforma", "local", "remoto", "publicada_em", "expirada_em")


def com_detalhe(vaga: VagaEncontrada, detalhe: DetalheVaga | None, agora: datetime) -> VagaEncontrada:
    """A vaga com o detalhe gravado por cima (e `detalhada_em`); sem detalhe, como estava."""
    if detalhe is None:
        return vaga
    return replace(
        vaga,
        descricao=detalhe.descricao,
        requisitos=list(detalhe.requisitos),
        salario=detalhe.salario,
        beneficios=list(detalhe.beneficios),
        subtitulo=detalhe.subtitulo,
        detalhada_em=agora,
    )


class VagaEncontradaRepositoryMemoria(VagaEncontradaRepository):
    def __init__(self, relogio: Callable[[], datetime] = _agora) -> None:
        self._linhas: dict[str, VagaEncontrada] = {}
        # Injetável: o teste faz o prazo de `expirada_em` passar sem esperar.
        self._agora = relogio

    def registrar(self, vagas: list[VagaExterna], termo: str) -> int:
        termo = validar_termo(termo)
        validas = vagas_validas(vagas)
        # Um instante só por registro, como o now() da transação do Postgres.
        agora = self._agora()
        novas = 0
        for vaga in validas:
            atual = self._linhas.get(vaga.url)
            if atual is None:
                self._linhas[vaga.url] = com_detalhe(
                    VagaEncontrada(
                        **{c: getattr(vaga, c) for c in _CAMPOS_DA_LISTAGEM},
                        termos=[termo],
                        primeira_vez_em=agora,
                        ultima_vez_em=agora,
                    ),
                    vaga.detalhe,
                    agora,
                )
                novas += 1
                continue
            atualizada = replace(
                atual,
                titulo=vaga.titulo or atual.titulo,
                empresa=vaga.empresa or atual.empresa,
                plataforma=vaga.plataforma or atual.plataforma,
                local=vaga.local if vaga.local is not None else atual.local,
                remoto=vaga.remoto if vaga.remoto is not None else atual.remoto,
                publicada_em=vaga.publicada_em if vaga.publicada_em is not None else atual.publicada_em,
                termos=atual.termos if termo in atual.termos else [*atual.termos, termo],
                expirada_em=expiracao_resultante(atual.expirada_em, vaga.expirada_em, agora, vaga.tem_prazo),
                ultima_vez_em=agora,
            )
            self._linhas[vaga.url] = com_detalhe(atualizada, vaga.detalhe, agora)
        return novas

    def listar(
        self, somente_novas: bool, limite: int, q: str | None = None, deslocamento: int = 0
    ) -> list[VagaEncontrada]:
        limite = validar_limite(limite)
        deslocamento = validar_deslocamento(deslocamento)
        linhas = filtrar_e_ordenar(list(self._linhas.values()), somente_novas, q, self._agora())
        return [replace(v, termos=list(v.termos)) for v in linhas[deslocamento : deslocamento + limite]]

    def contar(self, somente_novas: bool, q: str | None = None) -> int:
        return len(filtrar_e_ordenar(list(self._linhas.values()), somente_novas, q, self._agora()))

    def listar_recentes(self, somente_novas: bool, teto: int, q: str | None = None) -> list[VagaEncontrada]:
        teto = validar_teto(teto)
        exigidas = exigidas_da_busca(q)
        janela = filtrar_e_ordenar(list(self._linhas.values()), somente_novas, None, self._agora())[:teto]
        return [replace(v, termos=list(v.termos)) for v in janela if casa_busca(v, exigidas)]

    def obter(self, url: str) -> VagaEncontrada | None:
        vaga = self._linhas.get(url) if isinstance(url, str) else None
        return replace(vaga, termos=list(vaga.termos)) if vaga else None

    def gravar_detalhe(self, url: str, detalhe: DetalheVaga) -> bool:
        atual = self._linhas.get(url) if isinstance(url, str) else None
        limpo = detalhe_valido(detalhe)
        if atual is None or limpo is None:
            return False
        agora = self._agora()
        gravada = com_detalhe(atual, limpo, agora)
        self._linhas[url] = replace(
            gravada, expirada_em=expiracao_resultante(atual.expirada_em, limpo.expirada_em, agora, limpo.tem_prazo)
        )
        return True

    def marcar_expirada(self, url: str) -> bool:
        atual = self._linhas.get(url) if isinstance(url, str) else None
        if atual is None:
            return False
        agora = self._agora()
        if atual.expirada_em is None or atual.expirada_em > agora:
            self._linhas[url] = replace(atual, expirada_em=agora)
        return True

    def marcar_salva(self, url: str, salva: bool) -> bool:
        atual = self._linhas.get(url) if isinstance(url, str) else None
        if atual is None:
            return False
        if not salva:
            self._linhas[url] = replace(atual, salva_em=None)
        elif atual.salva_em is None:
            self._linhas[url] = replace(atual, salva_em=self._agora())
        return True

    def listar_salvas(self, teto: int, q: str | None = None) -> list[VagaEncontrada]:
        teto = validar_teto(teto)
        exigidas = exigidas_da_busca(q)
        salvas = sorted((v for v in self._linhas.values() if v.salva_em is not None), key=lambda v: v.url)
        salvas = sorted(salvas, key=lambda v: v.salva_em, reverse=True)[:teto]
        return [replace(v, termos=list(v.termos)) for v in salvas if casa_busca(v, exigidas)]

    def contar_salvas(self) -> int:
        return sum(1 for v in self._linhas.values() if v.salva_em is not None)

    def marcar_vistas(self, urls: list[str]) -> int:
        urls = validar_urls(urls)
        agora = self._agora()
        marcadas = 0
        for url in urls:
            vaga = self._linhas.get(url)
            if vaga is not None and vaga.vista_em is None:
                self._linhas[url] = replace(vaga, vista_em=agora)
                marcadas += 1
        return marcadas
