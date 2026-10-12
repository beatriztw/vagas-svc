"""Rotas do worker de IA (`/pedidos-ia`, com Bearer) e o rótulo da faixa (`PUT /vagas/rotulo-ia`).

As rotas do worker entregam currículo e perfil e aceitam escrita; por isso exigem token, ao contrário do
resto da API (só da rede interna). Sem token configurado respondem 503 e a app segue de pé. O servidor
nunca loga `resultado`, `erro`, currículo nem descrição.
"""

import hmac
import os
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from src.controller.candidaturas import exigir_json
from src.model.erros import DadoInvalido
from src.model.pedido_ia import Faixa, PedidoEmConflito, PedidoInexistente
from src.service.pedido_ia_service import PedidoIAService

SEM_CACHE = {"Cache-Control": "no-store"}


def token_configurado() -> str | None:
    """O token do worker: o arquivo de VAGAS_IA_TOKEN_FILE ou, na falta dele, VAGAS_IA_TOKEN.

    Ausente, ilegível ou vazio (depois do strip) é None: a rota responde 503 em vez de derrubar a app.
    """
    arquivo = os.environ.get("VAGAS_IA_TOKEN_FILE")
    if arquivo:
        try:
            with open(arquivo, encoding="utf-8") as f:
                token = f.read().strip()
        except (OSError, UnicodeDecodeError):
            token = ""
        if token:
            return token
    return os.environ.get("VAGAS_IA_TOKEN", "").strip() or None


def exigir_worker_ia(request: Request, response: Response) -> None:
    """503 sem token configurado; 401 (com WWW-Authenticate) sem Bearer ou com token errado."""
    esperado = token_configurado()
    if esperado is None:
        raise HTTPException(503, "Token do worker de IA não configurado", headers=SEM_CACHE)
    esquema, _, enviado = request.headers.get("authorization", "").partition(" ")
    if esquema.lower() != "bearer" or not hmac.compare_digest(enviado.strip().encode(), esperado.encode()):
        raise HTTPException(401, "Token inválido", headers={**SEM_CACHE, "WWW-Authenticate": "Bearer"})
    response.headers["Cache-Control"] = "no-store"


class ResultadoDoWorker(BaseModel):
    """Corpo do PUT /pedidos-ia/{id}: pronto com `resultado` ou erro com `erro`, nunca os dois."""

    model_config = ConfigDict(extra="forbid")

    tentativa: StrictInt = Field(ge=1)
    estado: Literal["pronto", "erro"]
    prompt_versao: str = Field(pattern=r"^[a-z]+-v[0-9]+$")
    modelo: str = Field(min_length=1, max_length=100)
    resultado: dict[str, Any] | None = None
    erro: str | None = None

    @model_validator(mode="after")
    def _um_dos_dois(self):
        if self.estado == "pronto" and (self.resultado is None or self.erro is not None):
            raise ValueError("pronto exige resultado e proíbe erro")
        if self.estado == "erro" and (not self.erro or not self.erro.strip() or self.resultado is not None):
            raise ValueError("erro exige o texto do erro e proíbe resultado")
        return self


class RotuloJson(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vaga_url: str = Field(min_length=1)
    # Obrigatório (sem default): null apaga o rótulo; `media` sem acento é recusado.
    faixa: Faixa | None


def _json(status: int, conteudo: dict) -> JSONResponse:
    return JSONResponse(status_code=status, content=conteudo, headers=SEM_CACHE)


def criar_router_pedidos_ia(servico: PedidoIAService) -> APIRouter:
    router = APIRouter()
    # Primeiro a autenticação: sem token, nada do corpo é lido.
    protegidas = [Depends(exigir_worker_ia), Depends(exigir_json)]

    @router.post("/pedidos-ia/proximo", include_in_schema=False, dependencies=protegidas)
    def proximo():
        pedido = servico.proximo()
        if pedido is None:
            return Response(status_code=204, headers=SEM_CACHE)
        return JSONResponse(content=pedido, headers=SEM_CACHE)

    @router.put("/pedidos-ia/{id}", include_in_schema=False, dependencies=protegidas)
    def resultado(id: int, corpo: ResultadoDoWorker):
        try:
            if corpo.estado == "pronto":
                pedido = servico.concluir(id, corpo.tentativa, corpo.resultado, corpo.prompt_versao, corpo.modelo)
            else:
                pedido = servico.falhar(id, corpo.tentativa, corpo.erro, corpo.prompt_versao, corpo.modelo)
        except PedidoInexistente:
            return _json(404, {"detail": "Pedido não encontrado"})
        except PedidoEmConflito:
            return _json(409, {"detail": "Pedido não está rodando ou a tentativa não é a atual"})
        except DadoInvalido as erro:
            return _json(422, {"detail": str(erro)})
        return _json(200, {"id": pedido.id, "estado": pedido.estado.value})

    @router.put("/vagas/rotulo-ia", dependencies=[Depends(exigir_json)])
    def rotular(corpo: RotuloJson) -> dict:
        faixa = servico.rotular(corpo.vaga_url, corpo.faixa)
        return {"vaga_url": corpo.vaga_url, "faixa": faixa.value if faixa else None}

    return router
