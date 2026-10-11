import os
from collections.abc import Mapping

import psycopg


def parametros(env: Mapping[str, str] | None = None) -> dict:
    """Parâmetros de conexão a partir das variáveis PG*. A senha vem de PGPASSWORD ou do arquivo em PGPASSWORD_FILE."""
    env = os.environ if env is None else env
    senha = env.get("PGPASSWORD")
    arquivo = env.get("PGPASSWORD_FILE")
    if not senha and arquivo:
        with open(arquivo, encoding="utf-8") as f:
            senha = f.read().strip()
    return {
        "host": env.get("PGHOST"),
        "port": int(env.get("PGPORT") or 5432),
        "dbname": env.get("PGDATABASE"),
        "user": env.get("PGUSER"),
        "password": senha,
    }


def conectar(env: Mapping[str, str] | None = None) -> psycopg.Connection:
    """Conexão em autocommit: cada gravação dos repositórios roda na própria transação."""
    return psycopg.connect(**parametros(env), autocommit=True)
