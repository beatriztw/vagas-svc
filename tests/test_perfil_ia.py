import json
import sqlite3
import tempfile
import unittest
import zlib
from pathlib import Path

from src.client.perfil_ia import (
    AgenteCLIRunner,
    ExtractorFixo,
    PerfilIAError,
    extrair_texto,
    validar_json,
)
from src.model.perfil import Perfil
from src.model.vaga import Modalidade, Nivel
from src.service.perfil_service import PerfilIAService, PerfilRepositoryMemoria, PerfilRepositorySQLite

BRUTO = {
    "termos": ["python", "backend"],
    "excluir": ["estágio"],
    "modalidades": ["Remoto"],
    "localidades": ["São Paulo, SP"],
    "plataformas": [],
    "niveis": ["Pleno"],
    "skills": ["python", "sql"],
    "justificativas": {"termos": "cargos atuais e anteriores", "niveis": "4 anos de experiência"},
}


def envelope(resposta: str) -> str:
    return json.dumps({"type": "result", "subtype": "success", "result": resposta}, ensure_ascii=False)


class PerfilIAServiceTest(unittest.TestCase):
    def test_importar_devolve_sugestao_sem_salvar(self):
        repo = PerfilRepositoryMemoria()
        service = PerfilIAService(ExtractorFixo(BRUTO), repo)

        sugestao = service.importar(Path("curriculo.pdf"))

        self.assertEqual(sugestao.perfil.termos, ["python", "backend"])
        self.assertEqual(sugestao.perfil.niveis, [Nivel.PLENO])
        self.assertEqual(sugestao.perfil.modalidades, [Modalidade.REMOTO])
        self.assertEqual(sugestao.justificativas["niveis"], "4 anos de experiência")
        self.assertIsNone(repo.obter())

    def test_salvar_so_depois_da_revisao(self):
        repo = PerfilRepositoryMemoria()
        service = PerfilIAService(ExtractorFixo(BRUTO), repo)

        sugestao = service.importar(Path("curriculo.pdf"))
        service.salvar(sugestao.perfil)

        self.assertEqual(repo.obter().termos, ["python", "backend"])

    def test_valor_invalido_vira_erro_de_resposta(self):
        service = PerfilIAService(ExtractorFixo({**BRUTO, "niveis": ["Diretor"]}), PerfilRepositoryMemoria())

        with self.assertRaises(PerfilIAError):
            service.importar(Path("curriculo.pdf"))

    def test_runner_falso_nao_usa_rede(self):
        calls = []

        def runner(cmd: list[str]) -> str:
            calls.append(cmd)
            return envelope(json.dumps(BRUTO, ensure_ascii=False))

        service = PerfilIAService(AgenteCLIRunner(runner=runner), PerfilRepositoryMemoria())
        sugestao = service.importar(Path(tempfile.gettempdir()) / "curriculo-ausente.pdf")

        self.assertEqual(sugestao.perfil.skills, ["python", "sql"])
        self.assertEqual(calls[0][:4], ["claude", "-p", "--output-format", "json"])
        self.assertIn("extrator de perfil", calls[0][-1])

    def test_claude_p_recusa_sem_login_de_assinatura_com_api_key(self):
        def runner(cmd: list[str]) -> str:
            return envelope(json.dumps({"error": "authentication", "message": "login required"}))

        service = PerfilIAService(AgenteCLIRunner(runner=runner), PerfilRepositoryMemoria())
        with self.assertRaises(PerfilIAError):
            service.importar(Path("curriculo.pdf"))


class ExtrairTextoTest(unittest.TestCase):
    def test_pdf_com_texto_legivel(self):
        pdf = Path(tempfile.gettempdir()) / "curriculo_teste.pdf"
        stream = zlib.compress(b"BT /F1 12 Tf (Matheus Toledo - engenheiro de software, python, sql) Tj ET")
        pdf.write_bytes(b"%PDF-1.4\n1 0 obj\nstream\n" + stream + b"\nendstream\n")
        try:
            texto = extrair_texto(pdf)
            self.assertIn("python", texto)
        finally:
            pdf.unlink()

    def test_arquivo_que_nao_e_pdf(self):
        with self.assertRaises(PerfilIAError):
            extrair_texto(Path("nao-e-pdf.txt"))

    def test_resposta_fora_de_json(self):
        with self.assertRaises(PerfilIAError):
            validar_json("Claro! Aqui está o perfil...")


class PerfilRepositorySQLiteTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE perfil (id INTEGER PRIMARY KEY AUTOINCREMENT, dados TEXT NOT NULL)")
        self.repo = PerfilRepositorySQLite(self.conn)

    def test_salvar_e_ler_depois_da_revisao(self):
        perfil = Perfil(termos=["python"], niveis=[Nivel.PLENO], modalidades=[Modalidade.HIBRIDO])
        self.repo.salvar(perfil)

        lido = self.repo.obter()
        self.assertEqual(lido.termos, ["python"])
        self.assertEqual(lido.niveis, [Nivel.PLENO])
        self.assertEqual(lido.modalidades, [Modalidade.HIBRIDO])

    def test_sem_perfil_salvo_devolve_none(self):
        self.assertIsNone(self.repo.obter())


if __name__ == "__main__":
    unittest.main()
