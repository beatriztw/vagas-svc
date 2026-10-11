import psycopg
from psycopg.rows import dict_row

from src.model.curriculo import Curriculo
from src.model.perfil import Perfil
from src.model.vaga import Modalidade, Nivel
from src.repository.base import CurriculoRepository, PerfilBuscaRepository, normalizar_perfil

_LISTAS = ("termos", "excluir", "localidades", "plataformas", "skills")
_COLUNAS_CURRICULO = "id, tipo, titulo, conteudo_md, base_id, vaga_url, arquivo_s3, criado_em"


def perfil_para_linha(perfil: Perfil) -> dict:
    """Perfil -> parâmetros da linha de perfil_busca. Enums viram o .value gravado no banco."""
    perfil = normalizar_perfil(perfil)
    linha = {campo: getattr(perfil, campo) for campo in _LISTAS}
    linha["modalidades"] = [m.value for m in perfil.modalidades]
    linha["niveis"] = [n.value for n in perfil.niveis]
    linha["publicada_desde_dias"] = perfil.publicada_desde_dias
    linha["ocultar_candidatadas"] = perfil.ocultar_candidatadas
    return linha


def linha_para_perfil(linha: dict) -> Perfil:
    """Linha de perfil_busca -> Perfil, com Modalidade/Nivel de volta."""
    return Perfil(
        **{campo: list(linha[campo]) for campo in _LISTAS},
        modalidades=[Modalidade(m) for m in linha["modalidades"]],
        niveis=[Nivel(n) for n in linha["niveis"]],
        publicada_desde_dias=linha["publicada_desde_dias"],
        ocultar_candidatadas=linha["ocultar_candidatadas"],
    )


class PerfilBuscaRepositoryPostgres(PerfilBuscaRepository):
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def obter(self) -> Perfil | None:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM perfil_busca WHERE id = 1")
            linha = cur.fetchone()
        return linha_para_perfil(linha) if linha else None

    def salvar(self, perfil: Perfil) -> None:
        colunas = list(perfil_para_linha(perfil))
        nomes = ", ".join(colunas)
        valores = ", ".join(f"%({c})s" for c in colunas)
        atualizacao = ", ".join(f"{c} = EXCLUDED.{c}" for c in colunas)
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO perfil_busca (id, {nomes}) VALUES (1, {valores}) "
                f"ON CONFLICT (id) DO UPDATE SET {atualizacao}, atualizado_em = now()",
                perfil_para_linha(perfil),
            )


class CurriculoRepositoryPostgres(CurriculoRepository):
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def _um(self, sql: str, params: tuple = ()) -> Curriculo | None:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            linha = cur.fetchone()
        return Curriculo(**linha) if linha else None

    def obter_base(self) -> Curriculo | None:
        return self._um(
            f"SELECT {_COLUNAS_CURRICULO} FROM curriculo WHERE tipo = 'base' "
            "ORDER BY criado_em DESC, id DESC LIMIT 1"
        )

    def salvar(self, curriculo: Curriculo) -> Curriculo:
        curriculo.validar()
        with self._conn.transaction(), self._conn.cursor(row_factory=dict_row) as cur:
            if curriculo.base_id is not None:
                # O CHECK da tabela não enxerga outra linha: adaptado de adaptado é barrado aqui.
                # FOR SHARE segura a base até o commit.
                cur.execute("SELECT tipo FROM curriculo WHERE id = %s FOR SHARE", (curriculo.base_id,))
                linha = cur.fetchone()
                if linha is not None and linha["tipo"] != "base":
                    raise ValueError(f"base_id {curriculo.base_id} é um currículo adaptado, não uma base")
            cur.execute(
                "INSERT INTO curriculo (tipo, titulo, conteudo_md, base_id, vaga_url, arquivo_s3) "
                f"VALUES (%s, %s, %s, %s, %s, %s) RETURNING {_COLUNAS_CURRICULO}",
                (
                    curriculo.tipo,
                    curriculo.titulo,
                    curriculo.conteudo_md,
                    curriculo.base_id,
                    curriculo.vaga_url,
                    curriculo.arquivo_s3,
                ),
            )
            return Curriculo(**cur.fetchone())

    def obter(self, id: int) -> Curriculo | None:
        return self._um(f"SELECT {_COLUNAS_CURRICULO} FROM curriculo WHERE id = %s", (id,))

    def listar_adaptados(self) -> list[Curriculo]:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT {_COLUNAS_CURRICULO} FROM curriculo WHERE tipo = 'adaptado' "
                "ORDER BY criado_em DESC, id DESC"
            )
            return [Curriculo(**linha) for linha in cur.fetchall()]
