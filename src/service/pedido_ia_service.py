"""Pedidos de IA: a fila que a tela alimenta e que o worker do claude-pi consome.

O log traz só id, tipo, estado e tentativa: nunca resultado, erro, currículo nem descrição.
"""

import copy
import json
import logging
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from src.model.erros import DadoInvalido
from src.model.pedido_ia import (
    ERRO_SEM_BASE,
    MAX_ERRO,
    MAX_RESULTADO_BYTES,
    PRIORIDADE_LOTE,
    EstadoPedidoIA,
    Faixa,
    NotaIA,
    PedidoEmConflito,
    PedidoIA,
    PedidoInexistente,
    TipoPedidoIA,
)
from src.model.resultado_ia import (
    validar_prompt_versao,
    validar_resultado_compatibilidade,
    validar_resultado_curriculo,
)
from src.repository.base import (
    CurriculoRepository,
    PerfilBuscaRepository,
    VagaEncontradaRepository,
)
from src.repository.candidatura import validar_vaga_url
from src.repository.pedido_ia import PedidoIARepository, validar_faixa, validar_tipo
from src.repository.trava import ColetaEmAndamento, TravaColeta, TravaColetaMemoria
from src.service.detalhe_service import DetalheService
from src.service.metricas_ia import maior_versao

log = logging.getLogger(__name__)

MAX_URL = 2048
MSG_SEM_BASE = "Salve um currículo base antes de usar a IA"
# O currículo é adaptado pelo texto da vaga (e no idioma dela): sem a descrição, a IA adaptaria às cegas.
MSG_SEM_DESCRICAO = (
    "Não foi possível carregar a descrição da vaga agora, e o currículo precisa dela. "
    "Abra a vaga de novo mais tarde e peça o currículo."
)
# Teto de pedidos sem base descartados numa só chamada de `proximo`: a próxima chamada continua.
_MAX_DESCARTES = 100
# Lote: por padrão sem teto (enfileira todas as que faltam); com IA_LOTE_MAX_POR_DIA, no máximo tantos
# pedidos de prioridade 1 criados em 24 h.
LOTE_MAX_POR_DIA_PADRAO: int | None = None
# `max_lote_por_dia` não informado: lê do ambiente (None ali quer dizer sem teto).
_DO_AMBIENTE = object()
_JANELA_LOTE = timedelta(hours=24)
# Vaga cuja avaliação deu erro com a base atual fica fora do lote por este tempo: sem isso ela voltaria
# em toda coleta e gastaria o teto diário (e a cota) com o mesmo erro. O clique da pessoa ainda pede.
_ESPERA_ERRO_LOTE = timedelta(days=7)
ESPERA_TRAVA_LOTE_S = 30


@dataclass(frozen=True)
class FabricasPorPessoa:
    """Como o serviço chega aos repositórios da pessoa de um pedido da fila (que é de todas as pessoas).

    Cada fábrica recebe o `pessoa_id` e devolve o repositório DELA. Em produção são `_PorChamada`, que pega
    uma conexão do pool a cada chamada: criar o repositório não segura conexão nenhuma.
    """

    perfis: Callable[[int], PerfilBuscaRepository]
    curriculos: Callable[[int], CurriculoRepository]
    encontradas: Callable[[int], VagaEncontradaRepository]


def lote_max_por_dia(env: Mapping[str, str] | None = None) -> int | None:
    """IA_LOTE_MAX_POR_DIA: inteiro >= 1 é o teto em 24 h, 0 desliga o lote; ausente, vazio ou inválido
    cai no padrão (None, sem teto), sem derrubar a app."""
    bruto = (os.environ if env is None else env).get("IA_LOTE_MAX_POR_DIA", "").strip()
    try:
        valor = int(bruto)
    except ValueError:
        return LOTE_MAX_POR_DIA_PADRAO
    return valor if valor >= 0 else LOTE_MAX_POR_DIA_PADRAO


def _log(pedido: PedidoIA) -> None:
    log.info("pedido_ia id=%s tipo=%s estado=%s tentativa=%s", pedido.id, pedido.tipo.value, pedido.estado.value, pedido.tentativas)


def _requisito(item: Any) -> dict | None:
    """Requisito da vaga como {texto, obrigatorio}; aceita objeto ou dict (JSONB), descarta o resto."""
    if isinstance(item, dict):
        texto, obrigatorio = item.get("texto"), item.get("obrigatorio")
    else:
        texto, obrigatorio = getattr(item, "texto", None), getattr(item, "obrigatorio", None)
    if not isinstance(texto, str) or not texto.strip():
        return None
    return {"texto": texto, "obrigatorio": bool(obrigatorio)}


class PedidoIAService:
    def __init__(
        self,
        pedidos: PedidoIARepository,
        encontradas: VagaEncontradaRepository,
        perfis: PerfilBuscaRepository,
        curriculos: CurriculoRepository,
        max_lote_por_dia: int | None | object = _DO_AMBIENTE,
        relogio: Callable[[], datetime] | None = None,
        trava_lote: TravaColeta | None = None,
        detalhe: DetalheService | None = None,
        fabricas: FabricasPorPessoa | None = None,
    ):
        # `pedidos`, `encontradas`, `perfis` e `curriculos` são da pessoa da requisição (tela, lote, pausa).
        # O worker lê a fila de todas as pessoas: `proximo` e `concluir` usam as `fabricas` com a pessoa do
        # pedido. Sem elas (testes de uma pessoa só), os repositórios acima valem para todos os pedidos.
        self.fabricas = fabricas
        self.pedidos = pedidos
        # Busca a descrição que faltar antes do pedido de currículo. None (testes): não busca nada.
        self.detalhe = detalhe
        self.encontradas = encontradas
        self.perfis = perfis
        self.curriculos = curriculos
        # None = sem teto; 0 = lote desligado.
        self.max_lote_por_dia: int | None = (
            lote_max_por_dia() if max_lote_por_dia is _DO_AMBIENTE else max_lote_por_dia
        )
        self._relogio = relogio or (lambda: datetime.now(timezone.utc))
        # Um enfileiramento por vez (coleta do timer, botão, duas abas): sem isso as chamadas leriam
        # a mesma contagem do teto diário e cada uma criaria até o teto. Produção usa a do Postgres.
        self.trava_lote = trava_lote if trava_lote is not None else TravaColetaMemoria()

    def da_pessoa(
        self,
        pedidos: PedidoIARepository,
        encontradas: VagaEncontradaRepository,
        perfis: PerfilBuscaRepository,
        curriculos: CurriculoRepository,
        detalhe: DetalheService | None,
    ) -> "PedidoIAService":
        """O mesmo serviço (teto do lote, relógio, trava, fábricas) com os repositórios de OUTRA pessoa: a da
        requisição. É uma cópia rasa, então a configuração e o que os testes trocaram na instância de origem
        valem para ela."""
        copia = copy.copy(self)
        copia.pedidos, copia.encontradas, copia.perfis, copia.curriculos, copia.detalhe = (
            pedidos,
            encontradas,
            perfis,
            curriculos,
            detalhe,
        )
        return copia

    def pedir(self, vaga_url: str, tipo: TipoPedidoIA | str) -> tuple[PedidoIA, bool]:
        """(pedido, criado). Já havendo um ativo para (vaga, tipo), devolve o ativo com criado=False."""
        tipo = validar_tipo(tipo)
        vaga_url = validar_vaga_url(vaga_url)
        if len(vaga_url) > MAX_URL:
            raise DadoInvalido(f"vaga_url passa de {MAX_URL} caracteres")
        if self.encontradas.obter(vaga_url) is None:
            raise DadoInvalido("vaga não encontrada no histórico")
        base = self.curriculos.obter_base()
        if base is None:
            raise DadoInvalido(MSG_SEM_BASE)
        if tipo == TipoPedidoIA.CURRICULO:
            self._garantir_descricao(vaga_url)
        pedido, criado = self.pedidos.criar(vaga_url, tipo, curriculo_base_id=base.id)
        if criado:
            _log(pedido)
        return pedido, criado

    def _garantir_descricao(self, vaga_url: str) -> None:
        """Carrega a descrição da vaga se ainda faltar (o mesmo `carregar` de quando a vaga é aberta).

        Só recusa quando havia o que buscar e o provider falhou (LinkedIn limitando, rede): plataforma
        sem detalhe sob demanda, ou vaga já detalhada, segue como antes. A compatibilidade (lote) não
        passa por aqui: buscar o detalhe de cada vaga do lote estouraria o limite do LinkedIn.
        """
        if self.detalhe is None:
            return
        aberta = self.detalhe.carregar(vaga_url)
        if aberta is not None and aberta.aviso is not None:
            raise DadoInvalido(MSG_SEM_DESCRICAO)

    def _da_pessoa(self, pessoa_id: int | None) -> tuple[VagaEncontradaRepository, PerfilBuscaRepository, CurriculoRepository]:
        """(vagas, perfis, currículos) da pessoa dona do pedido."""
        if self.fabricas is None:
            return self.encontradas, self.perfis, self.curriculos
        if pessoa_id is None:
            raise ValueError("pedido sem pessoa_id")
        return self.fabricas.encontradas(pessoa_id), self.fabricas.perfis(pessoa_id), self.fabricas.curriculos(pessoa_id)

    def curriculos_do_pedido(self, pedido_id: int, vaga_url: str) -> CurriculoRepository:
        """O repositório de currículos da pessoa dona do pedido, para o worker gravar o adaptado dela.

        É o `POST /curriculos/adaptados` com `pedido_id`: o servidor tira a pessoa do pedido (o worker não
        escolhe). Pedido inexistente, que não é de currículo, que não está rodando ou de outra vaga é
        `DadoInvalido`. Rodando porque a rota não exige o Bearer do worker: sem isso qualquer id gravaria
        currículo na pessoa dona dele.
        """
        pedido = self.pedidos.obter_da_fila(pedido_id)
        if pedido is None:
            raise DadoInvalido("pedido_id: pedido não encontrado")
        if pedido.tipo != TipoPedidoIA.CURRICULO or pedido.estado != EstadoPedidoIA.RODANDO:
            raise DadoInvalido("pedido_id: não é um pedido de currículo em andamento")
        if pedido.vaga_url != vaga_url:
            raise DadoInvalido("pedido_id: o pedido é de outra vaga")
        return self._da_pessoa(pedido.pessoa_id)[2]

    def versao_compatibilidade(self) -> str | None:
        """A versão do prompt de compatibilidade que vale: a maior `compatibilidade-vN` já vista nos
        prontos. O vagas-svc não sabe qual o worker usa; ele só vê o que volta."""
        return maior_versao(self.pedidos.versoes_compatibilidade())

    def notas_validas(self) -> dict[str, NotaIA]:
        """vaga_url -> nota que vale para a base vigente e a versão atual do prompt. Sem base, nenhuma."""
        base = self.curriculos.obter_base()
        if base is None:
            return {}
        return self.pedidos.notas_compatibilidade(base.id, self.versao_compatibilidade())

    def enfileirar_lote(self, candidatas: Iterable, limite: int | None = None) -> int:
        """`_enfileirar_lote` sob a trava do lote. Com outro enfileiramento rodando, ESPERA a vez
        (até `ESPERA_TRAVA_LOTE_S`; o normal é levar milissegundos) em vez de desistir: assim as
        vagas novas da coleta não ficam para o disparo seguinte, e o teto é relido dentro da trava.
        Só se a espera estourar devolve 0."""
        candidatas = list(candidatas)
        try:
            with self.trava_lote.segurar(esperar_s=ESPERA_TRAVA_LOTE_S):
                return self._enfileirar_lote(candidatas, limite)
        except ColetaEmAndamento:
            log.warning("pedido_ia lote: outro enfileiramento passou de %ss; nada enfileirado", ESPERA_TRAVA_LOTE_S)
            return 0

    def _enfileirar_lote(self, candidatas: Iterable, limite: int | None = None) -> int:
        """Cria pedidos de compatibilidade do lote (prioridade 1) para as `candidatas`, na ordem dada,
        pulando as que já têm pedido ativo ou nota válida. Devolve quantos criou.

        Respeita o teto diário, se houver (`max_lote_por_dia` pedidos do lote criados nas últimas 24 h),
        e, se informado, `limite`. Sem currículo base, ou com o lote pausado, não enfileira nada.
        """
        if self.pedidos.lote_pausado():
            return 0
        base = self.curriculos.obter_base()
        if base is None:
            return 0
        restante = self.restante_lote()
        if limite is not None:
            restante = limite if restante is None else min(restante, limite)
        if restante is not None and restante <= 0:
            return 0
        pular = self.pedidos.urls_ativas(TipoPedidoIA.COMPATIBILIDADE)
        pular |= set(self.pedidos.notas_compatibilidade(base.id, self.versao_compatibilidade()))
        pular |= self.pedidos.urls_com_erro_desde(base.id, self._relogio() - _ESPERA_ERRO_LOTE)
        criados = 0
        for vaga in candidatas:
            if restante is not None and criados >= restante:
                break
            if vaga.url in pular:
                continue
            pular.add(vaga.url)
            try:
                pedido, criado = self.pedidos.criar(
                    vaga.url, TipoPedidoIA.COMPATIBILIDADE, prioridade=PRIORIDADE_LOTE, curriculo_base_id=base.id
                )
            except DadoInvalido:
                continue  # a vaga saiu do histórico entre a leitura e o pedido
            if criado:
                criados += 1
                _log(pedido)
        log.info("pedido_ia lote criados=%s", criados)
        return criados

    def lote_pausado(self) -> bool:
        return self.pedidos.lote_pausado()

    def pausar_lote(self) -> int:
        """O botão "Pausar avaliação": tira da fila o lote pendente e para o lote automático da coleta.
        Sob a mesma trava do enfileiramento: uma coleta enfileirando agora termina antes, e nada que
        ela criar fica para trás na fila. Devolve quantos pedidos saíram da fila."""
        try:
            with self.trava_lote.segurar(esperar_s=ESPERA_TRAVA_LOTE_S):
                removidos = self.pedidos.pausar_lote()
        except ColetaEmAndamento:
            raise DadoInvalido("A fila da IA está sendo montada agora. Tente pausar de novo em instantes.") from None
        log.info("pedido_ia lote pausado removidos=%s", removidos)
        return removidos

    def retomar_lote(self, candidatas: Iterable) -> int:
        """O botão "Retomar avaliação": tira a pausa e já manda para a fila as vagas sem nota."""
        self.pedidos.retomar_lote()
        log.info("pedido_ia lote retomado")
        return self.enfileirar_lote(candidatas)

    def restante_lote(self) -> int | None:
        """Quantos pedidos do lote ainda cabem nas próximas 24 h (mínimo 0); None = sem teto. O teto é a cota do
        worker (a assinatura): conta o lote de todas as pessoas, não só o da pessoa do serviço."""
        if self.max_lote_por_dia is None:
            return None
        if self.max_lote_por_dia <= 0:
            return 0
        criados = self.pedidos.contar_lote_desde(self._relogio() - _JANELA_LOTE)
        return max(0, self.max_lote_por_dia - criados)

    def progresso_lote(
        self, candidatas: Iterable, notas: Mapping[str, NotaIA] | None = None
    ) -> tuple[int, int, int]:
        """(avaliadas, na_fila, sem_nota) entre as `candidatas`: com nota válida, com compatibilidade
        ativa e as que o botão "Avaliar" mandaria (sem nota, fora da fila e sem erro recente com a base
        vigente, que `_enfileirar_lote` pula). `notas` já lidas (as de `notas_validas`) evitam ler de novo."""
        urls = {v.url for v in candidatas}
        com_nota = urls & set(self.notas_validas() if notas is None else notas)
        ativas = urls & self.pedidos.urls_ativas(TipoPedidoIA.COMPATIBILIDADE)
        base = self.curriculos.obter_base()
        em_espera = (
            self.pedidos.urls_com_erro_desde(base.id, self._relogio() - _ESPERA_ERRO_LOTE) if base is not None else set()
        )
        return len(com_nota), len(ativas), len(urls - com_nota - ativas - em_espera)

    def obter(self, id: int) -> PedidoIA | None:
        return self.pedidos.obter(id)

    def ultimos(self, vaga_url: str) -> dict[str, PedidoIA | None]:
        """O último pedido de cada tipo da vaga (None se nunca pediu); as chaves são os valores do tipo."""
        return {tipo.value: self.pedidos.ultimo(vaga_url, tipo) for tipo in TipoPedidoIA}

    def proximo(self) -> dict | None:
        """O pedido do §5 do contrato para o worker, ou None com a fila vazia.

        Pedido sem currículo base vira erro e o seguinte é tentado. Não busca descrição no provider: lê o
        que a vaga já tem guardado (`descricao`, `requisitos` e `subtitulo` só existem depois do detalhe).
        """
        for _ in range(_MAX_DESCARTES):
            pedido = self.pedidos.proximo()
            if pedido is None:
                return None
            _log(pedido)
            # Os dados são da pessoa do pedido reivindicado, não da pessoa da requisição.
            encontradas, perfis, curriculos = self._da_pessoa(pedido.pessoa_id)
            base = curriculos.obter_base()
            vaga = encontradas.obter(pedido.vaga_url)
            if base is None or vaga is None:
                falho = self.pedidos.falhar(pedido.id, pedido.tentativas, ERRO_SEM_BASE if base is None else "Vaga não encontrada")
                if falho is not None:
                    _log(falho)
                continue
            if pedido.curriculo_base_id != base.id:
                # A nota vale para a base que o worker recebe agora, não a da criação do pedido.
                pedido = self.pedidos.marcar_base(pedido.id, pedido.tentativas, base.id) or pedido
            return self._montar(pedido, vaga, base, perfis.obter())
        return None

    def _montar(self, pedido: PedidoIA, vaga, base, perfil) -> dict:
        requisitos = [r for r in (_requisito(i) for i in (getattr(vaga, "requisitos", None) or [])) if r]
        return {
            "id": pedido.id,
            "tipo": pedido.tipo.value,
            "tentativa": pedido.tentativas,
            "vaga": {
                "url": vaga.url,
                "titulo": vaga.titulo,
                "empresa": vaga.empresa,
                "plataforma": vaga.plataforma,
                "local": vaga.local,
                "remoto": vaga.remoto,
                "publicada_em": vaga.publicada_em.isoformat() if vaga.publicada_em else None,
                "descricao": getattr(vaga, "descricao", None),
                "requisitos": requisitos,
                "subtitulo": getattr(vaga, "subtitulo", None),
                # "R$ 3.000 por mês"; None quando a vaga não informa (a IA compara com o salário mínimo do perfil).
                "salario": (vaga.salario.texto() or None) if getattr(vaga, "salario", None) else None,
            },
            "curriculo_base": {"id": base.id, "titulo": base.titulo, "conteudo_md": base.conteudo_md},
            "perfil": None if perfil is None else self._perfil(perfil),
        }

    @staticmethod
    def _perfil(perfil) -> dict:
        return {
            "termos": list(perfil.termos),
            "excluir": list(perfil.excluir),
            "modalidades": [m.value for m in perfil.modalidades],
            "localidades": list(perfil.localidades),
            "plataformas": list(perfil.plataformas),
            "niveis": [n.value for n in perfil.niveis],
            "skills": list(perfil.skills),
            "publicada_desde_dias": perfil.publicada_desde_dias,
            "salario_minimo": perfil.salario_minimo,
            "salario_minimo_usd": perfil.salario_minimo_usd,
            "ocultar_candidatadas": perfil.ocultar_candidatadas,
        }

    def _rodando(self, id: int, tentativa: int) -> PedidoIA:
        """404 se não existe; 409 se não está rodando ou a tentativa não é a atual."""
        # O worker lê a fila de todas as pessoas: o `obter` do repositório só acha os pedidos da pessoa dele.
        pedido = self.pedidos.obter_da_fila(id)
        if pedido is None:
            raise PedidoInexistente(id)
        if pedido.estado.value != "rodando" or pedido.tentativas != tentativa:
            raise PedidoEmConflito(id)
        return pedido

    def concluir(
        self, id: int, tentativa: int, resultado: dict, prompt_versao: str, modelo: str
    ) -> PedidoIA:
        """rodando -> pronto, com o resultado validado pelo tipo do pedido (nada muda se for inválido)."""
        pedido = self._rodando(id, tentativa)
        validar_prompt_versao(pedido.tipo, prompt_versao)
        tamanho = len(json.dumps(resultado, ensure_ascii=False).encode("utf-8"))
        if tamanho > MAX_RESULTADO_BYTES:
            raise DadoInvalido(f"resultado passa de {MAX_RESULTADO_BYTES} bytes")
        nota = faixa = curriculo_id = None
        if pedido.tipo == TipoPedidoIA.COMPATIBILIDADE:
            validar_resultado_compatibilidade(resultado)
            nota, faixa = resultado["nota"], Faixa(resultado["faixa"])
        else:
            validar_resultado_curriculo(resultado)
            curriculo_id = resultado["curriculo_id"]
            # O repositório é o da pessoa do pedido: o currículo de outra pessoa não existe para ele.
            adaptado = self._da_pessoa(pedido.pessoa_id)[2].obter(curriculo_id)
            if adaptado is None or adaptado.tipo != "adaptado" or adaptado.vaga_url != pedido.vaga_url:
                raise DadoInvalido("curriculo_id: não é um currículo adaptado desta vaga e desta pessoa")
        pronto = self.pedidos.concluir(id, tentativa, resultado, prompt_versao, modelo, nota, faixa, curriculo_id)
        if pronto is None:
            raise PedidoEmConflito(id)
        _log(pronto)
        return pronto

    def falhar(self, id: int, tentativa: int, erro: str, prompt_versao: str, modelo: str) -> PedidoIA:
        """rodando -> erro; o texto é cortado em 500 caracteres."""
        pedido = self._rodando(id, tentativa)
        validar_prompt_versao(pedido.tipo, prompt_versao)
        if not isinstance(erro, str) or not erro.strip():
            raise DadoInvalido("erro: não pode ser vazio")
        # O Postgres não grava NUL em TEXT.
        erro = erro.replace("\x00", "")[:MAX_ERRO]
        falho = self.pedidos.falhar(id, tentativa, erro, prompt_versao, modelo)
        if falho is None:
            raise PedidoEmConflito(id)
        _log(falho)
        return falho

    def rotular(self, vaga_url: str, faixa: Faixa | str | None) -> Faixa | None:
        """Grava o rótulo da pessoa para a vaga; `None` apaga. Vaga fora do histórico é DadoInvalido."""
        vaga_url = validar_vaga_url(vaga_url)
        faixa = validar_faixa(faixa)
        if faixa is not None and self.encontradas.obter(vaga_url) is None:
            raise DadoInvalido("vaga não encontrada no histórico")
        return self.pedidos.gravar_rotulo(vaga_url, faixa)

    def rotulo(self, vaga_url: str) -> Faixa | None:
        return self.pedidos.obter_rotulo(vaga_url)
