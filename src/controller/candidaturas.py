"""Rotas JSON das candidaturas (POST/GET /candidaturas, PATCH/DELETE /candidaturas/{id})."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from src.model.candidatura import CandidaturaComVaga, StatusCandidatura
from src.service.candidatura_service import CandidaturaService


class NovaCandidatura(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vaga_url: str = Field(min_length=1)


class MudancaDeStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Fora do enum: o Pydantic recusa com 422, igual ao DadoInvalido do repositório.
    status: StatusCandidatura


class CandidaturaJson(BaseModel):
    id: int
    vaga_url: str
    status: StatusCandidatura
    status_rotulo: str
    aplicada_em: datetime
    atualizada_em: datetime
    titulo: str
    empresa: str

    @classmethod
    def de(cls, c: CandidaturaComVaga) -> "CandidaturaJson":
        return cls(**{**vars(c), "status_rotulo": c.status.rotulo})


def exigir_json(request: Request) -> None:
    """CSRF: um POST de outro site (fetch no-cors com Blob sem tipo, form text/plain) não manda
    `application/json` sem preflight. Sem esse tipo, 415 e nada é lido nem gravado."""
    tipo = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if tipo != "application/json" and not (tipo.startswith("application/") and tipo.endswith("+json")):
        raise HTTPException(415, "Content-Type tem que ser application/json")


def criar_router_candidaturas(servico: CandidaturaService) -> APIRouter:
    router = APIRouter()

    @router.post("/candidaturas", status_code=201, dependencies=[Depends(exigir_json)])
    def marcar_aplicada(corpo: NovaCandidatura, resposta: Response) -> CandidaturaJson:
        # Vaga fora do histórico: DadoInvalido do repositório, 422.
        candidatura, criada = servico.marcar_aplicada(corpo.vaga_url)
        if not criada:
            resposta.status_code = 200
        return CandidaturaJson.de(candidatura)

    @router.get("/candidaturas")
    def listar_candidaturas() -> list[CandidaturaJson]:
        return [CandidaturaJson.de(c) for c in servico.listar()]

    @router.patch("/candidaturas/{id}")
    def mudar_status(id: int, corpo: MudancaDeStatus) -> CandidaturaJson:
        candidatura = servico.mudar_status(id, corpo.status)
        if candidatura is None:
            raise HTTPException(404, f"Candidatura {id} não encontrada")
        return CandidaturaJson.de(candidatura)

    @router.delete("/candidaturas/{id}", status_code=204)
    def desfazer(id: int) -> Response:
        if not servico.desfazer(id):
            raise HTTPException(404, f"Candidatura {id} não encontrada")
        return Response(status_code=204)

    return router
