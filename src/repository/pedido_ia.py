import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from src.model.erros import DadoInvalido
from src.model.pedido_ia import (
    ERRO_PRESO,
    MAX_ERRO,
    MAX_TENTATIVAS,
    PRAZO_RODANDO,
    PRIORIDADE_LOTE,
    PRIORIDADE_PESSOA,
    EstadoPedidoIA,
    Faixa,
    NotaIA,
    PedidoIA,
    TipoPedidoIA,
)
from src.model.vaga_encontrada import VagaEncontrada
from src.repository.candidatura import validar_vaga_url


class PedidoIARepository(ABC):
    """Fila de pedidos de IA e rótulo manual da faixa.

    Tudo é da pessoa do repositório (criar, ler, lote, pausa, notas, rótulos), menos a fila do worker:
    `proximo`, `obter_da_fila`, `concluir`, `falhar` e `marcar_base` leem e gravam os pedidos de todas as
    pessoas, e o pedido devolvido traz o `pessoa_id` dele.
    """

    @abstractmethod
    def criar(
        self,
        vaga_url: str,
        tipo: TipoPedidoIA | str,
        prioridade: int = PRIORIDADE_PESSOA,
        curriculo_base_id: int | None = None,
    ) -> tuple[PedidoIA, bool]:
        """Cria um pedido pendente. Idempotente com o ativo: se já há um pendente ou rodando para
        (vaga_url, tipo), devolve esse com criado=False. Depois que ele termina, cria um novo.

        O ativo que veio do lote e é pedido pela pessoa sobe para a prioridade dela (o clique não
        espera o lote). Vaga fora de `vaga_encontrada` é `DadoInvalido`.
        """

    @abstractmethod
    def obter(self, id: int) -> PedidoIA | None:
        """O pedido da pessoa pelo id; o de outra pessoa é None."""

    @abstractmethod
    def obter_da_fila(self, id: int) -> PedidoIA | None:
        """O pedido pelo id, de qualquer pessoa: é o que o worker, que lê a fila global, devolve."""

    @abstractmethod
    def ultimo(self, vaga_url: str, tipo: TipoPedidoIA | str) -> PedidoIA | None:
        """O pedido mais recente (maior id) do tipo para a vaga, em qualquer estado."""

    @abstractmethod
    def proximo(self, agora: datetime | None = None) -> PedidoIA | None:
        """Reivindica um pedido para o worker, de qualquer pessoa, numa transação:

        1. pedido rodando há mais de PRAZO_RODANDO que já gastou MAX_TENTATIVAS vira erro;
        2. escolhe um pendente ou preso (pendente primeiro; entre eles a menor prioridade, a da
           pessoa antes da do lote; depois o mais antigo);
        3. marca rodando, soma 1 em tentativas e grava reivindicado_em.

        None com a fila vazia. `agora` é o relógio (testes).
        """

    @abstractmethod
    def concluir(
        self,
        id: int,
        tentativa: int,
        resultado: dict,
        prompt_versao: str,
        modelo: str,
        nota: int | None = None,
        faixa: Faixa | str | None = None,
        curriculo_id: int | None = None,
    ) -> PedidoIA | None:
        """rodando -> pronto. None se o pedido não existe, não está rodando ou a tentativa é outra."""

    @abstractmethod
    def falhar(
        self, id: int, tentativa: int, erro: str, prompt_versao: str | None = None, modelo: str | None = None
    ) -> PedidoIA | None:
        """rodando -> erro (o texto é cortado em MAX_ERRO). None nas mesmas condições de `concluir`."""

    @abstractmethod
    def marcar_base(self, id: int, tentativa: int, curriculo_base_id: int) -> PedidoIA | None:
        """Grava a base que vai para o worker nesta tentativa (a da criação pode ter mudado até ele pegar).
        None nas mesmas condições de `concluir`."""

    @abstractmethod
    def listar_prontos_compatibilidade(self, prompt_versao: str | None = None) -> list[PedidoIA]:
        """Pedidos de compatibilidade `pronto` (da versão, se informada), do mais antigo ao mais novo (id)."""

    @abstractmethod
    def versoes_compatibilidade(self) -> set[str]:
        """As `prompt_versao` dos prontos de compatibilidade."""

    @abstractmethod
    def notas_compatibilidade(self, curriculo_base_id: int, prompt_versao: str | None) -> dict[str, NotaIA]:
        """vaga_url -> nota do pronto de compatibilidade MAIS RECENTE (maior id) feito com a base
        `curriculo_base_id` e, se informada, a `prompt_versao`. Uma consulta, para a lista inteira."""

    @abstractmethod
    def urls_ativas(self, tipo: TipoPedidoIA | str) -> set[str]:
        """Vagas com pedido pendente ou rodando do tipo."""

    @abstractmethod
    def urls_com_erro_desde(self, curriculo_base_id: int, desde: datetime) -> set[str]:
        """Vagas com compatibilidade em `erro` para a base, terminada em `desde` ou depois."""

    @abstractmethod
    def contar_lote_desde(self, desde: datetime) -> int:
        """Pedidos do lote (prioridade 1) criados em `desde` ou depois."""

    @abstractmethod
    def lote_pausado(self) -> bool:
        """O lote da IA está pausado (o botão "Pausar avaliação")? Sem registro: não."""

    @abstractmethod
    def pausar_lote(self) -> int:
        """Pausa o lote e apaga, numa transação, os pedidos de compatibilidade do lote ainda pendentes;
        devolve quantos apagou. Não toca no que está rodando (termina), nos pedidos da pessoa
        (prioridade 0), nos de currículo nem nos já terminados."""

    @abstractmethod
    def retomar_lote(self) -> None:
        """Tira a pausa do lote."""

    @abstractmethod
    def gravar_rotulo(self, vaga_url: str, faixa: Faixa | str | None) -> Faixa | None:
        """Grava ou troca o rótulo da vaga; `None` apaga. Vaga fora do histórico é `DadoInvalido`."""

    @abstractmethod
    def obter_rotulo(self, vaga_url: str) -> Faixa | None: ...

    @abstractmethod
    def listar_rotulos(self) -> dict[str, str]:
        """vaga_url -> faixa (o valor do enum, com acento)."""


def validar_tipo(tipo) -> TipoPedidoIA:
    try:
        return TipoPedidoIA(tipo)
    except ValueError:
        raise DadoInvalido("tipo inválido: use compatibilidade ou curriculo") from None


def validar_faixa(faixa) -> Faixa | None:
    """None passa (apagar); fora das 3 grafias exatas é DadoInvalido (`media` sem acento também)."""
    if faixa is None:
        return None
    try:
        return Faixa(faixa)
    except ValueError:
        raise DadoInvalido("faixa inválida: use alta, média ou baixa") from None


def validar_prioridade(prioridade) -> int:
    if isinstance(prioridade, bool) or prioridade not in (PRIORIDADE_PESSOA, PRIORIDADE_LOTE):
        raise DadoInvalido("prioridade inválida: use 0 (pessoa) ou 1 (lote)")
    return prioridade


def id_possivel(id: int) -> bool:
    """O BIGSERIAL só gera de 1 a 2**63-1: fora disso o pedido não existe (e o Postgres estouraria)."""
    return isinstance(id, int) and not isinstance(id, bool) and 0 < id < 2**63


@dataclass
class _ArmazemPedidos:
    """O "banco" dos pedidos em memória: a fila é uma só, com o id global; rótulo e pausa são por pessoa."""

    linhas: dict[int, PedidoIA] = field(default_factory=dict)
    rotulos: dict[int, dict[str, Faixa]] = field(default_factory=dict)
    pausadas: set[int] = field(default_factory=set)
    proximo_id: int = 1
    lock: threading.Lock = field(default_factory=threading.Lock)


class PedidoIARepositoryMemoria(PedidoIARepository):
    """Mesma regra do Postgres, com relógio injetável. `vaga_por_url` faz o papel da chave estrangeira e
    tem que ler as vagas da MESMA pessoa.

    `compartilhado`: outra instância cujo armazenamento esta usa, para duas pessoas no mesmo "banco".
    """

    def __init__(
        self,
        pessoa_id: int,
        vaga_por_url: Callable[[str], VagaEncontrada | None] | None = None,
        relogio: Callable[[], datetime] | None = None,
        compartilhado: "PedidoIARepositoryMemoria | None" = None,
    ) -> None:
        self._pessoa_id = pessoa_id
        self._vaga_por_url = vaga_por_url
        self._relogio = relogio or (lambda: datetime.now(timezone.utc))
        self._armazem = compartilhado._armazem if compartilhado is not None else _ArmazemPedidos()
        # Todos os pedidos (a fila é global); `_meus` é o recorte da pessoa.
        self._linhas: dict[int, PedidoIA] = self._armazem.linhas
        self._rotulos: dict[str, Faixa] = self._armazem.rotulos.setdefault(pessoa_id, {})
        self._lock = self._armazem.lock

    def _meus(self) -> list[PedidoIA]:
        return [p for p in self._linhas.values() if p.pessoa_id == self._pessoa_id]

    def _existe(self, vaga_url: str) -> bool:
        return self._vaga_por_url is None or self._vaga_por_url(vaga_url) is not None

    def criar(
        self,
        vaga_url: str,
        tipo: TipoPedidoIA | str,
        prioridade: int = PRIORIDADE_PESSOA,
        curriculo_base_id: int | None = None,
    ) -> tuple[PedidoIA, bool]:
        vaga_url = validar_vaga_url(vaga_url)
        tipo = validar_tipo(tipo)
        prioridade = validar_prioridade(prioridade)
        with self._lock:
            ativo = next((p for p in self._meus() if p.vaga_url == vaga_url and p.tipo == tipo and p.ativo), None)
            if ativo is not None:
                ativo.prioridade = min(ativo.prioridade, prioridade)
                return replace(ativo), False
            if not self._existe(vaga_url):
                raise DadoInvalido(f"vaga não encontrada no histórico: {vaga_url}")
            pedido = PedidoIA(
                self._armazem.proximo_id,
                tipo,
                vaga_url,
                prioridade=prioridade,
                curriculo_base_id=curriculo_base_id,
                criado_em=self._relogio(),
                pessoa_id=self._pessoa_id,
            )
            self._linhas[pedido.id] = pedido
            self._armazem.proximo_id += 1
            return replace(pedido), True

    def obter(self, id: int) -> PedidoIA | None:
        with self._lock:
            linha = self._linhas.get(id)
            return replace(linha) if linha and linha.pessoa_id == self._pessoa_id else None

    def obter_da_fila(self, id: int) -> PedidoIA | None:
        with self._lock:
            linha = self._linhas.get(id)
            return replace(linha) if linha else None

    def ultimo(self, vaga_url: str, tipo: TipoPedidoIA | str) -> PedidoIA | None:
        vaga_url = validar_vaga_url(vaga_url)
        tipo = validar_tipo(tipo)
        with self._lock:
            do_tipo = [p for p in self._meus() if p.vaga_url == vaga_url and p.tipo == tipo]
            return replace(max(do_tipo, key=lambda p: p.id)) if do_tipo else None

    def proximo(self, agora: datetime | None = None) -> PedidoIA | None:
        agora = agora or self._relogio()
        limite = agora - PRAZO_RODANDO
        with self._lock:
            for p in self._linhas.values():
                if (
                    p.estado == EstadoPedidoIA.RODANDO
                    and p.reivindicado_em < limite
                    and p.tentativas >= MAX_TENTATIVAS
                ):
                    p.estado = EstadoPedidoIA.ERRO
                    p.erro = ERRO_PRESO
                    p.terminado_em = agora
            candidatos = [
                p
                for p in self._linhas.values()
                if p.estado == EstadoPedidoIA.PENDENTE
                or (p.estado == EstadoPedidoIA.RODANDO and p.reivindicado_em < limite)
            ]
            if not candidatos:
                return None
            escolhido = min(
                candidatos, key=lambda p: (p.estado == EstadoPedidoIA.RODANDO, p.prioridade, p.criado_em, p.id)
            )
            escolhido.estado = EstadoPedidoIA.RODANDO
            escolhido.tentativas += 1
            escolhido.reivindicado_em = agora
            return replace(escolhido)

    def _rodando(self, id: int, tentativa: int) -> PedidoIA | None:
        linha = self._linhas.get(id)
        if linha is None or linha.estado != EstadoPedidoIA.RODANDO or linha.tentativas != tentativa:
            return None
        return linha

    def marcar_base(self, id: int, tentativa: int, curriculo_base_id: int) -> PedidoIA | None:
        with self._lock:
            linha = self._rodando(id, tentativa)
            if linha is None:
                return None
            linha.curriculo_base_id = curriculo_base_id
            return replace(linha)

    def concluir(
        self,
        id: int,
        tentativa: int,
        resultado: dict,
        prompt_versao: str,
        modelo: str,
        nota: int | None = None,
        faixa: Faixa | str | None = None,
        curriculo_id: int | None = None,
    ) -> PedidoIA | None:
        faixa = validar_faixa(faixa)
        with self._lock:
            linha = self._rodando(id, tentativa)
            if linha is None:
                return None
            linha.estado = EstadoPedidoIA.PRONTO
            linha.resultado = resultado
            linha.prompt_versao = prompt_versao
            linha.modelo = modelo
            linha.nota = nota
            linha.faixa = faixa
            linha.curriculo_id = curriculo_id
            linha.terminado_em = self._relogio()
            return replace(linha)

    def falhar(
        self, id: int, tentativa: int, erro: str, prompt_versao: str | None = None, modelo: str | None = None
    ) -> PedidoIA | None:
        with self._lock:
            linha = self._rodando(id, tentativa)
            if linha is None:
                return None
            linha.estado = EstadoPedidoIA.ERRO
            linha.erro = erro[:MAX_ERRO]
            linha.prompt_versao = prompt_versao
            linha.modelo = modelo
            linha.terminado_em = self._relogio()
            return replace(linha)

    def listar_prontos_compatibilidade(self, prompt_versao: str | None = None) -> list[PedidoIA]:
        with self._lock:
            return [
                replace(p)
                for p in sorted(self._meus(), key=lambda p: p.id)
                if p.tipo == TipoPedidoIA.COMPATIBILIDADE
                and p.estado == EstadoPedidoIA.PRONTO
                and (prompt_versao is None or p.prompt_versao == prompt_versao)
            ]

    def versoes_compatibilidade(self) -> set[str]:
        with self._lock:
            return {
                p.prompt_versao
                for p in self._meus()
                if p.tipo == TipoPedidoIA.COMPATIBILIDADE and p.estado == EstadoPedidoIA.PRONTO and p.prompt_versao
            }

    def notas_compatibilidade(self, curriculo_base_id: int, prompt_versao: str | None) -> dict[str, NotaIA]:
        with self._lock:
            notas: dict[str, NotaIA] = {}
            for p in sorted(self._meus(), key=lambda p: p.id):
                if (
                    p.tipo == TipoPedidoIA.COMPATIBILIDADE
                    and p.estado == EstadoPedidoIA.PRONTO
                    and p.curriculo_base_id == curriculo_base_id
                    and (prompt_versao is None or p.prompt_versao == prompt_versao)
                    and p.nota is not None
                    and p.faixa is not None
                ):
                    notas[p.vaga_url] = NotaIA(p.nota, p.faixa)
            return notas

    def urls_ativas(self, tipo: TipoPedidoIA | str) -> set[str]:
        tipo = validar_tipo(tipo)
        with self._lock:
            return {p.vaga_url for p in self._meus() if p.tipo == tipo and p.ativo}

    def urls_com_erro_desde(self, curriculo_base_id: int, desde: datetime) -> set[str]:
        with self._lock:
            return {
                p.vaga_url
                for p in self._meus()
                if p.tipo == TipoPedidoIA.COMPATIBILIDADE
                and p.estado == EstadoPedidoIA.ERRO
                and p.curriculo_base_id == curriculo_base_id
                and p.terminado_em is not None
                and p.terminado_em >= desde
            }

    def contar_lote_desde(self, desde: datetime) -> int:
        with self._lock:
            return sum(1 for p in self._meus() if p.prioridade == PRIORIDADE_LOTE and p.criado_em >= desde)

    def lote_pausado(self) -> bool:
        with self._lock:
            return self._pessoa_id in self._armazem.pausadas

    def pausar_lote(self) -> int:
        with self._lock:
            self._armazem.pausadas.add(self._pessoa_id)
            do_lote = [
                p.id
                for p in self._meus()
                if p.estado == EstadoPedidoIA.PENDENTE
                and p.prioridade == PRIORIDADE_LOTE
                and p.tipo == TipoPedidoIA.COMPATIBILIDADE
            ]
            for id in do_lote:
                del self._linhas[id]
            return len(do_lote)

    def retomar_lote(self) -> None:
        with self._lock:
            self._armazem.pausadas.discard(self._pessoa_id)

    def gravar_rotulo(self, vaga_url: str, faixa: Faixa | str | None) -> Faixa | None:
        vaga_url = validar_vaga_url(vaga_url)
        faixa = validar_faixa(faixa)
        with self._lock:
            if faixa is None:
                self._rotulos.pop(vaga_url, None)
                return None
            if not self._existe(vaga_url):
                raise DadoInvalido(f"vaga não encontrada no histórico: {vaga_url}")
            self._rotulos[vaga_url] = faixa
            return faixa

    def obter_rotulo(self, vaga_url: str) -> Faixa | None:
        with self._lock:
            return self._rotulos.get(vaga_url)

    def listar_rotulos(self) -> dict[str, str]:
        with self._lock:
            return {url: f.value for url, f in self._rotulos.items()}
