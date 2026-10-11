import json
import re
import shutil
import subprocess
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path

from src.model.perfil import Perfil, SugestaoPerfil
from src.model.vaga import Modalidade, Nivel

CAMPOS = ("termos", "excluir", "modalidades", "localidades", "plataformas", "niveis", "skills")
VALIDOS = {
    "termos": str,
    "excluir": str,
    "modalidades": Modalidade,
    "localidades": str,
    "plataformas": str,
    "niveis": Nivel,
    "skills": str,
}
MAX_PDF_BYTES = 32 * 1024 * 1024
# Cerca markdown em volta da resposta: ```json ... ``` ou só ``` ... ```.
CERCA = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)

INSTRUCAO = """Você é um extrator de perfil de currículo. Leia o PDF e devolve APENAS um JSON,
sem texto antes ou depois, no formato:

{
  "termos": ["python", "backend"],
  "excluir": ["estágio"],
  "modalidades": [],
  "localidades": ["São Paulo, SP"],
  "plataformas": [],
  "niveis": ["Pleno"],
  "skills": ["python", "sql", "aws"],
  "justificativas": {"termos": "cargos atuais e anteriores", "niveis": "4 anos de experiência"}
}

Regras: termos são cargos atuais e anteriores normalizados; niveis é deduzido dos anos de
experiência e responsabilidades; skills são tecnologias citadas, as mais recentes primeiro;
excluir são sugestões óbvias; localidades é a cidade do currículo, se houver; modalidades fica
vazio. Use exatamente os valores dos enums: modalidades Remoto/Híbrido e niveis
Júnior/Pleno/Sênior. justificativas tem uma frase por campo preenchido."""


class PerfilIAError(Exception):
    """A leitura do currículo falhou: arquivo inválido, modelo sem resposta, ou JSON inválido."""


def validar_bruto(bruto: dict) -> SugestaoPerfil:
    if "error" in bruto:
        raise PerfilIAError(f"claude -p não respondeu: {bruto}")
    perfil = Perfil()
    for campo in CAMPOS:
        valores = bruto.get(campo, [])
        if not isinstance(valores, list):
            raise PerfilIAError(f"campo {campo} não é uma lista")
        tipo = VALIDOS[campo]
        converted = []
        for valor in valores:
            if isinstance(valor, tipo):
                converted.append(valor)
            elif isinstance(valor, str):
                try:
                    converted.append(tipo(valor))
                except ValueError:
                    try:
                        converted.append(tipo[valor])
                    except KeyError as exc:
                        raise PerfilIAError(f"{campo} tem valor inválido: {valor}") from exc
            else:
                raise PerfilIAError(f"{campo} tem valor inválido: {valor}")
        setattr(perfil, campo, converted)

    justificativas = bruto.get("justificativas", {})
    if not isinstance(justificativas, dict):
        raise PerfilIAError("justificativas não é um objeto")
    return SugestaoPerfil(perfil=perfil, justificativas={k: str(v) for k, v in justificativas.items()})


def extrair_objeto_json(texto: str) -> dict:
    """Primeiro objeto JSON da resposta, tirando cerca markdown e frase antes ou depois.

    Tenta o conteúdo de cada cerca e depois o texto inteiro, e em cada um cada "{" até um
    raw_decode dar certo: chave numa frase antes do JSON não pode derrubar a importação.
    """
    candidatos = [cerca.group(1) for cerca in CERCA.finditer(texto)] + [texto]
    viu_chave = False
    for candidato in candidatos:
        inicio = candidato.find("{")
        while inicio >= 0:
            viu_chave = True
            try:
                bruto, _ = json.JSONDecoder().raw_decode(candidato, inicio)
            except json.JSONDecodeError:
                bruto = None
            # Só vale um objeto com algum campo do perfil: um {"arquivo": ...} antes, ou um
            # {"perfil": {...}} em volta, viraria um Perfil vazio aceito sem erro. O "{" de
            # dentro do embrulho é tentado na próxima volta.
            if isinstance(bruto, dict) and any(campo in bruto for campo in CAMPOS):
                return bruto
            inicio = candidato.find("{", inicio + 1)
    if not viu_chave:
        raise PerfilIAError("resposta não tem objeto JSON")
    raise PerfilIAError("resposta não é JSON com os campos do perfil")


def validar_json(texto: str) -> SugestaoPerfil:
    return validar_bruto(extrair_objeto_json(texto))


def validar_pdf(pdf: Path) -> Path:
    """Confere que o arquivo existe, é legível, é PDF e tem até MAX_PDF_BYTES. Devolve o caminho absoluto."""
    try:
        caminho = pdf.resolve()
        tamanho = caminho.stat().st_size
        with caminho.open("rb") as arquivo:
            cabecalho = arquivo.read(4)
    except OSError as exc:
        raise PerfilIAError(f"não deu para ler o PDF: {exc}") from exc
    if tamanho > MAX_PDF_BYTES:
        raise PerfilIAError(f"PDF maior que {MAX_PDF_BYTES // (1024 * 1024)} MB")
    if cabecalho != b"%PDF":
        raise PerfilIAError("o arquivo não é um PDF")
    return caminho


Extractor = Callable[[Path], dict]


class PerfilExtractor(ABC):
    nome: str

    @abstractmethod
    def extrair(self, pdf: Path) -> dict: ...


def regra_de_leitura(arquivo: Path) -> str:
    """Regra de permissão que libera o Read só para este arquivo, pelo caminho absoluto."""
    return f"Read(/{arquivo.resolve()})"


def _modelo() -> str:
    import os

    return os.environ.get("CLAUDE_PI_MODEL", "claude-sonnet-5-5")


def _runner(cmd: list[str], prompt: str, cwd: Path | None = None) -> str:
    if shutil.which(cmd[0]) is None:
        raise PerfilIAError(f"{cmd[0]} não está instalado no ambiente")
    try:
        # Prompt por stdin: como argumento, o Linux limita cada um a ~128 KB.
        result = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=180, cwd=cwd)
    except subprocess.TimeoutExpired as exc:
        raise PerfilIAError(f"claude -p passou de {exc.timeout:.0f}s sem responder") from exc
    except OSError as exc:
        raise PerfilIAError(f"não deu para rodar o claude -p: {exc}") from exc
    if result.returncode != 0:
        raise PerfilIAError(f"claude -p saiu com {result.returncode}: {result.stderr.strip()[:300]}")
    return result.stdout


class AgenteCLIRunner(PerfilExtractor):
    """Lê o currículo com o Claude Code em print mode, usando o login de assinatura.

    Não usa API key: `claude -p` aceita o login da assinatura (claude auth login) ou o
    CLAUDE_CODE_OAUTH_TOKEN de `claude setup-token`. Roda dentro do sandbox claude-pi,
    onde o login já está feito.
    """

    nome = "claude-code"

    def __init__(self, binary: str = "claude", runner: Callable[[list[str], str, Path], str] = _runner):
        self._binary = binary
        self._runner = runner

    def extrair(self, pdf: Path) -> dict:
        # Valida o arquivo (existe, é PDF, até 32 MB) antes de gastar uma chamada ao modelo.
        original = validar_pdf(pdf)
        # O PDF é conteúdo não confiável (pode trazer prompt injection). O claude -p só enxerga
        # uma pasta temporária com a cópia do currículo, que também é o diretório de trabalho:
        # nem os vizinhos do PDF (ex.: o Downloads inteiro) nem o repositório ficam ao alcance.
        with tempfile.TemporaryDirectory(prefix="perfil-ia-") as pasta:
            isolada = Path(pasta).resolve()
            curriculo = isolada / "curriculo.pdf"
            try:
                shutil.copyfile(original, curriculo)
            except OSError as exc:
                raise PerfilIAError(f"não deu para copiar o PDF: {exc}") from exc
            prompt = f"{INSTRUCAO}\n\nO currículo é o arquivo {curriculo}. Leia esse arquivo com a ferramenta Read."
            # Só a ferramenta Read existe. A regra de allow vale só para a cópia do currículo
            # ("//" é caminho absoluto na sintaxe de permissão do Claude Code): um "Read" sem
            # escopo liberaria qualquer arquivo da máquina. Com dontAsk, o que a regra não cobre
            # é negado sem pergunta.
            cmd = [
                self._binary,
                "-p",
                "--output-format",
                "json",
                "--model",
                _modelo(),
                "--tools",
                "Read",
                "--allowedTools",
                regra_de_leitura(curriculo),
                "--permission-mode",
                "dontAsk",
                "--add-dir",
                str(isolada),
            ]
            out = self._runner(cmd, prompt, isolada)
        try:
            envelope = json.loads(out)
        except json.JSONDecodeError as exc:
            raise PerfilIAError(f"saída do claude -p não é JSON: {exc}") from exc
        if not isinstance(envelope, dict):
            raise PerfilIAError("saída do claude -p não é um objeto JSON")
        resposta = envelope.get("result") or envelope.get("text") or ""
        if not envelope.get("is_error", False) and envelope.get("subtype") == "success":
            if isinstance(resposta, dict) and "error" in resposta:
                raise PerfilIAError(f"claude -p não respondeu: {resposta}")
            return validar_bruto(resposta) if isinstance(resposta, dict) else validar_json(resposta)
        raise PerfilIAError(f"claude -p não respondeu: {resposta or envelope}")


class ExtractorFixo(PerfilExtractor):
    """Extractor de teste: devolve o bruto já validado, sem rede e sem modelo."""

    def __init__(self, bruto: dict, nome: str = "fixo"):
        self._bruto = bruto
        self.nome = nome

    def extrair(self, pdf: Path) -> dict:
        return {campo: self._bruto.get(campo, []) for campo in CAMPOS} | {
            "justificativas": self._bruto.get("justificativas", {})
        }
