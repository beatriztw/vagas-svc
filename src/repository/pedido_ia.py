import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone

from src.model.erros import DadoInvalido
from src.model.pedido_ia import (
    ERRO_PRESO,
    MAX_ERRO,
    MAX_TENTATIVAS,
    PRAZO_RODANDO,
    EstadoPedidoIA,
    Faixa,
    PedidoIA,
    TipoPedidoIA,
)
from src.model.vaga_encontrada import VagaEncontrada
from src.repository.candidatura import validar_vaga_url


class PedidoIARepository(ABC):
    """Fila de pedidos de IA e rótulo manual da faixa."""

    @abstractmethod
    def criar(self, vaga_url: str, tipo: TipoPedidoIA | str) -> tuple[PedidoIA, bool]:
        """Cria um pedido pendente. Idempotente com o ativo: se já há um pendente ou rodando para
        (vaga_url, tipo), devolve esse com criado=False. Depois que ele termina, cria um novo.

        Vaga fora de `vaga_encontrada` é `DadoInvalido`.
        """

    @abstractmethod
    def obter(self, id: int) -> PedidoIA | None: ...

    @abstractmethod
    def ultimo(self, vaga_url: str, tipo: TipoPedidoIA | str) -> PedidoIA | None:
        """O pedido mais recente (maior id) do tipo para a vaga, em qualquer estado."""

    @abstractmethod
    def proximo(self, agora: datetime | None = None) -> PedidoIA | None:
        """Reivindica um pedido para o worker, numa transação:

        1. pedido rodando há mais de PRAZO_RODANDO que já gastou MAX_TENTATIVAS vira erro;
        2. escolhe um pendente ou preso (pendente primeiro, depois o mais antigo);
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
    def listar_prontos_compatibilidade(self, prompt_versao: str | None = None) -> list[PedidoIA]:
        """Pedidos de compatibilidade `pronto` (da versão, se informada), do mais antigo ao mais novo (id)."""

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


def id_possivel(id: int) -> bool:
    """O BIGSERIAL só gera de 1 a 2**63-1: fora disso o pedido não existe (e o Postgres estouraria)."""
    return isinstance(id, int) and not isinstance(id, bool) and 0 < id < 2**63


class PedidoIARepositoryMemoria(PedidoIARepository):
    """Mesma regra do Postgres, com relógio injetável. `vaga_por_url` faz o papel da chave estrangeira."""

    def __init__(
        self,
        vaga_por_url: Callable[[str], VagaEncontrada | None] | None = None,
        relogio: Callable[[], datetime] | None = None,
    ) -> None:
        self._vaga_por_url = vaga_por_url
        self._relogio = relogio or (lambda: datetime.now(timezone.utc))
        self._linhas: dict[int, PedidoIA] = {}
        self._rotulos: dict[str, Faixa] = {}
        self._proximo_id = 1
        self._lock = threading.Lock()

    def _existe(self, vaga_url: str) -> bool:
        return self._vaga_por_url is None or self._vaga_por_url(vaga_url) is not None

    def criar(self, vaga_url: str, tipo: TipoPedidoIA | str) -> tuple[PedidoIA, bool]:
        vaga_url = validar_vaga_url(vaga_url)
        tipo = validar_tipo(tipo)
        with self._lock:
            ativo = next(
                (p for p in self._linhas.values() if p.vaga_url == vaga_url and p.tipo == tipo and p.ativo), None
            )
            if ativo is not None:
                return replace(ativo), False
            if not self._existe(vaga_url):
                raise DadoInvalido(f"vaga não encontrada no histórico: {vaga_url}")
            pedido = PedidoIA(self._proximo_id, tipo, vaga_url, criado_em=self._relogio())
            self._linhas[pedido.id] = pedido
            self._proximo_id += 1
            return replace(pedido), True

    def obter(self, id: int) -> PedidoIA | None:
        with self._lock:
            linha = self._linhas.get(id)
            return replace(linha) if linha else None

    def ultimo(self, vaga_url: str, tipo: TipoPedidoIA | str) -> PedidoIA | None:
        vaga_url = validar_vaga_url(vaga_url)
        tipo = validar_tipo(tipo)
        with self._lock:
            do_tipo = [p for p in self._linhas.values() if p.vaga_url == vaga_url and p.tipo == tipo]
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
            escolhido = min(candidatos, key=lambda p: (p.estado == EstadoPedidoIA.RODANDO, p.criado_em, p.id))
            escolhido.estado = EstadoPedidoIA.RODANDO
            escolhido.tentativas += 1
            escolhido.reivindicado_em = agora
            return replace(escolhido)

    def _rodando(self, id: int, tentativa: int) -> PedidoIA | None:
        linha = self._linhas.get(id)
        if linha is None or linha.estado != EstadoPedidoIA.RODANDO or linha.tentativas != tentativa:
            return None
        return linha

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
                for p in sorted(self._linhas.values(), key=lambda p: p.id)
                if p.tipo == TipoPedidoIA.COMPATIBILIDADE
                and p.estado == EstadoPedidoIA.PRONTO
                and (prompt_versao is None or p.prompt_versao == prompt_versao)
            ]

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
