import json
import sqlite3
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

from src.client.perfil_ia import (
    AgenteCLIRunner,
    ExtractorFixo,
    PerfilIAError,
    _runner,
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


def criar_pdf(testcase: unittest.TestCase) -> Path:
    """PDF mínimo com texto legível, apagado no fim do teste."""
    pasta = tempfile.TemporaryDirectory()
    testcase.addCleanup(pasta.cleanup)
    pdf = Path(pasta.name) / "curriculo.pdf"
    stream = zlib.compress(b"BT /F1 12 Tf (Beatriz Souza - desenvolvedora, python, sql) Tj ET")
    pdf.write_bytes(b"%PDF-1.4\n1 0 obj\nstream\n" + stream + b"\nendstream\n")
    return pdf


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

        def runner(cmd: list[str], prompt: str) -> str:
            calls.append((cmd, prompt))
            return envelope(json.dumps(BRUTO, ensure_ascii=False))

        service = PerfilIAService(AgenteCLIRunner(runner=runner), PerfilRepositoryMemoria())
        sugestao = service.importar(criar_pdf(self))

        self.assertEqual(sugestao.perfil.skills, ["python", "sql"])
        cmd, prompt = calls[0]
        self.assertEqual(cmd[:4], ["claude", "-p", "--output-format", "json"])
        self.assertIn("extrator de perfil", prompt)
        self.assertIn("desenvolvedora, python, sql", prompt)
        self.assertNotIn(prompt, cmd)

    def test_arquivo_que_nao_e_pdf_nao_chega_ao_modelo(self):
        calls = []
        pasta = tempfile.TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        falso = Path(pasta.name) / "curriculo.pdf"
        falso.write_text("não sou um PDF")

        with self.assertRaises(PerfilIAError):
            AgenteCLIRunner(runner=lambda cmd, prompt: calls.append(cmd)).extrair(falso)
        self.assertEqual(calls, [])

    def test_saida_json_que_nao_e_objeto_vira_erro_de_resposta(self):
        runner = AgenteCLIRunner(runner=lambda cmd, prompt: "[1, 2]")

        with self.assertRaises(PerfilIAError):
            runner.extrair(criar_pdf(self))

    def test_claude_p_recusa_sem_login_de_assinatura_com_api_key(self):
        calls = []

        def runner(cmd: list[str], prompt: str) -> str:
            calls.append(cmd)
            return envelope(json.dumps({"error": "authentication", "message": "login required"}))

        service = PerfilIAService(AgenteCLIRunner(runner=runner), PerfilRepositoryMemoria())
        with self.assertRaises(PerfilIAError):
            service.importar(criar_pdf(self))
        self.assertEqual(len(calls), 1)


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
        pasta = tempfile.TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        falso = Path(pasta.name) / "nao-e-pdf.txt"
        falso.write_text("texto comum")

        with self.assertRaisesRegex(PerfilIAError, "não é um PDF"):
            extrair_texto(falso)

    def test_stream_comprimido_nao_estoura_a_memoria(self):
        pasta = tempfile.TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        bomba = Path(pasta.name) / "bomba.pdf"
        # 64 MB de "A" comprimidos viram ~64 KB; sem limite, o decompress devolveria tudo.
        stream = zlib.compress(b"A" * (64 * 1024 * 1024), 9)
        bomba.write_bytes(b"%PDF-1.4\n1 0 obj\nstream\n" + stream + b"\nendstream\n")

        with mock.patch("src.client.perfil_ia.MAX_PDF_BYTES", 1024 * 1024):
            texto = extrair_texto(bomba)
        self.assertLessEqual(len(texto), 100_000)

    def test_trecho_fora_de_stream_nao_entra_no_texto(self):
        pasta = tempfile.TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        pdf = Path(pasta.name) / "curriculo.pdf"
        dentro = zlib.compress(b"BT (Beatriz Souza - desenvolvedora python e sql ha quatro anos) Tj ET")
        fora = b"<< /Producer (METADADO-FORA-DO-STREAM-QUE-NAO-E-TEXTO-DO-CURRICULO) >>"
        pdf.write_bytes(b"%PDF-1.4\n1 0 obj\nstream\n" + dentro + b"\nendstream\n" + fora + b"\n")

        texto = extrair_texto(pdf)
        self.assertIn("desenvolvedora python", texto)
        self.assertNotIn("METADADO", texto)

    def test_resposta_fora_de_json(self):
        with self.assertRaises(PerfilIAError):
            validar_json("Claro! Aqui está o perfil...")


class RunnerTest(unittest.TestCase):
    def setUp(self):
        which = mock.patch("src.client.perfil_ia.shutil.which", return_value="/usr/local/bin/claude")
        which.start()
        self.addCleanup(which.stop)

    def test_timeout_vira_erro_de_resposta(self):
        with mock.patch("src.client.perfil_ia.subprocess.run", side_effect=subprocess.TimeoutExpired("claude", 180)):
            with self.assertRaises(PerfilIAError):
                _runner(["claude", "-p"], "prompt")

    def test_falha_ao_executar_vira_erro_de_resposta(self):
        with mock.patch("src.client.perfil_ia.subprocess.run", side_effect=PermissionError("sem permissão")):
            with self.assertRaises(PerfilIAError):
                _runner(["claude", "-p"], "prompt")

    def test_claude_ausente_vira_erro_de_resposta(self):
        with mock.patch("src.client.perfil_ia.shutil.which", return_value=None):
            with self.assertRaises(PerfilIAError):
                _runner(["claude", "-p"], "prompt")


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

    def test_filtros_escalares_sobrevivem_a_gravacao(self):
        self.repo.salvar(Perfil(termos=["python"], ocultar_candidatadas=False, publicada_desde_dias=7))

        lido = self.repo.obter()
        self.assertFalse(lido.ocultar_candidatadas)
        self.assertEqual(lido.publicada_desde_dias, 7)


if __name__ == "__main__":
    unittest.main()
