"""Compatibilidade pura (sem IA). Perfis, títulos e requisitos inventados à mão."""

import unittest
from types import SimpleNamespace

from src.model.perfil import Perfil
from src.model.vaga import Nivel
from src.service.compatibilidade_service import (
    compatibilidade,
    compatibilidade_da_vaga,
    requisitos_da_vaga,
)


def perfil(**kw):
    base = dict(termos=["analista de dados"], skills=["SQL", "Power BI"], niveis=[Nivel.JUNIOR, Nivel.PLENO])
    base.update(kw)
    return Perfil(**base)


class SkillsPorPalavraTest(unittest.TestCase):
    def fortes(self, skills, texto, obrig=True):
        return compatibilidade("Vaga", [(texto, obrig)], perfil(skills=skills, termos=[], niveis=[])).pontos_fortes

    def test_java_nao_casa_com_javascript(self):
        self.assertEqual(self.fortes(["Java"], "Experiência com JavaScript"), [])
        self.assertEqual(self.fortes(["Java"], "Experiência com Java e Spring"), ["Java"])

    def test_sql_nao_casa_com_nosql(self):
        self.assertEqual(self.fortes(["SQL"], "Bancos NoSQL"), [])
        self.assertEqual(self.fortes(["SQL"], "Consultas em SQL"), ["SQL"])

    def test_nosql_nao_casa_com_no_sql_de_ligacao(self):
        # Juntar o par vizinho ("Power BI" = "PowerBI") não vale com palavra de ligação: "no" + "sql".
        self.assertEqual(self.fortes(["NoSQL"], "Experiência no SQL Server"), [])
        self.assertEqual(self.fortes(["NoSQL"], "Bancos na SQL"), [])
        self.assertEqual(self.fortes(["NoSQL"], "Bancos NoSQL"), ["NoSQL"])
        self.assertEqual(self.fortes(["NoSQL"], "Bancos No-SQL"), ["NoSQL"])

    def test_c_nao_casa_com_cmais_nem_csharp(self):
        self.assertEqual(self.fortes(["C"], "C++ e C#"), [])
        self.assertEqual(self.fortes(["C++"], "C++ e C#"), ["C++"])

    def test_power_bi_expressao_de_duas_palavras(self):
        self.assertEqual(self.fortes(["Power BI"], "Dashboards em power bi"), ["Power BI"])
        self.assertEqual(self.fortes(["Power BI"], "Dashboards em PowerBI"), ["Power BI"])
        self.assertEqual(self.fortes(["Power BI"], "Dashboards em Power-BI"), ["Power BI"])
        self.assertEqual(self.fortes(["PowerBI"], "Dashboards em Power BI"), ["PowerBI"])
        self.assertEqual(self.fortes(["Power BI"], "Power de decisão e BI"), [])
        self.assertEqual(self.fortes(["Power BI"], "Power Query"), [])

    def test_acento_caixa_plural_e_hifen(self):
        self.assertEqual(self.fortes(["Conciliação"], "CONCILIAÇÕES bancárias"), ["Conciliação"])
        self.assertEqual(self.fortes(["conciliacao"], "conciliação bancária"), ["conciliacao"])
        self.assertEqual(self.fortes(["Front-end"], "experiência em front end"), ["Front-end"])
        self.assertEqual(self.fortes(["Google Sheets"], "Google Sheet"), ["Google Sheets"])

    def test_skill_no_titulo_conta(self):
        r = compatibilidade("Analista SQL", None, perfil(skills=["SQL"]))
        self.assertEqual(r.pontos_fortes[0], "SQL")

    def test_skill_repetida_e_vazia_do_perfil(self):
        r = compatibilidade("Analista SQL", None, perfil(skills=["SQL", "sql", " ", "—", "S.Q.L"]))
        self.assertEqual(r.pontos_fortes.count("SQL"), 1)
        self.assertNotIn("sql", r.pontos_fortes)


class PesosTest(unittest.TestCase):
    P = perfil(termos=[], niveis=[], skills=["SQL", "DAX"])

    def test_obrigatorio_pesa_mais_que_desejavel(self):
        # Cobre só o obrigatório (3 de 4) x só o desejável (1 de 4).
        a = compatibilidade("x", [("SQL", True), ("Rust", False)], self.P)
        b = compatibilidade("x", [("Rust", True), ("SQL", False)], self.P)
        self.assertEqual(a.percentual, 75)
        self.assertEqual(b.percentual, 25)
        self.assertFalse(a.parcial)

    def test_tudo_coberto_e_nada_coberto(self):
        self.assertEqual(compatibilidade("x", [("SQL", True), ("DAX", False)], self.P).percentual, 100)
        r = compatibilidade("x", [("Rust", True), ("Go", False)], self.P)
        self.assertEqual(r.percentual, 0)
        self.assertEqual(r.lacunas, ["Rust (obrigatório)", "Go"])

    def test_lacunas_com_obrigatorio_primeiro(self):
        r = compatibilidade("x", [("Go", False), ("Rust", True)], self.P)
        self.assertEqual(r.lacunas, ["Rust (obrigatório)", "Go"])

    def test_requisito_repetido_conta_uma_vez_e_fica_obrigatorio(self):
        r = compatibilidade("x", [("Rust", False), ("rust ", True), ("SQL", True)], self.P)
        self.assertEqual(r.lacunas, ["Rust (obrigatório)"])
        self.assertEqual(r.percentual, 50)

    def test_itens_invalidos_sao_descartados(self):
        r = compatibilidade("x", [None, "SQL", ("SQL",), (None, True), ("", True), ("  ", False), ("SQL", True)], self.P)
        self.assertEqual(r.percentual, 100)
        self.assertEqual(r.lacunas, [])

    def test_lacuna_longa_e_encurtada(self):
        r = compatibilidade("x", [("Rust " * 100, False)], self.P)
        self.assertLessEqual(len(r.lacunas[0]), 140)
        self.assertTrue(r.lacunas[0].endswith("…"))


class NivelETituloTest(unittest.TestCase):
    def test_termos_so_de_ligacao_ou_vazios_nao_contam_no_titulo(self):
        # Mesmo filtro de `relevancia_perfil`: sem termo útil, o título sai da conta (como sem termos).
        for termos in (["de"], ["—"], ["", "de", "na"]):
            with self.subTest(termos=termos):
                r = compatibilidade("Analista Pleno", None, perfil(skills=[], termos=termos, niveis=[Nivel.PLENO]))
                self.assertEqual(r.percentual, 100)
                self.assertNotIn("Título sem relação com os termos do perfil", r.lacunas)

    def test_nivel_do_titulo_nos_niveis_do_perfil(self):
        p = perfil(skills=[], termos=[], niveis=[Nivel.PLENO])
        for titulo in ("Analista Pleno", "Analista Pl", "Analista de dados (PLENA)"):
            r = compatibilidade(titulo, None, p)
            self.assertEqual(r.percentual, 100, titulo)
            self.assertIn("Nível Pleno", r.pontos_fortes)

    def test_nivel_fora_do_perfil_vira_lacuna(self):
        p = perfil(skills=[], termos=[], niveis=[Nivel.JUNIOR, Nivel.PLENO])
        for titulo in ("Analista Sênior", "Analista Senior", "Analista Sr."):
            r = compatibilidade(titulo, None, p)
            self.assertEqual(r.percentual, 0, titulo)
            self.assertEqual(r.lacunas, ["Nível Sênior fora do perfil (Júnior, Pleno)"])

    def test_pl_sql_nao_e_pleno(self):
        p = perfil(skills=[], termos=[], niveis=[Nivel.JUNIOR, Nivel.PLENO])
        r = compatibilidade("Analista PL/SQL Sênior", None, p)
        self.assertEqual(r.lacunas, ["Nível Sênior fora do perfil (Júnior, Pleno)"])
        self.assertNotIn("Nível Pleno", r.pontos_fortes)
        # Sem outro nível no título, nem entra na conta do nível.
        r = compatibilidade("Desenvolvedor PL/SQL", None, perfil(skills=[], termos=[], niveis=[Nivel.SENIOR]))
        self.assertEqual((r.percentual, r.lacunas, r.pontos_fortes), (0, [], []))
        self.assertEqual(compatibilidade("Dev PL SQL", None, p).lacunas, [])
        # "Pl" sozinho continua sendo Pleno.
        self.assertIn("Nível Pleno", compatibilidade("Analista Pl SQL Pl", None, p).pontos_fortes)

    def test_mid_market_nao_e_nivel_mas_mid_sozinho_e(self):
        p = perfil(skills=[], termos=[], niveis=[Nivel.SENIOR])
        for titulo in ("Mid-Market Analyst", "Mid Market Analyst"):
            r = compatibilidade(titulo, None, p)
            self.assertEqual((r.lacunas, r.pontos_fortes), ([], []), titulo)
        self.assertEqual(compatibilidade("Mid Data Analyst", None, p).lacunas, ["Nível Pleno fora do perfil (Sênior)"])

    def test_titulo_com_dois_niveis_basta_um(self):
        p = perfil(skills=[], termos=[], niveis=[Nivel.JUNIOR])
        self.assertEqual(compatibilidade("Analista Júnior/Pleno", None, p).percentual, 100)

    def test_titulo_sem_nivel_ou_perfil_sem_niveis_nao_pune(self):
        r = compatibilidade("Analista de Dados", None, perfil(niveis=[Nivel.SENIOR]))
        self.assertEqual(r.percentual, 100)
        self.assertEqual(compatibilidade("Analista Sênior de Dados", None, perfil(niveis=[])).percentual, 100)

    def test_titulo_pelos_termos_do_perfil(self):
        p = perfil(skills=[], niveis=[])
        self.assertEqual(compatibilidade("Analista de Dados", None, p).percentual, 100)
        self.assertEqual(compatibilidade("Data Analyst", None, p).percentual, 100)
        r = compatibilidade("Motorista", None, p)
        self.assertEqual(r.percentual, 0)
        self.assertIn("Título sem relação com os termos do perfil", r.lacunas)
        self.assertEqual(compatibilidade("Analista de Marketing", None, p).percentual, 50)

    def test_mistura_dos_pesos_com_requisitos(self):
        # requisitos 50 (nota 1), título 30 (nota 0), nível 20 (nota 1) -> 70.
        p = perfil(termos=["analista de dados"], skills=["SQL"], niveis=[Nivel.PLENO])
        r = compatibilidade("Motorista Pleno", [("SQL", True)], p)
        self.assertEqual(r.percentual, 70)


class SemRequisitosTest(unittest.TestCase):
    def test_none_e_lista_vazia_sao_parciais(self):
        for reqs in (None, []):
            r = compatibilidade("Analista de Dados Pleno", reqs, perfil())
            self.assertTrue(r.parcial)
            self.assertEqual(r.percentual, 100)

    def test_so_requisitos_invalidos_tambem_e_parcial(self):
        self.assertTrue(compatibilidade("Analista de Dados", [("", True), None], perfil()).parcial)

    def test_perfil_vazio_da_zero_sem_quebrar(self):
        r = compatibilidade("Analista", None, Perfil())
        self.assertEqual((r.percentual, r.pontos_fortes, r.lacunas, r.parcial), (0, [], [], True))

    def test_titulo_none(self):
        self.assertEqual(compatibilidade(None, None, perfil()).percentual, 0)


class ListasETest(unittest.TestCase):
    def test_listas_sem_repetidos_e_na_ordem_do_perfil(self):
        r = compatibilidade(
            "Analista de Dados SQL Pleno", [("SQL e Power BI", True), ("sql avançado", False), ("Rust", True), ("RUST", False)],
            perfil(skills=["Power BI", "SQL"]),
        )
        self.assertEqual(r.pontos_fortes, ["Power BI", "SQL", "Título casa com os termos do perfil", "Nível Pleno"])
        self.assertEqual(len(r.lacunas), len(set(r.lacunas)))

    def test_determinismo(self):
        args = ("Analista SQL", [("SQL", True), ("Go", False)], perfil())
        self.assertEqual(compatibilidade(*args), compatibilidade(*args))

    def test_percentual_inteiro_entre_0_e_100(self):
        r = compatibilidade("Analista", [("SQL", True), ("Go", False), ("Rust", False)], perfil())
        self.assertIsInstance(r.percentual, int)
        self.assertTrue(0 <= r.percentual <= 100)


class RequisitosDaVagaTest(unittest.TestCase):
    def test_objetos_com_texto_e_obrigatorio(self):
        # Objeto simples escrito à mão: imita o `Requisito` da frente do detalhe, que não existe aqui.
        Req = SimpleNamespace
        v = SimpleNamespace(titulo="Analista", requisitos=[Req(texto="SQL", obrigatorio=True), Req(texto="Go", obrigatorio=False)])
        self.assertEqual(requisitos_da_vaga(v), [("SQL", True), ("Go", False)])

    def test_item_sem_atributos_ou_com_tipo_errado_e_descartado(self):
        Req = SimpleNamespace
        v = SimpleNamespace(requisitos=[
            Req(texto="SQL", obrigatorio=True), Req(texto="Go"), Req(obrigatorio=True), Req(texto=1, obrigatorio=True),
            Req(texto="Rust", obrigatorio="sim"), {"texto": "X", "obrigatorio": True}, ("Y", True), None,
        ])
        self.assertEqual(requisitos_da_vaga(v), [("SQL", True)])
        self.assertIsNone(requisitos_da_vaga(SimpleNamespace(requisitos=[{"description": "SQL", "mandatory": True}])))

    def test_compatibilidade_da_vaga_usa_os_requisitos(self):
        Req = SimpleNamespace
        v = SimpleNamespace(titulo="x", requisitos=[Req(texto="SQL", obrigatorio=True), Req(texto="Go", obrigatorio=True)])
        r = compatibilidade_da_vaga(v, perfil(termos=[], niveis=[], skills=["SQL"]))
        self.assertFalse(r.parcial)
        self.assertEqual((r.percentual, r.lacunas), (50, ["Go (obrigatório)"]))

    def test_sem_atributo_ou_vazio(self):
        self.assertIsNone(requisitos_da_vaga(SimpleNamespace(titulo="x")))
        self.assertIsNone(requisitos_da_vaga(SimpleNamespace(titulo="x", requisitos=[])))
        self.assertIsNone(requisitos_da_vaga(SimpleNamespace(titulo="x", requisitos="SQL")))

    def test_sem_perfil_nao_calcula(self):
        self.assertIsNone(compatibilidade_da_vaga(SimpleNamespace(titulo="Analista"), None))


if __name__ == "__main__":
    unittest.main()
