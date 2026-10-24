"""O histórico (JSON e tela) obedece ao perfil salvo NA LEITURA: filtros de `aplicar_perfil` e ordem por match.

Todas as vagas daqui são escritas à mão (não são respostas gravadas de provider).
"""

import os
import unittest
from datetime import date
from unittest import mock
from urllib.parse import quote

from fastapi.testclient import TestClient

from src.controller.api import criar_app
from src.model.perfil import Perfil
from src.model.vaga import Modalidade
from src.model.vaga_externa import VagaExterna
from src.repository.base import CurriculoRepositoryMemoria, PerfilBuscaRepositoryMemoria, VagaEncontradaRepositoryMemoria
from src.service import historico_service
from src.service.busca_service import BuscaService
from src.service.historico_service import HistoricoService
from src.service.recomendacao_service import relevancia_perfil
from tests.test_coleta import _schema_descartavel
from tests.test_tela_lista import HX, URLS_DIFICEIS, Pagina

HOJE = date(2026, 10, 12)


def v(url: str, titulo: str, local: str | None = "São Paulo", remoto: bool | None = False, pub: date | None = None):
    return VagaExterna(titulo, "Empresa Teste", "Remotar", url, local=local, remoto=remoto, publicada_em=pub)


# Coletadas ANTES (primeira_vez_em mais antiga): as que mais combinam com o perfil.
ANTES = [
    v("a-django", "Desenvolvedor Python Django", remoto=True, local="Remoto", pub=date(2026, 10, 1)),
    v("a-py", "Desenvolvedor Python", remoto=True, local="Remoto", pub=date(2026, 10, 2)),
    v("a-presencial", "Desenvolvedor Python Django", remoto=False, local="São Paulo", pub=date(2026, 10, 3)),
    v("a-estagio", "Estágio em Python", remoto=True, local="Remoto", pub=date(2026, 10, 9)),
]
# Coletadas DEPOIS (último termo da coleta): sem relação com o perfil abaixo.
DEPOIS = [
    v("d-assist", "Assistente Administrativo", remoto=True, local="Remoto", pub=date(2026, 10, 10)),
    v("d-recep", "Recepcionista", remoto=False, local="Rio de Janeiro", pub=None),
]

PERFIL = Perfil(termos=["desenvolvedor python"], skills=["django"])
SO_REMOTO = Perfil(termos=["desenvolvedor python"], skills=["django"], modalidades=[Modalidade.REMOTO])

# Ordem por match esperada com PERFIL: (relevância, skills, publicada_em, primeira_vez_em).
ORDEM_PERFIL = ["a-presencial", "a-django", "a-py", "a-estagio", "d-assist", "d-recep"]
# Sem perfil: coletadas mais recentemente primeiro (o comportamento antigo).
ORDEM_SEM_PERFIL = ["d-assist", "d-recep", "a-estagio", "a-presencial", "a-py", "a-django"]


def urls(vagas) -> list[str]:
    return [x.url for x in vagas]


class ContratoHistoricoPerfil:
    """Mesmos casos na memória e no Postgres; a subclasse define `self.repo` e `self.perfis`."""

    def setUp(self):
        self.criar()
        # Dois registros: o segundo tem primeira_vez_em mais nova.
        self.repo.registrar(ANTES, "desenvolvedor python")
        self.repo.registrar(DEPOIS, "assistente administrativo")
        self.servico = HistoricoService(self.repo, self.perfis)

    def listar(self, novas=False, limite=500, q=None, deslocamento=0) -> list[str]:
        return urls(self.servico.listar(novas, limite, q=q, deslocamento=deslocamento, hoje=HOJE))

    def test_sem_perfil_mantem_o_comportamento_antigo(self):
        self.assertEqual(self.listar(), ORDEM_SEM_PERFIL)
        self.assertEqual(self.listar(), urls(self.repo.listar(False, 500)))
        self.assertEqual(self.servico.contar(False, hoje=HOJE), 6)
        self.assertEqual(self.listar(q="python"), urls(self.repo.listar(False, 500, q="python")))

    def test_ordem_por_match_poe_titulo_com_termo_e_skills_primeiro_mesmo_coletada_antes(self):
        self.perfis.salvar(PERFIL)

        self.assertEqual(self.listar(), ORDEM_PERFIL)

    def test_perfil_so_remoto_esconde_presencial_ja_gravada_e_volta_quando_o_perfil_muda(self):
        self.perfis.salvar(SO_REMOTO)
        visiveis = self.listar()
        self.assertNotIn("a-presencial", visiveis)
        self.assertNotIn("d-recep", visiveis)
        self.assertEqual(visiveis, ["a-django", "a-py", "a-estagio", "d-assist"])
        self.assertEqual(self.servico.contar(False, hoje=HOJE), 4)

        self.perfis.salvar(PERFIL)

        self.assertEqual(self.listar(), ORDEM_PERFIL)
        self.assertEqual(self.servico.contar(False, hoje=HOJE), 6)

    def test_demais_filtros_do_perfil_valem_no_historico(self):
        self.perfis.salvar(Perfil(termos=["desenvolvedor python"], excluir=["estágio"], publicada_desde_dias=5))
        # publicada_desde_dias=5 a partir de 12/10: corta até 06/10; sem data fica.
        self.assertEqual(self.listar(), ["d-assist", "d-recep"])
        self.perfis.salvar(Perfil(termos=["python"], localidades=["Rio de Janeiro"]))
        # Só a cidade do perfil ou remotas.
        self.assertNotIn("a-presencial", self.listar())
        self.assertIn("d-recep", self.listar())

    def test_contador_e_igual_aos_itens_visiveis(self):
        for perfil in (None, PERFIL, SO_REMOTO):
            for novas in (False, True):
                for q in (None, "python", "desenvolvedor django", "nada"):
                    with self.subTest(perfil=perfil, novas=novas, q=q):
                        if perfil is not None:
                            self.perfis.salvar(perfil)
                        todas = self.listar(novas=novas, q=q)
                        self.assertEqual(self.servico.contar(novas, q, hoje=HOJE), len(todas))
                        if perfil is SO_REMOTO:
                            # O contador conta o que o perfil deixa, não o histórico inteiro.
                            self.assertNotIn("a-presencial", todas)
                            self.assertLess(len(todas), self.repo.contar(novas, q) or 1)

    def test_paginacao_estavel_sem_repetir_nem_pular(self):
        self.perfis.salvar(SO_REMOTO)
        # Empates de relevância, skills e data: só a URL desempata.
        self.repo.registrar([v(f"e{i}", "Desenvolvedor Python", remoto=True, pub=date(2026, 10, 2)) for i in (3, 1, 2)], "x")
        inteira = self.listar()
        # Ordem por match: skills, depois data igual com coleta mais nova (e1..e3 pela URL), sem as presenciais.
        self.assertEqual(inteira, ["a-django", "e1", "e2", "e3", "a-py", "a-estagio", "d-assist"])
        for limite in (1, 2, 3, 4):
            with self.subTest(limite=limite):
                lidas = []
                for inicio in range(0, len(inteira) + limite, limite):
                    lidas += self.listar(limite=limite, deslocamento=inicio)
                self.assertEqual(lidas, inteira)
        self.assertEqual(self.listar(), inteira, "duas leituras iguais dão a mesma ordem")

    def test_q_e_perfil_juntos(self):
        self.perfis.salvar(SO_REMOTO)

        self.assertEqual(self.listar(q="python"), ["a-django", "a-py", "a-estagio"])
        self.assertEqual(self.servico.contar(False, "python", hoje=HOJE), 3)
        self.assertEqual(self.listar(q="django"), ["a-django"])

    def test_novas_com_perfil(self):
        self.perfis.salvar(SO_REMOTO)
        self.repo.marcar_vistas(["a-django"])

        self.assertEqual(self.listar(novas=True), ["a-py", "a-estagio", "d-assist"])
        self.assertEqual(self.servico.contar(True, hoje=HOJE), 3)

    def test_teto_de_varredura_corta_as_mais_antigas(self):
        self.perfis.salvar(PERFIL)
        with mock.patch.object(historico_service, "TETO_VARREDURA_HISTORICO", 2):
            # Só as 2 coletadas mais recentemente entram na janela.
            self.assertEqual(sorted(self.listar()), ["d-assist", "d-recep"])
            self.assertEqual(self.servico.contar(False, hoje=HOJE), 2)


class HistoricoPerfilMemoriaTest(ContratoHistoricoPerfil, unittest.TestCase):
    def criar(self):
        self.repo = VagaEncontradaRepositoryMemoria(1)
        self.perfis = PerfilBuscaRepositoryMemoria(1)


@unittest.skipUnless(os.environ.get("PGHOST"), "PGHOST não definido: integração com Postgres pulada")
class HistoricoPerfilPostgresTest(ContratoHistoricoPerfil, unittest.TestCase):
    def criar(self):
        from src.repository.postgres import PerfilBuscaRepositoryPostgres, VagaEncontradaRepositoryPostgres

        self.conn, _ = _schema_descartavel(self, "teste_historico_perfil")
        self.repo = VagaEncontradaRepositoryPostgres(self.conn, 1)
        self.perfis = PerfilBuscaRepositoryPostgres(self.conn, 1)

    def test_listar_recentes_igual_a_memoria(self):
        memoria = VagaEncontradaRepositoryMemoria(1)
        memoria.registrar(ANTES, "desenvolvedor python")
        memoria.registrar(DEPOIS, "assistente administrativo")
        for novas in (False, True):
            for teto in (1, 3, 500):
                for q in (None, "python", "nada"):
                    with self.subTest(novas=novas, teto=teto, q=q):
                        self.assertEqual(
                            urls(self.repo.listar_recentes(novas, teto, q)), urls(memoria.listar_recentes(novas, teto, q))
                        )


class RelevanciaPerfilTest(unittest.TestCase):
    def test_maior_relevancia_entre_os_termos_e_termo_sem_palavra_util_nao_conta(self):
        self.assertEqual(relevancia_perfil("Desenvolvedor Python", ["assistente", "desenvolvedor python"]), 1.0)
        self.assertEqual(relevancia_perfil("Estágio em Python", ["desenvolvedor python"]), 0.5)
        # "de" vale 1 para todo título em relevancia_termo: aqui é ignorado.
        self.assertEqual(relevancia_perfil("Recepcionista", ["de", "  ", "desenvolvedor python"]), 0.0)
        self.assertEqual(relevancia_perfil("Recepcionista", []), 0.0)


class ApiETelaComPerfilTest(unittest.TestCase):
    def setUp(self):
        self.repo = VagaEncontradaRepositoryMemoria(1)
        self.repo.registrar(ANTES, "desenvolvedor python")
        self.repo.registrar(DEPOIS, "assistente administrativo")
        self.perfis = PerfilBuscaRepositoryMemoria(1)
        app = criar_app(self.perfis, CurriculoRepositoryMemoria(1), BuscaService([]), encontradas=self.repo)
        self.cliente = TestClient(app, raise_server_exceptions=False)

    def json(self, consulta: str = "") -> list[str]:
        resposta = self.cliente.get(f"/vagas/encontradas?novas=false&{consulta}")
        self.assertEqual(resposta.status_code, 200, resposta.text)
        return [x["url"] for x in resposta.json()]

    def test_rota_json_aplica_o_perfil_e_a_ordem(self):
        self.assertEqual(self.json(), ORDEM_SEM_PERFIL)
        self.perfis.salvar(SO_REMOTO)
        self.assertEqual(self.json(), ["a-django", "a-py", "a-estagio", "d-assist"])
        self.assertEqual(self.json("limite=2&deslocamento=2"), ["a-estagio", "d-assist"])
        self.assertEqual(self.json("q=python"), ["a-django", "a-py", "a-estagio"])

    def test_tela_contador_cards_e_alternancia_com_perfil(self):
        self.perfis.salvar(SO_REMOTO)
        self.repo.marcar_vistas(["a-django"])

        pagina = Pagina(self.cliente.get("/tela").text)
        self.assertEqual([c.attrs["data-url"] for c in pagina.cards()], ["a-django", "a-py", "a-estagio", "d-assist"])
        self.assertIn("4 encontradas", pagina.por_id("vagas").texto)

        novas = Pagina(self.cliente.get("/tela/vagas?novas=1", headers=HX).text)
        self.assertEqual([c.attrs["data-url"] for c in novas.cards()], ["a-py", "a-estagio", "d-assist"])
        self.assertIn("3 não abertas", novas.por_id("vagas").texto)

        busca = Pagina(self.cliente.get("/tela/vagas?q=django", headers=HX).text)
        self.assertEqual([c.attrs["data-url"] for c in busca.cards()], ["a-django"])
        self.assertIn("1 encontradas", busca.por_id("vagas").texto)

    def test_carregar_mais_com_perfil_percorre_as_visiveis_sem_repetir(self):
        # 60 remotas + 10 presenciais; o perfil só remoto deixa 60: 50 + "carregar mais" com 10.
        self.repo.registrar([v(f"r{i:02}", f"Analista {i}", remoto=True) for i in range(60)], "y")
        self.repo.registrar([v(f"p{i:02}", f"Analista {i}", remoto=False) for i in range(10)], "y")
        self.perfis.salvar(Perfil(termos=["analista"], modalidades=[Modalidade.REMOTO]))
        esperado = self.json("limite=500")
        self.assertEqual(len(esperado), 64)  # 60 + a-django, a-py, a-estagio, d-assist

        pagina = Pagina(self.cliente.get("/tela").text)
        self.assertIn("64 encontradas", pagina.por_id("vagas").texto)
        lidas = [c.attrs["data-url"] for c in pagina.cards()]
        proximo = next(e for e in pagina.tags("li") if "mais" in (e.attrs.get("class") or "")).attrs["hx-get"]
        while proximo:
            parte = Pagina(self.cliente.get(proximo, headers=HX).text)
            lidas += [c.attrs["data-url"] for c in parte.cards()]
            sentinelas = [e for e in parte.tags("li") if "mais" in (e.attrs.get("class") or "")]
            proximo = sentinelas[0].attrs["hx-get"] if sentinelas else None
        self.assertEqual(lidas, esperado)

    def test_codificacao_de_url_ida_e_volta_com_perfil(self):
        self.perfis.salvar(SO_REMOTO)
        self.repo.registrar([v(url, "Desenvolvedor Python Django", remoto=True) for url in URLS_DIFICEIS], "z")
        presencial = "https://x.com/presencial?a=1&b=2#c"
        self.repo.registrar([v(presencial, "Desenvolvedor Python Django", remoto=False)], "z")
        cards = {c.attrs["data-url"]: c for c in Pagina(self.cliente.get("/tela").text).cards()}
        self.assertNotIn(presencial, cards, "o perfil só remoto esconde a presencial")
        # A escondida da lista continua abrindo pelo link direto (favorito antigo).
        direto = Pagina(self.cliente.get("/tela/vagas/detalhe?url=" + quote(presencial, safe="/"), headers=HX).text)
        self.assertEqual(direto.por_id("detalhe").attrs["data-url"], presencial)
        for url in URLS_DIFICEIS:
            with self.subTest(url=url):
                card = cards[url]
                self.assertEqual(card.attrs["hx-get"], "/tela/vagas/detalhe?url=" + quote(url, safe="/"))
                parcial = Pagina(self.cliente.get(card.attrs["hx-get"], headers=HX).text)
                self.assertEqual(parcial.por_id("detalhe").attrs["data-url"], url)


if __name__ == "__main__":
    unittest.main()
