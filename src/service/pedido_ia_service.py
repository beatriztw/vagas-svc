"""Pedidos de IA: a fila que a tela alimenta e que o worker do claude-pi consome.

O log traz só id, tipo, estado e tentativa: nunca resultado, erro, currículo nem descrição.
"""

import json
import logging
from typing import Any

from src.model.erros import DadoInvalido
from src.model.pedido_ia import (
    ERRO_SEM_BASE,
    MAX_ERRO,
    MAX_RESULTADO_BYTES,
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

log = logging.getLogger(__name__)

MAX_URL = 2048
MSG_SEM_BASE = "Salve um currículo base antes de usar a IA"
# Teto de pedidos sem base descartados numa só chamada de `proximo`: a próxima chamada continua.
_MAX_DESCARTES = 100


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
    ):
        self.pedidos = pedidos
        self.encontradas = encontradas
        self.perfis = perfis
        self.curriculos = curriculos

    def pedir(self, vaga_url: str, tipo: TipoPedidoIA | str) -> tuple[PedidoIA, bool]:
        """(pedido, criado). Já havendo um ativo para (vaga, tipo), devolve o ativo com criado=False."""
        tipo = validar_tipo(tipo)
        vaga_url = validar_vaga_url(vaga_url)
        if len(vaga_url) > MAX_URL:
            raise DadoInvalido(f"vaga_url passa de {MAX_URL} caracteres")
        if self.encontradas.obter(vaga_url) is None:
            raise DadoInvalido("vaga não encontrada no histórico")
        if self.curriculos.obter_base() is None:
            raise DadoInvalido(MSG_SEM_BASE)
        pedido, criado = self.pedidos.criar(vaga_url, tipo)
        if criado:
            _log(pedido)
        return pedido, criado

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
