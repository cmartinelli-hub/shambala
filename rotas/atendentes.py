# Este arquivo é um wrapper para a refatoração atendentes → usuarios
# O router de usuários/atendentes é importado daqui

from rotas.usuarios import router

__all__ = ["router"]
