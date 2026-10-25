from datetime import datetime

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from src.model.curriculo import Curriculo
from src.model.detalhe_vaga import (
    DetalheVaga,
    beneficios_de_json,
    requisitos_de_json,
    requisitos_para_json,
    salario_de_json,
    salario_para_json,
)
from src.model.erros import DadoInvalido
from src.model.perfil import Perfil
from src.model.vaga import Modalidade, Nivel
from src.model.vaga_encontrada import VagaEncontrada
from src.model.vaga_externa import VagaExterna
from src.repository.base import (
    CurriculoRepository,
    PerfilBuscaRepository,
    VagaEncontradaRepository,
    casa_busca,
    detalhe_valido,
    exigidas_da_busca,
    normalizar_perfil,
    vagas_validas,
    validar_deslocamento,
    validar_limite,
    validar_perfil,
    validar_teto,
    validar_termo,
    validar_urls,
)

_LISTAS = ("termos", "excluir", "localidades", "plataformas", "skills")
_COLUNAS_CURRICULO = "id, tipo, titulo, conteudo_md, base_id, vaga_url, arquivo_s3, criado_em"
# A lista não lê o detalhe (descrição de até 20 mil caracteres vezes milhares de linhas); só `obter` lê.
_COLUNAS_VAGA = (
    "url, titulo, empresa, plataforma, local, remoto, publicada_em, termos, primeira_vez_em, ultima_vez_em, "
    "vista_em, expirada_em, detalhada_em, salva_em"
)
_COLUNAS_VAGA_DETALHE = f"{_COLUNAS_VAGA}, descricao, requisitos, salario, beneficios, subtitulo"


def perfil_para_linha(perfil: Perfil) -> dict:
    """Perfil -> parâmetros da linha de perfil_busca. Enums viram o .value gravado no banco."""
    perfil = normalizar_perfil(perfil)
    linha = {campo: getattr(perfil, campo) for campo in _LISTAS}
    linha["modalidades"] = [m.value for m in perfil.modalidades]
    linha["niveis"] = [n.value for n in perfil.niveis]
    linha["publicada_desde_dias"] = perfil.publicada_desde_dias
    linha["ocultar_candidatadas"] = perfil.ocultar_candidatadas
    linha["salario_minimo"] = perfil.salario_minimo
    return linha


def linha_para_perfil(linha: dict) -> Perfil:
    """Linha de perfil_busca -> Perfil, com Modalidade/Nivel de volta."""
    return Perfil(
        **{campo: list(linha[campo]) for campo in _LISTAS},
        modalidades=[Modalidade(m) for m in linha["modalidades"]],
        niveis=[Nivel(n) for n in linha["niveis"]],
        publicada_desde_dias=linha["publicada_desde_dias"],
        ocultar_candidatadas=linha["ocultar_candidatadas"],
        salario_minimo=linha["salario_minimo"],
    )


class PerfilBuscaRepositoryPostgres(PerfilBuscaRepository):
    def __init__(self, conn: psycopg.Connection, pessoa_id: int) -> None:
        self._conn = conn
        self._pessoa_id = pessoa_id

    def obter(self) -> Perfil | None:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM perfil_busca WHERE pessoa_id = %s", (self._pessoa_id,))
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
                f"INSERT INTO perfil_busca (pessoa_id, {nomes}) VALUES (%(pessoa_id)s, {valores}) "
                f"ON CONFLICT (pessoa_id) DO UPDATE SET {atualizacao}, atualizado_em = now()",
                {**perfil_para_linha(perfil), "pessoa_id": self._pessoa_id},
            )


class CurriculoRepositoryPostgres(CurriculoRepository):
    def __init__(self, conn: psycopg.Connection, pessoa_id: int) -> None:
        self._conn = conn
        self._pessoa_id = pessoa_id

    def _um(self, sql: str, params: tuple = ()) -> Curriculo | None:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            linha = cur.fetchone()
        return Curriculo(**linha) if linha else None

    def obter_base(self) -> Curriculo | None:
        return self._um(
            f"SELECT {_COLUNAS_CURRICULO} FROM curriculo WHERE pessoa_id = %s AND tipo = 'base' "
            "ORDER BY criado_em DESC, id DESC LIMIT 1",
            (self._pessoa_id,),
        )

    def salvar(self, curriculo: Curriculo) -> Curriculo:
        curriculo.validar()
        with self._conn.transaction(), self._conn.cursor(row_factory=dict_row) as cur:
            if curriculo.base_id is not None:
                # O CHECK da tabela não enxerga outra linha: adaptado de adaptado é barrado aqui.
                # FOR SHARE segura a base até o commit. A base de outra pessoa não existe para esta: a FK de
                # base_id é global e deixaria o adaptado apontar para o currículo de outra pessoa.
                cur.execute("SELECT tipo, pessoa_id FROM curriculo WHERE id = %s FOR SHARE", (curriculo.base_id,))
                linha = cur.fetchone()
                if linha is not None and linha["pessoa_id"] != self._pessoa_id:
                    raise DadoInvalido(f"base_id {curriculo.base_id} não existe")
                if linha is not None and linha["tipo"] != "base":
                    raise DadoInvalido(f"base_id {curriculo.base_id} é um currículo adaptado, não uma base")
            cur.execute(
                "INSERT INTO curriculo (pessoa_id, tipo, titulo, conteudo_md, base_id, vaga_url, arquivo_s3) "
                f"VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING {_COLUNAS_CURRICULO}",
                (
                    self._pessoa_id,
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
        return self._um(
            f"SELECT {_COLUNAS_CURRICULO} FROM curriculo WHERE id = %s AND pessoa_id = %s", (id, self._pessoa_id)
        )

    def listar_adaptados(self, vaga_url: str | None = None) -> list[Curriculo]:
        filtro, params = ("", (self._pessoa_id,)) if vaga_url is None else (" AND vaga_url = %s", (self._pessoa_id, vaga_url))
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT {_COLUNAS_CURRICULO} FROM curriculo WHERE pessoa_id = %s AND tipo = 'adaptado'{filtro} "
                "ORDER BY criado_em DESC, id DESC",
                params,
            )
            return [Curriculo(**linha) for linha in cur.fetchall()]


# O que a listagem do provider preenche direto (o detalhe vai por `_parametros_do_detalhe`).
_CAMPOS_DA_LISTAGEM = ("url", "titulo", "empresa", "plataforma", "local", "remoto", "publicada_em", "expirada_em")


def _parametros_do_detalhe(detalhe: DetalheVaga | None) -> dict:
    """Parâmetros das colunas do detalhe; `tem_detalhe` False = a vaga não trouxe (não apaga o que há)."""
    limpo = detalhe_valido(detalhe) if detalhe is not None else None
    return {
        "tem_detalhe": limpo is not None,
        "descricao": limpo.descricao if limpo else None,
        "requisitos": Jsonb(requisitos_para_json(limpo.requisitos)) if limpo else None,
        "salario": Jsonb(salario_para_json(limpo.salario)) if limpo and limpo.salario else None,
        "beneficios": Jsonb(list(limpo.beneficios)) if limpo else None,
        "subtitulo": limpo.subtitulo if limpo else None,
    }


_ORDEM = 'ORDER BY primeira_vez_em DESC, publicada_em DESC NULLS LAST, url COLLATE "C"'


class VagaEncontradaRepositoryPostgres(VagaEncontradaRepository):
    def __init__(self, conn: psycopg.Connection, pessoa_id: int) -> None:
        self._conn = conn
        self._pessoa_id = pessoa_id

    def registrar(self, vagas: list[VagaExterna], termo: str) -> int:
        termo = validar_termo(termo)
        validas = vagas_validas(vagas)
        novas = 0
        with self._conn.transaction(), self._conn.cursor() as cur:
            for vaga in validas:
                params = {
                    **{c: getattr(vaga, c) for c in _CAMPOS_DA_LISTAGEM},
                    "pessoa_id": self._pessoa_id,
                    "termo": termo,
                    **_parametros_do_detalhe(vaga.detalhe),
                    # O provider disse que não há prazo: um prazo futuro sai (o vencido fica, pelo CASE).
                    "limpar_prazo": vaga.tem_prazo is False,
                }
                # ON CONFLICT DO NOTHING espera a transação concorrente que gravou a mesma URL;
                # sem linha devolvida, a vaga já existia e cai no UPDATE abaixo.
                cur.execute(
                    "INSERT INTO vaga_encontrada "
                    "(pessoa_id, url, titulo, empresa, plataforma, local, remoto, publicada_em, termos, expirada_em, "
                    "descricao, requisitos, salario, beneficios, subtitulo, detalhada_em) "
                    "VALUES (%(pessoa_id)s, %(url)s, %(titulo)s, %(empresa)s, %(plataforma)s, %(local)s, %(remoto)s, "
                    "%(publicada_em)s, ARRAY[%(termo)s::text], %(expirada_em)s::timestamptz, "
                    "%(descricao)s, %(requisitos)s, %(salario)s, %(beneficios)s, %(subtitulo)s, "
                    "CASE WHEN %(tem_detalhe)s THEN now() END) "
                    "ON CONFLICT (pessoa_id, url) DO NOTHING RETURNING url",
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
                    # Quem já expirou continua expirado; senão vale o prazo novo (pode mudar), nenhum se o provider
                    # disse que não há prazo, ou o que estava.
                    "expirada_em = CASE WHEN expirada_em IS NOT NULL AND expirada_em <= now() THEN expirada_em "
                    "ELSE COALESCE(%(expirada_em)s::timestamptz, "
                    "CASE WHEN %(limpar_prazo)s::boolean THEN NULL ELSE expirada_em END) END, "
                    # A listagem do Remotar traz o detalhe: sobrescreve; sem detalhe (outros providers), fica.
                    "descricao = CASE WHEN %(tem_detalhe)s THEN %(descricao)s ELSE descricao END, "
                    "requisitos = CASE WHEN %(tem_detalhe)s THEN %(requisitos)s ELSE requisitos END, "
                    "salario = CASE WHEN %(tem_detalhe)s THEN %(salario)s ELSE salario END, "
                    "beneficios = CASE WHEN %(tem_detalhe)s THEN %(beneficios)s ELSE beneficios END, "
                    "subtitulo = CASE WHEN %(tem_detalhe)s THEN %(subtitulo)s ELSE subtitulo END, "
                    "detalhada_em = CASE WHEN %(tem_detalhe)s THEN now() ELSE detalhada_em END, "
                    "ultima_vez_em = now() "
                    "WHERE pessoa_id = %(pessoa_id)s AND url = %(url)s",
                    params,
                )
        return novas

    def listar(
        self, somente_novas: bool, limite: int, q: str | None = None, deslocamento: int = 0
    ) -> list[VagaEncontrada]:
        limite = validar_limite(limite)
        deslocamento = validar_deslocamento(deslocamento)
        exigidas = exigidas_da_busca(q)
        # Sem q, paginação no SQL. Com q, o radical não existe no SQL: busca as linhas do filtro
        # `vista_em` na mesma ordem e filtra em Python com o `casa_busca` da memória; custo O(n)
        # do histórico por chamada (aceito para o tamanho esperado; índice de texto fica para depois).
        sql = f"SELECT {_COLUNAS_VAGA} FROM vaga_encontrada {self._filtro(somente_novas)}" + _ORDEM
        params: tuple = (self._pessoa_id,)
        if not exigidas:
            sql += " LIMIT %s OFFSET %s"
            params = (self._pessoa_id, limite, deslocamento)
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            vagas = [VagaEncontrada(**{**linha, "termos": list(linha["termos"])}) for linha in cur.fetchall()]
        if exigidas:
            vagas = [v for v in vagas if casa_busca(v, exigidas)][deslocamento : deslocamento + limite]
        return vagas

    def contar(self, somente_novas: bool, q: str | None = None) -> int:
        exigidas = exigidas_da_busca(q)
        with self._conn.cursor(row_factory=dict_row) as cur:
            if not exigidas:
                cur.execute(
                    f"SELECT count(*) AS n FROM vaga_encontrada {self._filtro(somente_novas)}", (self._pessoa_id,)
                )
                return cur.fetchone()["n"]
            cur.execute(
                f"SELECT {_COLUNAS_VAGA} FROM vaga_encontrada {self._filtro(somente_novas)}", (self._pessoa_id,)
            )
            return sum(1 for linha in cur.fetchall() if casa_busca(VagaEncontrada(**linha), exigidas))

    def listar_recentes(self, somente_novas: bool, teto: int, q: str | None = None) -> list[VagaEncontrada]:
        teto = validar_teto(teto)
        exigidas = exigidas_da_busca(q)
        # O teto vai no SQL (custo limitado); o `q` por radical é filtrado em Python, como em `listar`.
        sql = f"SELECT {_COLUNAS_VAGA} FROM vaga_encontrada {self._filtro(somente_novas)}" + _ORDEM + " LIMIT %s"
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, (self._pessoa_id, teto))
            vagas = [VagaEncontrada(**{**linha, "termos": list(linha["termos"])}) for linha in cur.fetchall()]
        return [v for v in vagas if casa_busca(v, exigidas)]

    def obter(self, url: str) -> VagaEncontrada | None:
        if not isinstance(url, str) or "\x00" in url:
            return None
        # `obter` acha também a vaga expirada (link direto mostra "vaga expirada").
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT {_COLUNAS_VAGA_DETALHE} FROM vaga_encontrada WHERE pessoa_id = %s AND url = %s",
                (self._pessoa_id, url),
            )
            linha = cur.fetchone()
        if not linha:
            return None
        # O CHECK da 004 garante a forma de cada coluna; o item torto dentro dela é descartado sozinho.
        return VagaEncontrada(
            **{
                **linha,
                "termos": list(linha["termos"]),
                "requisitos": requisitos_de_json(linha["requisitos"]),
                "salario": salario_de_json(linha["salario"]),
                "beneficios": beneficios_de_json(linha["beneficios"]),
            }
        )

    def gravar_detalhe(self, url: str, detalhe: DetalheVaga) -> bool:
        limpo = detalhe_valido(detalhe)
        if limpo is None or not isinstance(url, str) or "\x00" in url:
            return False
        parametros = {
            **_parametros_do_detalhe(limpo),
            "pessoa_id": self._pessoa_id,
            "url": url,
            "expirada_em": limpo.expirada_em,
            "limpar_prazo": limpo.tem_prazo is False,
        }
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(
                "UPDATE vaga_encontrada SET descricao = %(descricao)s, requisitos = %(requisitos)s, "
                "salario = %(salario)s, beneficios = %(beneficios)s, subtitulo = %(subtitulo)s, "
                "detalhada_em = now(), "
                "expirada_em = CASE WHEN expirada_em IS NOT NULL AND expirada_em <= now() THEN expirada_em "
                "ELSE COALESCE(%(expirada_em)s::timestamptz, "
                "CASE WHEN %(limpar_prazo)s::boolean THEN NULL ELSE expirada_em END) END "
                "WHERE pessoa_id = %(pessoa_id)s AND url = %(url)s",
                parametros,
            )
            return cur.rowcount > 0

    def marcar_expirada(self, url: str) -> bool:
        if not isinstance(url, str) or "\x00" in url:
            return False
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(
                "UPDATE vaga_encontrada SET expirada_em = CASE WHEN expirada_em IS NOT NULL AND expirada_em <= now() "
                "THEN expirada_em ELSE now() END WHERE pessoa_id = %s AND url = %s",
                (self._pessoa_id, url),
            )
            return cur.rowcount > 0

    def marcar_salva(self, url: str, salva: bool) -> bool:
        if not isinstance(url, str) or "\x00" in url:
            return False
        # Já salva fica com a data da primeira vez; tirar dos salvos volta a NULL.
        valor = "COALESCE(salva_em, now())" if salva else "NULL"
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(
                f"UPDATE vaga_encontrada SET salva_em = {valor} WHERE pessoa_id = %s AND url = %s",
                (self._pessoa_id, url),
            )
            return cur.rowcount > 0

    def listar_salvas(self, teto: int, q: str | None = None) -> list[VagaEncontrada]:
        teto = validar_teto(teto)
        exigidas = exigidas_da_busca(q)
        # Sem o filtro `no_ar`: a salva que expirou continua na aba, marcada como encerrada.
        sql = (
            f"SELECT {_COLUNAS_VAGA} FROM vaga_encontrada WHERE pessoa_id = %s AND salva_em IS NOT NULL "
            'ORDER BY salva_em DESC, url COLLATE "C" LIMIT %s'
        )
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, (self._pessoa_id, teto))
            vagas = [VagaEncontrada(**{**linha, "termos": list(linha["termos"])}) for linha in cur.fetchall()]
        return [v for v in vagas if casa_busca(v, exigidas)]

    def contar_salvas(self) -> int:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT count(*) AS n FROM vaga_encontrada WHERE pessoa_id = %s AND salva_em IS NOT NULL",
                (self._pessoa_id,),
            )
            return cur.fetchone()["n"]

    def ultima_coleta(self) -> datetime | None:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT max(ultima_vez_em) AS em FROM vaga_encontrada WHERE pessoa_id = %s", (self._pessoa_id,))
            return cur.fetchone()["em"]

    @staticmethod
    def _filtro(somente_novas: bool) -> str:
        """O WHERE da lista, com UM parâmetro posicional (a pessoa) que quem chama passa antes dos demais."""
        # A vaga com o prazo vencido some da lista e do contador (`obter` ainda a acha); com o prazo
        # por vir, aparece até o prazo passar, sem nova coleta.
        no_ar = "(expirada_em IS NULL OR expirada_em > now())"
        return (
            f"WHERE pessoa_id = %s AND {no_ar} AND vista_em IS NULL "
            if somente_novas
            else f"WHERE pessoa_id = %s AND {no_ar} "
        )

    def marcar_vistas(self, urls: list[str]) -> int:
        urls = validar_urls(urls)
        if not urls:
            return 0
        with self._conn.transaction(), self._conn.cursor() as cur:
            # Já vista fica com a data da primeira vez.
            cur.execute(
                "UPDATE vaga_encontrada SET vista_em = now() WHERE pessoa_id = %s AND url = ANY(%s) AND vista_em IS NULL",
                (self._pessoa_id, urls),
            )
            return cur.rowcount
