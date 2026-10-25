"""Providers por pessoa: validação antes de gravar, repositório (memória e Postgres), fábrica, cache da coleta,
rotas JSON e tela.

FIXTURE ESCRITA À MÃO: as respostas do Ashby abaixo são inventadas (não são resposta gravada da API real). O
slug `posthog` NÃO foi conferido contra a API do Ashby neste ambiente (host bloqueado). O HTTP é falso no nível
de `urllib.request.urlopen`, então o `AshbyClient` real monta a URL real.
"""

import io
import json
import os
import re
import unittest
from contextlib import contextmanager
from datetime import date
from html import unescape
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.client.apify_linkedin import ApifyLinkedInClient
from src.client.apify_linkedin_posts import ApifyLinkedInPostsClient
from src.client.ashby import AshbyClient
from src.client.remotive import RemotiveClient
from src.controller.api import PESSOA_PADRAO, criar_app
from src.model.erros import DadoInvalido
from src.model.perfil import Perfil
from src.model.provider_pessoa import ProviderPessoa, limite_apify_max, limite_posts_max, validar_providers
from src.repository.base import CurriculoRepositoryMemoria, PerfilBuscaRepositoryMemoria
from src.repository.pessoa_provider import PessoaProviderRepositoryMemoria
from src.service.busca_service import BuscaService
from src.service.providers_pessoa import PROVIDERS_DO_AMBIENTE, ProvidersDaPessoa
from tests.test_busca_service import ClientFixo, ClientQuebrado
from tests.test_coleta import ClientPorTermo, _schema_descartavel, vaga
from tests.test_coleta_multipessoa import Banco

HOJE = date(2026, 10, 20)
JSON = {"Content-Type": "application/json"}
HX = {"HX-Request": "true", "Origin": "http://testserver"}
URL_POSTHOG = "https://api.ashbyhq.com/posting-api/job-board/posthog"
URL_OUTRA = "https://api.ashbyhq.com/posting-api/job-board/outraempresa"

ASHBY_POSTHOG = {
    "jobs": [
        {
            "title": "Senior Python Engineer",
            "jobUrl": "https://jobs.ashbyhq.com/posthog/aaaa-1111",
            "location": "Remote",
            "isRemote": True,
            "publishedAt": "2026-10-15T10:00:00.000+00:00",
            "isListed": True,
        },
        {
            "title": "Product Designer",
            "jobUrl": "https://jobs.ashbyhq.com/posthog/bbbb-2222",
            "location": "Remote",
            "isRemote": True,
            "publishedAt": "2026-10-14T10:00:00.000+00:00",
            "isListed": True,
        },
    ]
}
ASHBY_OUTRA = {
    "jobs": [
        {
            "title": "Python Developer",
            "jobUrl": "https://jobs.ashbyhq.com/outraempresa/cccc-3333",
            "location": "Remote",
            "isRemote": True,
            "publishedAt": "2026-10-13T10:00:00.000+00:00",
            "isListed": True,
        }
    ]
}


class HttpFalso:
    """Substitui `urllib.request.urlopen`: devolve o JSON da URL pedida e guarda as URLs chamadas."""

    def __init__(self, respostas: dict[str, object]):
        self.respostas = respostas
        self.urls: list[str] = []

    def __call__(self, req, timeout=None):
        self.urls.append(req.full_url)
        return io.BytesIO(json.dumps(self.respostas[req.full_url]).encode())


def http_falso(respostas):
    http = HttpFalso(respostas)
    return http, patch("urllib.request.urlopen", http)


@contextmanager
def ambiente(**variaveis):
    """O ambiente sem nenhuma APIFY_* e com só estas variáveis."""
    novo = {k: v for k, v in os.environ.items() if not k.startswith("APIFY_")}
    novo.update(variaveis)
    with patch.dict(os.environ, novo, clear=True):
        yield


def ashby(*empresas):
    return ProviderPessoa("ashby", {"empresas": list(empresas)})


class ValidacaoTest(unittest.TestCase):
    def recusa(self, itens, trecho, **tetos):
        with self.assertRaisesRegex(DadoInvalido, trecho):
            validar_providers(itens, **tetos)

    def test_config_valida_sai_normalizada(self):
        [item] = validar_providers([ashby(" posthog ", "PostHog", "outra")])
        self.assertEqual(item.config, {"empresas": ["posthog", "outra"]})  # sem espaço, sem repetido

    def test_ordem_fixa_na_saida(self):
        saida = validar_providers([ashby("a"), ProviderPessoa("remotar"), ProviderPessoa("greenhouse", {"empresas": ["b"]})])
        self.assertEqual([i.provider for i in saida], ["remotar", "greenhouse", "ashby"])

    def test_provider_desconhecido(self):
        self.recusa([ProviderPessoa("gupy", {})], "provider desconhecido")

    def test_config_que_nao_e_objeto(self):
        self.recusa([ProviderPessoa("ashby", ["posthog"])], "config tem que ser um objeto")

    def test_provider_repetido(self):
        self.recusa([ashby("a"), ashby("b")], "provider repetido: ashby")

    def test_chave_desconhecida_inclusive_segredo(self):
        for provider, config in (
            ("linkedin", {"token": "x"}),
            ("linkedin", {"APIFY_TOKEN": "x"}),
            ("ashby", {"empresas": ["a"], "extra": 1}),
            ("remotar", {"limite": 1}),
            ("himalayas", {"empresas": ["a"]}),
        ):
            with self.subTest(provider=provider, config=config):
                self.recusa([ProviderPessoa(provider, config)], "chave desconhecida")

    def test_empresas(self):
        for config, trecho in (
            ({"empresas": []}, "pelo menos uma"),
            ({"empresas": [""]}, "item vazio"),
            ({"empresas": ["   "]}, "item vazio"),
            ({"empresas": "posthog"}, "lista de textos"),
            ({"empresas": [1]}, "lista de textos"),
            ({}, "lista de textos"),
            ({"empresas": ["../x"]}, "empresa inválida"),
            ({"empresas": ["a/b"]}, "empresa inválida"),
            ({"empresas": ["a b"]}, "empresa inválida"),
            ({"empresas": ["a?x=1"]}, "empresa inválida"),
            ({"empresas": ["a" * 101]}, "empresa inválida"),
            ({"empresas": [f"e{i}" for i in range(201)]}, "até 200"),
        ):
            with self.subTest(config=str(config)[:40]):
                self.recusa([ProviderPessoa("lever", config)], trecho)

    def test_ate_200_empresas_passa(self):
        [item] = validar_providers([ProviderPessoa("greenhouse", {"empresas": [f"e{i}" for i in range(200)]})])
        self.assertEqual(len(item.config["empresas"]), 200)

    def test_locais_do_linkedin(self):
        for config, trecho in (
            ({"locais": []}, "pelo menos um"),
            ({"locais": [""]}, "item vazio"),
            ({"locais": ["—"]}, "item vazio"),
            ({"locais": "Brazil"}, "lista de textos"),
            ({"locais": [f"Local {i}" for i in range(6)]}, "até 5"),
            ({"locais": ["x" * 101]}, "até 100"),
        ):
            with self.subTest(config=str(config)[:40]):
                self.recusa([ProviderPessoa("linkedin", config)], trecho)

    def test_locais_sem_repetidos_por_comparacao_normalizada(self):
        [item] = validar_providers([ProviderPessoa("linkedin", {"locais": ["Brazil", " brazil ", "Latin America"]})])
        self.assertEqual(item.config["locais"], ["Brazil", "Latin America"])

    def test_limite_invalido(self):
        for provider, limite in (
            ("linkedin", True),
            ("linkedin", "10"),
            ("linkedin", 1.5),
            ("linkedin", 0),
            ("linkedin", -1),
            ("linkedin_posts", 0),
            ("himalayas", 21),
            ("remotive", 101),
            ("remotive", None),
        ):
            with self.subTest(provider=provider, limite=limite):
                self.recusa([ProviderPessoa(provider, {"limite": limite})], "limite tem que ser um inteiro")

    def test_teto_do_apify_padrao_e_o_limite_padrao(self):
        with ambiente():
            self.assertEqual(limite_apify_max(), 50)
            validar_providers([ProviderPessoa("linkedin", {"limite": 50})])
            self.recusa([ProviderPessoa("linkedin", {"limite": 51})], "entre 1 e 50")

    def test_teto_do_apify_vem_do_ambiente(self):
        with ambiente(APIFY_LIMITE_MAX="10"):
            validar_providers([ProviderPessoa("linkedin", {"limite": 10})])
            self.recusa([ProviderPessoa("linkedin", {"limite": 11})], "entre 1 e 10")

    def test_teto_invalido_no_ambiente_cai_no_padrao(self):
        for valor in ("abc", "", "0", "-5", "1.5"):
            with self.subTest(valor=valor), ambiente(APIFY_LIMITE_MAX=valor, APIFY_POSTS_LIMITE_MAX=valor):
                self.assertEqual(limite_apify_max(), 50)
                self.assertEqual(limite_posts_max(), 5)

    def test_teto_dos_posts(self):
        with ambiente():
            validar_providers([ProviderPessoa("linkedin_posts", {"limite": 5})])
            self.recusa([ProviderPessoa("linkedin_posts", {"limite": 6})], "entre 1 e 5")
        with ambiente(APIFY_POSTS_LIMITE_MAX="2"):
            self.recusa([ProviderPessoa("linkedin_posts", {"limite": 3})], "entre 1 e 2")

    def test_o_custo_total_do_linkedin_tem_teto_porque_locais_tem_teto(self):
        # limite x locais é o que o Apify cobra: sem teto de locais, o teto de limite seria contornável.
        with ambiente(APIFY_LIMITE_MAX="10"):
            self.recusa([ProviderPessoa("linkedin", {"limite": 10, "locais": [f"L{i}" for i in range(6)]})], "até 5")


class RepositorioMemoriaTest(unittest.TestCase):
    def setUp(self):
        self.um = PessoaProviderRepositoryMemoria(1)
        self.dois = PessoaProviderRepositoryMemoria(2, self.um)

    def test_pessoa_sem_config_lista_vazio(self):
        self.assertEqual(self.um.listar(), [])

    def test_salvar_substitui_tudo(self):
        self.um.salvar([ProviderPessoa("remotar"), ashby("posthog")])
        self.um.salvar([ashby("outra")])
        self.assertEqual(self.um.listar(), [ashby("outra")])

    def test_lista_vazia_volta_ao_ambiente(self):
        self.um.salvar([ashby("posthog")])
        self.um.salvar([])
        self.assertEqual(self.um.listar(), [])

    def test_invalido_nao_grava_nada_nem_parte_da_lista(self):
        self.um.salvar([ashby("posthog")])
        with self.assertRaises(DadoInvalido):
            self.um.salvar([ProviderPessoa("remotar"), ProviderPessoa("linkedin", {"limite": 999})])
        self.assertEqual(self.um.listar(), [ashby("posthog")])

    def test_pessoas_separadas(self):
        self.dois.salvar([ashby("posthog")])
        self.assertEqual(self.um.listar(), [])
        self.um.salvar([ProviderPessoa("trampos")])
        self.assertEqual(self.dois.listar(), [ashby("posthog")])

    def test_listar_devolve_copia(self):
        self.um.salvar([ashby("posthog")])
        self.um.listar()[0].config["empresas"].append("x")
        self.assertEqual(self.um.listar(), [ashby("posthog")])


@unittest.skipUnless(os.environ.get("PGHOST"), "PGHOST não definido: providers por pessoa com Postgres pulados")
class RepositorioPostgresTest(unittest.TestCase):
    def setUp(self):
        from src.repository.pessoa_provider_postgres import PessoaProviderRepositoryPostgres

        self.conn, _ = _schema_descartavel(self, "teste_prov")
        self.conn.execute("INSERT INTO pessoa (id, nome_exibicao) VALUES (2, 'Pessoa 2') ON CONFLICT DO NOTHING")
        self.um = PessoaProviderRepositoryPostgres(self.conn, 1)
        self.dois = PessoaProviderRepositoryPostgres(self.conn, 2)

    def test_migration_nao_semeia_nada(self):
        self.assertEqual(self.conn.execute("SELECT count(*) FROM pessoa_provider").fetchone()[0], 0)
        self.assertEqual(self.um.listar(), [])

    def test_ida_e_volta_na_ordem_dos_nomes(self):
        self.um.salvar([ashby("posthog"), ProviderPessoa("linkedin", {"locais": ["Brazil"], "limite": 5}), ProviderPessoa("trampos")])
        self.assertEqual(
            self.um.listar(),
            [ProviderPessoa("trampos"), ashby("posthog"), ProviderPessoa("linkedin", {"locais": ["Brazil"], "limite": 5})],
        )

    def test_salvar_substitui_e_pessoas_ficam_separadas(self):
        self.um.salvar([ProviderPessoa("remotar"), ashby("a")])
        self.dois.salvar([ashby("posthog")])
        self.um.salvar([ashby("b")])
        self.assertEqual(self.um.listar(), [ashby("b")])
        self.assertEqual(self.dois.listar(), [ashby("posthog")])
        self.um.salvar([])
        self.assertEqual(self.um.listar(), [])
        self.assertEqual(self.dois.listar(), [ashby("posthog")])

    def test_invalido_nao_grava(self):
        self.um.salvar([ashby("a")])
        with self.assertRaises(DadoInvalido):
            self.um.salvar([ashby("b"), ProviderPessoa("linkedin", {"limite": 10_000})])
        self.assertEqual(self.um.listar(), [ashby("a")])

    def test_falha_no_meio_desfaz_a_troca_inteira(self):
        self.um.salvar([ashby("a")])
        self.conn.execute(
            "CREATE FUNCTION quebra_lever() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
            "IF NEW.provider = 'lever' THEN RAISE EXCEPTION 'falha simulada'; END IF; RETURN NEW; END $$"
        )
        self.conn.execute(
            "CREATE TRIGGER quebra_lever BEFORE INSERT ON pessoa_provider FOR EACH ROW EXECUTE FUNCTION quebra_lever()"
        )
        with self.assertRaises(Exception):
            self.um.salvar([ProviderPessoa("remotar"), ProviderPessoa("lever", {"empresas": ["x"]})])
        self.assertEqual(self.um.listar(), [ashby("a")])  # o DELETE e o INSERT do remotar foram desfeitos

    def test_o_banco_recusa_provider_desconhecido_e_config_que_nao_e_objeto(self):
        from psycopg import errors

        for provider, config in (("gupy", "{}"), ("ashby", "[]"), ("ashby", "null"), ("ashby", '"x"')):
            with self.subTest(provider=provider, config=config), self.assertRaises(errors.CheckViolation):
                self.conn.execute(
                    "INSERT INTO pessoa_provider (pessoa_id, provider, config) VALUES (1, %s, %s::jsonb)", (provider, config)
                )

    def test_pessoa_inexistente_nao_grava(self):
        from psycopg import errors
        from src.repository.pessoa_provider_postgres import PessoaProviderRepositoryPostgres

        with self.assertRaises(errors.ForeignKeyViolation):
            PessoaProviderRepositoryPostgres(self.conn, 99).salvar([ashby("a")])


class FabricaTest(unittest.TestCase):
    def setUp(self):
        self.repo = PessoaProviderRepositoryMemoria(1)
        self.ambiente = BuscaService([ClientFixo("Ambiente", [vaga("u1")])])
        self.buscas = ProvidersDaPessoa(self.ambiente, lambda pessoa_id: PessoaProviderRepositoryMemoria(pessoa_id, self.repo))

    def clients(self, pessoa_id=1):
        return self.buscas.busca(pessoa_id)[1].clients

    def test_sem_config_vale_o_ambiente(self):
        chave, busca = self.buscas.busca(1)
        self.assertEqual(chave, PROVIDERS_DO_AMBIENTE)
        self.assertIs(busca, self.ambiente)

    def test_com_config_so_valem_os_dela(self):
        PessoaProviderRepositoryMemoria(2, self.repo).salvar([ashby("posthog")])
        [client] = self.clients(2)
        self.assertIsInstance(client, AshbyClient)
        self.assertEqual(client.empresas, ["posthog"])
        self.assertIs(self.buscas.busca(1)[1], self.ambiente)  # a pessoa 1 continua no ambiente

    def test_chave_e_hashable_e_igual_para_configs_iguais(self):
        outro = PessoaProviderRepositoryMemoria(2, self.repo)
        self.repo.salvar([ProviderPessoa("remotar"), ashby("a", "b")])
        outro.salvar([ashby("b", "a"), ProviderPessoa("remotar")])  # outra ordem
        chave_um, chave_dois = self.buscas.busca(1)[0], self.buscas.busca(2)[0]
        hash(chave_um)
        self.assertEqual(chave_um, chave_dois)
        self.assertNotEqual(chave_um, PROVIDERS_DO_AMBIENTE)

    def test_configs_diferentes_tem_chaves_diferentes(self):
        outro = PessoaProviderRepositoryMemoria(2, self.repo)
        self.repo.salvar([ashby("posthog")])
        outro.salvar([ashby("posthog", "outra")])
        self.assertNotEqual(self.buscas.busca(1)[0], self.buscas.busca(2)[0])

    def test_defaults_explicitos_e_ausentes_dao_a_mesma_chave(self):
        outro = PessoaProviderRepositoryMemoria(2, self.repo)
        with ambiente(APIFY_TOKEN="t"):
            self.repo.salvar([ProviderPessoa("linkedin", {})])
            outro.salvar([ProviderPessoa("linkedin", {"locais": ["Latin America", "Brasil"], "limite": 50})])
            self.assertEqual(self.buscas.busca(1)[0], self.buscas.busca(2)[0])  # o Apify não é pago duas vezes

    def test_linkedin_usa_a_config_da_pessoa_e_o_token_do_ambiente(self):
        self.repo.salvar([ProviderPessoa("linkedin", {"locais": ["United States"], "limite": 7})])
        with ambiente(APIFY_TOKEN="token-do-ambiente"):
            [client] = self.clients()
        self.assertIsInstance(client, ApifyLinkedInClient)
        self.assertEqual((client.locais, client.limite, client._token), (("United States",), 7, "token-do-ambiente"))
        # O segredo nunca foi para a tabela.
        self.assertNotIn("token-do-ambiente", json.dumps([vars(p) for p in self.repo.listar()]))

    def test_linkedin_sem_token_fica_desligado_e_aparece_em_erros(self):
        self.repo.salvar([ProviderPessoa("linkedin", {"limite": 3}), ProviderPessoa("linkedin_posts", {})])
        with ambiente():
            resultado = self.buscas.busca(1)[1].buscar("python")
        self.assertEqual(resultado.vagas, [])
        self.assertEqual(set(resultado.erros), {"LinkedIn (Apify)", "LinkedIn (posts)"})
        self.assertTrue(all("APIFY_TOKEN" in e for e in resultado.erros.values()))

    def test_padroes_do_linkedin_vem_do_ambiente_e_nao_passam_do_teto(self):
        self.repo.salvar([ProviderPessoa("linkedin", {})])
        with ambiente(APIFY_TOKEN="t", APIFY_LOCAIS="Chile", APIFY_LIMITE="30", APIFY_LIMITE_MAX="20"):
            [client] = self.clients()
        self.assertEqual((client.locais, client.limite), (("Chile",), 20))

    def test_posts_sem_limite_usa_1(self):
        self.repo.salvar([ProviderPessoa("linkedin_posts", {})])
        with ambiente(APIFY_TOKEN="t"):
            [client] = self.clients()
        self.assertIsInstance(client, ApifyLinkedInPostsClient)
        self.assertEqual(client.limite, 1)

    def test_config_gravada_acima_do_teto_e_cortada_no_teto(self):
        # Gravada por fora (SQL) ou antes de o teto baixar: a validação ao gravar não a pegaria.
        self.repo._linhas[1] = [ProviderPessoa("linkedin", {"limite": 500, "locais": ["Brazil"]})]
        with ambiente(APIFY_TOKEN="t", APIFY_LIMITE_MAX="12"):
            [client] = self.clients()
        self.assertEqual(client.limite, 12)

    def test_entrada_torta_gravada_por_fora_sai_sem_derrubar_as_outras(self):
        self.repo._linhas[1] = [
            ProviderPessoa("ashby", {"empresas": ["../etc"]}),
            ProviderPessoa("ashby2", {}),
            ProviderPessoa("trampos", {}),
        ]
        [client] = self.clients()
        self.assertEqual(client.nome, "Trampos")

    def test_remotive_mantem_o_mesmo_client_entre_buscas(self):
        # O Remotive guarda a listagem por 6 h e espera 60 s entre chamadas (termos de uso): client novo a cada
        # coleta perderia isso.
        self.repo.salvar([ProviderPessoa("remotive", {"limite": 10})])
        [antes] = self.clients()
        [depois] = self.clients()
        self.assertIsInstance(antes, RemotiveClient)
        self.assertIs(antes, depois)
        self.assertEqual(antes.limite, 10)

    def test_todos_os_nomes_conhecidos_montam(self):
        itens = [
            ProviderPessoa("remotar"),
            ProviderPessoa("trampos"),
            ProviderPessoa("himalayas", {"limite": 5}),
            ProviderPessoa("remotive"),
            ProviderPessoa("greenhouse", {"empresas": ["a"]}),
            ProviderPessoa("lever", {"empresas": ["a"]}),
            ashby("a"),
            ProviderPessoa("linkedin"),
            ProviderPessoa("linkedin_posts"),
        ]
        self.repo.salvar(itens)
        with ambiente(APIFY_TOKEN="t"):
            self.assertEqual(len(self.clients()), 9)


class ColetaPorPessoaTest(unittest.TestCase):
    def setUp(self):
        self.banco = Banco(1, 2)
        self.fontes = PessoaProviderRepositoryMemoria(1)
        self.ambiente = ClientPorTermo("Ambiente", {"python": [vaga("amb-1", "Dev Python")]})

    def servico(self):
        buscas = ProvidersDaPessoa(
            BuscaService([self.ambiente]), lambda pessoa_id: PessoaProviderRepositoryMemoria(pessoa_id, self.fontes)
        )
        return self.banco.servico(self.ambiente, buscas=buscas)

    def perfis(self, *pessoas):
        for p in pessoas:
            self.banco.perfis[p].salvar(Perfil(termos=["python"]))

    def test_sem_config_e_igual_a_hoje(self):
        self.perfis(1, 2)
        self.servico().coletar(HOJE)
        self.assertEqual(self.ambiente.termos_buscados, ["python"])  # as duas no ambiente: uma busca só
        self.assertEqual(self.banco.urls(1), {"amb-1"})
        self.assertEqual(self.banco.urls(2), {"amb-1"})

    def test_pessoa_2_com_ashby_posthog_chama_so_o_ashby_com_posthog_e_a_pessoa_1_segue_no_ambiente(self):
        PessoaProviderRepositoryMemoria(2, self.fontes).salvar([ashby("posthog")])
        self.perfis(1, 2)
        http, patcher = http_falso({URL_POSTHOG: ASHBY_POSTHOG})

        with patcher:
            resultado = self.servico().coletar(HOJE)

        self.assertEqual(http.urls, [URL_POSTHOG])  # nenhum outro provider foi chamado para ela
        self.assertEqual(self.banco.urls(2), {"https://jobs.ashbyhq.com/posthog/aaaa-1111"})  # "Product Designer" não casa
        self.assertEqual(self.banco.urls(1), {"amb-1"})
        self.assertEqual(self.ambiente.termos_buscados, ["python"])  # o ambiente só para a pessoa 1
        self.assertFalse(resultado.falhou)

    def test_mesma_config_compartilha_a_busca_do_termo(self):
        PessoaProviderRepositoryMemoria(1, self.fontes).salvar([ashby("posthog")])
        PessoaProviderRepositoryMemoria(2, self.fontes).salvar([ashby("posthog")])
        self.perfis(1, 2)
        http, patcher = http_falso({URL_POSTHOG: ASHBY_POSTHOG})

        with patcher:
            self.servico().coletar(HOJE)

        self.assertEqual(http.urls, [URL_POSTHOG])  # uma busca para as duas
        self.assertEqual(self.banco.urls(1), self.banco.urls(2))
        self.assertEqual(len(self.banco.urls(1)), 1)
        self.assertEqual(self.ambiente.termos_buscados, [])

    def test_config_diferente_nao_compartilha(self):
        PessoaProviderRepositoryMemoria(1, self.fontes).salvar([ashby("posthog")])
        PessoaProviderRepositoryMemoria(2, self.fontes).salvar([ashby("outraempresa")])
        self.perfis(1, 2)
        http, patcher = http_falso({URL_POSTHOG: ASHBY_POSTHOG, URL_OUTRA: ASHBY_OUTRA})

        with patcher:
            self.servico().coletar(HOJE)

        self.assertEqual(sorted(http.urls), [URL_OUTRA, URL_POSTHOG])  # duas buscas
        self.assertEqual(self.banco.urls(1), {"https://jobs.ashbyhq.com/posthog/aaaa-1111"})
        self.assertEqual(self.banco.urls(2), {"https://jobs.ashbyhq.com/outraempresa/cccc-3333"})

    def test_config_nao_mistura_com_o_ambiente_no_cache(self):
        PessoaProviderRepositoryMemoria(2, self.fontes).salvar([ProviderPessoa("trampos")])
        self.perfis(1, 2)
        trampos = ClientPorTermo("Trampos", {"python": [vaga("tr-1", "Dev Python")]})
        with patch("src.service.providers_pessoa._criar_cliente", return_value=trampos):
            self.servico().coletar(HOJE)
        self.assertEqual(self.banco.urls(1), {"amb-1"})
        self.assertEqual(self.banco.urls(2), {"tr-1"})
        self.assertEqual(trampos.termos_buscados, ["python"])

    def test_provider_da_pessoa_que_falha_so_aparece_nos_erros_dela(self):
        PessoaProviderRepositoryMemoria(2, self.fontes).salvar([ashby("posthog")])
        self.perfis(1, 2)
        servico = self.servico()
        with patch("urllib.request.urlopen", side_effect=TimeoutError("sem resposta")):
            resultado = servico.coletar(HOJE)
        self.assertEqual(resultado.da_pessoa(1).termos[0].erros, {})
        self.assertIn("Ashby", resultado.da_pessoa(2).termos[0].erros)
        # Um provider só, falhou: a coleta dela não buscou nada de verdade (contagem de providers é da pessoa).
        self.assertTrue(resultado.da_pessoa(2).falhou)
        self.assertFalse(resultado.da_pessoa(1).falhou)

    def test_a_contagem_de_providers_e_da_pessoa(self):
        PessoaProviderRepositoryMemoria(2, self.fontes).salvar([ashby("posthog"), ProviderPessoa("trampos")])
        self.perfis(1, 2)
        http, patcher = http_falso({URL_POSTHOG: ASHBY_POSTHOG})
        trampos = ClientQuebrado()
        with patcher, patch("src.service.providers_pessoa._criar_cliente", side_effect=lambda n, e: trampos if n == "trampos" else AshbyClient(e["empresas"])):
            resultado = self.servico().coletar(HOJE)
        # Dois providers, um quebrou: erro parcial não é falha.
        self.assertEqual(resultado.da_pessoa(2).providers, 2)
        self.assertFalse(resultado.da_pessoa(2).falhou)


class ApiJsonTest(unittest.TestCase):
    def setUp(self):
        self.fontes = PessoaProviderRepositoryMemoria(PESSOA_PADRAO)
        self.ambiente = ClientFixo("Ambiente", [vaga("amb-1", "Dev Python")])
        self.perfis = PerfilBuscaRepositoryMemoria(PESSOA_PADRAO)
        self.app = criar_app(
            self.perfis,
            CurriculoRepositoryMemoria(PESSOA_PADRAO),
            BuscaService([self.ambiente]),
            providers_da_pessoa=lambda pessoa_id: PessoaProviderRepositoryMemoria(pessoa_id, self.fontes),
        )
        self.api = TestClient(self.app)

    def put(self, corpo, headers=JSON):
        return self.api.put("/perfil/providers", content=json.dumps(corpo), headers=headers)

    def test_sem_config_devolve_lista_vazia(self):
        resposta = self.api.get("/perfil/providers")
        self.assertEqual((resposta.status_code, resposta.json()), (200, {"providers": []}))

    def test_salva_e_le_de_volta(self):
        corpo = {"providers": [{"provider": "ashby", "config": {"empresas": [" posthog ", "PostHog"]}}, {"provider": "trampos"}]}
        resposta = self.put(corpo)
        esperado = {"providers": [{"provider": "trampos", "config": {}}, {"provider": "ashby", "config": {"empresas": ["posthog"]}}]}
        self.assertEqual((resposta.status_code, resposta.json()), (200, esperado))
        self.assertEqual(self.api.get("/perfil/providers").json(), esperado)

    def test_invalido_e_422_e_nao_grava(self):
        self.put({"providers": [{"provider": "trampos"}]})
        antes = self.api.get("/perfil/providers").json()
        invalidos = (
            {"providers": [{"provider": "gupy"}]},
            {"providers": [{"provider": "linkedin", "config": {"limite": 51}}]},
            {"providers": [{"provider": "linkedin", "config": {"token": "segredo"}}]},
            {"providers": [{"provider": "ashby", "config": {"empresas": []}}]},
            {"providers": [{"provider": "ashby", "config": {"empresas": ["a/b"]}}]},
            {"providers": [{"provider": "ashby", "config": {"empresas": ["a"]}}, {"provider": "ashby", "config": {"empresas": ["b"]}}]},
            {"providers": [{"provider": "trampos"}, {"provider": "linkedin", "config": {"locais": []}}]},
            {"providers": "ashby"},
            {"providers": [{"provider": "trampos", "extra": 1}]},
            {"extra": 1},
        )
        for corpo in invalidos:
            with self.subTest(corpo=str(corpo)[:60]):
                self.assertEqual(self.put(corpo).status_code, 422)
                self.assertEqual(self.api.get("/perfil/providers").json(), antes)

    def test_lista_vazia_volta_ao_ambiente(self):
        self.put({"providers": [{"provider": "trampos"}]})
        self.assertEqual(self.put({"providers": []}).json(), {"providers": []})
        self.assertEqual(self.fontes.listar(), [])

    def test_csrf_como_as_outras_rotas_json(self):
        corpo = json.dumps({"providers": [{"provider": "trampos"}]})
        casos = (
            ({}, 415),
            ({"Content-Type": "text/plain"}, 415),
            ({**JSON, "Origin": "https://malicioso.example"}, 403),
            ({**JSON, "Sec-Fetch-Site": "cross-site"}, 403),
        )
        for headers, status in casos:
            with self.subTest(headers=headers):
                self.assertEqual(self.api.put("/perfil/providers", content=corpo, headers=headers).status_code, status)
                self.assertEqual(self.fontes.listar(), [])
        self.assertEqual(self.api.put("/perfil/providers", content=corpo, headers={**JSON, "Origin": "http://testserver"}).status_code, 200)

    def test_a_resposta_nunca_tem_o_token(self):
        with ambiente(APIFY_TOKEN="segredo-do-apify"):
            self.put({"providers": [{"provider": "linkedin", "config": {"limite": 5}}]})
            self.assertNotIn("segredo-do-apify", self.api.get("/perfil/providers").text)

    def test_busca_ao_vivo_sem_config_usa_o_ambiente(self):
        resposta = self.api.get("/vagas", params={"termo": "python"})
        self.assertEqual([v["url"] for v in resposta.json()["vagas"]], ["amb-1"])

    def test_busca_ao_vivo_usa_os_providers_da_pessoa(self):
        self.put({"providers": [{"provider": "ashby", "config": {"empresas": ["posthog"]}}]})
        http, patcher = http_falso({URL_POSTHOG: ASHBY_POSTHOG})
        with patcher:
            resposta = self.api.get("/vagas", params={"termo": "python"})
        self.assertEqual(http.urls, [URL_POSTHOG])
        self.assertEqual([v["url"] for v in resposta.json()["vagas"]], ["https://jobs.ashbyhq.com/posthog/aaaa-1111"])
        self.assertEqual(resposta.json()["erros"], {})


def textarea(html, nome):
    achado = re.search(rf'<textarea[^>]*name="{nome}"[^>]*>(.*?)</textarea>', html, re.S)
    assert achado, nome
    return unescape(achado.group(1))


def marcados(html):
    return re.findall(r'<input type="checkbox" name="usar" value="([^"]*)" checked>', html)


class TelaTest(unittest.TestCase):
    def setUp(self):
        self.fontes = PessoaProviderRepositoryMemoria(PESSOA_PADRAO)
        app = criar_app(
            PerfilBuscaRepositoryMemoria(PESSOA_PADRAO),
            CurriculoRepositoryMemoria(PESSOA_PADRAO),
            BuscaService([]),
            providers_da_pessoa=lambda pessoa_id: PessoaProviderRepositoryMemoria(pessoa_id, self.fontes),
        )
        self.api = TestClient(app)

    def put(self, dados, headers=HX):
        return self.api.put("/tela/perfil/providers", data=dados, headers=headers)

    def test_sem_config_diz_que_usa_as_fontes_padrao_do_servidor(self):
        html = self.api.get("/tela/perfil").text
        self.assertIn("Fontes de vaga", html)
        self.assertIn("Usando as fontes padrão do servidor", html)
        self.assertEqual(marcados(html), [])
        self.assertIn('hx-put="/tela/perfil/providers"', html)

    def test_salva_ashby_posthog_e_mostra_o_que_ficou_gravado(self):
        resposta = self.put({"usar": ["ashby"], "ashby_empresas": "posthog\n PostHog \n\n"})
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(self.fontes.listar(), [ashby("posthog")])
        self.assertIn("Fontes salvas", resposta.text)
        self.assertNotIn("Usando as fontes padrão do servidor", resposta.text)
        self.assertEqual(marcados(resposta.text), ["ashby"])
        self.assertEqual(textarea(resposta.text, "ashby_empresas"), "posthog")
        # A página completa mostra o mesmo.
        pagina = self.api.get("/tela/perfil").text
        self.assertEqual(marcados(pagina), ["ashby"])
        self.assertNotIn("Usando as fontes padrão do servidor", pagina)

    def test_so_valem_os_providers_marcados(self):
        self.put({"usar": ["trampos"], "ashby_empresas": "posthog", "linkedin_limite": "5"})
        self.assertEqual(self.fontes.listar(), [ProviderPessoa("trampos")])

    def test_campos_de_linkedin_e_limites(self):
        resposta = self.put(
            {"usar": ["linkedin", "himalayas", "linkedin_posts"], "linkedin_locais": "United States\nCanada", "linkedin_limite": "20",
             "himalayas_limite": "10", "linkedin_posts_limite": ""}
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(
            self.fontes.listar(),
            [
                ProviderPessoa("himalayas", {"limite": 10}),
                ProviderPessoa("linkedin", {"locais": ["United States", "Canada"], "limite": 20}),
                ProviderPessoa("linkedin_posts", {}),
            ],
        )

    def test_desmarcar_tudo_volta_ao_padrao(self):
        self.put({"usar": ["trampos"]})
        resposta = self.put({})
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(self.fontes.listar(), [])
        self.assertIn("Usando as fontes padrão do servidor", resposta.text)

    def test_invalido_e_422_com_o_aviso_e_nao_grava(self):
        self.put({"usar": ["trampos"]})
        invalidos = (
            {"usar": ["gupy"]},
            {"usar": ["ashby"], "ashby_empresas": ""},
            {"usar": ["ashby"], "ashby_empresas": "a/b"},
            {"usar": ["linkedin"], "linkedin_limite": "51"},
            {"usar": ["linkedin"], "linkedin_limite": "abc"},
            {"usar": ["linkedin"], "linkedin_limite": "0"},
            {"usar": ["linkedin_posts"], "linkedin_posts_limite": "6"},
            {"usar": ["linkedin"], "linkedin_locais": "\n".join(f"L{i}" for i in range(6))},
            {"usar": ["trampos", "trampos"]},
        )
        with ambiente():
            for dados in invalidos:
                with self.subTest(dados=str(dados)[:60]):
                    resposta = self.put(dados)
                    self.assertEqual(resposta.status_code, 422)
                    self.assertEqual(resposta.headers["HX-Retarget"], "#aviso")
                    self.assertEqual(self.fontes.listar(), [ProviderPessoa("trampos")])

    def test_csrf_da_tela(self):
        dados = {"usar": ["trampos"]}
        self.assertEqual(self.put(dados, headers={}).status_code, 403)  # sem HX-Request
        self.assertEqual(self.put(dados, headers={"HX-Request": "true", "Origin": "https://malicioso.example"}).status_code, 403)
        self.assertEqual(self.put(dados, headers={"HX-Request": "true"}).status_code, 403)  # sem Origin nem Sec-Fetch-Site
        self.assertEqual(self.fontes.listar(), [])

    def test_sem_js_nem_css_inline_e_sem_segredo_na_pagina(self):
        with ambiente(APIFY_TOKEN="segredo-do-apify"):
            self.put({"usar": ["linkedin"], "linkedin_limite": "5"})
            for html in (self.api.get("/tela/perfil").text, self.put({"usar": ["linkedin"]}).text):
                trecho = html[html.index('id="providers-form"'):]
                self.assertNotIn("<script", trecho)
                self.assertNotIn("<style", trecho)
                self.assertNotIn(" style=", trecho)
                self.assertNotIn(" onclick=", trecho)
                self.assertNotIn("segredo-do-apify", html)
                self.assertNotIn("APIFY_TOKEN", html)

    def test_limite_mostra_o_teto_do_ambiente(self):
        with ambiente(APIFY_LIMITE_MAX="12"):
            html = self.api.get("/tela/perfil").text
        self.assertIn('name="linkedin_limite" min="1" max="12"', html)


if __name__ == "__main__":
    unittest.main()
