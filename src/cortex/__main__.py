"""CLI do Cortex: `python -m cortex chat` (ou o console script `cortex chat`).

Interface mínima de uso da Fase 2: carrega a persona de personas/, abre uma
Session efêmera e conversa no terminal. Com o StubProvider padrão roda sem
chave nenhuma — provider real é decisão de config, não de código.
"""

import argparse
import logging
import sys

from cortex.config import CortexConfig
from cortex.identity import carregar_persona
from cortex.runtime import (
    AgentLoop,
    LoopLimiteExcedidoError,
    Session,
    criar_provider,
    criar_registry_mock,
)

COMANDOS_SAIDA = {"sair", "exit", "quit"}


def _chat(config: CortexConfig) -> int:
    persona = carregar_persona(config.personas_dir)
    provider = criar_provider(config)
    registry = criar_registry_mock(persona.tools)
    loop = AgentLoop(provider, registry, max_iteracoes=config.max_iteracoes)
    session = Session(persona)

    print(
        f"Cortex — conversando com {persona.soul.nome} ({persona.soul.papel}) "
        f"[provider={config.provider}]"
    )
    print("Digite 'sair' (ou Ctrl-D) para encerrar. A sessão é efêmera: nada persiste.\n")

    while True:
        try:
            entrada = input("você> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not entrada:
            continue
        if entrada.lower() in COMANDOS_SAIDA:
            break
        try:
            resposta = loop.executar_turno(session, entrada)
        except LoopLimiteExcedidoError as exc:
            print(f"[cortex] turno abortado: {exc}")
            continue
        print(f"{persona.soul.nome}> {resposta}\n")

    print("Sessão encerrada — histórico descartado (efêmero por design).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cortex", description="Cortex — runtime da ACP")
    subparsers = parser.add_subparsers(dest="comando", required=True)
    subparsers.add_parser("chat", help="abre uma sessão de conversa com a persona")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if args.comando == "chat":
        return _chat(CortexConfig())
    return 1


if __name__ == "__main__":
    sys.exit(main())
