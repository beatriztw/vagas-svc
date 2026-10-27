"""Remunerações e vagas inventadas, escritas à mão; nunca dados de pessoas reais."""
import unittest
from datetime import date

from src.model.detalhe_vaga import DetalheVaga, Salario
from src.model.perfil import Perfil
from src.model.vaga_externa import VagaExterna
from src.service.recomendacao_service import aplicar_perfil


def vaga(url, descricao=None, salario=None):
    return VagaExterna("Backend Engineer", "Empresa Exemplo", "Fonte", url, remoto=True,
                       detalhe=DetalheVaga(descricao=descricao, salario=salario))


class PisoUsdTest(unittest.TestCase):
    def filtrar(self, vagas):
        perfil = Perfil()
        perfil.salario_minimo_usd = 10000
        return aplicar_perfil(vagas, perfil, date(2026, 10, 1))

    def test_faixa_anual_precisa_alcancar_o_piso_mensal(self):
        vagas = [vaga("abaixo", salario=Salario(6000000, 11900000, "USD", "yearly")),
                 vaga("alcanca", salario=Salario(8500000, 12500000, "USD", "yearly")),
                 vaga("igual", salario=Salario(12000000, 12000000, "USD", "yearly"))]
        self.assertEqual([v.url for v in self.filtrar(vagas)], ["alcanca", "igual"])

    def test_nao_confunde_brl_ou_periodo_ausente_com_usd_mensal(self):
        vagas = [vaga("brl", salario=Salario(2000000, 3000000, "BRL", "monthly")),
                 vaga("sem-periodo", salario=Salario(10000000, None, "USD")),
                 vaga("mes", salario=Salario(1000000, 1200000, "USD", "monthly"))]
        self.assertEqual([v.url for v in self.filtrar(vagas)], ["mes"])

    def test_texto_explicito_de_salario_usd_com_periodo(self):
        vagas = [vaga("anual", "Competitive salary of USD $85,000-$125,000 annual base."),
                 vaga("mensal", "Salário: US$ 10.000 a 12.000 por mês."),
                 vaga("abaixo", "Base salary USD 60k-110k per year.")]
        self.assertEqual([v.url for v in self.filtrar(vagas)], ["anual", "mensal"])

    def test_moeda_valor_e_periodo_desconhecidos_nao_sao_confirmacao(self):
        vagas = [vaga("controle", "Base salary USD 120k per year."),
                 vaga("so-dolar", "Salary $150,000 per year."),
                 vaga("so-moeda", "Excellent compensation in USD or your local currency."),
                 vaga("receita", "Our annual revenue is USD 150,000. Salary to be discussed."),
                 vaga("equity", "Equity compensation USD 150,000 per year."),
                 vaga("equity-no-valor", "Salary USD 150,000 in equity per year."),
                 vaga("bonus", "Salary USD 150,000 bonus per year."),
                 vaga("periodo-do-beneficio", "Base salary USD 95,000. Monthly home office allowance."),
                 vaga("frequencia-do-pagamento", "Salary: USD 120,000 - 150,000 gross, paid monthly."),
                 vaga("sem-periodo", "Salary USD 150,000."), vaga("ausente")]
        self.assertEqual([v.url for v in self.filtrar(vagas)], ["controle"])

    def test_sem_piso_preserva_comportamento_atual(self):
        vagas = [vaga("sem-salario"), vaga("brl", salario=Salario(300000, None, "BRL", "monthly"))]
        self.assertEqual(aplicar_perfil(vagas, Perfil(), date(2026, 10, 1)), vagas)

    def test_limite_desconhecido_nao_sugere_salario_ilimitado(self):
        vagas = [vaga("a-partir-abaixo", salario=Salario(900000, None, "USD", "monthly")),
                 vaga("a-partir-acima", salario=Salario(1100000, None, "USD", "monthly"))]
        self.assertEqual([v.url for v in self.filtrar(vagas)], ["a-partir-acima"])

    def test_api_formulario_e_sqlite_preservam_piso(self):
        from tests.test_tela_perfil import cliente, HX
        from src.service.perfil_service import from_json, json_dumps
        api, perfis = cliente()
        self.assertEqual(api.put("/perfil", json={"salario_minimo_usd": 10000}).status_code, 200)
        self.assertEqual(api.get("/perfil").json()["salario_minimo_usd"], 10000)
        html = api.get("/tela/perfil").text
        self.assertIn('name="salario_minimo_usd"', html)
        self.assertIn('value="10000"', html)
        api.put("/tela/perfil", data={"salario_minimo_usd": "11000"}, headers=HX)
        self.assertEqual(perfis.obter().salario_minimo_usd, 11000)
        self.assertEqual(from_json(json_dumps(perfis.obter())), perfis.obter())
        for invalido in [0, -1, True, 1.5, 1000001]:
            with self.subTest(invalido=invalido):
                self.assertEqual(api.put("/perfil", json={"salario_minimo_usd": invalido}).status_code, 422)
                self.assertEqual(perfis.obter().salario_minimo_usd, 11000)

    def test_himalayas_preserva_faixa_usd_estruturada_sem_descricao(self):
        from src.client.himalayas import HimalayasClient
        item = {"title": "Backend Engineer", "companyName": "Empresa Exemplo", "guid": "https://himalayas.app/jobs/exemplo",
                "minSalary": 100000, "maxSalary": 140000, "currency": "USD", "salaryPeriod": "annual"}
        [anual] = HimalayasClient(http=lambda _: {"jobs": [item]}).buscar("backend")
        self.assertEqual(anual.detalhe.salario, Salario(10000000, 14000000, "USD", "yearly"))
        self.assertEqual(self.filtrar([anual]), [anual])
        for moeda in [None, "BRL"]:
            with self.subTest(moeda=moeda):
                item["currency"] = moeda
                [vaga_sem_usd] = HimalayasClient(http=lambda _: {"jobs": [item]}).buscar("backend")
                self.assertEqual(self.filtrar([vaga_sem_usd]), [])

    def test_linha_de_perfil_anterior_a_migration_usd_continua_legivel(self):
        from src.repository.postgres import linha_para_perfil
        linha_antiga = {"termos": ["python"], "excluir": [], "localidades": [], "plataformas": [],
                        "skills": [], "modalidades": [], "niveis": [], "publicada_desde_dias": None,
                        "ocultar_candidatadas": True, "salario_minimo": None}
        perfil = linha_para_perfil(linha_antiga)
        self.assertEqual(perfil.termos, ["python"])
        self.assertIsNone(perfil.salario_minimo_usd)
