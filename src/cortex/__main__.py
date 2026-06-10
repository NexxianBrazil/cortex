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
from cortex.memory import (
    AutoridadeInsuficienteError,
    Procedencia,
    ProposalStatus,
    PropostaJaDecididaError,
)
from cortex.runtime import (
    LoopLimiteExcedidoError,
    Session,
    autoridade_da_persona,
    montar_engine,
    montar_runtime,
)

COMANDOS_SAIDA = {"sair", "exit", "quit"}


def _chat(config: CortexConfig) -> int:
    persona = carregar_persona(config.personas_dir)
    loop, _engine = montar_runtime(config, persona)
    session = Session(persona)

    print(
        f"Cortex — conversando com {persona.soul.nome} ({persona.soul.papel}) "
        f"[provider={config.provider} | classifier={config.classifier} | "
        f"store={config.store} | decisão={config.decision_mode}]"
    )
    print(
        "Digite 'sair' (ou Ctrl-D) para encerrar. A conversa é efêmera; o que a "
        "Mariana aprende é promovido à memória.\n"
    )

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


def _montar_engine_com_autoridade(config: CortexConfig):
    """Engine com o authority map vindo do USER.md (quem pode decidir)."""
    persona = carregar_persona(config.personas_dir)
    return montar_engine(config, authority_map=autoridade_da_persona(persona))


def _fila_listar(config: CortexConfig) -> int:
    """Lista as propostas PENDENTES com o warrant completo."""
    engine = _montar_engine_com_autoridade(config)
    pendentes = engine.store.proposals(ProposalStatus.PENDENTE)
    if not pendentes:
        print("Learning Queue vazia — nenhuma proposta pendente.")
        return 0

    print(f"Learning Queue — {len(pendentes)} proposta(s) pendente(s):\n")
    for p in pendentes:
        vigente = p.current_value if p.current_value is not None else "(assunto novo)"
        print(f"  #{p.id}  [{p.key}]  risco={p.risk.value}")
        print(f"      {vigente}  →  {p.proposed_value}")
        proc = ""
        if p.source.procedencia is Procedencia.EXTERNA:
            proc = "  ⚠ ORIGEM EXTERNA NÃO AUTENTICADA"
        print(f"      fonte: {p.source.name} ({p.source.kind.value}){proc}")
        if p.justification.why or p.justification.evidence:
            print(
                f"      porquê: {p.justification.why or '—'}"
                f" | evidência: {p.justification.evidence or '—'}"
            )
        print(f"      escalou porque: {p.reason}")
        print(f"      quando: {p.created_at.isoformat()}\n")
    return 0


def _fila_decidir(config: CortexConfig, acao: str, pid: int, autor: str, razao: str) -> int:
    """Aprova ou rejeita uma proposta, imprimindo o episódio (ou o erro)."""
    engine = _montar_engine_com_autoridade(config)
    try:
        ep = engine.aprovar(pid, autor, razao) if acao == "aprovar" else engine.rejeitar(
            pid, autor, razao
        )
    except (AutoridadeInsuficienteError, PropostaJaDecididaError, ValueError) as exc:
        print(f"[cortex] não foi possível {acao} a proposta #{pid}: {exc}")
        return 1
    print(f"[cortex] {ep.action}")
    print(f"      por: {ep.source.name} | razão: {ep.reason}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cortex", description="Cortex — runtime da ACP")
    subparsers = parser.add_subparsers(dest="comando", required=True)
    subparsers.add_parser("chat", help="abre uma sessão de conversa com a persona")

    fila = subparsers.add_parser("fila", help="Learning Queue: lista/aprova/rejeita propostas")
    fila_sub = fila.add_subparsers(dest="acao")  # sem ação = listar
    for nome in ("aprovar", "rejeitar"):
        sp = fila_sub.add_parser(nome, help=f"{nome} uma proposta pendente")
        sp.add_argument("id", type=int, help="id da proposta")
        sp.add_argument(
            "--autor", required=True, help="nome de quem decide (deve ser autoritativo)"
        )
        sp.add_argument("--razao", required=True, help="motivo da decisão (vira memória)")

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    config = CortexConfig()
    if args.comando == "chat":
        return _chat(config)
    if args.comando == "fila":
        if args.acao in ("aprovar", "rejeitar"):
            return _fila_decidir(config, args.acao, args.id, args.autor, args.razao)
        return _fila_listar(config)
    return 1


if __name__ == "__main__":
    sys.exit(main())
