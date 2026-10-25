"""Rotas JSON das fontes de vaga da pessoa (`GET/PUT /perfil/providers`).

O CSRF do PUT vem da dependência do app (`seguranca_json.py`). A config nunca carrega segredo: o token do Apify
fica no ambiente, e uma chave desconhecida na config é recusada (422)."""

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from src.controller.contexto_pessoa import ContextoPessoa
from src.model.provider_pessoa import ProviderPessoa


class ProviderJson(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    config: dict[str, Any] = {}


class ProvidersJson(BaseModel):
    """Lista vazia = a pessoa usa as fontes padrão do servidor."""

    model_config = ConfigDict(extra="forbid")

    providers: list[ProviderJson] = []

    @classmethod
    def de(cls, itens: list[ProviderPessoa]) -> "ProvidersJson":
        return cls(providers=[ProviderJson(provider=i.provider, config=i.config) for i in itens])


def criar_router_perfil_providers(contexto_da_pessoa: Callable[..., ContextoPessoa]) -> APIRouter:
    router = APIRouter()

    @router.get("/perfil/providers")
    def obter(ctx: ContextoPessoa = Depends(contexto_da_pessoa)) -> ProvidersJson:
        return ProvidersJson.de(ctx.providers.listar())

    @router.put("/perfil/providers")
    def salvar(corpo: ProvidersJson, ctx: ContextoPessoa = Depends(contexto_da_pessoa)) -> ProvidersJson:
        # Valida no repositório (DadoInvalido -> 422) antes de gravar: lista inválida não grava nem parte dela.
        ctx.providers.salvar([ProviderPessoa(p.provider, p.config) for p in corpo.providers])
        return ProvidersJson.de(ctx.providers.listar())

    return router
