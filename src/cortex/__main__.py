"""CLI do Cortex: `python -m cortex chat` (ou o console script `cortex chat`).

Interface mínima de uso da Fase 2: carrega a persona de personas/, abre uma
Session efêmera e conversa no terminal. Com o StubProvider padrão roda sem
chave nenhuma — provider real é decisão de config, não de código.
"""

import argparse
import logging
import sys

from cortex.config import CortexConfig
from cortex.governance import AuditTrail
from cortex.identity import carregar_persona
from cortex.knowledge import KBIndexError, KnowledgeBase, criar_embedder
from cortex.memory import (
    AutoridadeInsuficienteError,
    Procedencia,
    ProposalKind,
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

    # Propostas de AÇÃO aprovadas e ainda não consumidas (exceções disponíveis).
    excecoes = [
        p
        for p in engine.store.proposals(ProposalStatus.APROVADA)
        if p.kind is ProposalKind.ACAO and p.consumed_at is None
    ]

    print(f"Learning Queue — {len(pendentes)} proposta(s) pendente(s):\n")
    for p in pendentes:
        if p.kind is ProposalKind.ACAO:
            tool = p.key.removeprefix("acao:")
            print(f"  #{p.id}  AÇÃO: {tool}  risco={p.risk.value}")
            print(f"      argumentos: {p.proposed_value}")
            print(f"      bloqueada porque: {p.justification.why}")
        else:
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

    if excecoes:
        print(f"Exceções aprovadas aguardando uso (one-shot): {len(excecoes)}")
        for p in excecoes:
            print(f"  #{p.id}  {p.key.removeprefix('acao:')}  args: {p.proposed_value}")
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


def _kb_indexar(config: CortexConfig) -> int:
    """(Re)indexa a KB inteira — ato DELIBERADO do curador (embedding pode ser remoto)."""
    kb = KnowledgeBase(config.kb_path, criar_embedder(config))
    resumo = kb.indexar()
    print(
        f"KB indexada em {config.kb_path}: {resumo['documentos']} documento(s), "
        f"{resumo['chunks']} chunk(s)."
    )
    return 0


def _kb_buscar(
    config: CortexConfig, pergunta: str, dominio: str | None, revogados: bool
) -> int:
    """Consulta a KB pela linha de comando — debug do curador, mesma busca da tool.

    `--revogados` traz também o histórico revogado, sempre MARCADO: o curador
    inspeciona a linhagem bi-temporal sem que um documento antigo se passe por
    vigente (a tool consultar_kb, em produção, nunca traz revogados).
    """
    kb = KnowledgeBase(config.kb_path, criar_embedder(config))
    try:
        resultados = kb.buscar(pergunta, dominio=dominio, incluir_revogados=revogados)
    except KBIndexError as exc:
        print(f"[cortex] {exc}")
        return 1
    if not resultados:
        print("Nada relevante na KB para essa pergunta.")
        return 0
    for r in resultados:
        marca = "  ⚠ REVOGADO" if r.revogado else ""
        print(f"[{r.score}] {r.arquivo} — {r.titulo} ({r.autoridade}){marca}")
        print(f"    {r.texto[:200].strip()}")
    return 0


def _audit_listar(config: CortexConfig, n: int) -> int:
    """Imprime as últimas N linhas da trilha de auditoria de forma legível."""
    linhas = AuditTrail(config.audit_path).ultimos(n)
    if not linhas:
        print(f"Sem trilha de auditoria em {config.audit_path}.")
        return 0
    for ln in linhas:
        tipo = ln.get("tipo")
        ts = ln.get("ts", "")
        if tipo == "decisao_tool":
            extra = f" [{ln.get('soul_behavior_id')}]" if ln.get("soul_behavior_id") else ""
            print(
                f"{ts}  DECISÃO  {ln.get('tool')}  risco={ln.get('risco')} "
                f"modo={ln.get('modo')} verdict={ln.get('verdict')}{extra}"
            )
        elif tipo == "llm_request":
            print(
                f"{ts}  LLM      it={ln.get('iteracao')} "
                f"in={ln.get('input_tokens')} out={ln.get('output_tokens')}"
            )
        elif tipo == "turno":
            print(
                f"{ts}  TURNO    iterações={ln.get('iteracoes')} "
                f"tools={ln.get('tools')} tokens={ln.get('input_tokens')}+"
                f"{ln.get('output_tokens')} bloqueio={ln.get('houve_bloqueio')}"
            )
        else:
            print(f"{ts}  {tipo}  {ln}")
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

    kb = subparsers.add_parser("kb", help="Knowledge Base: (re)indexa e consulta a KB curada")
    kb_sub = kb.add_subparsers(dest="acao", required=True)
    kb_sub.add_parser("indexar", help="(re)indexa a KB inteira — ato deliberado do curador")
    kb_buscar = kb_sub.add_parser("buscar", help="consulta a KB (debug do curador)")
    kb_buscar.add_argument("pergunta", help="pergunta em linguagem natural")
    kb_buscar.add_argument("--dominio", default=None, help="filtra por domínio (ex.: comercial)")
    kb_buscar.add_argument(
        "--revogados",
        action="store_true",
        help="inclui documentos revogados no histórico (sempre marcados ⚠)",
    )

    audit_p = subparsers.add_parser("audit", help="inspeciona a trilha de auditoria")
    audit_p.add_argument(
        "--ultimos", type=int, default=20, help="quantas linhas mostrar (default 20)"
    )

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
    if args.comando == "kb":
        if args.acao == "indexar":
            return _kb_indexar(config)
        return _kb_buscar(config, args.pergunta, args.dominio, args.revogados)
    if args.comando == "audit":
        return _audit_listar(config, args.ultimos)
    return 1


if __name__ == "__main__":
    sys.exit(main())
