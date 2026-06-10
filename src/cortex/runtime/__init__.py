"""Fase 2 — Loop do agente e runtime state.

O runtime carrega uma persona (Fase 1), conversa via um LLMProvider
configurável, executa tools mockadas e mantém estado EFÊMERO por sessão.
Memória persistente (Fase 3) e governança (Fase 4) plugam por cima disto.
"""

from cortex.runtime.app import (
    autoridade_da_persona,
    montar_engine,
    montar_runtime,
)
from cortex.runtime.loop import AgentLoop, LoopLimiteExcedidoError, montar_system_prompt
from cortex.runtime.messages import LLMResponse, Message, Role, ToolCall
from cortex.runtime.mock_tools import criar_registry_mock
from cortex.runtime.promotion import (
    PromotionCandidate,
    extrair_candidatos,
    promover,
)
from cortex.runtime.providers import (
    ClaudeProvider,
    ConfiguracaoProviderError,
    LLMProvider,
    OpenAICompatProvider,
    StubProvider,
    criar_provider,
)
from cortex.runtime.recall import recuperar_beliefs
from cortex.runtime.session import Session
from cortex.runtime.tools import (
    ArgumentosInvalidosError,
    ToolError,
    ToolNaoEncontradaError,
    ToolRegistry,
)

__all__ = [
    "AgentLoop",
    "ArgumentosInvalidosError",
    "ClaudeProvider",
    "ConfiguracaoProviderError",
    "LLMProvider",
    "LLMResponse",
    "LoopLimiteExcedidoError",
    "Message",
    "OpenAICompatProvider",
    "PromotionCandidate",
    "Role",
    "Session",
    "StubProvider",
    "ToolCall",
    "ToolError",
    "ToolNaoEncontradaError",
    "ToolRegistry",
    "autoridade_da_persona",
    "criar_provider",
    "criar_registry_mock",
    "extrair_candidatos",
    "montar_engine",
    "montar_runtime",
    "montar_system_prompt",
    "promover",
    "recuperar_beliefs",
]
