class DadoInvalido(ValueError):
    """Dado recusado pela validação do domínio (perfil, currículo). A API devolve 422.

    É subclasse de ValueError para quem já trata ValueError, mas a API só transforma em 422
    esta exceção: um ValueError qualquer (configuração, dado inesperado do banco) é falha do
    servidor e tem que sair como 500.
    """
