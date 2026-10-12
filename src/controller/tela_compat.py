"""Compatibilidade na tela: funções de template que leem o perfil salvo do app da requisição.

Registradas no `templates` compartilhado, mas o perfil vem de `request.app.state.perfis`, então
vários apps no mesmo processo (testes) não se misturam. O perfil é lido uma vez por requisição.
Sem perfil salvo as funções devolvem None e os templates não mostram nada.
"""

from jinja2 import pass_context

from src.controller.tela_seguranca import templates
from src.service.compatibilidade_service import (
    ROTULO_PARCIAL,
    Compatibilidade,
    classe_da_faixa,
    compatibilidade,
    compatibilidade_da_vaga,
)

_SEM_LEITURA = object()


def _perfil_da_requisicao(contexto):
    request = contexto.get("request")
    if request is None:
        return None
    perfis = getattr(request.app.state, "perfis", None)
    if perfis is None:
        return None
    lido = getattr(request.state, "perfil_compat", _SEM_LEITURA)
    if lido is _SEM_LEITURA:
        lido = perfis.obter()
        request.state.perfil_compat = lido
    return lido


@pass_context
def compat_da_vaga(contexto, vaga) -> Compatibilidade | None:
    return compatibilidade_da_vaga(vaga, _perfil_da_requisicao(contexto))


@pass_context
def compat_do_titulo(contexto, titulo) -> Compatibilidade | None:
    """Para a tabela de aplicadas: só o título da vaga (as linhas não carregam requisitos)."""
    perfil = _perfil_da_requisicao(contexto)
    return None if perfil is None else compatibilidade(titulo, None, perfil)


templates.env.globals.update(
    compat_da_vaga=compat_da_vaga,
    compat_do_titulo=compat_do_titulo,
    compat_faixa=classe_da_faixa,
    compat_rotulo_parcial=ROTULO_PARCIAL,
)
