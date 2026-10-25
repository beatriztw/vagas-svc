"""Senha de login: hash `scrypt` da biblioteca padrão, com sal aleatório por senha.

Formato guardado em `pessoa.senha_hash`, auto-descritivo para os parâmetros poderem subir sem invalidar
as senhas já gravadas: `scrypt$n$r$p$salt_b64$hash_b64` (base64 padrão, com `=`).

Parâmetros de hoje: n=2**14, r=8, p=1, sal de 16 bytes, hash de 32 bytes. É o custo interativo
recomendado pelo OWASP para scrypt em memória pequena (16 MiB e algumas dezenas de ms no Pi); subir o `n`
é só mudar `N` aqui: o `verificar` lê n, r e p do próprio hash, então senhas antigas continuam valendo.

A senha é normalizada em NFKC antes do hash (o mesmo texto digitado em dispositivos diferentes pode vir
composto ou decomposto). A comparação é `hmac.compare_digest`. O `verificar` NUNCA levanta: hash ausente,
torto ou com parâmetros fora dos limites é só "não confere".
"""

import base64
import binascii
import hashlib
import hmac
import secrets
import unicodedata

N = 2**14
R = 8
P = 1
TAMANHO_SAL = 16
TAMANHO_HASH = 32

# Limites do que `verificar` aceita ler de um hash gravado: um hash adulterado não pode pedir gigabytes.
_N_MIN, _N_MAX = 2**10, 2**16
_R_MAX = 8
_P_MAX = 4
_TAMANHO_MIN, _TAMANHO_MAX = 8, 64

# Mínimo e teto da senha NOVA (a CLI). O login aceita qualquer texto até MAX_LOGIN: o que passa disso nunca confere.
MIN_SENHA = 10
MAX_SENHA = 128
MAX_LOGIN = 1024


def _bytes(senha: str) -> bytes:
    return unicodedata.normalize("NFKC", senha).encode("utf-8", "surrogatepass")


def _scrypt(senha: str, sal: bytes, n: int, r: int, p: int, tamanho: int) -> bytes:
    # maxmem explícito: o padrão do OpenSSL (32 MiB) ficaria justo para n=2**15 com r=8.
    return hashlib.scrypt(_bytes(senha), salt=sal, n=n, r=r, p=p, dklen=tamanho, maxmem=256 * n * r + (1 << 20))


def hash_senha(senha: str) -> str:
    sal = secrets.token_bytes(TAMANHO_SAL)
    derivado = _scrypt(senha, sal, N, R, P, TAMANHO_HASH)
    return "$".join(
        ["scrypt", str(N), str(R), str(P), base64.b64encode(sal).decode("ascii"), base64.b64encode(derivado).decode("ascii")]
    )


def _ler(armazenado: object) -> tuple[int, int, int, bytes, bytes] | None:
    """Os campos do hash gravado, ou None se o texto não é um hash que este módulo gerou/aceita."""
    if not isinstance(armazenado, str):
        return None
    partes = armazenado.split("$")
    if len(partes) != 6 or partes[0] != "scrypt":
        return None
    try:
        n, r, p = (int(x) if x.isascii() and x.isdigit() else -1 for x in partes[1:4])
        sal = base64.b64decode(partes[4], validate=True)
        esperado = base64.b64decode(partes[5], validate=True)
    except (binascii.Error, ValueError):
        return None
    if not (_N_MIN <= n <= _N_MAX and n & (n - 1) == 0 and 1 <= r <= _R_MAX and 1 <= p <= _P_MAX):
        return None
    if not (_TAMANHO_MIN <= len(sal) <= _TAMANHO_MAX and _TAMANHO_MIN <= len(esperado) <= _TAMANHO_MAX):
        return None
    return n, r, p, sal, esperado


# Hash de uma senha sorteada na hora: quem não tem hash válido (nome inexistente, senha NULL, hash torto)
# gasta o mesmo scrypt, para o tempo da resposta não dizer se o nome existe. Nunca confere com nada.
HASH_FICTICIO = hash_senha(secrets.token_urlsafe(24))


def verificar(senha: str, armazenado: str | None) -> bool:
    """True só se `senha` confere com o hash gravado. Sempre gasta um scrypt, mesmo sem hash utilizável."""
    campos = _ler(armazenado)
    if campos is None or not isinstance(senha, str) or len(senha) > MAX_LOGIN:
        campos = _ler(HASH_FICTICIO)
        valido = False
    else:
        valido = True
    n, r, p, sal, esperado = campos
    try:
        obtido = _scrypt(senha if valido else "", sal, n, r, p, len(esperado))
    except (ValueError, MemoryError):
        return False
    return hmac.compare_digest(obtido, esperado) and valido
