"""Compatibilidade determinística entre uma vaga e o perfil salvo, sem IA e sem custo.

A função `compatibilidade` é pura (mesma entrada, mesma saída) e compara por PALAVRA inteira com
`palavras`/`radical` de `src/client/base.py`: "Java" não casa com "JavaScript", "SQL" não casa com
"NoSQL", "Power BI" é uma expressão de duas palavras (e também casa com "PowerBI" e "Power-BI"),
e acento, caixa, hífen e plural não mudam o resultado.

Como o percentual é calculado (0 a 100, inteiro, arredondado para cima a partir de .5):

    percentual = soma(peso_i * nota_i) / soma(peso_i) dos componentes que têm informação

Componentes (um componente sem informação sai da conta e os outros são renormalizados):

- requisitos (peso 50): cada requisito vale 3 se obrigatório e 1 se desejável; ele conta como
  coberto quando o texto cita ao menos uma skill do perfil. Nota = peso coberto / peso total.
  Só existe quando a vaga traz requisitos.
- título (peso 30): `relevancia_perfil` do título contra os termos do perfil (0 a 1); skill do
  perfil citada no título soma 0,5 (no máximo 1). Sem termos e sem skill no título, não conta.
- nível (peso 20): 1 se algum nível do título (júnior/pleno/sênior) está nos níveis do perfil,
  0 se nenhum está. Título sem nível, ou perfil sem níveis, não conta.

Sem requisitos (`None` ou lista vazia) o resultado é `parcial=True`: o percentual sai só de
título, skills no título e nível, e a tela diz "parcial (sem requisitos da vaga)".

Limites conhecidos: só as skills do perfil são reconhecidas, então um requisito que não cita
nenhuma delas ("boa comunicação") aparece como lacuna; "T-SQL" é uma palavra só (hífen entre
letras junta) e não casa com "SQL", como na busca do histórico.
"""

from dataclasses import dataclass, field

from src.client.base import palavras, radical
from src.model.perfil import Perfil
from src.model.vaga import Nivel
from src.service.recomendacao_service import relevancia_perfil

PESO_REQUISITOS = 50
PESO_TITULO = 30
PESO_NIVEL = 20
# Requisito obrigatório pesa o triplo do desejável.
PESO_OBRIGATORIO = 3
PESO_DESEJAVEL = 1
# Quanto um skill do perfil citado no título soma à nota do título.
BONUS_SKILL_NO_TITULO = 0.5
# Limite do texto de uma lacuna na tela (o requisito inteiro pode ser um parágrafo).
MAX_TEXTO_LACUNA = 140

ROTULO_PARCIAL = "parcial (sem requisitos da vaga)"

# Palavras do título (já em radical) que dizem o nível. "pl" e "sr" são as abreviações usuais.
_NIVEL_DA_PALAVRA = {
    "junior": Nivel.JUNIOR,
    "jr": Nivel.JUNIOR,
    "plen": Nivel.PLENO,  # radical("pleno") e radical("plena")
    "pl": Nivel.PLENO,
    "mid": Nivel.PLENO,
    "midlevel": Nivel.PLENO,
    "senior": Nivel.SENIOR,
    "sr": Nivel.SENIOR,
}


@dataclass
class Compatibilidade:
    percentual: int
    pontos_fortes: list[str] = field(default_factory=list)
    lacunas: list[str] = field(default_factory=list)
    parcial: bool = False


def _raizes(texto: str | None) -> list[str]:
    return [radical(p) for p in palavras(texto)]


def _formas_da_skill(skill: str) -> tuple[tuple[str, ...], str] | None:
    raizes = tuple(_raizes(skill))
    if not raizes:
        return None
    return raizes, "".join(raizes)


def _contem_skill(raizes: list[str], skill: tuple[tuple[str, ...], str]) -> bool:
    """A skill aparece como palavras vizinhas inteiras; "Power BI" também casa com "PowerBI"."""
    sequencia, junta = skill
    n = len(sequencia)
    if any(tuple(raizes[i : i + n]) == sequencia for i in range(len(raizes) - n + 1)):
        return True
    if n > 1 and junta in raizes:
        return True
    # "PowerBI" no perfil e "Power BI" na vaga: duas palavras coladas.
    return n == 1 and any(a + b == junta for a, b in zip(raizes, raizes[1:]))


def _skills_do_perfil(perfil: Perfil) -> list[tuple[str, tuple[tuple[str, ...], str]]]:
    """(nome como o perfil escreveu, formas) sem repetidos (por palavras) e sem skill vazia."""
    vistas: set[tuple[str, ...]] = set()
    skills = []
    for nome in perfil.skills:
        if not isinstance(nome, str):
            continue
        formas = _formas_da_skill(nome)
        if formas is None or formas[0] in vistas:
            continue
        vistas.add(formas[0])
        skills.append((nome.strip(), formas))
    return skills


# Abreviações que viram outra coisa quando seguidas de certa palavra: "PL/SQL" é uma linguagem
# (não Pleno) e "Mid Market" é segmento de mercado (não nível). "Mid-Market" já vira uma palavra só.
_NAO_E_NIVEL_SE_SEGUIDA_DE = {"pl": {"sql"}, "mid": {"market"}}


def _niveis_do_titulo(titulo: str | None) -> list[Nivel]:
    raizes = _raizes(titulo)
    achados = []
    for i, r in enumerate(raizes):
        if r not in _NIVEL_DA_PALAVRA:
            continue
        seguinte = raizes[i + 1] if i + 1 < len(raizes) else None
        if seguinte in _NAO_E_NIVEL_SE_SEGUIDA_DE.get(r, ()):
            continue
        achados.append(_NIVEL_DA_PALAVRA[r])
    return list(dict.fromkeys(achados))


def _requisitos_validos(requisitos) -> list[tuple[str, bool]]:
    """Itens que não são (texto, obrigatório), texto vazio e repetidos saem; o repetido fica
    obrigatório se qualquer cópia for."""
    por_chave: dict[str, tuple[str, bool]] = {}
    for item in requisitos or []:
        if not isinstance(item, (tuple, list)) or len(item) != 2 or not isinstance(item[0], str):
            continue
        texto = " ".join(item[0].split())
        chave = " ".join(palavras(texto))
        if not chave:
            continue
        anterior = por_chave.get(chave)
        obrigatorio = bool(item[1]) or (anterior is not None and anterior[1])
        por_chave[chave] = (anterior[0] if anterior else texto, obrigatorio)
    return list(por_chave.values())


def _encurtar(texto: str) -> str:
    return texto if len(texto) <= MAX_TEXTO_LACUNA else texto[: MAX_TEXTO_LACUNA - 1].rstrip() + "…"


def compatibilidade(
    titulo: str | None, requisitos: list[tuple[str, bool]] | None, perfil: Perfil
) -> Compatibilidade:
    skills = _skills_do_perfil(perfil)
    validos = _requisitos_validos(requisitos)
    raizes_titulo = _raizes(titulo)

    fortes: list[str] = []
    lacunas: list[str] = []
    componentes: list[tuple[int, float]] = []  # (peso, nota de 0 a 1)

    citadas_no_titulo = [nome for nome, formas in skills if _contem_skill(raizes_titulo, formas)]
    citadas = set(citadas_no_titulo)

    # Requisitos: coberto = cita alguma skill do perfil.
    if validos:
        total = coberto = 0
        descobertos: list[tuple[bool, str]] = []
        for texto, obrigatorio in validos:
            peso = PESO_OBRIGATORIO if obrigatorio else PESO_DESEJAVEL
            total += peso
            raizes = _raizes(texto)
            achadas = [nome for nome, formas in skills if _contem_skill(raizes, formas)]
            if achadas:
                coberto += peso
                citadas.update(achadas)
            else:
                descobertos.append((obrigatorio, _encurtar(texto) + (" (obrigatório)" if obrigatorio else "")))
        componentes.append((PESO_REQUISITOS, coberto / total))
        # Obrigatórios primeiro; sorted é estável, então a ordem da vaga se mantém dentro de cada grupo.
        lacunas.extend(t for _, t in sorted(descobertos, key=lambda d: not d[0]))

    # Título.
    termos_uteis = bool(perfil.termos)
    relevancia = relevancia_perfil(titulo or "", perfil.termos) if termos_uteis else None
    if citadas_no_titulo:
        nota = 1.0 if relevancia is None else min(1.0, relevancia + BONUS_SKILL_NO_TITULO)
        componentes.append((PESO_TITULO, nota))
    elif relevancia is not None:
        componentes.append((PESO_TITULO, relevancia))
    if relevancia == 1.0:
        fortes.append("Título casa com os termos do perfil")
    elif relevancia == 0.0:
        lacunas.append("Título sem relação com os termos do perfil")

    # Nível.
    niveis_titulo = _niveis_do_titulo(titulo)
    niveis_perfil = [Nivel(n) for n in perfil.niveis]
    if niveis_titulo and niveis_perfil:
        bate = [n for n in niveis_titulo if n in niveis_perfil]
        componentes.append((PESO_NIVEL, 1.0 if bate else 0.0))
        if bate:
            fortes.append("Nível " + " / ".join(n.value for n in bate))
        else:
            lacunas.append(
                "Nível " + " / ".join(n.value for n in niveis_titulo)
                + " fora do perfil (" + ", ".join(n.value for n in niveis_perfil) + ")"
            )

    # Skills do perfil achadas (título ou requisitos), na ordem do perfil.
    fortes = [nome for nome, _ in skills if nome in citadas] + fortes

    peso_total = sum(p for p, _ in componentes)
    percentual = int(sum(p * n for p, n in componentes) / peso_total * 100 + 0.5) if peso_total else 0
    return Compatibilidade(
        percentual=max(0, min(100, percentual)),
        pontos_fortes=list(dict.fromkeys(fortes)),
        lacunas=list(dict.fromkeys(lacunas)),
        parcial=not validos,
    )


def requisitos_da_vaga(vaga) -> list[tuple[str, bool]] | None:
    """Requisitos guardados na vaga como (texto, obrigatório), ou None se ela não tem.

    A frente do detalhe (migration 004) guarda `requisitos` como lista de objetos do Remotar
    (`{"description": ..., "mandatory": ...}`); aceita também pares. Enquanto a vaga não tem o
    atributo, devolve None e a compatibilidade sai parcial.
    """
    brutos = getattr(vaga, "requisitos", None)
    if not isinstance(brutos, (list, tuple)):
        return None
    saida = []
    for item in brutos:
        if isinstance(item, dict):
            saida.append((item.get("description"), bool(item.get("mandatory"))))
        else:
            saida.append(item)
    return saida or None


def compatibilidade_da_vaga(vaga, perfil: Perfil | None) -> Compatibilidade | None:
    """O que a tela mostra: None sem perfil salvo (a tela não exibe compatibilidade)."""
    if perfil is None:
        return None
    return compatibilidade(vaga.titulo, requisitos_da_vaga(vaga), perfil)


def classe_da_faixa(percentual: int) -> str:
    return "compat-alta" if percentual >= 70 else "compat-media" if percentual >= 40 else "compat-baixa"
