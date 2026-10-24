import os
import tempfile
import unittest

from src.model.curriculo import Curriculo
from src.model.perfil import Perfil
from src.model.vaga import Modalidade, Nivel
from src.repository.base import CurriculoRepositoryMemoria, PerfilBuscaRepositoryMemoria
from src.repository.conexao import parametros
from src.repository.postgres import linha_para_perfil, perfil_para_linha


def perfil_exemplo(**campos) -> Perfil:
    dados = dict(
        termos=["python", "dados"],
        excluir=["estágio"],
        modalidades=[Modalidade.REMOTO, Modalidade.HIBRIDO],
        localidades=["São Paulo"],
        plataformas=["greenhouse"],
        niveis=[Nivel.PLENO, Nivel.SENIOR],
        skills=["sql"],
        publicada_desde_dias=7,
        ocultar_candidatadas=False,
    )
    dados.update(campos)
    return Perfil(**dados)


class ConversaoPerfilTest(unittest.TestCase):
    def test_enums_viram_value_na_linha(self):
        linha = perfil_para_linha(perfil_exemplo())
        self.assertEqual(linha["modalidades"], ["Remoto", "Híbrido"])
        self.assertEqual(linha["niveis"], ["Pleno", "Sênior"])
        self.assertEqual(type(linha["modalidades"][0]), str)
        self.assertEqual(type(linha["niveis"][0]), str)
        self.assertEqual(linha["publicada_desde_dias"], 7)
        self.assertIs(linha["ocultar_candidatadas"], False)

    def test_ida_e_volta_devolve_enums(self):
        perfil = perfil_exemplo()
        linha = dict(perfil_para_linha(perfil), id=1, atualizado_em=None)
        volta = linha_para_perfil(linha)
        self.assertEqual(volta, perfil)
        self.assertIsInstance(volta.modalidades[0], Modalidade)
        self.assertIsInstance(volta.niveis[0], Nivel)

    def test_string_valida_e_aceita(self):
        linha = perfil_para_linha(perfil_exemplo(modalidades=["Remoto"], niveis=["Júnior"]))
        self.assertEqual(linha["modalidades"], ["Remoto"])
        self.assertEqual(linha["niveis"], ["Júnior"])

    def test_valor_fora_do_enum_e_recusado(self):
        with self.assertRaises(ValueError):
            perfil_para_linha(perfil_exemplo(modalidades=["Presencial"]))
        with self.assertRaises(ValueError):
            perfil_para_linha(perfil_exemplo(niveis=["Estagiário"]))


class PerfilBuscaMemoriaTest(unittest.TestCase):
    def test_vazio_devolve_none(self):
        self.assertIsNone(PerfilBuscaRepositoryMemoria(1).obter())

    def test_salvar_duas_vezes_fica_o_ultimo(self):
        repo = PerfilBuscaRepositoryMemoria(1)
        repo.salvar(perfil_exemplo())
        repo.salvar(perfil_exemplo(termos=["go"], niveis=["Júnior"]))
        perfil = repo.obter()
        self.assertEqual(perfil.termos, ["go"])
        self.assertEqual(perfil.niveis, [Nivel.JUNIOR])

    def test_alterar_objeto_depois_de_salvar_nao_muda_o_gravado(self):
        repo = PerfilBuscaRepositoryMemoria(1)
        perfil = perfil_exemplo()
        repo.salvar(perfil)
        perfil.termos.append("java")
        self.assertEqual(repo.obter().termos, ["python", "dados"])

    def test_recusa_enum_invalido_e_dias_nao_positivos(self):
        repo = PerfilBuscaRepositoryMemoria(1)
        with self.assertRaises(ValueError):
            repo.salvar(perfil_exemplo(modalidades=["Presencial"]))
        with self.assertRaises(ValueError):
            repo.salvar(perfil_exemplo(publicada_desde_dias=0))
        self.assertIsNone(repo.obter())


class CurriculoMemoriaTest(unittest.TestCase):
    def test_salvar_preenche_id_e_criado_em(self):
        salvo = CurriculoRepositoryMemoria(1).salvar(Curriculo("base", "Base", "# texto"))
        self.assertIsNotNone(salvo.id)
        self.assertIsNotNone(salvo.criado_em)

    def test_base_vigente_e_a_mais_recente_e_historico_fica(self):
        repo = CurriculoRepositoryMemoria(1)
        primeira = repo.salvar(Curriculo("base", "v1", "# v1"))
        segunda = repo.salvar(Curriculo("base", "v2", "# v2"))
        self.assertEqual(repo.obter_base().id, segunda.id)
        self.assertEqual(repo.obter(primeira.id).titulo, "v1")

    def test_sem_base_devolve_none(self):
        self.assertIsNone(CurriculoRepositoryMemoria(1).obter_base())

    def test_adaptado_exige_base_existente(self):
        repo = CurriculoRepositoryMemoria(1)
        with self.assertRaises(ValueError):
            repo.salvar(Curriculo("adaptado", "Vaga X", "# x", vaga_url="https://exemplo.com/1"))
        with self.assertRaises(ValueError):
            repo.salvar(Curriculo("adaptado", "Vaga X", "# x", base_id=99))

    def test_adaptado_nao_pode_derivar_de_outro_adaptado(self):
        repo = CurriculoRepositoryMemoria(1)
        base = repo.salvar(Curriculo("base", "Base", "# b"))
        adaptado = repo.salvar(Curriculo("adaptado", "Vaga X", "# x", base_id=base.id))

        with self.assertRaisesRegex(ValueError, "adaptado"):
            repo.salvar(Curriculo("adaptado", "Vaga Y", "# y", base_id=adaptado.id))

    def test_base_nao_aceita_base_id_e_tipo_invalido(self):
        repo = CurriculoRepositoryMemoria(1)
        base = repo.salvar(Curriculo("base", "Base", "# b"))
        with self.assertRaises(ValueError):
            repo.salvar(Curriculo("base", "Outra", "# o", base_id=base.id))
        with self.assertRaises(ValueError):
            repo.salvar(Curriculo("rascunho", "R", "# r"))

    def test_listar_adaptados_so_traz_adaptados_mais_recente_primeiro(self):
        repo = CurriculoRepositoryMemoria(1)
        base = repo.salvar(Curriculo("base", "Base", "# b"))
        a1 = repo.salvar(Curriculo("adaptado", "A1", "# a1", base_id=base.id))
        a2 = repo.salvar(Curriculo("adaptado", "A2", "# a2", base_id=base.id))
        self.assertEqual([c.id for c in repo.listar_adaptados()], [a2.id, a1.id])

    def test_obter_inexistente_devolve_none(self):
        self.assertIsNone(CurriculoRepositoryMemoria(1).obter(1))


class ParametrosConexaoTest(unittest.TestCase):
    def test_le_variaveis_e_porta_padrao(self):
        p = parametros({"PGHOST": "h", "PGDATABASE": "d", "PGUSER": "u", "PGPASSWORD": "s"})
        self.assertEqual(p, {"host": "h", "port": 5432, "dbname": "d", "user": "u", "password": "s"})

    def test_senha_do_arquivo(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("segredo\n")
        self.addCleanup(os.remove, f.name)
        p = parametros({"PGHOST": "h", "PGPORT": "6543", "PGPASSWORD_FILE": f.name})
        self.assertEqual(p["password"], "segredo")
        self.assertEqual(p["port"], 6543)

    def test_pgpassword_tem_precedencia_sobre_arquivo(self):
        p = parametros({"PGPASSWORD": "env", "PGPASSWORD_FILE": "/nao/existe"})
        self.assertEqual(p["password"], "env")


if __name__ == "__main__":
    unittest.main()
