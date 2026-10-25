"""Tela "Meu perfil" (/tela/perfil). Termos e vagas escritos à mão e inventados (não são respostas gravadas)."""

import re
import unittest
from html import unescape

from tests.sessao import ClienteLogado

from src.controller.api import criar_app
from src.model.perfil import Perfil
from src.model.vaga import Modalidade, Nivel
from src.repository.base import CurriculoRepositoryMemoria, PerfilBuscaRepositoryMemoria
from src.service.busca_service import BuscaService

HX = {"HX-Request": "true", "Origin": "http://testserver"}


def cliente(perfil=None):
    perfis = PerfilBuscaRepositoryMemoria(1)
    if perfil is not None:
        perfis.salvar(perfil)
    app = criar_app(perfis, CurriculoRepositoryMemoria(1), BuscaService([]))
    return ClienteLogado(app), perfis


def textarea(html, nome):
    achado = re.search(rf'<textarea[^>]*name="{nome}"[^>]*>(.*?)</textarea>', html, re.S)
    assert achado, nome
    return unescape(achado.group(1))


def marcados(html, nome):
    return [unescape(v) for v in re.findall(rf'<input type="checkbox" name="{nome}" value="([^"]*)" checked>', html)]


COMPLETO = {
    "termos": "analista de dados\nanalista de indicadores",
    "excluir": "estágio\nvendas",
    "localidades": "São Paulo",
    "plataformas": "Remotar",
    "skills": "SQL\nPower BI",
    "modalidades": ["Remoto"],
    "niveis": ["Júnior", "Pleno"],
    "publicada_desde_dias": "14",
    "ocultar_candidatadas": "1",
    "salario_minimo": "3500",
}


class PaginaTest(unittest.TestCase):
    def test_sem_perfil_o_formulario_vem_vazio(self):
        api, _ = cliente()

        resposta = api.get("/tela/perfil")

        self.assertEqual(resposta.status_code, 200)
        html = resposta.text
        self.assertIn("<html", html)
        for campo in ("termos", "excluir", "localidades", "plataformas", "skills"):
            self.assertEqual(textarea(html, campo), "")
        self.assertEqual(marcados(html, "modalidades"), [])
        self.assertEqual(marcados(html, "niveis"), [])
        self.assertIn('hx-put="/tela/perfil"', html)
        self.assertNotIn("Perfil salvo", html)

    def test_checkboxes_tem_os_valores_dos_enums(self):
        api, _ = cliente()

        html = api.get("/tela/perfil").text

        for modalidade in Modalidade:
            self.assertIn(f'name="modalidades" value="{modalidade.value}"', html)
        for nivel in Nivel:
            self.assertIn(f'name="niveis" value="{nivel.value}"', html)

    def test_com_perfil_salvo_o_formulario_vem_preenchido(self):
        perfil = Perfil(
            termos=["analista de dados", "analista fiscal"],
            excluir=["estágio"],
            modalidades=[Modalidade.REMOTO],
            niveis=[Nivel.JUNIOR, Nivel.PLENO],
            skills=["SQL"],
            publicada_desde_dias=30,
            ocultar_candidatadas=False,
        )
        api, _ = cliente(perfil)

        html = api.get("/tela/perfil").text

        self.assertEqual(textarea(html, "termos"), "analista de dados\nanalista fiscal")
        self.assertEqual(textarea(html, "excluir"), "estágio")
        self.assertEqual(marcados(html, "modalidades"), ["Remoto"])
        self.assertEqual(marcados(html, "niveis"), ["Júnior", "Pleno"])
        self.assertIn('name="publicada_desde_dias" min="1" max="3650" step="1" value="30"', html)
        self.assertNotIn('name="ocultar_candidatadas" value="1" checked', html)

    def test_htmx_devolve_so_o_formulario(self):
        api, _ = cliente()

        resposta = api.get("/tela/perfil", headers={"HX-Request": "true"})

        self.assertEqual(resposta.status_code, 200)
        self.assertNotIn("<html", resposta.text)
        self.assertIn('<form id="perfil-form"', resposta.text)

    def test_cabecalhos_de_seguranca(self):
        api, _ = cliente()

        resposta = api.get("/tela/perfil")

        self.assertEqual(resposta.headers["Cache-Control"], "no-store")
        self.assertIn("default-src 'self'", resposta.headers["Content-Security-Policy"])
        self.assertNotRegex(resposta.text, r"<style|\sstyle=|\sonclick=|<script(?![^>]*\bsrc=)")

    def test_header_tem_link_para_o_perfil(self):
        api, _ = cliente()

        for caminho in ("/tela", "/tela/perfil"):
            self.assertRegex(api.get(caminho).text, r'<a[^>]*href="/tela/perfil"[^>]*>\s*Meu perfil\s*</a>')

    def test_css_do_perfil_e_servido(self):
        api, _ = cliente()

        self.assertEqual(api.get("/static/tela-perfil.css").status_code, 200)
        self.assertIn("/static/tela-perfil.css", api.get("/tela").text)


class SalvarTest(unittest.TestCase):
    def test_salva_e_mostra_perfil_salvo_com_link_de_volta(self):
        api, perfis = cliente()

        resposta = api.put("/tela/perfil", data=COMPLETO, headers=HX)

        self.assertEqual(resposta.status_code, 200)
        self.assertNotIn("<html", resposta.text)
        self.assertIn("Perfil salvo", resposta.text)
        self.assertIn('href="/tela"', resposta.text)
        salvo = perfis.obter()
        self.assertEqual(salvo.termos, ["analista de dados", "analista de indicadores"])
        self.assertEqual(salvo.excluir, ["estágio", "vendas"])
        self.assertEqual(salvo.localidades, ["São Paulo"])
        self.assertEqual(salvo.plataformas, ["Remotar"])
        self.assertEqual(salvo.skills, ["SQL", "Power BI"])
        self.assertEqual(salvo.modalidades, [Modalidade.REMOTO])
        self.assertEqual(salvo.niveis, [Nivel.JUNIOR, Nivel.PLENO])
        self.assertEqual(salvo.publicada_desde_dias, 14)
        self.assertTrue(salvo.ocultar_candidatadas)
        self.assertEqual(salvo.salario_minimo, 3500)

    def test_o_mesmo_perfil_da_rota_json(self):
        api, _ = cliente()

        api.put("/tela/perfil", data=COMPLETO, headers=HX)
        json = api.get("/perfil").json()

        self.assertEqual(json["termos"], ["analista de dados", "analista de indicadores"])
        self.assertEqual(json["modalidades"], ["Remoto"])
        self.assertEqual(json["niveis"], ["Júnior", "Pleno"])
        self.assertEqual(json["publicada_desde_dias"], 14)
        self.assertEqual(json["salario_minimo"], 3500)

    def test_ida_e_volta_com_acento_virgula_e_comercial_e_aspas(self):
        api, perfis = cliente()
        termos = ['analista, de dados & "BI"', "gestão de <projetos>", "o'brien"]

        resposta = api.put("/tela/perfil", data={**COMPLETO, "termos": "\n".join(termos)}, headers=HX)

        self.assertEqual(perfis.obter().termos, termos)
        self.assertEqual(textarea(resposta.text, "termos"), "\n".join(termos))
        self.assertEqual(textarea(api.get("/tela/perfil").text, "termos"), "\n".join(termos))

    def test_termo_com_script_volta_escapado(self):
        api, _ = cliente()
        termo = '<script>alert(1)</script>'

        salvou = api.put("/tela/perfil", data={**COMPLETO, "termos": termo}, headers=HX)
        pagina = api.get("/tela/perfil")

        for resposta in (salvou, pagina):
            self.assertNotIn("<script>alert", resposta.text)
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", resposta.text)

    def test_repetidos_e_linhas_em_branco(self):
        api, perfis = cliente()

        api.put("/tela/perfil", data={**COMPLETO, "termos": "SQL\n\n   \nSQL\r\nDAX\n"}, headers=HX)

        self.assertEqual(perfis.obter().termos, ["SQL", "DAX"])

    def test_dias_vazio_vira_sem_limite_e_checkbox_desmarcado_vira_false(self):
        api, perfis = cliente()
        dados = {k: v for k, v in COMPLETO.items() if k != "ocultar_candidatadas"}

        api.put("/tela/perfil", data={**dados, "publicada_desde_dias": ""}, headers=HX)

        self.assertIsNone(perfis.obter().publicada_desde_dias)
        self.assertFalse(perfis.obter().ocultar_candidatadas)

    def test_formulario_vazio_grava_perfil_vazio(self):
        api, perfis = cliente(Perfil(termos=["x"]))

        resposta = api.put("/tela/perfil", data={}, headers=HX)

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(perfis.obter().termos, [])
        self.assertEqual(perfis.obter().modalidades, [])

    def test_lista_da_tela_obedece_ao_perfil_salvo(self):
        from src.model.vaga_externa import VagaExterna
        from src.repository.base import VagaEncontradaRepositoryMemoria

        encontradas = VagaEncontradaRepositoryMemoria(1)
        encontradas.registrar(
            [
                VagaExterna("Analista de Dados", "Alfa", "Remotar", "https://exemplo.test/a"),
                VagaExterna("Estagiário de Vendas", "Beta", "Remotar", "https://exemplo.test/b"),
            ],
            "dados",
        )
        perfis = PerfilBuscaRepositoryMemoria(1)
        api = ClienteLogado(criar_app(perfis, CurriculoRepositoryMemoria(1), BuscaService([]), encontradas=encontradas))

        api.put("/tela/perfil", data={"termos": "dados", "excluir": "vendas"}, headers=HX)
        html = api.get("/tela").text

        self.assertIn("Analista de Dados", html)
        self.assertNotIn("Estagiário de Vendas", html)


class InvalidoTest(unittest.TestCase):
    def assertInvalido(self, api, perfis, anterior, dados, trecho):
        resposta = api.put("/tela/perfil", data=dados, headers=HX)

        self.assertEqual(resposta.status_code, 422)
        self.assertNotIn("<html", resposta.text)
        self.assertIn(trecho, unescape(resposta.text))
        self.assertEqual(resposta.headers["HX-Retarget"], "#aviso")
        # Nada gravado: o perfil anterior continua.
        self.assertEqual(perfis.obter(), anterior)

    def setUp(self):
        self.api, self.perfis = cliente(Perfil(termos=["analista fiscal"], niveis=[Nivel.SENIOR]))
        self.anterior = self.perfis.obter()

    def test_modalidade_fora_do_enum(self):
        self.assertInvalido(self.api, self.perfis, self.anterior, {**COMPLETO, "modalidades": ["Presencial"]}, "Presencial")

    def test_nivel_fora_do_enum(self):
        self.assertInvalido(self.api, self.perfis, self.anterior, {**COMPLETO, "niveis": ["Estagiário"]}, "Estagiário")

    def test_dias_fora_do_intervalo(self):
        for dias in ("0", "3651"):
            self.assertInvalido(self.api, self.perfis, self.anterior, {**COMPLETO, "publicada_desde_dias": dias}, "entre 1 e 3650")

    def test_salario_fora_do_intervalo_ou_com_ponto_de_milhar(self):
        for salario, aviso in (("0", "entre 1 e 1000000"), ("1000001", "entre 1 e 1000000"), ("9" * 5000, "entre 1 e 1000000"),
                               ("3.050", "número inteiro"), ("3050,00", "número inteiro")):
            with self.subTest(salario=salario[:10]):
                self.assertInvalido(self.api, self.perfis, self.anterior, {**COMPLETO, "salario_minimo": salario}, aviso)

    def test_dias_com_muitos_digitos_e_422_e_nao_500(self):
        # `int()` levanta ValueError acima de 4300 dígitos; aqui tem de ser o aviso de intervalo.
        for dias in ("9" * 5000, "1" + "0" * 4400, "99999"):
            with self.subTest(digitos=len(dias)):
                self.assertInvalido(self.api, self.perfis, self.anterior, {**COMPLETO, "publicada_desde_dias": dias}, "entre 1 e 3650")

    def test_dias_com_zeros_a_esquerda_vale_o_numero(self):
        resposta = self.api.put("/tela/perfil", data={**COMPLETO, "publicada_desde_dias": "0014"}, headers=HX)

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(self.perfis.obter().publicada_desde_dias, 14)

    def test_dias_que_nao_e_numero(self):
        for dias in ("abc", "-3", "1.5", "１２"):
            self.assertInvalido(self.api, self.perfis, self.anterior, {**COMPLETO, "publicada_desde_dias": dias}, "número inteiro")

    def test_lista_gigante(self):
        self.assertInvalido(self.api, self.perfis, self.anterior, {**COMPLETO, "termos": "\n".join(["x"] * 201)}, "até 200 itens")

    def test_lista_gigante_diz_qual_lista_passou(self):
        self.assertInvalido(self.api, self.perfis, self.anterior, {**COMPLETO, "skills": "x\n" + "y" * 201}, "skills:")

    def test_o_valor_do_erro_volta_escapado(self):
        resposta = self.api.put("/tela/perfil", data={**COMPLETO, "modalidades": ["<script>x</script>"]}, headers=HX)

        self.assertEqual(resposta.status_code, 422)
        self.assertNotIn("<script>x", resposta.text)


class CsrfTest(unittest.TestCase):
    def test_put_sem_htmx_e_403(self):
        api, perfis = cliente(Perfil(termos=["a"]))

        resposta = api.put("/tela/perfil", data=COMPLETO)

        self.assertEqual(resposta.status_code, 403)
        self.assertEqual(perfis.obter().termos, ["a"])

    def test_put_de_outra_origem_e_403(self):
        api, perfis = cliente(Perfil(termos=["a"]))

        resposta = api.put("/tela/perfil", data=COMPLETO, headers={"HX-Request": "true", "Origin": "https://mau.test"})

        self.assertEqual(resposta.status_code, 403)
        self.assertEqual(perfis.obter().termos, ["a"])

    def test_nenhuma_mutacao_por_get_ou_post(self):
        api, perfis = cliente(Perfil(termos=["a"]))

        self.assertEqual(api.get("/tela/perfil", params=COMPLETO, headers=HX).status_code, 200)
        self.assertEqual(api.post("/tela/perfil", data=COMPLETO, headers=HX).status_code, 405)
        self.assertEqual(perfis.obter().termos, ["a"])


class TetoNasDuasRotasTest(unittest.TestCase):
    """O teto de 200 itens / 200 caracteres está em `validar_perfil`: o `PUT /perfil` (JSON) recusa o mesmo que a tela,
    e o que o JSON grava a tela salva de volta (antes o JSON gravava 250 skills e a tela não salvava mais nada)."""

    def setUp(self):
        self.api, self.perfis = cliente(Perfil(termos=["analista fiscal"]))
        self.anterior = self.perfis.obter()

    def put_json(self, corpo):
        return self.api.put("/perfil", json=corpo)

    def test_json_recusa_lista_com_mais_de_200_itens(self):
        resposta = self.put_json({"skills": [f"skill {i}" for i in range(201)]})

        self.assertEqual(resposta.status_code, 422)
        self.assertIn("skills:", resposta.json()["detail"])
        self.assertIn("até 200 itens", resposta.json()["detail"])
        self.assertEqual(self.perfis.obter(), self.anterior)

    def test_json_recusa_item_com_mais_de_200_caracteres(self):
        resposta = self.put_json({"termos": ["analista", "x" * 201]})

        self.assertEqual(resposta.status_code, 422)
        self.assertIn("termos:", resposta.json()["detail"])
        self.assertEqual(self.perfis.obter(), self.anterior)

    def test_validar_perfil_e_a_fonte_unica(self):
        from src.model.erros import DadoInvalido
        from src.repository.base import MAX_ITENS_POR_LISTA, MAX_TAMANHO_ITEM, validar_perfil

        for campo in ("termos", "excluir", "localidades", "plataformas", "skills"):
            with self.subTest(campo=campo):
                with self.assertRaisesRegex(DadoInvalido, f"^{campo}:"):
                    validar_perfil(Perfil(**{campo: ["a"] * (MAX_ITENS_POR_LISTA + 1)}))
                with self.assertRaisesRegex(DadoInvalido, f"^{campo}:"):
                    validar_perfil(Perfil(**{campo: ["a" * (MAX_TAMANHO_ITEM + 1)]}))
                self.assertEqual(len(getattr(validar_perfil(Perfil(**{campo: [f"a{i}" for i in range(200)]})), campo)), 200)


if __name__ == "__main__":
    unittest.main()
