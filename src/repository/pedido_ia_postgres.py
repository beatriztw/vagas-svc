from datetime import datetime

import psycopg
from psycopg import errors
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

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
from src.repository.candidatura import validar_vaga_url
from src.repository.pedido_ia import PedidoIARepository, id_possivel, validar_faixa, validar_tipo

_COLUNAS = (
    "id, tipo, vaga_url, estado, tentativas, resultado, erro, prompt_versao, modelo, nota, faixa, "
    "curriculo_id, criado_em, reivindicado_em, terminado_em"
)
# `AGORA` é o parâmetro do relógio; sem ele, o now() da transação.
_AGORA = "COALESCE(%(agora)s::timestamptz, now())"
_PRAZO = f"{_AGORA} - make_interval(secs => %(prazo)s)"


def _pedido(linha: dict) -> PedidoIA:
    return PedidoIA(
        **{
            **linha,
            "tipo": TipoPedidoIA(linha["tipo"]),
            "estado": EstadoPedidoIA(linha["estado"]),
            "faixa": Faixa(linha["faixa"]) if linha["faixa"] else None,
        }
    )


class PedidoIARepositoryPostgres(PedidoIARepository):
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def _um(self, filtro: str, params: tuple) -> PedidoIA | None:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f"SELECT {_COLUNAS} FROM pedido_ia WHERE {filtro}", params)
            linha = cur.fetchone()
        return _pedido(linha) if linha else None

    def criar(self, vaga_url: str, tipo: TipoPedidoIA | str) -> tuple[PedidoIA, bool]:
        vaga_url = validar_vaga_url(vaga_url)
        tipo = validar_tipo(tipo)
        # Se o ativo termina entre o INSERT que não gravou e a leitura, tenta de novo.
        for _ in range(3):
            try:
                with self._conn.transaction(), self._conn.cursor(row_factory=dict_row) as cur:
                    # O índice parcial pedido_ia_ativo_uq garante um ativo por (vaga, tipo); o ON CONFLICT
                    # espera a transação concorrente que gravou o mesmo par.
                    cur.execute(
                        "INSERT INTO pedido_ia (vaga_url, tipo) VALUES (%s, %s) "
                        "ON CONFLICT (vaga_url, tipo) WHERE estado IN ('pendente', 'rodando') DO NOTHING "
                        f"RETURNING {_COLUNAS}",
                        (vaga_url, tipo.value),
                    )
                    linha = cur.fetchone()
            except errors.ForeignKeyViolation:
                # Só a vaga inexistente (a única chave estrangeira que o INSERT preenche).
                raise DadoInvalido(f"vaga não encontrada no histórico: {vaga_url}") from None
            if linha is not None:
                return _pedido(linha), True
            ativo = self._um("vaga_url = %s AND tipo = %s AND estado IN ('pendente', 'rodando')", (vaga_url, tipo.value))
            if ativo is not None:
                return ativo, False
        raise RuntimeError("não foi possível criar nem achar o pedido ativo")

    def obter(self, id: int) -> PedidoIA | None:
        return self._um("id = %s", (id,)) if id_possivel(id) else None

    def ultimo(self, vaga_url: str, tipo: TipoPedidoIA | str) -> PedidoIA | None:
        vaga_url = validar_vaga_url(vaga_url)
        tipo = validar_tipo(tipo)
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT {_COLUNAS} FROM pedido_ia WHERE vaga_url = %s AND tipo = %s ORDER BY id DESC LIMIT 1",
                (vaga_url, tipo.value),
            )
            linha = cur.fetchone()
        return _pedido(linha) if linha else None

    def proximo(self, agora: datetime | None = None) -> PedidoIA | None:
        params = {"agora": agora, "prazo": PRAZO_RODANDO.total_seconds(), "max": MAX_TENTATIVAS, "erro": ERRO_PRESO}
        with self._conn.transaction(), self._conn.cursor(row_factory=dict_row) as cur:
            # 1. Preso que já gastou as tentativas vira erro.
            cur.execute(
                "UPDATE pedido_ia SET estado = 'erro', erro = %(erro)s, "
                f"terminado_em = {_AGORA} "
                f"WHERE estado = 'rodando' AND reivindicado_em < {_PRAZO} AND tentativas >= %(max)s",
                params,
            )
            # 2. Pendente antes de preso; dentro de cada grupo, o mais antigo. SKIP LOCKED: dois workers
            # simultâneos nunca pegam o mesmo pedido.
            cur.execute(
                "SELECT id FROM pedido_ia "
                f"WHERE estado = 'pendente' OR (estado = 'rodando' AND reivindicado_em < {_PRAZO}) "
                "ORDER BY (estado = 'rodando'), criado_em, id LIMIT 1 FOR UPDATE SKIP LOCKED",
                params,
            )
            escolhido = cur.fetchone()
            if escolhido is None:
                return None
            # 3. Reivindica.
            cur.execute(
                "UPDATE pedido_ia SET estado = 'rodando', tentativas = tentativas + 1, "
                f"reivindicado_em = {_AGORA} WHERE id = %(id)s RETURNING {_COLUNAS}",
                {**params, "id": escolhido["id"]},
            )
            return _pedido(cur.fetchone())

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
        if not id_possivel(id):
            return None
        try:
            with self._conn.transaction(), self._conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "UPDATE pedido_ia SET estado = 'pronto', resultado = %s, prompt_versao = %s, modelo = %s, "
                    "nota = %s, faixa = %s, curriculo_id = %s, terminado_em = now() "
                    f"WHERE id = %s AND estado = 'rodando' AND tentativas = %s RETURNING {_COLUNAS}",
                    (
                        Jsonb(resultado),
                        prompt_versao,
                        modelo,
                        nota,
                        faixa.value if faixa else None,
                        curriculo_id,
                        id,
                        tentativa,
                    ),
                )
                linha = cur.fetchone()
        except errors.ForeignKeyViolation:
            raise DadoInvalido("curriculo_id não existe") from None
        return _pedido(linha) if linha else None

    def falhar(
        self, id: int, tentativa: int, erro: str, prompt_versao: str | None = None, modelo: str | None = None
    ) -> PedidoIA | None:
        if not id_possivel(id):
            return None
        with self._conn.transaction(), self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "UPDATE pedido_ia SET estado = 'erro', erro = %s, prompt_versao = %s, modelo = %s, "
                f"terminado_em = now() WHERE id = %s AND estado = 'rodando' AND tentativas = %s RETURNING {_COLUNAS}",
                (erro[:MAX_ERRO], prompt_versao, modelo, id, tentativa),
            )
            linha = cur.fetchone()
        return _pedido(linha) if linha else None

    def listar_prontos_compatibilidade(self, prompt_versao: str | None = None) -> list[PedidoIA]:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT {_COLUNAS} FROM pedido_ia WHERE tipo = 'compatibilidade' AND estado = 'pronto' "
                "AND (%(versao)s::text IS NULL OR prompt_versao = %(versao)s::text) ORDER BY id",
                {"versao": prompt_versao},
            )
            return [_pedido(linha) for linha in cur.fetchall()]

    def gravar_rotulo(self, vaga_url: str, faixa: Faixa | str | None) -> Faixa | None:
        vaga_url = validar_vaga_url(vaga_url)
        faixa = validar_faixa(faixa)
        try:
            with self._conn.transaction(), self._conn.cursor() as cur:
                if faixa is None:
                    cur.execute("DELETE FROM rotulo_ia WHERE vaga_url = %s", (vaga_url,))
                    return None
                cur.execute(
                    "INSERT INTO rotulo_ia (vaga_url, faixa) VALUES (%s, %s) "
                    "ON CONFLICT (vaga_url) DO UPDATE SET faixa = EXCLUDED.faixa, atualizado_em = now()",
                    (vaga_url, faixa.value),
                )
        except errors.ForeignKeyViolation:
            raise DadoInvalido(f"vaga não encontrada no histórico: {vaga_url}") from None
        return faixa

    def obter_rotulo(self, vaga_url: str) -> Faixa | None:
        with self._conn.cursor() as cur:
            cur.execute("SELECT faixa FROM rotulo_ia WHERE vaga_url = %s", (validar_vaga_url(vaga_url),))
            linha = cur.fetchone()
        return Faixa(linha[0]) if linha else None

    def listar_rotulos(self) -> dict[str, str]:
        with self._conn.cursor() as cur:
            cur.execute("SELECT vaga_url, faixa FROM rotulo_ia")
            return {url: faixa for url, faixa in cur.fetchall()}
