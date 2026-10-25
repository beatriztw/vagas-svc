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
    PRIORIDADE_LOTE,
    PRIORIDADE_PESSOA,
    EstadoPedidoIA,
    Faixa,
    NotaIA,
    PedidoIA,
    TipoPedidoIA,
)
from src.repository.candidatura import validar_vaga_url
from src.repository.pedido_ia import (
    PedidoIARepository,
    id_possivel,
    validar_faixa,
    validar_prioridade,
    validar_tipo,
)

_COLUNAS = (
    "id, tipo, vaga_url, estado, tentativas, resultado, erro, prompt_versao, modelo, nota, faixa, "
    "curriculo_id, prioridade, curriculo_base_id, criado_em, reivindicado_em, terminado_em, pessoa_id"
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
    """Tudo é da pessoa do construtor, menos a fila do worker (`proximo`, `obter_da_fila`, `concluir`, `falhar`,
    `marcar_base`), que lê e grava os pedidos de todas as pessoas; o pedido traz o `pessoa_id` dele."""

    def __init__(self, conn: psycopg.Connection, pessoa_id: int) -> None:
        self._conn = conn
        self._pessoa_id = pessoa_id

    def _um(self, filtro: str, params: tuple) -> PedidoIA | None:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f"SELECT {_COLUNAS} FROM pedido_ia WHERE {filtro}", params)
            linha = cur.fetchone()
        return _pedido(linha) if linha else None

    def criar(
        self,
        vaga_url: str,
        tipo: TipoPedidoIA | str,
        prioridade: int = PRIORIDADE_PESSOA,
        curriculo_base_id: int | None = None,
    ) -> tuple[PedidoIA, bool]:
        vaga_url = validar_vaga_url(vaga_url)
        tipo = validar_tipo(tipo)
        prioridade = validar_prioridade(prioridade)
        # Se o ativo termina entre o INSERT que não gravou e a leitura, tenta de novo.
        for _ in range(3):
            try:
                with self._conn.transaction(), self._conn.cursor(row_factory=dict_row) as cur:
                    # O índice parcial pedido_ia_ativo_uq garante um ativo por (pessoa, vaga, tipo); o ON CONFLICT
                    # espera a transação concorrente que gravou a mesma trinca.
                    cur.execute(
                        "INSERT INTO pedido_ia (pessoa_id, vaga_url, tipo, prioridade, curriculo_base_id) "
                        "VALUES (%s, %s, %s, %s, %s) "
                        "ON CONFLICT (pessoa_id, vaga_url, tipo) WHERE estado IN ('pendente', 'rodando') DO NOTHING "
                        f"RETURNING {_COLUNAS}",
                        (self._pessoa_id, vaga_url, tipo.value, prioridade, curriculo_base_id),
                    )
                    linha = cur.fetchone()
            except errors.ForeignKeyViolation:
                # A vaga inexistente; ou a base, que só chega aqui apagada entre a leitura e o INSERT.
                raise DadoInvalido(f"vaga não encontrada no histórico: {vaga_url}") from None
            if linha is not None:
                return _pedido(linha), True
            # O ativo do lote pedido pela pessoa sobe para a prioridade dela: o clique não espera o lote.
            with self._conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "UPDATE pedido_ia SET prioridade = LEAST(prioridade, %s) "
                    "WHERE pessoa_id = %s AND vaga_url = %s AND tipo = %s AND estado IN ('pendente', 'rodando') "
                    f"RETURNING {_COLUNAS}",
                    (prioridade, self._pessoa_id, vaga_url, tipo.value),
                )
                linha = cur.fetchone()
            if linha is not None:
                return _pedido(linha), False
        raise RuntimeError("não foi possível criar nem achar o pedido ativo")

    def obter(self, id: int) -> PedidoIA | None:
        return self._um("pessoa_id = %s AND id = %s", (self._pessoa_id, id)) if id_possivel(id) else None

    def obter_da_fila(self, id: int) -> PedidoIA | None:
        return self._um("id = %s", (id,)) if id_possivel(id) else None

    def ultimo(self, vaga_url: str, tipo: TipoPedidoIA | str) -> PedidoIA | None:
        vaga_url = validar_vaga_url(vaga_url)
        tipo = validar_tipo(tipo)
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT {_COLUNAS} FROM pedido_ia WHERE pessoa_id = %s AND vaga_url = %s AND tipo = %s "
                "ORDER BY id DESC LIMIT 1",
                (self._pessoa_id, vaga_url, tipo.value),
            )
            linha = cur.fetchone()
        return _pedido(linha) if linha else None

    def proximo(self, agora: datetime | None = None) -> PedidoIA | None:
        params = {
            "agora": agora,
            "prazo": PRAZO_RODANDO.total_seconds(),
            "max": MAX_TENTATIVAS,
            "erro": ERRO_PRESO,
            "lote": PRIORIDADE_LOTE,
        }
        with self._conn.transaction(), self._conn.cursor(row_factory=dict_row) as cur:
            # 1. Preso que já gastou as tentativas vira erro.
            cur.execute(
                "UPDATE pedido_ia SET estado = 'erro', erro = %(erro)s, "
                f"terminado_em = {_AGORA} "
                f"WHERE estado = 'rodando' AND reivindicado_em < {_PRAZO} AND tentativas >= %(max)s",
                params,
            )
            # 2. Pendente antes de preso; dentro de cada grupo, a prioridade (a pessoa antes do lote). Entre os
            # pendentes do lote, rodízio: a pessoa cujo último pedido de lote foi reivindicado há mais tempo (ou
            # nunca: NULL primeiro) vai na frente, para uma não gastar a cota do worker da outra. Dentro da
            # pessoa, e com uma pessoa só, vale a ordem de sempre (o mais antigo). Clique (prioridade 0) e presos
            # não entram no rodízio. SKIP LOCKED: dois workers simultâneos nunca pegam o mesmo pedido.
            cur.execute(
                "WITH ultima_do_lote AS ("
                " SELECT pe.id AS pessoa_id, (SELECT max(l.reivindicado_em) FROM pedido_ia l"
                "  WHERE l.pessoa_id = pe.id AND l.prioridade = %(lote)s) AS em FROM pessoa pe) "
                "SELECT p.id FROM pedido_ia p JOIN ultima_do_lote u ON u.pessoa_id = p.pessoa_id "
                f"WHERE p.estado = 'pendente' OR (p.estado = 'rodando' AND p.reivindicado_em < {_PRAZO}) "
                "ORDER BY (p.estado = 'rodando'), p.prioridade, "
                "CASE WHEN p.estado = 'pendente' AND p.prioridade = %(lote)s THEN u.em END NULLS FIRST, "
                "p.criado_em, p.id LIMIT 1 FOR UPDATE OF p SKIP LOCKED",
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

    def marcar_base(self, id: int, tentativa: int, curriculo_base_id: int) -> PedidoIA | None:
        if not id_possivel(id):
            return None
        with self._conn.transaction(), self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"UPDATE pedido_ia SET curriculo_base_id = %s WHERE id = %s AND estado = 'rodando' AND tentativas = %s "
                f"RETURNING {_COLUNAS}",
                (curriculo_base_id, id, tentativa),
            )
            linha = cur.fetchone()
        return _pedido(linha) if linha else None

    def listar_prontos_compatibilidade(self, prompt_versao: str | None = None) -> list[PedidoIA]:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT {_COLUNAS} FROM pedido_ia WHERE pessoa_id = %(pessoa_id)s "
                "AND tipo = 'compatibilidade' AND estado = 'pronto' "
                "AND (%(versao)s::text IS NULL OR prompt_versao = %(versao)s::text) ORDER BY id",
                {"versao": prompt_versao, "pessoa_id": self._pessoa_id},
            )
            return [_pedido(linha) for linha in cur.fetchall()]

    def versoes_compatibilidade(self) -> set[str]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT prompt_versao FROM pedido_ia "
                "WHERE pessoa_id = %s AND tipo = 'compatibilidade' AND estado = 'pronto' AND prompt_versao IS NOT NULL",
                (self._pessoa_id,),
            )
            return {versao for (versao,) in cur.fetchall()}

    def notas_compatibilidade(self, curriculo_base_id: int, prompt_versao: str | None) -> dict[str, NotaIA]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT ON (vaga_url) vaga_url, nota, faixa FROM pedido_ia "
                "WHERE pessoa_id = %(pessoa_id)s AND tipo = 'compatibilidade' AND estado = 'pronto' "
                "AND curriculo_base_id = %(base)s "
                "AND nota IS NOT NULL AND faixa IS NOT NULL "
                "AND (%(versao)s::text IS NULL OR prompt_versao = %(versao)s::text) "
                "ORDER BY vaga_url, id DESC",
                {"base": curriculo_base_id, "versao": prompt_versao, "pessoa_id": self._pessoa_id},
            )
            return {url: NotaIA(nota, Faixa(faixa)) for url, nota, faixa in cur.fetchall()}

    def urls_com_erro_desde(self, curriculo_base_id: int, desde: datetime) -> set[str]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT vaga_url FROM pedido_ia WHERE pessoa_id = %s AND tipo = 'compatibilidade' "
                "AND estado = 'erro' AND curriculo_base_id = %s AND terminado_em >= %s",
                (self._pessoa_id, curriculo_base_id, desde),
            )
            return {url for (url,) in cur.fetchall()}

    def urls_ativas(self, tipo: TipoPedidoIA | str) -> set[str]:
        tipo = validar_tipo(tipo)
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT vaga_url FROM pedido_ia WHERE pessoa_id = %s AND tipo = %s "
                "AND estado IN ('pendente', 'rodando')",
                (self._pessoa_id, tipo.value),
            )
            return {url for (url,) in cur.fetchall()}

    def contar_lote_desde(self, desde: datetime) -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                # Global, de todas as pessoas: o teto é a cota do worker (a assinatura), não a de cada pessoa.
                "SELECT count(*) FROM pedido_ia WHERE prioridade = %s AND criado_em >= %s",
                (PRIORIDADE_LOTE, desde),
            )
            return cur.fetchone()[0]

    def lote_pausado(self) -> bool:
        with self._conn.cursor() as cur:
            cur.execute("SELECT pausado FROM ia_lote_pausa WHERE pessoa_id = %s", (self._pessoa_id,))
            linha = cur.fetchone()
        return bool(linha and linha[0])

    def pausar_lote(self) -> int:
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ia_lote_pausa (pessoa_id, pausado) VALUES (%s, true) "
                "ON CONFLICT (pessoa_id) DO UPDATE SET pausado = true, atualizado_em = now()",
                (self._pessoa_id,),
            )
            # O pedido que o worker reivindica ao mesmo tempo fica travado (FOR UPDATE do `proximo`): o
            # DELETE espera, relê a linha já `rodando` e não a apaga. O que está rodando termina.
            cur.execute(
                "DELETE FROM pedido_ia WHERE pessoa_id = %s AND estado = 'pendente' AND prioridade = %s AND tipo = %s",
                (self._pessoa_id, PRIORIDADE_LOTE, TipoPedidoIA.COMPATIBILIDADE.value),
            )
            return cur.rowcount

    def retomar_lote(self) -> None:
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ia_lote_pausa (pessoa_id, pausado) VALUES (%s, false) "
                "ON CONFLICT (pessoa_id) DO UPDATE SET pausado = false, atualizado_em = now()",
                (self._pessoa_id,),
            )

    def gravar_rotulo(self, vaga_url: str, faixa: Faixa | str | None) -> Faixa | None:
        vaga_url = validar_vaga_url(vaga_url)
        faixa = validar_faixa(faixa)
        try:
            with self._conn.transaction(), self._conn.cursor() as cur:
                if faixa is None:
                    cur.execute(
                        "DELETE FROM rotulo_ia WHERE pessoa_id = %s AND vaga_url = %s", (self._pessoa_id, vaga_url)
                    )
                    return None
                cur.execute(
                    "INSERT INTO rotulo_ia (pessoa_id, vaga_url, faixa) VALUES (%s, %s, %s) "
                    "ON CONFLICT (pessoa_id, vaga_url) DO UPDATE SET faixa = EXCLUDED.faixa, atualizado_em = now()",
                    (self._pessoa_id, vaga_url, faixa.value),
                )
        except errors.ForeignKeyViolation:
            raise DadoInvalido(f"vaga não encontrada no histórico: {vaga_url}") from None
        return faixa

    def obter_rotulo(self, vaga_url: str) -> Faixa | None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT faixa FROM rotulo_ia WHERE pessoa_id = %s AND vaga_url = %s",
                (self._pessoa_id, validar_vaga_url(vaga_url)),
            )
            linha = cur.fetchone()
        return Faixa(linha[0]) if linha else None

    def listar_rotulos(self) -> dict[str, str]:
        with self._conn.cursor() as cur:
            cur.execute("SELECT vaga_url, faixa FROM rotulo_ia WHERE pessoa_id = %s", (self._pessoa_id,))
            return {url: faixa for url, faixa in cur.fetchall()}
