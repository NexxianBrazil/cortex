"""Criador Visual de Cortexes — wizard web de instalação e criação (casca, não motor).

App FastAPI SEPARADO do servidor de operação (`cortex.server`): este pacote só
ORQUESTRA o que já existe — `gerar_deploy` para criar, `cortex servir` como
subprocesso para subir, `KnowledgeBase.indexar` para a KB. Nenhuma rota edita
SOUL/formação nem escreve memória (teste-fronteira garante). Local-first:
127.0.0.1 por padrão.
"""

from cortex.creator.app import criar_app_criador
from cortex.creator.processos import GerenciadorProcessos, ProcessoError

__all__ = ["GerenciadorProcessos", "ProcessoError", "criar_app_criador"]
