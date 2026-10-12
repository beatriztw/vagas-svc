"""Histórico da coleta: `obter`, `contar`, `listar` com `q` e deslocamento (memória, Postgres e API).

As vagas daqui são escritas à mão (não são respostas gravadas de provider).
"""

import os
import unittest
from datetime import date

from fastapi.testclient import TestClient

from src.controller.api import criar_app
from src.model.erros import DadoInvalido
from src.model.vaga_externa import VagaExterna
from src.repository.base import (
    CurriculoRepositoryMemoria,
    PerfilBuscaRepositoryMemoria,
    VagaEncontradaRepositoryMemoria,
)
from src.service.busca_service import BuscaService
from tests.test_coleta import _schema_descartavel

# (url, título, empresa, local, publicada_em): conjunto escrito à mão, mesma ordem de `listar`
# (todas entram no mesmo registro: publicada_em desc, sem data por último, depois a URL).
CONJUNTO = [
    ("u1", "Desenvolvedor Java Sênior", "Acme", "São Paulo", date(2026, 10, 9)),
    ("u2", "Desenvolvedora JavaScript", "Beta", "Remoto", date(2026, 10, 8)),
    ("u3", "Analista de Conciliação", "Banco Zeta", "Rio de Janeiro", date(2026, 10, 7)),
    ("u4", "Front-end Engineer", "Gama", None, date(2026, 10, 6)),
    ("u5", "Engenheiro Frontend", "Delta", "Vitória da Conquista", date(2026, 10, 5)),
    ("u6", "NoSQL Specialist", "Epsilon", "Vitória", date(2026, 10, 4)),
    ("u7", "SQL Analyst", "Eta", "Remoto", date(2026, 10, 3)),
    ("u8", "Barista", "Java Coffee", "Belo Horizonte", date(2026, 10, 2)),
    ("u9", "Analistas de Dados", "Teta", "Curitiba", None),
]

BUSCAS = [
    None, "", "   ", "de", "--", "java", "JAVA", "javascript", "sql", "nosql", "conciliação",
    "Conciliacao", "conciliações", "front-end", "front end", "frontend", "Front End", "analista",
    "analistas", "analista de dados", "engenheiro", "engineer", "desenvolvedor", "desenvolvedora",
    "vitória", "vitoria da conquista", "remoto", "java remoto", "java sênior acme", "banco",
    "são paulo", "sao paulo java", "nada disso", "c", "dados analista", "data",
]


def vagas_do_conjunto() -> list[VagaExterna]:
    return [
        VagaExterna(titulo=t, empresa=e, plataforma="A", url=u, local=loc, publicada_em=pub)
        for u, t, e, loc, pub in CONJUNTO
    ]


def urls(vagas) -> list[str]:
    return [v.url for v in vagas]


class ContratoHistorico:
    """Mesmos casos para a memória e o Postgres; a subclasse define `self.repo`."""

    def carregar(self):
        self.repo.registrar(vagas_do_conjunto(), "termo")

    def buscar(self, q, **kw) -> list[str]:
        return urls(self.repo.listar(somente_novas=False, limite=500, q=q, **kw))

    def test_listar_antigo_continua_funcionando_e_sem_q_devolve_tudo_na_ordem(self):
        self.carregar()

        self.assertEqual(urls(self.repo.listar(somente_novas=False, limite=500)), [c[0] for c in CONJUNTO])
        self.assertEqual(urls(self.repo.listar(True, 3)), ["u1", "u2", "u3"])

    def test_obter_devolve_a_vaga_pela_url_exata_ou_none(self):
        self.carregar()

        vaga = self.repo.obter("u3")

        self.assertEqual((vaga.url, vaga.titulo, vaga.empresa, vaga.local), ("u3", "Analista de Conciliação", "Banco Zeta", "Rio de Janeiro"))
        self.assertEqual(vaga.termos, ["termo"])
        self.assertIsNone(self.repo.obter("nao-existe"))
        self.assertIsNone(self.repo.obter("U3"), "a url é a chave: não se normaliza caixa")
        self.assertIsNone(self.repo.obter(" u3"))
        self.assertIsNone(self.repo.obter(""))

    def test_obter_com_caracteres_especiais_na_url(self):
        especiais = [
            "https://x.com/vaga?a=1&b=2#c=d",
            "https://x.com/vaga com espaço/conciliação?q=a b&x=%20",
            "https://x.com/a%2Fb?x=1+2;y=é",
            "https://x.com/vaga'--\";DROP TABLE vaga_encontrada",
        ]
        self.repo.registrar([VagaExterna(titulo="T", empresa="E", plataforma="A", url=u) for u in especiais], "t")

        for url in especiais:
            with self.subTest(url=url):
                self.assertEqual(self.repo.obter(url).url, url)
        # Só a url exata: o pedaço antes do `?` ou da `#` não acha nada.
        self.assertIsNone(self.repo.obter("https://x.com/vaga"))
        self.assertIsNone(self.repo.obter("https://x.com/vaga?a=1&b=2"))
        self.assertIsNone(self.repo.obter("https://x.com/vaga?a=1&b=2#c=d "))

    def test_obter_nao_confunde_url_decodificada(self):
        self.repo.registrar([VagaExterna(titulo="T", empresa="E", plataforma="A", url="https://x.com/a%20b")], "t")

        self.assertIsNotNone(self.repo.obter("https://x.com/a%20b"))
        self.assertIsNone(self.repo.obter("https://x.com/a b"))

    def test_q_ignora_acento_caixa_hifen_genero_e_plural(self):
        self.carregar()

        for q in ("conciliação", "Conciliacao", "CONCILIAÇÕES", "conciliacoes"):
            with self.subTest(q=q):
                self.assertEqual(self.buscar(q), ["u3"])
        for q in ("front-end", "front end", "frontend", "Front End", "FRONT-END"):
            with self.subTest(q=q):
                self.assertEqual(self.buscar(q), ["u4", "u5"])
        for q in ("analista", "analistas", "Analista"):
            with self.subTest(q=q):
                self.assertEqual(self.buscar(q), ["u3", "u7", "u9"])
        self.assertEqual(self.buscar("desenvolvedor"), ["u1", "u2"])
        self.assertEqual(self.buscar("desenvolvedora"), ["u1", "u2"])

    def test_q_e_por_palavra_nunca_por_pedaco(self):
        self.carregar()

        # Java casa a vaga Java e a empresa "Java Coffee", nunca JavaScript.
        self.assertEqual(self.buscar("java"), ["u1", "u8"])
        self.assertEqual(self.buscar("javascript"), ["u2"])
        self.assertEqual(self.buscar("sql"), ["u7"])
        self.assertEqual(self.buscar("nosql"), ["u6"])
        # Vitória não traz Vitória da Conquista... e vice-versa só com todas as palavras.
        self.assertEqual(self.buscar("vitória"), ["u5", "u6"])  # a palavra existe nos dois locais
        self.assertEqual(self.buscar("vitoria conquista"), ["u5"])
        self.assertEqual(self.buscar("itória"), [])
        self.assertEqual(self.buscar("jav"), [])
        self.assertEqual(self.buscar("c"), [])

    def test_q_casa_titulo_empresa_ou_local_com_e_entre_as_palavras(self):
        self.carregar()

        self.assertEqual(self.buscar("banco"), ["u3"])  # empresa
        self.assertEqual(self.buscar("curitiba"), ["u9"])  # local
        self.assertEqual(self.buscar("java remoto"), [])  # nenhuma vaga tem as duas
        self.assertEqual(self.buscar("javascript remoto"), ["u2"])
        self.assertEqual(self.buscar("java sênior acme"), ["u1"])  # título + empresa
        self.assertEqual(self.buscar("sao paulo java"), ["u1"])  # local + título
        self.assertEqual(self.buscar("analista dados"), ["u9"])
        self.assertEqual(self.buscar("analista de dados"), ["u9"])  # ligação ignorada
        self.assertEqual(self.buscar("data analyst"), ["u9"])  # dados/data e analista/analyst, como a recomendação

    def test_q_vazio_ou_sem_palavra_util_nao_filtra(self):
        self.carregar()

        todas = [c[0] for c in CONJUNTO]
        for q in (None, "", "   ", "--", "!!!", "de"):
            with self.subTest(q=q):
                self.assertEqual(self.buscar(q), todas)
                self.assertEqual(self.repo.contar(False, q), len(todas))

    def test_q_invalido_e_dado_invalido(self):
        for q in ("x" * 201, 5, ["java"]):
            with self.subTest(q=q):
                with self.assertRaises(DadoInvalido):
                    self.repo.listar(False, 10, q=q)
                with self.assertRaises(DadoInvalido):
                    self.repo.contar(False, q=q)

    def test_contar_bate_com_a_lista_sem_limite_mesmo_com_limite_menor_que_o_total(self):
        self.carregar()
        self.repo.marcar_vistas(["u1", "u8", "u3"])

        for q in BUSCAS:
            for novas in (False, True):
                with self.subTest(q=q, novas=novas):
                    esperada = urls(self.repo.listar(novas, 500, q=q))
                    self.assertEqual(self.repo.contar(novas, q), len(esperada))
        # Limite menor que o total: a lista corta, a contagem não.
        self.assertEqual(len(self.repo.listar(False, 2)), 2)
        self.assertEqual(self.repo.contar(False), len(CONJUNTO))
        self.assertEqual(len(self.repo.listar(False, 1, q="analista")), 1)
        self.assertEqual(self.repo.contar(False, "analista"), 3)
        self.assertEqual(self.repo.contar(True, "java"), 0)
        self.assertEqual(self.repo.contar(False, "java"), 2)

    def test_q_respeita_somente_novas(self):
        self.carregar()
        self.repo.marcar_vistas(["u1"])

        self.assertEqual(urls(self.repo.listar(True, 10, q="java")), ["u8"])
        self.assertEqual(urls(self.repo.listar(False, 10, q="java")), ["u1", "u8"])

    def test_deslocamento_pula_as_primeiras_ja_filtradas(self):
        self.carregar()

        self.assertEqual(urls(self.repo.listar(False, 3, deslocamento=0)), ["u1", "u2", "u3"])
        self.assertEqual(urls(self.repo.listar(False, 3, deslocamento=3)), ["u4", "u5", "u6"])
        self.assertEqual(urls(self.repo.listar(False, 3, deslocamento=8)), ["u9"])
        self.assertEqual(self.repo.listar(False, 3, deslocamento=9), [])
        self.assertEqual(self.repo.listar(False, 3, deslocamento=10_000), [])
        # Com q o deslocamento conta depois do filtro.
        self.assertEqual(urls(self.repo.listar(False, 1, q="analista", deslocamento=1)), ["u7"])
        self.assertEqual(urls(self.repo.listar(False, 5, q="analista", deslocamento=2)), ["u9"])
        # Página por página junta tudo sem repetir nem perder.
        paginas = []
        for desloc in range(0, 9, 4):
            paginas += urls(self.repo.listar(False, 4, deslocamento=desloc))
        self.assertEqual(paginas, [c[0] for c in CONJUNTO])

    def test_deslocamento_invalido_e_dado_invalido(self):
        for deslocamento in (-1, 10_001, True, 1.5, "0"):
            with self.subTest(deslocamento=deslocamento), self.assertRaises(DadoInvalido):
                self.repo.listar(False, 10, deslocamento=deslocamento)


class HistoricoMemoriaTest(ContratoHistorico, unittest.TestCase):
    def setUp(self):
        self.repo = VagaEncontradaRepositoryMemoria()

    def test_obter_devolve_copia(self):
        self.carregar()
        self.repo.obter("u1").termos.append("lixo")
        self.repo.obter("u1").titulo = "mudou"

        self.assertEqual(self.repo.obter("u1").termos, ["termo"])
        self.assertEqual(self.repo.obter("u1").titulo, "Desenvolvedor Java Sênior")


@unittest.skipUnless(os.environ.get("PGHOST"), "PGHOST não definido: integração com Postgres pulada")
class HistoricoPostgresTest(ContratoHistorico, unittest.TestCase):
    def setUp(self):
        from src.repository.postgres import VagaEncontradaRepositoryPostgres

        self.conn, _ = _schema_descartavel(self, "teste_historico")
        self.repo = VagaEncontradaRepositoryPostgres(self.conn)

    def test_url_com_nul_nao_estoura(self):
        self.assertIsNone(self.repo.obter("a\x00b"))


@unittest.skipUnless(os.environ.get("PGHOST"), "PGHOST não definido: integração com Postgres pulada")
class ParidadeMemoriaPostgresTest(unittest.TestCase):
    """Mesmo conjunto escrito à mão nos dois: toda busca dá a mesma lista, na mesma ordem, e a mesma contagem."""

    def setUp(self):
        from src.repository.postgres import VagaEncontradaRepositoryPostgres

        self.conn, _ = _schema_descartavel(self, "teste_paridade")
        self.pg = VagaEncontradaRepositoryPostgres(self.conn)
        self.mem = VagaEncontradaRepositoryMemoria()
        for repo in (self.mem, self.pg):
            repo.registrar(vagas_do_conjunto(), "termo")
            repo.marcar_vistas(["u2", "u5"])

    def test_listar_e_contar_iguais_em_todas_as_combinacoes(self):
        combinacoes = 0
        for q in BUSCAS:
            for novas in (False, True):
                for limite, deslocamento in ((500, 0), (2, 0), (2, 1), (3, 4), (1, 0), (5, 20)):
                    with self.subTest(q=q, novas=novas, limite=limite, deslocamento=deslocamento):
                        esperado = self.mem.listar(novas, limite, q=q, deslocamento=deslocamento)
                        obtido = self.pg.listar(novas, limite, q=q, deslocamento=deslocamento)
                        self.assertEqual(urls(obtido), urls(esperado))
                        combinacoes += 1
                self.assertEqual(self.pg.contar(novas, q), self.mem.contar(novas, q))
        self.assertGreater(combinacoes, 400)

    def test_obter_igual(self):
        for url, *_ in CONJUNTO:
            self.assertEqual(self.pg.obter(url).titulo, self.mem.obter(url).titulo)
        self.assertIsNone(self.pg.obter("x"))
        self.assertIsNone(self.mem.obter("x"))


class ApiHistoricoTest(unittest.TestCase):
    def setUp(self):
        self.repo = VagaEncontradaRepositoryMemoria()
        self.repo.registrar(vagas_do_conjunto(), "termo")
        self.api = TestClient(
            criar_app(PerfilBuscaRepositoryMemoria(), CurriculoRepositoryMemoria(), BuscaService([]), encontradas=self.repo)
        )

    def urls(self, consulta: str) -> list[str]:
        resposta = self.api.get(f"/vagas/encontradas?novas=false&{consulta}")
        self.assertEqual(resposta.status_code, 200, resposta.text)
        return [v["url"] for v in resposta.json()]

    def test_q_com_acento_codificado(self):
        self.assertEqual(self.urls("q=concilia%C3%A7%C3%A3o"), ["u3"])
        self.assertEqual(self.urls("q=Conciliacao"), ["u3"])

    def test_q_com_espaco_codificado(self):
        self.assertEqual(self.urls("q=front%20end"), ["u4", "u5"])
        self.assertEqual(self.urls("q=front+end"), ["u4", "u5"])
        self.assertEqual(self.urls("q=analista%20de%20dados"), ["u9"])

    def test_q_pelo_params_do_cliente_faz_a_ida_e_volta(self):
        resposta = self.api.get("/vagas/encontradas", params={"q": "vitória da conquista & cia?#", "novas": "false"})

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), [])
        resposta = self.api.get("/vagas/encontradas", params={"q": "vitória da conquista", "novas": "false"})
        self.assertEqual([v["url"] for v in resposta.json()], ["u5"])

    def test_java_nao_traz_javascript(self):
        self.assertEqual(self.urls("q=java"), ["u1", "u8"])

    def test_q_vazio_nao_filtra(self):
        self.assertEqual(len(self.urls("q=")), len(CONJUNTO))
        self.assertEqual(len(self.urls("q=%20%20")), len(CONJUNTO))

    def test_deslocamento_e_limite(self):
        self.assertEqual(self.urls("limite=3&deslocamento=3"), ["u4", "u5", "u6"])
        self.assertEqual(self.urls("limite=2&q=analista&deslocamento=1"), ["u7", "u9"])

    def test_formato_da_resposta_nao_mudou(self):
        [vaga] = self.api.get("/vagas/encontradas?novas=false&q=barista&limite=5").json()

        self.assertEqual(
            sorted(vaga), sorted(["url", "titulo", "empresa", "plataforma", "local", "remoto", "publicada_em", "termos", "primeira_vez_em", "ultima_vez_em", "vista_em"])
        )

    def test_parametros_invalidos_sao_422(self):
        for consulta in ("deslocamento=-1", "deslocamento=abc", "deslocamento=10001", f"q={'x' * 201}", "limite=0"):
            with self.subTest(consulta=consulta):
                self.assertEqual(self.api.get(f"/vagas/encontradas?{consulta}").status_code, 422)

    def test_dado_invalido_do_repositorio_vira_422_e_outro_erro_vira_500(self):
        class Invalido(VagaEncontradaRepositoryMemoria):
            def listar_recentes(self, *a, **k):
                raise DadoInvalido("q ruim")

        class Quebrado(VagaEncontradaRepositoryMemoria):
            def listar_recentes(self, *a, **k):
                raise ValueError("config")

        for classe, status in ((Invalido, 422), (Quebrado, 500)):
            api = TestClient(
                criar_app(PerfilBuscaRepositoryMemoria(), CurriculoRepositoryMemoria(), BuscaService([]), encontradas=classe()),
                raise_server_exceptions=False,
            )
            self.assertEqual(api.get("/vagas/encontradas?q=a").status_code, status)


if __name__ == "__main__":
    unittest.main()
