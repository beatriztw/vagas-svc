import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.client.perfil_ia import (
    AgenteCLIRunner,
    ExtractorFixo,
    PerfilIAError,
    _runner,
    validar_json,
    validar_pdf,
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


def escrever_pdf(pdf: Path, texto: str) -> Path:
    """PDF válido de uma página, com xref, e o texto em Helvetica."""
    conteudo = f"BT /F1 12 Tf 72 720 Td ({texto}) Tj ET".encode("latin-1")
    objetos = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(conteudo) + conteudo + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    data = b"%PDF-1.4\n"
    offsets = []
    for numero, objeto in enumerate(objetos, start=1):
        offsets.append(len(data))
        data += b"%d 0 obj\n" % numero + objeto + b"\nendobj\n"
    xref = len(data)
    data += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objetos) + 1)
    data += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    data += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objetos) + 1, xref)
    pdf.write_bytes(data)
    return pdf


def criar_pdf(testcase: unittest.TestCase, texto: str = "Beatriz Souza - desenvolvedora em São Paulo") -> Path:
    """PDF real numa pasta temporária, apagada no fim do teste."""
    pasta = tempfile.TemporaryDirectory()
    testcase.addCleanup(pasta.cleanup)
    return escrever_pdf(Path(pasta.name) / "curriculo.pdf", texto)


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

        def runner(cmd: list[str], prompt: str, cwd: Path) -> str:
            calls.append((cmd, prompt))
            return envelope(json.dumps(BRUTO, ensure_ascii=False))

        pdf = criar_pdf(self)
        service = PerfilIAService(AgenteCLIRunner(runner=runner), PerfilRepositoryMemoria())
        sugestao = service.importar(pdf)

        self.assertEqual(sugestao.perfil.skills, ["python", "sql"])
        cmd, prompt = calls[0]
        self.assertEqual(cmd[:4], ["claude", "-p", "--output-format", "json"])
        self.assertIn("extrator de perfil", prompt)
        self.assertIn(str(Path(cmd[cmd.index("--add-dir") + 1]) / "curriculo.pdf"), prompt)
        self.assertNotIn(prompt, cmd)

    def test_comando_libera_o_read_so_para_a_copia_do_curriculo(self):
        # Confere os argumentos do comando; a aplicação da regra pelo claude -p real não é testada aqui.
        calls = []

        def runner(cmd: list[str], prompt: str, cwd: Path) -> str:
            calls.append((cmd, prompt))
            return envelope(json.dumps(BRUTO, ensure_ascii=False))

        AgenteCLIRunner(runner=runner).extrair(criar_pdf(self))

        cmd, prompt = calls[0]
        copia = Path(cmd[cmd.index("--add-dir") + 1]) / "curriculo.pdf"
        self.assertEqual(cmd[cmd.index("--tools") + 1], "Read")
        self.assertEqual(cmd[cmd.index("--allowedTools") + 1], f"Read(/{copia})")
        self.assertTrue(cmd[cmd.index("--allowedTools") + 1].startswith("Read(//"))
        self.assertNotIn("Read", cmd[cmd.index("--allowedTools") + 2 :])
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "dontAsk")
        # O texto do PDF não vai no prompt: quem lê o arquivo é o Claude Code.
        self.assertNotIn("desenvolvedora", prompt)

    def test_comando_aponta_so_para_a_copia_do_pdf_numa_pasta_isolada(self):
        vistos = {}

        def runner(cmd: list[str], prompt: str, cwd: Path) -> str:
            pasta = Path(cmd[cmd.index("--add-dir") + 1])
            vistos.update(pasta=pasta, cwd=cwd, prompt=prompt, arquivos=sorted(p.name for p in pasta.iterdir()))
            vistos["bytes"] = (pasta / "curriculo.pdf").read_bytes()
            return envelope(json.dumps(BRUTO, ensure_ascii=False))

        pdf = criar_pdf(self)
        segredo = pdf.parent / ".env"
        segredo.write_text("SENHA=nao-pode-vazar")
        AgenteCLIRunner(runner=runner).extrair(pdf)

        self.assertNotEqual(vistos["pasta"], pdf.resolve().parent)
        self.assertEqual(vistos["arquivos"], ["curriculo.pdf"])
        self.assertEqual(vistos["bytes"], pdf.read_bytes())
        self.assertEqual(vistos["cwd"], vistos["pasta"])
        self.assertIn(f"arquivo {vistos['pasta'] / 'curriculo.pdf'}.", vistos["prompt"])
        self.assertNotIn(str(pdf.parent), vistos["prompt"])
        # A cópia some depois da chamada.
        self.assertFalse(vistos["pasta"].exists())

    def test_caminho_relativo_e_aceito(self):
        calls = []

        def runner(cmd: list[str], prompt: str, cwd: Path) -> str:
            calls.append((cmd, (Path(cmd[cmd.index("--add-dir") + 1]) / "curriculo.pdf").read_bytes()))
            return envelope(json.dumps(BRUTO, ensure_ascii=False))

        pdf = criar_pdf(self)
        relativo = Path(os.path.relpath(pdf, Path.cwd()))
        self.assertFalse(relativo.is_absolute())
        AgenteCLIRunner(runner=runner).extrair(relativo)

        cmd, copia = calls[0]
        self.assertTrue(Path(cmd[cmd.index("--add-dir") + 1]).is_absolute())
        self.assertEqual(copia, pdf.read_bytes())

    def test_resposta_em_cerca_markdown_e_aceita(self):
        resposta = "Aqui está o perfil:\n```json\n" + json.dumps(BRUTO, ensure_ascii=False) + "\n```\nQualquer dúvida, avise."
        runner = AgenteCLIRunner(runner=lambda cmd, prompt, cwd: envelope(resposta))

        sugestao = PerfilIAService(runner, PerfilRepositoryMemoria()).importar(criar_pdf(self))

        self.assertEqual(sugestao.perfil.localidades, ["São Paulo, SP"])
        self.assertEqual(sugestao.perfil.niveis, [Nivel.PLENO])

    def test_arquivo_que_nao_e_pdf_nao_chega_ao_modelo(self):
        calls = []
        pasta = tempfile.TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        falso = Path(pasta.name) / "curriculo.pdf"
        falso.write_text("não sou um PDF")

        with self.assertRaises(PerfilIAError):
            AgenteCLIRunner(runner=lambda cmd, prompt, cwd: calls.append(cmd)).extrair(falso)
        self.assertEqual(calls, [])

    def test_saida_json_que_nao_e_objeto_vira_erro_de_resposta(self):
        runner = AgenteCLIRunner(runner=lambda cmd, prompt, cwd: "[1, 2]")

        with self.assertRaises(PerfilIAError):
            runner.extrair(criar_pdf(self))

    def _importar_com_saida(self, saida: dict) -> list:
        calls = []

        def runner(cmd: list[str], prompt: str, cwd: Path) -> str:
            calls.append(cmd)
            return json.dumps(saida, ensure_ascii=False)

        service = PerfilIAService(AgenteCLIRunner(runner=runner), PerfilRepositoryMemoria())
        with self.assertRaisesRegex(PerfilIAError, "não respondeu"):
            service.importar(criar_pdf(self))
        return calls

    def test_claude_p_recusa_sem_login_de_assinatura_com_api_key(self):
        # Sem login de assinatura, o claude -p responde com is_error e a mensagem em result.
        calls = self._importar_com_saida(
            {"type": "result", "subtype": "success", "is_error": True, "result": "Invalid API key · Please run /login"}
        )
        self.assertEqual(len(calls), 1)

    def test_claude_p_que_nao_termina_com_sucesso(self):
        self._importar_com_saida({"type": "result", "subtype": "error_during_execution", "is_error": False})

    def test_result_com_objeto_de_erro(self):
        self._importar_com_saida(
            {"type": "result", "subtype": "success", "result": {"error": "authentication", "message": "login required"}}
        )


class ValidarPdfTest(unittest.TestCase):
    def setUp(self):
        pasta = tempfile.TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        self.pasta = Path(pasta.name)

    def test_pdf_valido_devolve_caminho_absoluto(self):
        pdf = escrever_pdf(self.pasta / "curriculo.pdf", "Matheus Toledo - engenheiro de software")

        self.assertEqual(validar_pdf(pdf), pdf.resolve())
        self.assertTrue(validar_pdf(pdf).is_absolute())

    def test_arquivo_que_nao_e_pdf(self):
        falso = self.pasta / "nao-e-pdf.txt"
        falso.write_text("texto comum")

        with self.assertRaisesRegex(PerfilIAError, "não é um PDF"):
            validar_pdf(falso)

    def test_arquivo_inexistente(self):
        with self.assertRaisesRegex(PerfilIAError, "não deu para ler"):
            validar_pdf(self.pasta / "sumiu.pdf")

    def test_pdf_maior_que_o_limite(self):
        pdf = escrever_pdf(self.pasta / "grande.pdf", "x" * 2048)

        with mock.patch("src.client.perfil_ia.MAX_PDF_BYTES", 1024):
            with self.assertRaisesRegex(PerfilIAError, "maior que"):
                validar_pdf(pdf)

    def test_pdf_grande_nao_chega_ao_modelo(self):
        calls = []
        pdf = escrever_pdf(self.pasta / "grande.pdf", "x" * 2048)

        with mock.patch("src.client.perfil_ia.MAX_PDF_BYTES", 1024):
            with self.assertRaises(PerfilIAError):
                AgenteCLIRunner(runner=lambda cmd, prompt, cwd: calls.append(cmd)).extrair(pdf)
        self.assertEqual(calls, [])


class ValidarJsonTest(unittest.TestCase):
    def test_json_puro(self):
        self.assertEqual(validar_json(json.dumps(BRUTO)).perfil.termos, ["python", "backend"])

    def test_cerca_markdown_json(self):
        texto = "```json\n" + json.dumps(BRUTO, ensure_ascii=False) + "\n```"
        self.assertEqual(validar_json(texto).perfil.skills, ["python", "sql"])

    def test_cerca_markdown_sem_linguagem(self):
        texto = "```\n" + json.dumps(BRUTO, ensure_ascii=False) + "\n```"
        self.assertEqual(validar_json(texto).perfil.niveis, [Nivel.PLENO])

    def test_frase_antes_e_depois(self):
        texto = "Claro! Segue o perfil: " + json.dumps(BRUTO, ensure_ascii=False) + " Espero ter ajudado {:)}"
        self.assertEqual(validar_json(texto).perfil.excluir, ["estágio"])

    def test_chave_em_frase_antes_do_json(self):
        texto = "Segue o perfil {versão 2}: " + json.dumps(BRUTO, ensure_ascii=False)
        self.assertEqual(validar_json(texto).perfil.termos, ["python", "backend"])

    def test_primeira_cerca_sem_o_json(self):
        texto = "Comando usado:\n```\nclaude -p\n```\nResultado:\n```json\n" + json.dumps(BRUTO) + "\n```"
        self.assertEqual(validar_json(texto).perfil.skills, ["python", "sql"])

    def test_primeira_cerca_com_chaves_que_nao_sao_json(self):
        texto = "```\nfor x in {a, b}\n```\n```json\n" + json.dumps(BRUTO) + "\n```"
        self.assertEqual(validar_json(texto).perfil.niveis, [Nivel.PLENO])

    def test_objeto_sem_campos_do_perfil_antes_do_perfil(self):
        texto = 'Li o arquivo {"arquivo": "curriculo.pdf"} e segue: ' + json.dumps(BRUTO, ensure_ascii=False)
        self.assertEqual(validar_json(texto).perfil.termos, ["python", "backend"])

    def test_perfil_embrulhado_em_outro_objeto(self):
        texto = json.dumps({"perfil": BRUTO}, ensure_ascii=False)
        self.assertEqual(validar_json(texto).perfil.skills, ["python", "sql"])

    def test_so_objeto_sem_campos_do_perfil_vira_erro(self):
        with self.assertRaises(PerfilIAError):
            validar_json('{"arquivo": "curriculo.pdf", "status": "ok"}')

    def test_resposta_sem_objeto_json(self):
        with self.assertRaisesRegex(PerfilIAError, "não tem objeto JSON"):
            validar_json("Claro! Aqui está o perfil...")

    def test_objeto_json_quebrado(self):
        with self.assertRaisesRegex(PerfilIAError, "não é JSON"):
            validar_json('Segue: {"termos": ["python"')


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

    def test_perfil_invalido_nao_e_gravado(self):
        with self.assertRaises(ValueError):
            self.repo.salvar(Perfil(modalidades=["Presencial"]))
        with self.assertRaises(ValueError):
            self.repo.salvar(Perfil(publicada_desde_dias=0))

        self.assertIsNone(self.repo.obter())

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
