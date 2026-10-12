import psycopg
from psycopg.rows import dict_row

from src.model.curriculo import Curriculo
from src.model.erros import DadoInvalido
from src.model.perfil import Perfil
from src.model.vaga import Modalidade, Nivel
from src.model.vaga_encontrada import VagaEncontrada
from src.model.vaga_externa import VagaExterna
from src.repository.base import (
    CurriculoRepository,
    PerfilBuscaRepository,
    VagaEncontradaRepository,
    normalizar_perfil,
    vagas_validas,
    validar_limite,
    validar_perfil,
    validar_termo,
    validar_urls,
)

_LISTAS = ("termos", "excluir", "localidades", "plataformas", "skills")
_COLUNAS_CURRICULO = "id, tipo, titulo, conteudo_md, base_id, vaga_url, arquivo_s3, criado_em"
_COLUNAS_VAGA = (
    "url, titulo, empresa, plataforma, local, remoto, publicada_em, termos, primeira_vez_em, ultima_vez_em, vista_em"
)


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
        # Mesmo contrato da memória e do SQLite: ValueError antes de chegar ao CHECK do banco.
        perfil = validar_perfil(perfil)
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
                    raise DadoInvalido(f"base_id {curriculo.base_id} é um currículo adaptado, não uma base")
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


class VagaEncontradaRepositoryPostgres(VagaEncontradaRepository):
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def registrar(self, vagas: list[VagaExterna], termo: str) -> int:
        termo = validar_termo(termo)
        validas = vagas_validas(vagas)
        novas = 0
        with self._conn.transaction(), self._conn.cursor() as cur:
            for vaga in validas:
                params = {**vars(vaga), "termo": termo}
                # ON CONFLICT DO NOTHING espera a transação concorrente que gravou a mesma URL;
                # sem linha devolvida, a vaga já existia e cai no UPDATE abaixo.
                cur.execute(
                    "INSERT INTO vaga_encontrada "
                    "(url, titulo, empresa, plataforma, local, remoto, publicada_em, termos) "
                    "VALUES (%(url)s, %(titulo)s, %(empresa)s, %(plataforma)s, %(local)s, %(remoto)s, "
                    "%(publicada_em)s, ARRAY[%(termo)s::text]) "
                    "ON CONFLICT (url) DO NOTHING RETURNING url",
                    params,
                )
                if cur.fetchone() is not None:
                    novas += 1
                    continue
                # Só os campos que vieram preenchidos; primeira_vez_em e vista_em ficam como estão.
                cur.execute(
                    "UPDATE vaga_encontrada SET "
                    "titulo = COALESCE(NULLIF(%(titulo)s::text, ''), titulo), "
                    "empresa = COALESCE(NULLIF(%(empresa)s::text, ''), empresa), "
                    "plataforma = COALESCE(NULLIF(%(plataforma)s::text, ''), plataforma), "
                    "local = COALESCE(%(local)s::text, local), "
                    "remoto = COALESCE(%(remoto)s::boolean, remoto), "
                    "publicada_em = COALESCE(%(publicada_em)s::date, publicada_em), "
                    "termos = CASE WHEN %(termo)s::text = ANY(termos) THEN termos "
                    "ELSE array_append(termos, %(termo)s::text) END, "
                    "ultima_vez_em = now() "
                    "WHERE url = %(url)s",
                    params,
                )
        return novas

    def listar(self, somente_novas: bool, limite: int) -> list[VagaEncontrada]:
        limite = validar_limite(limite)
        filtro = "WHERE vista_em IS NULL " if somente_novas else ""
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT {_COLUNAS_VAGA} FROM vaga_encontrada {filtro}"
                'ORDER BY primeira_vez_em DESC, publicada_em DESC NULLS LAST, url COLLATE "C" LIMIT %s',
                (limite,),
            )
            return [VagaEncontrada(**{**linha, "termos": list(linha["termos"])}) for linha in cur.fetchall()]

    def marcar_vistas(self, urls: list[str]) -> int:
        urls = validar_urls(urls)
        if not urls:
            return 0
        with self._conn.transaction(), self._conn.cursor() as cur:
            # Já vista fica com a data da primeira vez.
            cur.execute(
                "UPDATE vaga_encontrada SET vista_em = now() WHERE url = ANY(%s) AND vista_em IS NULL",
                (urls,),
            )
            return cur.rowcount
