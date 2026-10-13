import json
import os
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from src.client.apify_linkedin_posts import (
    CURSO,
    MAX_CARACTERES_CONSULTA,
    MAX_OPERADORES,
    PROCURA_EMPREGO,
    SEM_CONTRATACAO,
    TORTO,
    ApifyLinkedInPostsClient,
    consultas,
    motivo_de_descarte,
    operadores,
    remoto_do_texto,
    titulo_do_texto,
)

FIXTURES = Path(__file__).parent / "fixtures"
# Escrita à mão, com texto e nomes inventados, imitando a forma dos itens reais do actor (conferida ao vivo em 12/10/2026).
FIXTURE = "apify_linkedin_posts_escrito_a_mao.json"


def fake_post(chamadas: list[tuple] | None = None, itens=None):
    dados = json.loads((FIXTURES / FIXTURE).read_text(encoding="utf-8")) if itens is None else itens

    def http_post(url: str, corpo: dict, token: str):
        if chamadas is not None:
            chamadas.append((url, corpo, token))
        return dados

    return http_post


def _client(limite=3, **kw) -> ApifyLinkedInPostsClient:
    return ApifyLinkedInPostsClient(limite=limite, token="tk", http_post=fake_post(**kw))


class InputDoActorTest(unittest.TestCase):
    def test_input_bate_com_o_schema_gravado_do_actor(self):
        """Schema do build 0.0.114, baixado da API do Apify em 2026-10-12."""
        schema = json.loads((FIXTURES / "apify_linkedin_posts_input_schema.json").read_text(encoding="utf-8"))
        chamadas: list[tuple] = []
        _client(chamadas=chamadas).buscar("assistente financeiro")

        _, corpo, _ = chamadas[0]
        self.assertLessEqual(set(corpo), set(schema["properties"]))
        self.assertLessEqual(set(schema["required"]), set(corpo))
        for campo, valor in corpo.items():
            prop = schema["properties"][campo]
            tipo = {"string": str, "integer": int, "boolean": bool, "array": list}[prop["type"]]
            self.assertIsInstance(valor, tipo, campo)
            if "enum" in prop:
                self.assertIn(valor, prop["enum"], campo)

    def test_um_run_de_24h_mais_novos_primeiro_com_teto_de_cobranca(self):
        chamadas: list[tuple] = []
        _client(limite=4, chamadas=chamadas).buscar("assistente financeiro")

        self.assertEqual(len(chamadas), 1)
        url, corpo, token = chamadas[0]
        self.assertEqual(
            url,
            "https://api.apify.com/v2/acts/harvestapi~linkedin-post-search/run-sync-get-dataset-items"
            "?maxItems=4&maxTotalChargeUsd=0.01",
        )
        self.assertEqual(corpo["maxPosts"], 4)
        self.assertEqual((corpo["postedLimit"], corpo["sortBy"]), ("24h", "date"))
        # Reações, comentários e o perfil "main" do autor são cobrados à parte.
        self.assertEqual((corpo["scrapeReactions"], corpo["scrapeComments"], corpo["profileScraperMode"]), (False, False, "short"))
        self.assertEqual(token, "tk")

    def test_teto_em_dolar_acompanha_o_limite(self):
        chamadas: list[tuple] = []
        _client(limite=10, chamadas=chamadas).buscar("assistente financeiro")

        # 10 posts x US$ 0,002 + folga da largada e da consulta sem resultado.
        self.assertTrue(chamadas[0][0].endswith("?maxItems=10&maxTotalChargeUsd=0.022"))

    def test_consulta_de_vaga_remota_dentro_dos_limites_do_linkedin(self):
        (consulta,) = consultas("assistente financeiro")

        self.assertEqual(
            consulta,
            '"assistente financeiro" AND ("vaga" OR "contratando" OR "contratamos") AND ("remoto" OR "home office")',
        )
        self.assertLessEqual(operadores(consulta), MAX_OPERADORES)

    def test_termo_com_aspas_e_operadores_nao_quebra_a_consulta(self):
        (consulta,) = consultas('analista "AND" (dados) OR x' + " y" * 400)

        # AND/OR do termo ficam dentro das aspas: não contam como operador nem abrem grupo.
        self.assertEqual(operadores(consulta), 5)
        self.assertLessEqual(len(consulta), MAX_CARACTERES_CONSULTA)
        self.assertEqual(consulta.count('"') % 2, 0)
        self.assertTrue(consulta.startswith('"analista AND dados OR x y'))

    def test_termo_vazio_nao_gasta_um_run(self):
        chamadas: list[tuple] = []
        for termo in ("", "  ", '""', "( )"):
            self.assertEqual(_client(chamadas=chamadas).buscar(termo), [], repr(termo))
        self.assertEqual(chamadas, [])

    def test_sem_token_falha_sem_chamar_o_apify(self):
        chamadas: list[tuple] = []
        client = ApifyLinkedInPostsClient(limite=3, http_post=fake_post(chamadas))
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(RuntimeError):
            client.buscar("dados")
        self.assertEqual(chamadas, [])


class ConversaoTest(unittest.TestCase):
    def test_converte_post_de_empresa_em_vaga(self):
        vaga = _client().buscar("assistente financeiro")[0]

        self.assertEqual(vaga.titulo, "Vaga aberta: Assistente Financeiro (100% remoto) vagas financeiro")
        self.assertEqual(vaga.empresa, "Empresa Fictícia")
        self.assertEqual(vaga.plataforma, "LinkedIn (posts)")
        # Pelo id: estável, sem o identificador do autor que vem no linkedinUrl.
        self.assertEqual(vaga.url, "https://www.linkedin.com/feed/update/urn:li:activity:7000000000000000001/")
        self.assertTrue(vaga.remoto)
        self.assertEqual(vaga.publicada_em, date(2026, 10, 12))
        self.assertTrue(vaga.detalhe.descricao.startswith("🚀 Vaga aberta"))
        self.assertIn("• Conciliação bancária", vaga.detalhe.descricao)

    def test_nome_da_pessoa_autora_nunca_vira_empresa(self):
        vaga = _client().buscar("contas a pagar")[1]

        self.assertEqual(vaga.url, "https://www.linkedin.com/feed/update/urn:li:activity:7000000000000000002/")
        self.assertEqual(vaga.empresa, "")
        for campo in (vaga.titulo, vaga.empresa, vaga.url, vaga.detalhe.descricao):
            self.assertNotIn("Pessoa", campo)
            self.assertNotIn("pessoa-ficticia", campo)

    def test_titulo_pula_linha_so_de_hashtag(self):
        vaga = _client().buscar("contas a pagar")[1]

        self.assertEqual(vaga.titulo, "Estamos contratando analista de contas a pagar, home-office, CLT.")

    def test_titulo_cortado_e_sem_texto_util_cai_no_padrao(self):
        self.assertEqual(len(titulo_do_texto("Vaga " + "x" * 300, "")), 120)
        self.assertTrue(titulo_do_texto("Vaga " + "x" * 300, "").endswith("…"))
        self.assertEqual(titulo_do_texto("#vaga #remoto\n🚀🚀", "Empresa X"), "Vaga em publicação de Empresa X")
        self.assertEqual(titulo_do_texto("#vaga", ""), "Vaga em publicação do LinkedIn")

    def test_vagas_finais_da_fixture_sem_repetido(self):
        client = _client()
        vagas = client.buscar("assistente financeiro")

        # O último item repete o id do primeiro (repost): sai uma vez só.
        self.assertEqual(
            [v.url.rsplit(":", 1)[1] for v in vagas],
            ["7000000000000000001/", "7000000000000000002/", "7000000000000000006/", "7000000000000000009/"],
        )
        self.assertEqual(client.descartes, {PROCURA_EMPREGO: 1, CURSO: 1, SEM_CONTRATACAO: 1, TORTO: 2})

    def test_data_absurda_ou_torta_fica_sem_data_e_nao_derruba_o_post(self):
        vaga = _client().buscar("assistente")[3]

        self.assertEqual(vaga.empresa, "Terceira Empresa Fictícia")
        self.assertIsNone(vaga.publicada_em)

    def test_item_torto_descarta_so_ele(self):
        bom = {"type": "post", "id": "7000000000000000010", "content": "Vaga remota inventada.", "author": None}
        itens = [
            None,
            ["lista"],
            {"type": "post", "id": 7000000000000000011, "content": "Vaga com id numérico."},
            {"type": "post", "id": "../x", "content": "Vaga com id que não é número."},
            {"type": "post", "id": "7000000000000000012", "content": ["não é texto"]},
            {"type": "post", "id": "7000000000000000013", "content": "   "},
            {"type": "comment", "id": "7000000000000000014", "content": "Vaga num comentário."},
            {**bom, "postedAt": "não é data"},
        ]
        client = _client(itens=itens)
        vagas = client.buscar("dados")

        self.assertEqual([v.url for v in vagas], ["https://www.linkedin.com/feed/update/urn:li:activity:7000000000000000010/"])
        self.assertIsNone(vagas[0].publicada_em)
        self.assertEqual(client.descartes, {TORTO: 7})

    def test_descricao_cortada_no_teto(self):
        from src.client.sanitizar import MAX_CARACTERES_DESCRICAO

        itens = [{"type": "post", "id": "7000000000000000015", "content": "Vaga remota. " + "y" * 30_000}]
        vaga = _client(itens=itens).buscar("dados")[0]

        self.assertEqual(len(vaga.detalhe.descricao), MAX_CARACTERES_DESCRICAO)
        self.assertTrue(vaga.detalhe.descricao.endswith("…"))

    def test_resposta_que_nao_e_lista_vira_erro(self):
        with self.assertRaises(ValueError):
            _client(itens={"error": {"type": "run-failed"}}).buscar("dados")


class FiltroDeRuidoTest(unittest.TestCase):
    def test_quem_procura_emprego(self):
        for texto in (
            "Vaga? Estou aberta: #OpenToWork",
            "Open to work: procuro vaga de analista.",
            "Estou em BUSCA de oportunidade como analista.",
            "Procuro oportunidades na área financeira (vaga remota).",
            "Em busca de recolocação: aceito vaga remota.",
            "Estou disponível para vaga imediata.",
        ):
            self.assertEqual(motivo_de_descarte(texto), PROCURA_EMPREGO, texto)

    def test_curso_e_mentoria(self):
        for texto in (
            "Mentoria para conseguir sua vaga.",
            "INSCRIÇÕES abertas para o curso! Oportunidade única de vaga.",
            "Vaga garantida: link na bio.",
            "Curso gratuito de Excel com oportunidade de vaga.",
        ):
            self.assertEqual(motivo_de_descarte(texto), CURSO, texto)

    def test_sem_verbo_de_contratacao(self):
        self.assertEqual(motivo_de_descarte("Cinco dicas de Excel para quem trabalha remoto."), SEM_CONTRATACAO)

    def test_post_de_recrutador_e_programa_de_estagio_nao_sao_descartados(self):
        # Achado do review do #49: a abertura de recrutador e o programa com inscrição são vaga.
        for texto in (
            "Estou em busca de Assistente Financeiro para vaga 100% remota, CLT.",
            "Inscrições abertas para a vaga de estágio remoto em finanças!",
        ):
            self.assertIsNone(motivo_de_descarte(texto), texto)

    def test_vaga_de_verdade_passa(self):
        for texto in (
            "Estamos contratando assistente financeiro.",
            "Vaga: analista de dados.",
            "Hiring a remote data analyst.",
            "Buscamos analista. Curso superior completo.",
            "Procuramos analista fiscal.",
            "Nova oportunidade em finanças.",
        ):
            self.assertIsNone(motivo_de_descarte(texto), texto)

    def test_por_palavra_e_nao_por_pedaco(self):
        # "vagabundo" não é "vaga", "opentoworking" não é "opentowork", "disponivelmente" não é "disponível".
        self.assertEqual(motivo_de_descarte("Um vagabundo e um contratante."), SEM_CONTRATACAO)
        self.assertIsNone(motivo_de_descarte("#opentoworking vaga aberta"))
        self.assertIsNone(motivo_de_descarte("Vaga: estou disponivelmente feliz."))


class RemotoTest(unittest.TestCase):
    def test_remoto_por_palavra(self):
        for texto in ("Vaga 100% REMOTA", "trabalho remoto", "Home office", "home-office", "#homeoffice", "remote job"):
            self.assertIs(remoto_do_texto(texto), True, texto)

    def test_pedaco_de_palavra_nao_e_remoto(self):
        # "remotamente"/"remotely" e "office" sozinho não dizem a modalidade.
        for texto in ("Vaga em Remotândia", "Atendimento remotamente", "Pacote Office avançado", "home sweet home"):
            self.assertIsNone(remoto_do_texto(texto), texto)

    def test_hibrido_ou_presencial_nao_e_remoto(self):
        for texto in ("Híbrido, 2 dias de home office", "Vaga presencial", "hybrid role"):
            self.assertIs(remoto_do_texto(texto), False, texto)


class ProvidersPadraoPostsTest(unittest.TestCase):
    def _posts(self, env: dict) -> list:
        from src.controller.api import providers_padrao

        with mock.patch.dict(os.environ, env, clear=True):
            providers = providers_padrao()
        self.assertTrue(providers)
        return [p for p in providers if isinstance(p, ApifyLinkedInPostsClient)]

    def test_desligado_por_padrao_ou_com_zero(self):
        self.assertEqual(self._posts({"APIFY_TOKEN": "tk"}), [])
        self.assertEqual(self._posts({"APIFY_TOKEN": "tk", "APIFY_POSTS_LIMITE": "0"}), [])

    def test_invalido_fica_desligado_sem_derrubar_a_app(self):
        for valor in ("", "um", "1.5", "-3", " "):
            self.assertEqual(self._posts({"APIFY_TOKEN": "tk", "APIFY_POSTS_LIMITE": valor}), [], valor)

    def test_liga_com_token_e_limite(self):
        (posts,) = self._posts({"APIFY_TOKEN": "tk", "APIFY_POSTS_LIMITE": " 2 "})

        self.assertEqual((posts.limite, posts._token), (2, "tk"))

    def test_sem_token_nao_liga(self):
        self.assertEqual(self._posts({"APIFY_POSTS_LIMITE": "2"}), [])
        self.assertEqual(self._posts({"APIFY_TOKEN": "  ", "APIFY_POSTS_LIMITE": "2"}), [])


if __name__ == "__main__":
    unittest.main()
