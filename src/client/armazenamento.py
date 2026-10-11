import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def ler_segredo(env: Mapping[str, str], nome: str) -> str | None:
    """Valor de `nome` ou o conteúdo do arquivo em `nome_FILE` (secrets do Swarm)."""
    if env.get(nome):
        return env[nome]
    arquivo = env.get(f"{nome}_FILE")
    return Path(arquivo).read_text(encoding="utf-8").strip() if arquivo else None


def criar_cliente_s3(env: Mapping[str, str]) -> Any:
    """Cliente S3 do boto3. No Pi, S3_ENDPOINT=http://garage:3900 (alias na public_network): o botocore
    recusa host com "_" ("Invalid endpoint"), então o nome completo do service do Swarm não serve."""
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=env.get("S3_ENDPOINT") or None,
        region_name=env.get("S3_REGION") or "garage",
        aws_access_key_id=env.get("S3_ACCESS_KEY_ID") or None,
        aws_secret_access_key=ler_segredo(env, "S3_SECRET_ACCESS_KEY"),
        config=Config(
            # O Garage não resolve bucket por subdomínio e não exige os checksums
            # que o boto3 passou a mandar por padrão.
            s3={"addressing_style": "path"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


class Armazenamento:
    """Arquivos no bucket S3 (Garage no Pi). O cliente boto3 só é criado no primeiro uso."""

    def __init__(self, cliente: Any = None, bucket: str | None = None, env: Mapping[str, str] | None = None):
        self._env = os.environ if env is None else env
        self._cliente = cliente
        self.bucket = bucket or self._env.get("S3_BUCKET") or "vagas"

    @property
    def cliente(self) -> Any:
        if self._cliente is None:
            self._cliente = criar_cliente_s3(self._env)
        return self._cliente

    def enviar(self, chave: str, dados: bytes, content_type: str) -> str:
        """Grava o objeto e devolve a chave, que é o que vai em `Curriculo.arquivo_s3`."""
        self.cliente.put_object(Bucket=self.bucket, Key=chave, Body=dados, ContentType=content_type)
        return chave

    def baixar(self, chave: str) -> bytes:
        resposta = self.cliente.get_object(Bucket=self.bucket, Key=chave)
        return resposta["Body"].read()
