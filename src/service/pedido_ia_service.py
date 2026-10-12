"""Pedidos de IA: a fila que a tela alimenta e que o worker do claude-pi consome.

O log traz só id, tipo, estado e tentativa: nunca resultado, erro, currículo nem descrição.
"""

import json
import logging
import os
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from src.model.erros import DadoInvalido
from src.model.pedido_ia import (
    ERRO_SEM_BASE,
    MAX_ERRO,
    MAX_RESULTADO_BYTES,
    PRIORIDADE_LOTE,
    NotaIA,
    PedidoEmConflito,
    PedidoIA,
    PedidoInexistente,
    TipoPedidoIA,
    Faixa,
)
from src.model.resultado_ia import (
    validar_prompt_versao,
    validar_resultado_compatibilidade,
    validar_resultado_curriculo,
)
from src.repository.base import CurriculoRepository, PerfilBuscaRepository, VagaEncontradaRepository
from src.repository.candidatura import validar_vaga_url
from src.repository.pedido_ia import PedidoIARepository, validar_faixa, validar_tipo
from src.service.metricas_ia import maior_versao

log = logging.getLogger(__name__)

MAX_URL = 2048
MSG_SEM_BASE = "Salve um currículo base antes de usar a IA"
# Teto de pedidos sem base descartados numa só chamada de `proximo`: a próxima chamada continua.
_MAX_DESCARTES = 100
# Lote: no máximo tantos pedidos de prioridade 1 criados em 24 h (a cota da assinatura do worker é curta).
LOTE_MAX_POR_DIA_PADRAO = 40
_JANELA_LOTE = timedelta(hours=24)


def lote_max_por_dia(env: Mapping[str, str] | None = None) -> int:
    """IA_LOTE_MAX_POR_DIA: inteiro >= 0 (0 desliga o lote). Inválido cai no padrão, sem derrubar a app."""
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
        max_lote_por_dia: int | None = None,
        relogio: Callable[[], datetime] | None = None,
    ):
        self.pedidos = pedidos
        self.encontradas = encontradas
        self.perfis = perfis
        self.curriculos = curriculos
        self.max_lote_por_dia = lote_max_por_dia() if max_lote_por_dia is None else max_lote_por_dia
        self._relogio = relogio or (lambda: datetime.now(timezone.utc))

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
        pedido, criado = self.pedidos.criar(vaga_url, tipo, curriculo_base_id=base.id)
        if criado:
            _log(pedido)
        return pedido, criado

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
        """Cria pedidos de compatibilidade do lote (prioridade 1) para as `candidatas`, na ordem dada,
        pulando as que já têm pedido ativo ou nota válida. Devolve quantos criou.

        Respeita o teto diário (`max_lote_por_dia` pedidos do lote criados nas últimas 24 h) e, se
        informado, `limite`. Sem currículo base não enfileira nada.
        """
        base = self.curriculos.obter_base()
        if base is None:
            return 0
        restante = self.max_lote_por_dia - self.pedidos.contar_lote_desde(self._relogio() - _JANELA_LOTE)
        if limite is not None:
            restante = min(restante, limite)
        if restante <= 0:
            return 0
        pular = self.pedidos.urls_ativas(TipoPedidoIA.COMPATIBILIDADE)
        pular |= set(self.pedidos.notas_compatibilidade(base.id, self.versao_compatibilidade()))
        criados = 0
        for vaga in candidatas:
            if criados >= restante:
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

    def progresso_lote(self, candidatas: Iterable) -> tuple[int, int]:
        """(avaliadas, na_fila) entre as `candidatas`: com nota válida e com compatibilidade ativa."""
        urls = {v.url for v in candidatas}
        avaliadas = len(urls & set(self.notas_validas()))
        na_fila = len(urls & self.pedidos.urls_ativas(TipoPedidoIA.COMPATIBILIDADE))
        return avaliadas, na_fila

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
            base = self.curriculos.obter_base()
            vaga = self.encontradas.obter(pedido.vaga_url)
            if base is None or vaga is None:
                falho = self.pedidos.falhar(pedido.id, pedido.tentativas, ERRO_SEM_BASE if base is None else "Vaga não encontrada")
                if falho is not None:
                    _log(falho)
                continue
            if pedido.curriculo_base_id != base.id:
                # A nota vale para a base que o worker recebe agora, não a da criação do pedido.
                pedido = self.pedidos.marcar_base(pedido.id, pedido.tentativas, base.id) or pedido
            return self._montar(pedido, vaga, base)
        return None

    def _montar(self, pedido: PedidoIA, vaga, base) -> dict:
        perfil = self.perfis.obter()
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
            "ocultar_candidatadas": perfil.ocultar_candidatadas,
        }

    def _rodando(self, id: int, tentativa: int) -> PedidoIA:
        """404 se não existe; 409 se não está rodando ou a tentativa não é a atual."""
        pedido = self.pedidos.obter(id)
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
            adaptado = self.curriculos.obter(curriculo_id)
            if adaptado is None or adaptado.tipo != "adaptado" or adaptado.vaga_url != pedido.vaga_url:
                raise DadoInvalido("curriculo_id: não é um currículo adaptado desta vaga")
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
