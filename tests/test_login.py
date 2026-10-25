"""Login, sessão, logout, força bruta, CSRF do login e a pessoa da sessão nas rotas.

Pessoas, senhas, vagas e currículos daqui são inventados e escritos à mão (não são respostas gravadas).
"""

import hashlib
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.routing import Mount

from src.controller.api import criar_app
from src.controller.pedidos_ia import exigir_worker_ia
from src.controller.sessao import COOKIE, ISENTAS, PREFIXOS_ISENTOS, exigir_sessao
from src.model.curriculo import Curriculo
from src.model.perfil import Perfil
from src.model.vaga_externa import VagaExterna
from src.repository.base import (
    CurriculoRepositoryMemoria,
    PerfilBuscaRepositoryMemoria,
    VagaEncontradaRepositoryMemoria,
)
from src.repository.candidatura import CandidaturaRepositoryMemoria, vaga_por_url_em
from src.repository.pedido_ia import PedidoIARepositoryMemoria
from src.repository.pessoa import PessoaRepositoryMemoria
from src.repository.pessoa_provider import PessoaProviderRepositoryMemoria
from src.repository.sessao import SessaoRepositoryMemoria
from src.service import senha as senhas
from src.service.autenticacao import Autenticacao, hash_do_token
from src.service.busca_service import BuscaService
from src.service.coleta_service import RepositoriosDaPessoa
from src.service.pedido_ia_service import FabricasPorPessoa
from tests.sessao import ClienteLogado, abrir_sessao
from tests.test_coleta import ClientPorTermo, vaga

SENHA_UM = "senha-um-de-teste"
SENHA_DOIS = "senha-dois-de-teste"
HASH_UM = senhas.hash_senha(SENHA_UM)
HASH_DOIS = senhas.hash_senha(SENHA_DOIS)
ORIGEM = {"Origin": "http://testserver"}
HX = {"HX-Request": "true", "Origin": "http://testserver"}
TOKEN_WORKER = "token-do-worker-de-teste"
AUTH = {"Authorization": f"Bearer {TOKEN_WORKER}"}
INICIO = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)

URL_UM = "https://exemplo.com/vaga-da-pessoa-um"
URL_DOIS = "https://exemplo.com/vaga-da-pessoa-dois"
URL_COMUM = "https://exemplo.com/vaga-comum"


class Relogio:
    def __init__(self):
        self.agora = INICIO

    def __call__(self):
        return self.agora

    def avancar(self, **kw):
        self.agora += timedelta(**kw)


class Mundo:
    """Duas pessoas (1 e 2, com senha) e uma sem senha (3), no mesmo "banco" em memória, com repositórios por pessoa."""

    def __init__(self, providers=()):
        self.relogio = Relogio()
        self.pessoas = PessoaRepositoryMemoria()
        self.pessoas.adicionar(1, "Pessoa Teste Um", HASH_UM)
        self.pessoas.adicionar(2, "Pessoa Teste Dois", HASH_DOIS)
        self.pessoas.adicionar(3, "Pessoa Sem Senha", None)
        self.sessoes = SessaoRepositoryMemoria(self.pessoas)
        self.autenticacao = Autenticacao(self.pessoas, self.sessoes, relogio=self.relogio)
        self.vagas, self.perfis, self.curriculos, self.pedidos, self.candidaturas, self.providers = {}, {}, {}, {}, {}, {}
        for p in (1, 2, 3):
            primeiro = p == 1
            self.vagas[p] = VagaEncontradaRepositoryMemoria(p, compartilhado=None if primeiro else self.vagas[1])
            self.perfis[p] = PerfilBuscaRepositoryMemoria(p, None if primeiro else self.perfis[1])
            self.curriculos[p] = CurriculoRepositoryMemoria(p, None if primeiro else self.curriculos[1])
            self.pedidos[p] = PedidoIARepositoryMemoria(
                p, vaga_por_url_em(self.vagas[p]), self.relogio, compartilhado=None if primeiro else self.pedidos[1]
            )
            self.candidaturas[p] = CandidaturaRepositoryMemoria(
                p, vaga_por_url_em(self.vagas[p]), None if primeiro else self.candidaturas[1]
            )
            self.providers[p] = PessoaProviderRepositoryMemoria(p, None if primeiro else self.providers[1])
        self.vagas[1].registrar([VagaExterna("Analista Um", "Acme", "Remotar", URL_UM, "Brasil", True)], "dados")
        self.vagas[1].registrar([VagaExterna("Vaga Comum Um", "Acme", "Remotar", URL_COMUM, "Brasil", True)], "dados")
        self.vagas[2].registrar([VagaExterna("Engenheira Dois", "Globex", "Ashby", URL_DOIS, "Remote", True)], "data")
        self.vagas[2].registrar([VagaExterna("Vaga Comum Dois", "Globex", "Ashby", URL_COMUM, "Remote", True)], "data")
        self.perfis[1].salvar(Perfil(termos=["python"]))
        self.perfis[2].salvar(Perfil(termos=["golang"]))
        self.base = {
            p: self.curriculos[p].salvar(Curriculo(tipo="base", titulo=f"Base {n}", conteudo_md=f"# {n}"))
            for p, n in ((1, "Um"), (2, "Dois"))
        }
        fabricas = FabricasPorPessoa(self.perfis.__getitem__, self.curriculos.__getitem__, self.vagas.__getitem__)
        self.app = criar_app(
            self.perfis[1],
            self.curriculos[1],
            BuscaService(list(providers)),
            encontradas=self.vagas[1],
            candidaturas=self.candidaturas[1],
            pedidos_ia=self.pedidos[1],
            pessoas=self.pessoas,
            repositorios_da_pessoa=lambda p: RepositoriosDaPessoa(
                self.perfis[p], self.vagas[p], self.curriculos[p], self.pedidos[p], self.candidaturas[p]
            ),
            providers_da_pessoa=self.providers.__getitem__,
            fabricas=fabricas,
            autenticacao=self.autenticacao,
        )

    def anonimo(self, **kw) -> TestClient:
        return TestClient(self.app, follow_redirects=False, **kw)

    def logado(self, pessoa_id: int, **kw) -> TestClient:
        """Entra de verdade, pelo formulário de login."""
        cliente = self.anonimo(**kw)
        nome, senha = {1: ("Pessoa Teste Um", SENHA_UM), 2: ("Pessoa Teste Dois", SENHA_DOIS)}[pessoa_id]
        resposta = cliente.post("/login", data={"nome": nome, "senha": senha}, headers=ORIGEM)
        assert resposta.status_code == 303, resposta.text
        return cliente

    def entrar(self, nome: str, senha: str, cliente: TestClient | None = None):
        return (cliente or self.anonimo()).post("/login", data={"nome": nome, "senha": senha}, headers=ORIGEM)


class LoginTest(unittest.TestCase):
    def setUp(self):
        self.m = Mundo()

    def test_login_certo_redireciona_para_a_tela_e_seta_o_cookie_com_as_flags(self):
        resposta = self.m.entrar("Pessoa Teste Um", SENHA_UM)

        self.assertEqual((resposta.status_code, resposta.headers["location"]), (303, "/tela"))
        cookie = resposta.headers["set-cookie"].lower()
        self.assertIn(f"{COOKIE}=", cookie)
        for flag in ("httponly", "samesite=strict", "path=/", f"max-age={30 * 86400}"):
            self.assertIn(flag, cookie)
        self.assertNotIn("secure", cookie)  # padrão: a LAN é http
        self.assertEqual(resposta.headers["cache-control"], "no-store")

    def test_cookie_secure_por_variavel_e_valor_invalido_cai_no_padrao(self):
        for valor, esperado in (("1", True), ("true", True), ("0", False), ("", False), ("talvez", False)):
            with self.subTest(valor=valor), mock.patch.dict(os.environ, {"SESSAO_COOKIE_SECURE": valor}):
                cookie = self.m.entrar("Pessoa Teste Um", SENHA_UM).headers["set-cookie"].lower()
                self.assertEqual("secure" in cookie, esperado)

    def test_dias_da_sessao_por_variavel_e_valor_invalido_cai_no_padrao(self):
        for valor, dias in (("7", 7), ("365", 365), ("0", 30), ("-1", 30), ("abc", 30), ("366", 30), ("", 30)):
            with self.subTest(valor=valor), mock.patch.dict(os.environ, {"SESSAO_DIAS": valor}):
                cookie = self.m.entrar("Pessoa Teste Um", SENHA_UM).headers["set-cookie"].lower()
                self.assertIn(f"max-age={dias * 86400}", cookie)
        mundo = Mundo()
        with mock.patch.dict(os.environ, {"SESSAO_DIAS": "7"}):
            mundo.entrar("Pessoa Teste Um", SENHA_UM)
        self.assertEqual([e for _, e in mundo.sessoes.linhas.values()], [mundo.relogio() + timedelta(days=7)])

    def test_o_banco_guarda_so_o_hash_do_token(self):
        cliente = self.m.logado(1)
        token = cliente.cookies.get(COOKIE)

        self.assertGreaterEqual(len(token), 40)
        self.assertNotIn(token, self.m.sessoes.linhas)
        self.assertIn(hashlib.sha256(token.encode()).hexdigest(), self.m.sessoes.linhas)

    def test_cada_login_tem_token_novo(self):
        a, b = self.m.logado(1).cookies.get(COOKIE), self.m.logado(1).cookies.get(COOKIE)

        self.assertNotEqual(a, b)

    def test_nome_sem_diferenciar_caixa_nem_pontas(self):
        self.assertEqual(self.m.entrar("  pessoa TESTE um ", SENHA_UM).status_code, 303)

    def test_login_errado_e_generico_e_nao_diz_se_o_nome_existe(self):
        casos = {
            "senha errada": ("Pessoa Teste Um", "senha-errada-aqui"),
            "nome inexistente": ("Ninguem Assim", SENHA_UM),
            "pessoa sem senha": ("Pessoa Sem Senha", SENHA_UM),
            "pessoa sem senha e senha vazia": ("Pessoa Sem Senha", ""),
            "nome vazio": ("", SENHA_UM),
            "nome com NUL": ("Pessoa\x00Um", SENHA_UM),
            "nome enorme": ("a" * 500, SENHA_UM),
        }
        corpos = set()
        for nome, (usuario, senha) in casos.items():
            with self.subTest(nome):
                resposta = self.m.entrar(usuario, senha)
                self.assertEqual(resposta.status_code, 401)
                self.assertNotIn("set-cookie", resposta.headers)
                self.assertIn("Nome ou senha incorretos.", resposta.text)
                corpos.add(resposta.text.replace(usuario[:60], ""))
        self.assertEqual(len(corpos), 1)  # a página é a mesma, só o nome digitado volta no campo
        self.assertEqual(self.m.sessoes.linhas, {})

    def test_nome_digitado_volta_escapado_e_a_senha_nunca_volta(self):
        resposta = self.m.entrar('"><script>x</script>', "minha-senha-secreta")

        self.assertNotIn("<script>x", resposta.text)
        self.assertNotIn("minha-senha-secreta", resposta.text)

    def test_login_errado_e_nulo_gastam_o_mesmo_scrypt_da_senha_certa(self):
        real = senhas._scrypt
        for usuario in ("Pessoa Teste Um", "Ninguem Assim", "Pessoa Sem Senha"):
            with self.subTest(usuario), mock.patch.object(senhas, "_scrypt", wraps=real) as scrypt:
                self.m.entrar(usuario, "senha-errada-aqui")
                self.assertEqual(scrypt.call_count, 1)

    def test_formulario_de_login_e_aberto_e_nao_vai_para_o_cache(self):
        resposta = self.m.anonimo().get("/login")

        self.assertEqual(resposta.status_code, 200)
        self.assertIn('type="password"', resposta.text)
        self.assertEqual(resposta.headers["cache-control"], "no-store")
        self.assertIn("default-src 'self'", resposta.headers["content-security-policy"])

    def test_quem_ja_esta_logado_vai_do_login_para_a_tela(self):
        resposta = self.m.logado(1).get("/login")

        self.assertEqual((resposta.status_code, resposta.headers["location"]), (303, "/tela"))

    def test_trocar_de_pessoa_no_mesmo_navegador_encerra_a_sessao_anterior(self):
        cliente = self.m.logado(1)
        antigo = cliente.cookies.get(COOKIE)

        cliente.post("/login", data={"nome": "Pessoa Teste Dois", "senha": SENHA_DOIS}, headers=ORIGEM)

        self.assertNotIn(hashlib.sha256(antigo.encode()).hexdigest(), self.m.sessoes.linhas)
        self.assertIn("Pessoa Teste Dois", cliente.get("/tela").text)

    def test_header_da_tela_mostra_o_nome_escapado_e_o_botao_sair(self):
        self.m.pessoas.adicionar(2, "<b>Pessoa</b> & Dois", HASH_DOIS)
        cliente = self.m.anonimo()
        cliente.post("/login", data={"nome": "<b>Pessoa</b> & Dois", "senha": SENHA_DOIS}, headers=ORIGEM)
        pagina = cliente.get("/tela").text

        self.assertIn("&lt;b&gt;Pessoa&lt;/b&gt; &amp; Dois", pagina)
        self.assertNotIn("<b>Pessoa</b>", pagina)
        self.assertIn('action="/logout"', pagina)
        self.assertIn(">Sair<", pagina)

    def test_header_do_login_nao_tem_sair(self):
        self.assertNotIn("/logout", self.m.anonimo().get("/login").text)


class ForcaBrutaTest(unittest.TestCase):
    def setUp(self):
        self.m = Mundo()

    def errar(self, vezes: int, nome="Pessoa Teste Um"):
        return [self.m.entrar(nome, "senha-errada-aqui").status_code for _ in range(vezes)]

    def test_dez_falhas_em_15_minutos_bloqueiam_ate_a_senha_certa(self):
        self.assertEqual(self.errar(10), [401] * 10)

        certa = self.m.entrar("Pessoa Teste Um", SENHA_UM)
        errada = self.m.entrar("Pessoa Teste Um", "senha-errada-aqui")

        self.assertEqual(certa.status_code, 429)
        self.assertNotIn("set-cookie", certa.headers)
        self.assertEqual(errada.status_code, 429)
        self.assertIn("Retry-After", certa.headers)
        self.assertEqual(self.m.sessoes.linhas, {})

    def test_nove_falhas_e_a_senha_certa_entra(self):
        self.errar(9)

        self.assertEqual(self.m.entrar("Pessoa Teste Um", SENHA_UM).status_code, 303)

    def test_o_bloqueio_nao_confere_a_senha(self):
        self.errar(10)
        with mock.patch.object(senhas, "_scrypt") as scrypt:
            self.m.entrar("Pessoa Teste Um", SENHA_UM)
        scrypt.assert_not_called()

    def test_conta_por_nome_e_nao_trava_as_outras_pessoas(self):
        self.errar(10)

        self.assertEqual(self.m.entrar("Pessoa Teste Dois", SENHA_DOIS).status_code, 303)

    def test_nome_inexistente_tambem_bloqueia_sem_revelar_nada(self):
        self.assertEqual(self.errar(10, "Ninguem Assim"), [401] * 10)

        self.assertEqual(self.m.entrar("Ninguem Assim", "x").status_code, 429)

    def test_caixa_e_espacos_nao_fogem_do_limite(self):
        for variante in ("PESSOA TESTE UM", " pessoa teste um", "Pessoa Teste Um "):
            self.errar(4, variante)

        self.assertEqual(self.m.entrar("Pessoa Teste Um", SENHA_UM).status_code, 429)

    def test_depois_de_15_minutos_libera(self):
        self.errar(10)
        self.m.relogio.avancar(minutes=14)
        self.assertEqual(self.m.entrar("Pessoa Teste Um", SENHA_UM).status_code, 429)

        self.m.relogio.avancar(minutes=2)

        self.assertEqual(self.m.entrar("Pessoa Teste Um", SENHA_UM).status_code, 303)

    def test_login_certo_zera_a_contagem(self):
        self.errar(9)
        self.assertEqual(self.m.entrar("Pessoa Teste Um", SENHA_UM).status_code, 303)

        self.assertEqual(self.errar(9), [401] * 9)

    def test_tentativas_em_paralelo_nao_furam_o_limite(self):
        import threading

        resultados = []

        def tentar():
            resultados.append(self.m.entrar("Pessoa Teste Um", "senha-errada-aqui").status_code)

        threads = [threading.Thread(target=tentar) for _ in range(25)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(sorted(resultados), [401] * 10 + [429] * 15)

    def test_mapa_do_limite_tem_teto_de_memoria(self):
        from src.service.autenticacao import LimiteDeTentativas

        limite = LimiteDeTentativas(max_chaves=50, relogio=self.m.relogio)
        for i in range(500):
            limite.reservar(f"nome-{i}")

        self.assertLessEqual(len(limite._tentativas), 50)


class SessaoTest(unittest.TestCase):
    def setUp(self):
        self.m = Mundo()

    def test_sessao_valida_abre_a_tela(self):
        self.assertEqual(self.m.logado(1).get("/tela").status_code, 200)

    def test_sessao_expirada_e_sem_sessao(self):
        cliente = self.m.logado(1)
        self.m.relogio.avancar(days=29, hours=23)
        self.assertEqual(cliente.get("/tela").status_code, 200)

        self.m.relogio.avancar(hours=2)

        resposta = cliente.get("/tela")
        self.assertEqual((resposta.status_code, resposta.headers["location"]), (303, "/login"))
        self.assertEqual(cliente.get("/perfil").status_code, 401)

    def test_login_novo_limpa_as_sessoes_expiradas(self):
        self.m.logado(1)
        self.m.relogio.avancar(days=31)

        self.m.logado(2)

        self.assertEqual(len(self.m.sessoes.linhas), 1)

    def test_cookie_desconhecido_ou_torto_e_sem_sessao(self):
        for valor in ("nada", "", "a" * 5000, "x" * 43):
            with self.subTest(valor=valor[:10]):
                cliente = self.m.anonimo()
                cliente.cookies.set(COOKIE, valor)
                self.assertEqual(cliente.get("/perfil").status_code, 401)

    def test_sessao_de_pessoa_que_sumiu_nao_vale(self):
        token = abrir_sessao(self.m.app, 1)
        del self.m.pessoas._pessoas[1]
        cliente = self.m.anonimo()
        cliente.cookies.set(COOKIE, token)

        self.assertEqual(cliente.get("/perfil").status_code, 401)

    def test_logout_apaga_a_linha_e_o_cookie(self):
        cliente = self.m.logado(1)
        token = cliente.cookies.get(COOKIE)

        resposta = cliente.post("/logout", headers=ORIGEM)

        self.assertEqual((resposta.status_code, resposta.headers["location"]), (303, "/login"))
        cookie = resposta.headers["set-cookie"].lower()
        self.assertIn(f"{COOKIE}=", cookie)
        self.assertTrue("max-age=0" in cookie or "1970" in cookie)
        self.assertEqual(self.m.sessoes.linhas, {})
        # O token antigo, mesmo se guardado por alguém, não abre mais nada.
        velho = self.m.anonimo()
        velho.cookies.set(COOKIE, token)
        self.assertEqual(velho.get("/perfil").status_code, 401)

    def test_logout_so_acaba_a_sessao_de_quem_saiu(self):
        um, dois = self.m.logado(1), self.m.logado(2)

        um.post("/logout", headers=ORIGEM)

        self.assertEqual(dois.get("/perfil").status_code, 200)

    def test_logout_sem_sessao_nao_quebra(self):
        resposta = self.m.anonimo().post("/logout", headers=ORIGEM)

        self.assertEqual(resposta.status_code, 303)

    def test_trocar_a_senha_encerra_as_sessoes_da_pessoa(self):
        from src.controller.pessoa import trocar_senha

        cliente = self.m.logado(1)
        outra = self.m.logado(2)

        trocar_senha(self.m.pessoas, self.m.sessoes, 1, lambda _: "outra-senha-de-teste")

        self.assertEqual(cliente.get("/perfil").status_code, 401)
        self.assertEqual(outra.get("/perfil").status_code, 200)
        self.assertEqual(self.m.entrar("Pessoa Teste Um", SENHA_UM).status_code, 401)
        self.assertEqual(self.m.entrar("Pessoa Teste Um", "outra-senha-de-teste").status_code, 303)


class CsrfDoLoginTest(unittest.TestCase):
    def setUp(self):
        self.m = Mundo()
        self.dados = {"nome": "Pessoa Teste Um", "senha": SENHA_UM}

    def post(self, caminho="/login", **headers):
        return self.m.anonimo().post(caminho, data=self.dados, headers=headers)

    def test_origin_de_outro_site_e_403_e_nao_cria_sessao(self):
        for origem in ("http://site-do-mal.example", "null", "http://testserver.evil.example", "http://testserver:9999"):
            with self.subTest(origem):
                self.assertEqual(self.post(Origin=origem).status_code, 403)
        self.assertEqual(self.m.sessoes.linhas, {})

    def test_sem_origin_e_sem_sec_fetch_site_e_403(self):
        self.assertEqual(self.post().status_code, 403)

    def test_sem_origin_so_com_sec_fetch_site_same_origin(self):
        self.assertEqual(self.post(**{"Sec-Fetch-Site": "cross-site"}).status_code, 403)
        self.assertEqual(self.post(**{"Sec-Fetch-Site": "same-site"}).status_code, 403)
        self.assertEqual(self.post(**{"Sec-Fetch-Site": "same-origin"}).status_code, 303)

    def test_mesma_origem_entra_sem_hx_request(self):
        self.assertEqual(self.post(Origin="http://testserver").status_code, 303)

    def test_logout_tem_a_mesma_protecao(self):
        cliente = self.m.logado(1)

        self.assertEqual(cliente.post("/logout", headers={"Origin": "http://site-do-mal.example"}).status_code, 403)
        self.assertEqual(cliente.post("/logout").status_code, 403)
        self.assertEqual(len(self.m.sessoes.linhas), 1)
        self.assertEqual(cliente.post("/logout", headers=ORIGEM).status_code, 303)

    def test_get_no_logout_nao_existe(self):
        cliente = self.m.logado(1)

        self.assertEqual(cliente.get("/logout").status_code, 405)
        self.assertEqual(len(self.m.sessoes.linhas), 1)


class SemSessaoTest(unittest.TestCase):
    """Toda rota exige sessão, salvo a lista explícita de exceções."""

    # (método, caminho) que respondem sem sessão, e por quê.
    EXCECOES = {
        ("GET", "/"): "redireciona para /tela",
        ("GET", "/saude"): "healthcheck",
        ("GET", "/login"): "o formulário",
        ("POST", "/login"): "o login",
        ("POST", "/logout"): "o logout",
        ("GET", "/openapi.json"): "esquema da API, sem dado",
        ("GET", "/docs"): "esquema da API, sem dado",
        ("GET", "/docs/oauth2-redirect"): "esquema da API, sem dado",
        ("GET", "/redoc"): "esquema da API, sem dado",
        ("POST", "/pedidos-ia/proximo"): "Bearer do worker",
        ("PUT", "/pedidos-ia/{id}"): "Bearer do worker",
    }

    def setUp(self):
        self.m = Mundo()

    def rotas(self):
        """(método, caminho) de todas as rotas do app, e os caminhos dos Mount. O FastAPI novo guarda cada
        `include_router` como um `_IncludedRouter`: abre-se um por um e confere-se que a sessão está nas dependências."""
        achadas, montagens = set(), []

        def somar(rota, prefixo=""):
            if isinstance(rota, Mount):
                montagens.append(rota.path)
            elif hasattr(rota, "original_router"):
                contexto = rota.include_context
                self.assertIn(exigir_sessao, [d.dependency for d in contexto.dependencies], "router sem a sessão")
                for filha in rota.original_router.routes:
                    somar(filha, prefixo + contexto.prefix)
            else:  # APIRoute ou Route simples (docs, openapi)
                achadas.update(
                    (metodo, prefixo + rota.path) for metodo in (rota.methods or ()) if metodo not in ("HEAD", "OPTIONS")
                )

        for rota in self.m.app.routes:
            somar(rota)
        return achadas, montagens

    @staticmethod
    def url(caminho: str) -> str:
        return caminho.replace("{id}", "1").replace("{arquivo}", "1.md")

    def test_lista_de_excecoes_nao_tem_rota_inexistente_nem_esquecida(self):
        achadas, montagens = self.rotas()

        self.assertEqual(montagens, ["/static"])
        self.assertTrue(set(self.EXCECOES) <= achadas, set(self.EXCECOES) - achadas)
        # O código tem as mesmas exceções que o teste (nada a mais, nada a menos).
        self.assertEqual({c for _, c in self.EXCECOES if not c.startswith("/pedidos-ia")}, set(ISENTAS))
        self.assertEqual(PREFIXOS_ISENTOS, ("/static", "/pedidos-ia"))
        self.assertGreater(len(achadas), 40)  # o passeio não é vazio

    def test_toda_rota_fora_das_excecoes_sem_sessao_redireciona_ou_da_401(self):
        achadas, _ = self.rotas()
        cliente = self.m.anonimo()
        conferidas = 0
        for metodo, caminho in sorted(achadas - set(self.EXCECOES)):
            with self.subTest(metodo=metodo, caminho=caminho):
                for cabecalhos in ({}, HX, {"Content-Type": "application/json"}):
                    resposta = cliente.request(metodo, self.url(caminho), headers=cabecalhos)
                    if caminho == "/tela" or caminho.startswith("/tela/"):
                        if cabecalhos.get("HX-Request"):
                            self.assertEqual(resposta.status_code, 401, resposta.text)
                            self.assertEqual(resposta.headers["hx-redirect"], "/login")
                        else:
                            self.assertEqual(resposta.status_code, 303, (metodo, caminho, resposta.text[:200]))
                            self.assertEqual(resposta.headers["location"], "/login")
                    else:
                        self.assertEqual(resposta.status_code, 401, (metodo, caminho, resposta.text[:200]))
                        self.assertEqual(resposta.json(), {"detail": "Faça login"})
                    self.assertNotIn("set-cookie", resposta.headers)
                conferidas += 1
        self.assertEqual(conferidas, len(achadas) - len(self.EXCECOES))

    def test_rotas_do_worker_exigem_o_bearer_e_nao_a_sessao(self):
        rotas_do_worker = 0
        for rota in self.m.app.routes:
            for filha in getattr(rota, "original_router", rota).routes if hasattr(rota, "original_router") else [rota]:
                if isinstance(filha, APIRoute) and filha.path.startswith("/pedidos-ia"):
                    self.assertIn(exigir_worker_ia, {d.call for d in filha.dependant.dependencies}, filha.path)
                    rotas_do_worker += 1
        self.assertEqual(rotas_do_worker, 2)
        with mock.patch.dict(os.environ, {"VAGAS_IA_TOKEN": TOKEN_WORKER}):
            cliente = self.m.logado(1)  # a sessão não substitui o Bearer
            self.assertEqual(cliente.post("/pedidos-ia/proximo", json={}).status_code, 401)
            self.assertIn(self.m.anonimo().post("/pedidos-ia/proximo", json={}, headers=AUTH).status_code, (200, 204))

    def test_rota_nova_ja_nasce_protegida(self):
        @self.m.app.get("/tela/rota-nova")
        def nova():
            return {"segredo": 1}

        @self.m.app.get("/api-nova")
        def nova_json():
            return {"segredo": 1}

        cliente = self.m.anonimo()
        self.assertEqual(cliente.get("/tela/rota-nova").status_code, 303)
        self.assertEqual(cliente.get("/api-nova").status_code, 401)
        self.assertEqual(self.m.logado(1).get("/api-nova").json(), {"segredo": 1})

    def test_saude_login_e_estaticos_abrem_sem_sessao(self):
        cliente = self.m.anonimo()

        self.assertEqual(cliente.get("/saude").json(), {"ok": True})
        self.assertEqual(cliente.get("/login").status_code, 200)
        self.assertEqual(cliente.get("/static/tela.css").status_code, 200)

    def test_raiz_continua_redirecionando_para_a_tela(self):
        resposta = self.m.anonimo().get("/")

        self.assertEqual((resposta.status_code, resposta.headers["location"]), (307, "/tela"))

    def test_tela_sem_sessao_nao_vaza_nada_da_tela(self):
        resposta = self.m.anonimo().get("/tela?q=Analista", follow_redirects=True)

        self.assertNotIn("Analista Um", resposta.text)
        self.assertNotIn("Engenheira Dois", resposta.text)
        self.assertIn('name="senha"', resposta.text)


class DuasPessoasTest(unittest.TestCase):
    """Duas pessoas logadas ao mesmo tempo veem só os próprios dados."""

    def setUp(self):
        self.m = Mundo()
        self.um, self.dois = self.m.logado(1), self.m.logado(2)

    def test_perfil_json(self):
        self.assertEqual(self.um.get("/perfil").json()["termos"], ["python"])
        self.assertEqual(self.dois.get("/perfil").json()["termos"], ["golang"])

        corpo = {"termos": ["rust"]}
        self.assertEqual(self.dois.put("/perfil", json=corpo).status_code, 200)

        self.assertEqual(self.um.get("/perfil").json()["termos"], ["python"])
        self.assertEqual(self.m.perfis[2].obter().termos, ["rust"])

    def test_perfil_da_tela(self):
        self.dois.put("/tela/perfil", data={"termos": "kotlin"}, headers=HX)

        self.assertIn("python", self.um.get("/tela/perfil").text)
        self.assertNotIn("kotlin", self.um.get("/tela/perfil").text)
        self.assertIn("kotlin", self.dois.get("/tela/perfil").text)

    def test_vagas_na_json_e_na_tela(self):
        urls = lambda c: {v["url"] for v in c.get("/vagas/encontradas?novas=false").json()}  # noqa: E731
        self.assertEqual(urls(self.um), {URL_UM, URL_COMUM})
        self.assertEqual(urls(self.dois), {URL_DOIS, URL_COMUM})
        pagina_um, pagina_dois = self.um.get("/tela").text, self.dois.get("/tela").text
        self.assertIn("Analista Um", pagina_um)
        self.assertNotIn("Engenheira Dois", pagina_um)
        self.assertIn("Engenheira Dois", pagina_dois)
        self.assertNotIn("Analista Um", pagina_dois)

    def test_mesma_url_e_uma_vaga_de_cada_pessoa(self):
        self.assertIn("Vaga Comum Um", self.um.get("/tela/vagas/detalhe", params={"url": URL_COMUM}, headers=HX).text)
        self.assertIn("Vaga Comum Dois", self.dois.get("/tela/vagas/detalhe", params={"url": URL_COMUM}, headers=HX).text)

    def test_vaga_so_da_outra_pessoa_nao_abre(self):
        resposta = self.um.get("/tela/vagas/detalhe", params={"url": URL_DOIS}, headers=HX)

        self.assertEqual(resposta.status_code, 404)
        self.assertNotIn("Engenheira Dois", resposta.text)

    def test_marcar_vista_so_mexe_nas_proprias(self):
        self.assertEqual(self.um.post("/vagas/vistas", json={"urls": [URL_COMUM, URL_DOIS]}).json(), {"marcadas": 1})

        novas_dois = {v["url"] for v in self.dois.get("/vagas/encontradas").json()}
        self.assertEqual(novas_dois, {URL_DOIS, URL_COMUM})

    def test_salvar_vaga_da_outra_pessoa_nao_acha(self):
        self.assertEqual(self.um.post("/tela/vagas/salva", data={"url": URL_DOIS, "salvar": "1"}, headers=HX).status_code, 404)
        self.assertEqual(self.dois.post("/tela/vagas/salva", data={"url": URL_DOIS, "salvar": "1"}, headers=HX).status_code, 200)

    def test_candidaturas_json_e_tela(self):
        self.assertEqual(self.um.post("/candidaturas", json={"vaga_url": URL_UM}).status_code, 201)

        self.assertEqual(len(self.um.get("/candidaturas").json()), 1)
        self.assertEqual(self.dois.get("/candidaturas").json(), [])
        self.assertIn("Analista Um", self.um.get("/tela").text.split('id="aplicadas"')[-1])
        self.assertNotIn("Analista Um", self.dois.get("/tela").text)

    def test_candidatura_numa_vaga_que_so_a_outra_tem_e_recusada(self):
        self.assertEqual(self.um.post("/candidaturas", json={"vaga_url": URL_DOIS}).status_code, 422)
        self.assertEqual(self.um.post("/tela/candidaturas", data={"url": URL_DOIS}, headers=HX).status_code, 422)

    def test_candidatura_da_outra_pessoa_nao_se_altera_nem_se_apaga(self):
        id_ = self.um.post("/candidaturas", json={"vaga_url": URL_UM}).json()["id"]

        self.assertEqual(self.dois.patch(f"/candidaturas/{id_}", json={"status": "entrevista"}).status_code, 404)
        self.assertEqual(self.dois.delete(f"/candidaturas/{id_}", headers={"Content-Type": "application/json"}).status_code, 404)
        self.assertEqual(self.dois.delete(f"/tela/candidaturas/{id_}", headers=HX).status_code, 404)
        self.assertEqual(len(self.um.get("/candidaturas").json()), 1)

    def test_providers_json_e_tela(self):
        corpo = {"providers": [{"provider": "ashby", "config": {"empresas": ["empresa-da-dois"]}}]}
        self.assertEqual(self.dois.put("/perfil/providers", json=corpo).status_code, 200)

        self.assertEqual(self.um.get("/perfil/providers").json(), {"providers": []})
        self.assertEqual(self.dois.get("/perfil/providers").json()["providers"][0]["provider"], "ashby")
        self.assertNotIn("empresa-da-dois", self.um.get("/tela/perfil").text)
        self.assertIn("empresa-da-dois", self.dois.get("/tela/perfil").text)

    def test_curriculo_base_e_adaptados(self):
        self.assertEqual(self.um.get("/curriculos/base").json()["titulo"], "Base Um")
        self.assertEqual(self.dois.get("/curriculos/base").json()["titulo"], "Base Dois")
        self.dois.put("/curriculos/base", json={"titulo": "Nova Dois", "conteudo_md": "# n"})
        self.assertEqual(self.um.get("/curriculos/base").json()["titulo"], "Base Um")

    def test_adaptado_da_outra_pessoa_nao_baixa_nem_lista(self):
        adaptado = self.m.curriculos[2].salvar(
            Curriculo(tipo="adaptado", titulo="Adaptado Dois", conteudo_md="# so da dois", base_id=self.m.base[2].id, vaga_url=URL_COMUM)
        )

        self.assertEqual(self.um.get(f"/tela/curriculos/{adaptado.id}.md").status_code, 404)
        self.assertEqual(self.dois.get(f"/tela/curriculos/{adaptado.id}.md").status_code, 200)
        self.assertEqual(self.um.get("/curriculos/adaptados").json(), [])
        self.assertEqual(len(self.dois.get("/curriculos/adaptados").json()), 1)
        self.assertNotIn("Adaptado Dois", self.um.get("/tela/curriculos/da-vaga", params={"url": URL_COMUM}).text)

    def test_pedido_de_ia_da_outra_pessoa_nao_aparece(self):
        pedido, _ = self.m.pedidos[2].criar(URL_COMUM, "compatibilidade")

        self.assertEqual(self.um.get(f"/tela/vagas/ia/{pedido.id}", headers=HX).status_code, 404)
        self.assertEqual(self.dois.get(f"/tela/vagas/ia/{pedido.id}", headers=HX).status_code, 200)

    def test_pedir_ia_usa_a_base_e_a_vaga_da_pessoa_da_sessao(self):
        resposta = self.dois.post("/tela/vagas/ia", data={"vaga_url": URL_DOIS, "tipo": "compatibilidade"}, headers=HX)

        self.assertEqual(resposta.status_code, 202, resposta.text)
        pedido = self.m.pedidos[2].ultimo(URL_DOIS, "compatibilidade")
        self.assertEqual(pedido.curriculo_base_id, self.m.base[2].id)
        self.assertIsNone(self.m.pedidos[1].ultimo(URL_DOIS, "compatibilidade"))
        # A pessoa 1 não pede IA de vaga que só a 2 tem.
        self.assertEqual(self.um.post("/tela/vagas/ia", data={"vaga_url": URL_DOIS, "tipo": "compatibilidade"}, headers=HX).status_code, 422)

    def test_rotulo_de_ia_e_da_pessoa(self):
        self.assertEqual(self.dois.put("/vagas/rotulo-ia", json={"vaga_url": URL_COMUM, "faixa": "alta"}).status_code, 200)

        self.assertEqual(self.m.pedidos[2].obter_rotulo(URL_COMUM).value, "alta")
        self.assertIsNone(self.m.pedidos[1].obter_rotulo(URL_COMUM))
        self.assertEqual(self.um.put("/vagas/rotulo-ia", json={"vaga_url": URL_DOIS, "faixa": "alta"}).status_code, 422)

    def test_lote_de_ia_e_da_pessoa(self):
        self.dois.post("/tela/ia/lote", headers=HX)

        self.assertEqual(self.m.pedidos[2].urls_ativas("compatibilidade"), {URL_DOIS, URL_COMUM})
        self.assertEqual(self.m.pedidos[1].urls_ativas("compatibilidade"), set())

    def test_pessoa_vem_so_da_sessao_nunca_de_parametro(self):
        for extra in ({"pessoa_id": "2"}, {"pessoa": "2"}, {"p": "2"}):
            self.assertEqual(self.um.get("/perfil", params=extra).json()["termos"], ["python"])
        self.assertEqual(
            self.um.get("/perfil", headers={"X-Pessoa-Id": "2", "X-Pessoa": "2"}).json()["termos"], ["python"]
        )

    def test_pessoa_sem_senha_nunca_entra_e_nao_tem_sessao(self):
        self.assertEqual(self.m.entrar("Pessoa Sem Senha", "qualquer-coisa-aqui").status_code, 401)
        self.assertEqual(self.m.entrar("Pessoa Sem Senha", "").status_code, 401)


class ColetaDaSessaoTest(unittest.TestCase):
    def test_coleta_roda_para_todas_mas_responde_so_a_da_sessao(self):
        provider = ClientPorTermo(
            "A", {"python": [vaga("u1", "Dev Python")], "golang": [vaga("g1", "Dev Golang"), vaga("g2", "Dev Golang Senior")]}
        )
        m = Mundo(providers=[provider])
        um, dois = m.logado(1), m.logado(2)

        resposta = um.post("/coletas", json={})

        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertEqual(resposta.json(), {"termos": [{"termo": "python", "encontradas": 1, "novas": 1, "erros": {}}], "novas": 1})
        self.assertEqual({v.url for v in m.vagas[2].listar(somente_novas=False, limite=100)} >= {"g1", "g2"}, True)
        da_dois = dois.post("/coletas", json={}).json()
        self.assertEqual([t["termo"] for t in da_dois["termos"]], ["golang"])

    def test_botao_da_tela_devolve_a_lista_da_pessoa_da_sessao(self):
        provider = ClientPorTermo("A", {"python": [vaga("u1", "Dev Python")], "golang": [vaga("g1", "Dev Golang")]})
        m = Mundo(providers=[provider])

        resposta = m.logado(2).post("/tela/coletas", headers=HX)

        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertIn("Dev Golang", resposta.text)
        self.assertNotIn("Dev Python", resposta.text)

    def test_coleta_sem_sessao_nao_roda(self):
        provider = ClientPorTermo("A", {"python": [vaga("u1", "Dev Python")]})
        m = Mundo(providers=[provider])

        self.assertEqual(m.anonimo().post("/coletas", json={}).status_code, 401)
        self.assertEqual(m.anonimo().post("/tela/coletas", headers=HX).status_code, 401)
        self.assertEqual(provider.termos_buscados, [])


class WorkerNoCurriculoAdaptadoTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"VAGAS_IA_TOKEN": TOKEN_WORKER})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("VAGAS_IA_TOKEN_FILE", None)
        self.m = Mundo()
        self.pedido, _ = self.m.pedidos[2].criar(URL_COMUM, "curriculo")
        self.worker = self.m.anonimo()
        self.assertEqual(self.worker.post("/pedidos-ia/proximo", json={}, headers=AUTH).json()["id"], self.pedido.id)

    def corpo(self, **extra):
        return {"titulo": "Adaptado", "conteudo_md": "# a", "vaga_url": URL_COMUM, **extra}

    def test_worker_com_bearer_e_pedido_id_grava_na_pessoa_do_pedido_sem_sessao(self):
        resposta = self.worker.post("/curriculos/adaptados", json=self.corpo(pedido_id=self.pedido.id), headers=AUTH)

        self.assertEqual(resposta.status_code, 201, resposta.text)
        self.assertEqual(resposta.json()["base_id"], self.m.base[2].id)
        self.assertEqual(len(self.m.curriculos[2].listar_adaptados()), 1)
        self.assertEqual(self.m.curriculos[1].listar_adaptados(), [])

    def test_worker_sem_pedido_id_e_422_porque_nao_tem_pessoa(self):
        resposta = self.worker.post("/curriculos/adaptados", json=self.corpo(), headers=AUTH)

        self.assertEqual(resposta.status_code, 422)
        self.assertEqual(self.m.curriculos[1].listar_adaptados() + self.m.curriculos[2].listar_adaptados(), [])

    def test_bearer_errado_e_401_mesmo_com_sessao_valida(self):
        um = self.m.logado(1)

        resposta = um.post(
            "/curriculos/adaptados", json=self.corpo(pedido_id=self.pedido.id), headers={"Authorization": "Bearer errado"}
        )

        self.assertEqual(resposta.status_code, 401)
        self.assertEqual(self.m.curriculos[2].listar_adaptados(), [])

    def test_sem_bearer_e_sem_sessao_e_401(self):
        self.assertEqual(self.worker.post("/curriculos/adaptados", json=self.corpo(pedido_id=self.pedido.id)).status_code, 401)

    def test_sem_token_configurado_o_bearer_e_503_e_a_sessao_segue_valendo(self):
        os.environ.pop("VAGAS_IA_TOKEN")
        um = self.m.logado(1)

        self.assertEqual(self.worker.post("/curriculos/adaptados", json=self.corpo(), headers=AUTH).status_code, 503)
        self.assertEqual(um.post("/curriculos/adaptados", json=self.corpo()).status_code, 201)

    def test_pessoa_logada_sem_pedido_id_grava_nos_proprios_curriculos(self):
        resposta = self.m.logado(1).post("/curriculos/adaptados", json=self.corpo())

        self.assertEqual(resposta.status_code, 201, resposta.text)
        self.assertEqual(resposta.json()["base_id"], self.m.base[1].id)
        self.assertEqual(self.m.curriculos[2].listar_adaptados(), [])

    def test_pessoa_logada_nao_usa_pedido_de_outra_pessoa(self):
        resposta = self.m.logado(1).post("/curriculos/adaptados", json=self.corpo(pedido_id=self.pedido.id))

        self.assertEqual(resposta.status_code, 422)
        self.assertEqual(self.m.curriculos[1].listar_adaptados() + self.m.curriculos[2].listar_adaptados(), [])

    def test_pessoa_logada_com_o_proprio_pedido_grava_nela(self):
        resposta = self.m.logado(2).post("/curriculos/adaptados", json=self.corpo(pedido_id=self.pedido.id))

        self.assertEqual(resposta.status_code, 201, resposta.text)
        self.assertEqual(len(self.m.curriculos[2].listar_adaptados()), 1)

    def test_csrf_do_json_continua_valendo_no_adaptados(self):
        resposta = self.m.logado(1).post("/curriculos/adaptados", content=b"{}", headers={"Content-Type": "text/plain"})

        self.assertEqual(resposta.status_code, 415)


class SemFabricaTest(unittest.TestCase):
    def test_sessao_de_outra_pessoa_sem_repositorios_dela_falha_alto(self):
        """Sem fábrica por pessoa (testes de uma pessoa só) a pessoa 2 NÃO enxerga os dados da pessoa 1."""
        pessoas = PessoaRepositoryMemoria([1, 2])
        app = criar_app(PerfilBuscaRepositoryMemoria(1), CurriculoRepositoryMemoria(1), BuscaService([]), pessoas=pessoas)
        um = ClienteLogado(app, raise_server_exceptions=False)
        dois = ClienteLogado(app, pessoa_id=2, raise_server_exceptions=False)
        um.put("/perfil", json={"termos": ["segredo-da-um"]})

        resposta = dois.get("/perfil")

        self.assertEqual(resposta.status_code, 500)
        self.assertNotIn("segredo-da-um", resposta.text)


if __name__ == "__main__":
    unittest.main()
