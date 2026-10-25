"""Login, sessão e proteção contra força bruta.

- O token da sessão é `secrets.token_urlsafe(32)` e só existe no cookie; o banco guarda o sha256 dele. Nada
  daqui loga token, senha ou nome.
- A sessão expira em `SESSAO_DIAS` dias (30 por padrão) depois do login, sem renovar a cada uso.
- Força bruta: no máximo 10 tentativas por nome em 15 minutos (`LimiteDeTentativas`). O contador mora na
  MEMÓRIA do processo: reiniciar a app o zera e, com mais de uma réplica, cada uma conta o seu (a app roda numa
  réplica só, na LAN). O custo disso é que quem erra o nome de alguém 10 vezes tranca esse nome por 15 minutos.
"""

import hashlib
import os
import secrets
import threading
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.repository.pessoa import MAX_NOME, PessoaRepository, chave_do_nome
from src.repository.sessao import PessoaLogada, SessaoRepository
from src.service import senha as senhas

SESSAO_DIAS_PADRAO = 30
SESSAO_DIAS_MAX = 365
MAX_FALHAS = 10
JANELA_FALHAS = timedelta(minutes=15)
MAX_CHAVES_DO_LIMITE = 10_000


def sessao_dias(env: Mapping[str, str] | None = None) -> int:
    """SESSAO_DIAS: inteiro de 1 a 365; ausente, vazio ou inválido cai no padrão (30), sem derrubar a app."""
    bruto = (os.environ if env is None else env).get("SESSAO_DIAS", "").strip()
    if not (bruto.isascii() and bruto.isdigit()) or len(bruto) > 4:
        return SESSAO_DIAS_PADRAO
    dias = int(bruto)
    return dias if 1 <= dias <= SESSAO_DIAS_MAX else SESSAO_DIAS_PADRAO


def cookie_secure(env: Mapping[str, str] | None = None) -> bool:
    """SESSAO_COOKIE_SECURE: 1/true/sim ligam; 0/false/nao desligam. Ausente ou inválido cai no padrão (desligado:
    a LAN é http, e um cookie Secure nunca voltaria)."""
    bruto = (os.environ if env is None else env).get("SESSAO_COOKIE_SECURE", "").strip().lower()
    return bruto in ("1", "true", "sim", "yes", "on")


def hash_do_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8", "surrogatepass")).hexdigest()


class CredenciaisInvalidas(Exception):
    """Nome ou senha não conferem. A mensagem é a mesma para qualquer causa: não diz se o nome existe."""


class MuitasTentativas(Exception):
    """Passou de 10 tentativas para o nome em 15 minutos."""


class LimiteDeTentativas:
    """Janela deslizante de tentativas por chave, em memória. `reservar` conta a tentativa ANTES de conferir a
    senha (tentativas em paralelo não furam o limite) e `limpar` a devolve no login certo."""

    def __init__(
        self,
        maximo: int = MAX_FALHAS,
        janela: timedelta = JANELA_FALHAS,
        relogio: Callable[[], datetime] | None = None,
        max_chaves: int = MAX_CHAVES_DO_LIMITE,
    ):
        self.maximo = maximo
        self.janela = janela
        self.max_chaves = max_chaves
        self._relogio = relogio or (lambda: datetime.now(timezone.utc))
        self._tentativas: dict[str, deque[datetime]] = {}
        self._lock = threading.Lock()

    def _recentes(self, chave: str, agora: datetime) -> deque[datetime] | None:
        fila = self._tentativas.get(chave)
        if fila is None:
            return None
        while fila and fila[0] <= agora - self.janela:
            fila.popleft()
        if not fila:
            del self._tentativas[chave]
            return None
        return fila

    def _abrir_espaco(self, agora: datetime) -> bool:
        """Com o mapa cheio: tira o que já saiu da janela e, se ainda for preciso, as chaves não bloqueadas
        (as bloqueadas ficam: é o que o limite protege). False se não coube."""
        for chave in list(self._tentativas):
            self._recentes(chave, agora)
        if len(self._tentativas) < self.max_chaves:
            return True
        for chave in list(self._tentativas):
            if len(self._tentativas[chave]) < self.maximo:
                del self._tentativas[chave]
                return True
        return False

    def reservar(self, chave: str) -> bool:
        """Conta uma tentativa para a chave. False (sem contar) se ela já está no limite."""
        with self._lock:
            agora = self._relogio()
            fila = self._recentes(chave, agora)
            if fila is not None and len(fila) >= self.maximo:
                return False
            if fila is None:
                if len(self._tentativas) >= self.max_chaves and not self._abrir_espaco(agora):
                    return True  # sem lugar: melhor deixar entrar do que travar o login de todos
                fila = self._tentativas.setdefault(chave, deque())
            fila.append(agora)
            return True

    def limpar(self, chave: str) -> None:
        with self._lock:
            self._tentativas.pop(chave, None)


@dataclass(frozen=True)
class SessaoNova:
    token: str
    pessoa: PessoaLogada
    expira_em: datetime


class Autenticacao:
    def __init__(
        self,
        pessoas: PessoaRepository,
        sessoes: SessaoRepository,
        limite: LimiteDeTentativas | None = None,
        relogio: Callable[[], datetime] | None = None,
    ):
        self.pessoas = pessoas
        self.sessoes = sessoes
        self._relogio = relogio or (lambda: datetime.now(timezone.utc))
        self.limite = limite if limite is not None else LimiteDeTentativas(relogio=self._relogio)

    def agora(self) -> datetime:
        return self._relogio()

    def entrar(self, nome: str, senha: str) -> SessaoNova:
        """Confere nome e senha e abre a sessão. Levanta `MuitasTentativas` (antes de conferir qualquer coisa) ou
        `CredenciaisInvalidas`. Nome inexistente, pessoa sem senha e senha errada gastam o mesmo scrypt."""
        nome = nome if isinstance(nome, str) else ""
        chave = chave_do_nome(nome)[:MAX_NOME]
        if not self.limite.reservar(chave):
            raise MuitasTentativas()
        credencial = None
        # Nome fora do formato (vazio, longo, com caractere de controle) não existe: nem vai ao banco (NUL daria 500).
        if chave and len(nome.strip()) <= MAX_NOME and not any(ord(c) < 32 or ord(c) == 127 for c in nome):
            credencial = self.pessoas.credencial_por_nome(nome)
        conferiu = senhas.verificar(senha, credencial.senha_hash if credencial else None)
        if credencial is None or credencial.senha_hash is None or not conferiu:
            raise CredenciaisInvalidas()
        self.limite.limpar(chave)
        return self._abrir_sessao(PessoaLogada(credencial.id, credencial.nome_exibicao))

    def _abrir_sessao(self, pessoa: PessoaLogada) -> SessaoNova:
        agora = self.agora()
        self.sessoes.limpar_expiradas(agora)
        token = secrets.token_urlsafe(32)
        expira_em = agora + timedelta(days=sessao_dias())
        self.sessoes.criar(hash_do_token(token), pessoa.id, expira_em)
        return SessaoNova(token, pessoa, expira_em)

    def pessoa_da_sessao(self, token: str | None) -> PessoaLogada | None:
        if not token or len(token) > 256:
            return None
        return self.sessoes.obter(hash_do_token(token), self.agora())

    def sair(self, token: str | None) -> None:
        if token and len(token) <= 256:
            self.sessoes.remover(hash_do_token(token))
