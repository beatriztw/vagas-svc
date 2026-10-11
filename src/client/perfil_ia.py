import json
import shutil
import subprocess
import zlib
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


def validar_json(texto: str) -> SugestaoPerfil:
    try:
        bruto = json.loads(texto)
    except json.JSONDecodeError as exc:
        raise PerfilIAError(f"resposta não é JSON: {exc}") from exc
    if not isinstance(bruto, dict):
        raise PerfilIAError("resposta não é um objeto JSON")
    return validar_bruto(bruto)


def extrair_texto(pdf: Path) -> str:
    """Lê o texto do PDF sem bibliotecas. Se não conseguir, devolve o caminho para o modelo ler."""
    try:
        data = pdf.read_bytes()
    except OSError as exc:
        raise PerfilIAError(f"não deu para ler o PDF: {exc}") from exc
    if len(data) > MAX_PDF_BYTES:
        raise PerfilIAError(f"PDF maior que {MAX_PDF_BYTES // (1024 * 1024)} MB")
    if not data.startswith(b"%PDF"):
        raise PerfilIAError("o arquivo não é um PDF")

    texto = []
    for part in data.split(b"stream"):
        chunk = part.split(b"endstream", 1)[0].lstrip(b"\r\n")
        try:
            chunk = zlib.decompress(chunk)
        except zlib.error:
            pass
        readable = bytes(b for b in chunk if 32 <= b < 127 or b in (9, 10, 13))
        if len(readable) > 40:
            texto.append(readable.decode())
    limpo = " ".join(texto).strip()
    return limpo or str(pdf)


Extractor = Callable[[Path], dict]


class PerfilExtractor(ABC):
    nome: str

    @abstractmethod
    def extrair(self, pdf: Path) -> dict: ...


def _modelo() -> str:
    import os

    return os.environ.get("CLAUDE_PI_MODEL", "claude-sonnet-5-5")


def _runner(cmd: list[str]) -> str:
    if shutil.which(cmd[0]) is None:
        raise PerfilIAError(f"{cmd[0]} não está instalado no ambiente")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
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

    def __init__(self, binary: str = "claude", runner: Callable[[list[str]], str] = _runner):
        self._binary = binary
        self._runner = runner

    def extrair(self, pdf: Path) -> dict:
        # Valida o arquivo (existe, é PDF, até 32 MB) antes de gastar uma chamada ao modelo.
        curriculo = extrair_texto(pdf)
        prompt = f"{INSTRUCAO}\n\nCurrículo: {curriculo}"
        out = self._runner([self._binary, "-p", "--output-format", "json", "--model", _modelo(), prompt])
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
