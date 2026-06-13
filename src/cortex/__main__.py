"""CLI do Cortex: `python -m cortex chat` (ou o console script `cortex chat`).

Interface mínima de uso da Fase 2: carrega a persona de personas/, abre uma
Session efêmera e conversa no terminal. Com o StubProvider padrão roda sem
chave nenhuma — provider real é decisão de config, não de código.
"""

import argparse
import logging
import sys
from pathlib import Path

from cortex.config import CortexConfig, carregar_config
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
    Identidade,
    LoopLimiteExcedidoError,
    Session,
    autoridade_da_persona,
    identidade_externa,
    identidade_interna,
    montar_engine,
    montar_runtime,
)
from cortex.sor import SORIndisponivelError, criar_gateway

COMANDOS_SAIDA = {"sair", "exit", "quit"}


def _resolver_identidade_chat(
    persona, como: str | None, externo: bool
) -> Identidade | None:
    """Identidade para o `cortex chat`: --como NOME (interno) | --externo | dev (None)."""
    if externo:
        return identidade_externa("cli", "externo")
    if como:
        return identidade_interna(persona, como, canal="cli", canal_id=como)
    return None


def _chat(config: CortexConfig, identidade: Identidade | None) -> int:
    persona = carregar_persona(config.personas_dir)
    loop, _engine = montar_runtime(config, persona)
    session = Session(persona, identidade=identidade)

    como = (
        f" | como {identidade.nome} ({identidade.procedencia.value})"
        if identidade is not None
        else ""
    )
    print(
        f"Cortex — conversando com {persona.soul.nome} ({persona.soul.papel}) "
        f"[provider={config.provider} | classifier={config.classifier} | "
        f"store={config.store} | decisão={config.decision_mode}{como}]"
    )
    print(
        "Digite 'sair' (ou Ctrl-D) para encerrar. A conversa é efêmera; o que a "
        "persona aprende é promovido à memória.\n"
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


def _sor_preco(config: CortexConfig, codigo: str) -> int:
    """Consulta o preço VIVO de um produto no system of record (debug do operador)."""
    gateway = criar_gateway(config)
    try:
        preco = gateway.preco(codigo)
    except SORIndisponivelError as exc:
        print(f"[cortex] system of record indisponível (provider={config.sor_provider}): {exc}")
        return 1
    if preco is None:
        print(f"Produto {codigo} não encontrado no SOR (provider={config.sor_provider}).")
        return 0
    disp = "disponível" if preco.disponivel else "indisponível"
    print(
        f"[SOR {config.sor_provider}] {preco.codigo_produto}: "
        f"{preco.moeda} {preco.preco_unitario:.2f} ({disp})"
    )
    return 0


def _sor_cliente(config: CortexConfig, cliente_id: str) -> int:
    """Consulta o cadastro VIVO de um cliente no system of record (debug do operador)."""
    gateway = criar_gateway(config)
    try:
        cliente = gateway.cliente(cliente_id)
    except SORIndisponivelError as exc:
        print(f"[cortex] system of record indisponível (provider={config.sor_provider}): {exc}")
        return 1
    if cliente is None:
        print(f"Cliente {cliente_id} não encontrado no SOR (provider={config.sor_provider}).")
        return 0
    estado = "BLOQUEADO" if cliente.bloqueado else "ativo"
    print(
        f"[SOR {config.sor_provider}] {cliente.cliente_id} — {cliente.razao_social} ({estado})\n"
        f"    limite de crédito: R$ {cliente.limite_credito:.2f} | "
        f"condição padrão: {cliente.condicao_pagamento_padrao}"
    )
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


def _novo(args) -> int:
    """Gera um deploy de Cortex novo a partir dos templates e o valida."""
    from cortex.scaffold import ScaffoldError, gerar_deploy, proximos_passos

    try:
        destino = gerar_deploy(
            args.diretorio,
            nome=args.nome,
            funcao=args.funcao,
            gestor=args.gestor,
            dominio=args.dominio,
        )
    except ScaffoldError as exc:
        print(f"[cortex] {exc}")
        return 1
    print(proximos_passos(destino))
    return 0


def _servir(config: CortexConfig, deploy: str | None, host: str | None, porta: int | None) -> int:
    """Sobe o gateway HTTP do deploy (FastAPI/uvicorn)."""
    import uvicorn

    from cortex.server import carregar_mapa_identidades, criar_app

    persona = carregar_persona(config.personas_dir)
    loop, _engine = montar_runtime(config, persona)
    deploy_dir = Path(deploy) if deploy else Path.cwd()
    mapa = carregar_mapa_identidades(deploy_dir / "canais.yaml", persona)
    token = config.server_token.get_secret_value() if config.server_token else None
    if token is None:
        print(
            "[cortex] aviso: sem server_token na config — o gateway recusará tudo (401). "
            "Gere um deploy com `cortex novo` ou defina server_token no cortex.toml."
        )
    app = criar_app(
        persona=persona,
        loop=loop,
        mapa_identidades=mapa,
        token=token,
        ttl_minutos=config.session_ttl_minutos,
    )
    host = host or config.server_host
    porta = porta or config.server_porta
    print(
        f"Cortex {persona.soul.nome} ({persona.soul.papel}) ouvindo em "
        f"http://{host}:{porta}  [deploy={deploy_dir} | {len(mapa)} canal(is) mapeado(s)]"
    )
    uvicorn.run(app, host=host, port=porta, log_level="info")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cortex", description="Cortex — runtime da ACP")
    subparsers = parser.add_subparsers(dest="comando", required=True)

    # --deploy: carrega o cortex.toml de um deploy auto-contido (Fase 7a). Sem
    # ele, usa o CWD (o repo é o deploy de desenvolvimento da Mariana).
    comum = argparse.ArgumentParser(add_help=False)
    comum.add_argument(
        "--deploy", default=None, metavar="DIR", help="diretório do deploy (cortex.toml)"
    )

    chat = subparsers.add_parser(
        "chat", parents=[comum], help="abre uma sessão de conversa com a persona"
    )
    chat.add_argument(
        "--como", default=None, metavar="NOME", help="fala como esta pessoa do USER.md (interno)"
    )
    chat.add_argument(
        "--externo", action="store_true", help="simula um remetente desconhecido (externo)"
    )

    novo = subparsers.add_parser("novo", help="cria um Cortex novo (scaffold de uma ACP)")
    novo.add_argument("diretorio", help="diretório do novo deploy (deve estar vazio)")
    novo.add_argument("--nome", required=True, help="nome da persona (ex.: Rafael)")
    novo.add_argument("--funcao", required=True, help="função/papel (ex.: 'suporte técnico')")
    novo.add_argument("--gestor", required=True, help="nome do gestor humano")
    novo.add_argument("--dominio", default="geral", help="domínio operacional (rótulo)")

    servir = subparsers.add_parser(
        "servir", parents=[comum], help="sobe o gateway HTTP do deploy (para bridges)"
    )
    servir.add_argument("--host", default=None, help="host de escuta (default da config)")
    servir.add_argument(
        "--porta", type=int, default=None, help="porta de escuta (default da config)"
    )

    fila = subparsers.add_parser(
        "fila", parents=[comum], help="Learning Queue: lista/aprova/rejeita propostas"
    )
    fila_sub = fila.add_subparsers(dest="acao")  # sem ação = listar
    for nome in ("aprovar", "rejeitar"):
        sp = fila_sub.add_parser(nome, help=f"{nome} uma proposta pendente")
        sp.add_argument("id", type=int, help="id da proposta")
        sp.add_argument(
            "--autor", required=True, help="nome de quem decide (deve ser autoritativo)"
        )
        sp.add_argument("--razao", required=True, help="motivo da decisão (vira memória)")

    kb = subparsers.add_parser(
        "kb", parents=[comum], help="Knowledge Base: (re)indexa e consulta a KB curada"
    )
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

    sor = subparsers.add_parser(
        "sor", parents=[comum], help="System of Record: consulta dado vivo (preço/cliente)"
    )
    sor_sub = sor.add_subparsers(dest="acao", required=True)
    sor_preco = sor_sub.add_parser("preco", help="consulta o preço vivo de um produto")
    sor_preco.add_argument("codigo", help="código do produto (ex.: PRD-001)")
    sor_cliente = sor_sub.add_parser("cliente", help="consulta o cadastro vivo de um cliente")
    sor_cliente.add_argument("cliente_id", help="identificador do cliente (ex.: CLI-001)")

    audit_p = subparsers.add_parser(
        "audit", parents=[comum], help="inspeciona a trilha de auditoria"
    )
    audit_p.add_argument(
        "--ultimos", type=int, default=20, help="quantas linhas mostrar (default 20)"
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # `novo` cria um deploy — não carrega config de um deploy existente.
    if args.comando == "novo":
        return _novo(args)

    try:
        config = carregar_config(args.deploy)
    except (FileNotFoundError, OSError) as exc:
        print(f"[cortex] não consegui carregar o deploy: {exc}")
        return 1

    if args.comando == "chat":
        persona = carregar_persona(config.personas_dir)
        try:
            identidade = _resolver_identidade_chat(persona, args.como, args.externo)
        except ValueError as exc:
            print(f"[cortex] {exc}")
            return 1
        return _chat(config, identidade)
    if args.comando == "servir":
        return _servir(config, args.deploy, args.host, args.porta)
    if args.comando == "fila":
        if args.acao in ("aprovar", "rejeitar"):
            return _fila_decidir(config, args.acao, args.id, args.autor, args.razao)
        return _fila_listar(config)
    if args.comando == "kb":
        if args.acao == "indexar":
            return _kb_indexar(config)
        return _kb_buscar(config, args.pergunta, args.dominio, args.revogados)
    if args.comando == "sor":
        if args.acao == "preco":
            return _sor_preco(config, args.codigo)
        return _sor_cliente(config, args.cliente_id)
    if args.comando == "audit":
        return _audit_listar(config, args.ultimos)
    return 1


if __name__ == "__main__":
    sys.exit(main())
