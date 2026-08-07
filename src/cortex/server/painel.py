"""Painel HTML do operador (Fase 7d) — montado no MESMO servidor da 7a.

FRONTEIRA DE GOVERNANÇA (o ponto da fase): o painel é ALOCAÇÃO (do cliente). Ele
configura, cura a KB, OPERA a Learning Queue (aprova/rejeita com justificativa) e
VISUALIZA memória/audit. Ele NÃO edita o SOUL (formação é da Nexxian, via Git) e
NÃO escreve crença direto no banco — toda escrita passa pelo observe()/governança.
O painel PROPÕE e APROVA; nunca digita verdade. Não existe rota que crie belief.

Autenticação do OPERADOR (pessoa no navegador) por cookie assinado (HMAC stdlib,
HttpOnly) — separada do token de bridge das rotas /v1/* (clientes diferentes).
"""

import difflib
import hashlib
import hmac
import logging
import re
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Cookie, FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from cortex.identity.models import Persona
from cortex.knowledge.index import KnowledgeBase
from cortex.knowledge.models import vigente
from cortex.knowledge.parser import KBParseError, carregar_documento
from cortex.memory.engine import (
    AutoridadeInsuficienteError,
    MemoryEngine,
    PropostaJaDecididaError,
)
from cortex.memory.models import Procedencia
from cortex.runtime.identidade import papel_no_user_md

logger = logging.getLogger("cortex.server")

_STATIC_DIR = Path(__file__).parent / "painel_static"
COOKIE = "painel_sessao"


# --------------------------- cookie assinado (HMAC) ------------------------ #

PAPEL_OPERADOR = "operador"
PAPEL_MESTRE = "mestre"


def _assinar(segredo: str, expira_em: int, papel: str = PAPEL_OPERADOR) -> str:
    """Cookie = papel.expiração.assinatura, assinado com o segredo DAQUELE papel.

    O papel entra no material assinado: forjar `mestre` exige a senha mestre —
    quem tem só a senha de operador não consegue escalar editando o cookie.
    """
    msg = f"{papel}.{expira_em}".encode()
    sig = hmac.new(segredo.encode(), msg, hashlib.sha256).hexdigest()
    return f"{papel}.{expira_em}.{sig}"


def _cookie_valido(segredo: str, valor: str | None, papel: str = PAPEL_OPERADOR) -> bool:
    if not segredo or not valor:
        return False
    partes = valor.split(".")
    if len(partes) != 3:
        return False
    papel_cookie, exp_str, sig = partes
    if papel_cookie != papel:
        return False
    try:
        expira_em = int(exp_str)
    except ValueError:
        return False
    msg = f"{papel_cookie}.{expira_em}".encode()
    esperado = hmac.new(segredo.encode(), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, esperado) and expira_em > int(time.time())


def papel_do_cookie(valor: str | None, senha: str, senha_mestre: str | None) -> str | None:
    """Papel da sessão pelo cookie: 'mestre', 'operador' — ou None se inválido."""
    if senha_mestre and _cookie_valido(senha_mestre, valor, PAPEL_MESTRE):
        return PAPEL_MESTRE
    if _cookie_valido(senha, valor, PAPEL_OPERADOR):
        return PAPEL_OPERADOR
    return None


# --------------------------- senha do operador ----------------------------- #

SENHA_MIN = 8
# Linha `painel_senha = "..."` do cortex.toml (aspas simples ou duplas).
_LINHA_SENHA = re.compile(r'^(\s*painel_senha\s*=\s*)(".*"|\'.*\')(\s*(?:#.*)?)$', re.MULTILINE)


class SenhaTomlError(Exception):
    """Não deu para persistir a senha nova no cortex.toml do deploy."""


def _escapar_toml(valor: str) -> str:
    """Escapa para string básica TOML (barra invertida e aspas duplas)."""
    return valor.replace("\\", "\\\\").replace('"', '\\"')


def atualizar_senha_no_toml(toml_path: Path, nova: str) -> None:
    """Reescreve APENAS a linha `painel_senha` do cortex.toml, preservando o resto.

    Substituição cirúrgica por regex (em vez de reserializar o TOML) de
    propósito: o arquivo é curado por humano e cheio de comentários que
    explicam cada campo — reserializar perderia tudo isso.
    """
    if not toml_path.is_file():
        raise SenhaTomlError(
            f"não encontrei o {toml_path.name} do deploy em {toml_path} — "
            "troque a senha editando o arquivo e reinicie o serviço"
        )
    texto = toml_path.read_text(encoding="utf-8")
    novo_texto, trocas = _LINHA_SENHA.subn(
        lambda m: f'{m.group(1)}"{_escapar_toml(nova)}"{m.group(3)}', texto, count=1
    )
    if trocas != 1:
        raise SenhaTomlError(
            f"não achei a linha `painel_senha = \"...\"` em {toml_path} — "
            "troque a senha editando o arquivo e reinicie o serviço"
        )
    # Escrita atômica: um crash no meio não pode deixar o deploy sem senha
    # válida (o painel não subiria no próximo boot — fail-safe às avessas).
    temp = toml_path.with_suffix(toml_path.suffix + ".tmp")
    temp.write_text(novo_texto, encoding="utf-8")
    temp.replace(toml_path)


# --------------------------- formação (modo mestre) ------------------------ #

PASTA_HISTORICO = ".historico"
# Arquivos de formação editáveis pelo modo mestre. tools.yaml fica de FORA: o
# catálogo tem integridade referencial com os playbooks (quebrar um nome de
# tool derruba a montagem inteira) — edição dele segue por Git.
_VALIDADORES = {
    "SOUL.md": "carregar_soul",
    "USER.md": "carregar_user",
    "AGENTS.md": None,      # índice em prosa: sem schema estruturado a validar
}


class FormacaoPathError(Exception):
    """Caminho pedido não é um arquivo de formação válido deste deploy."""


def resolver_arquivo_formacao(personas_dir: Path, arquivo: str) -> Path:
    """Traduz o nome pedido em caminho ABSOLUTO dentro de personas/ — ou levanta.

    Deny-by-default contra traversal: recusa caminho absoluto, '..', qualquer
    coisa que não termine em .md e tudo que, depois de resolvido, caia fora de
    personas/. Só SOUL/USER/AGENTS na raiz e .md dentro de playbooks/.
    """
    nome = (arquivo or "").strip().replace("\\", "/")
    if not nome or nome.startswith("/") or ".." in nome.split("/"):
        raise FormacaoPathError(f"caminho inválido: {arquivo!r}")
    if not nome.endswith(".md"):
        raise FormacaoPathError("só arquivos .md da formação podem ser lidos/editados")
    partes = nome.split("/")
    permitido = (len(partes) == 1 and partes[0] in _VALIDADORES) or (
        len(partes) == 2 and partes[0] == "playbooks"
    )
    if not permitido:
        raise FormacaoPathError(
            f"{arquivo!r} não é um arquivo de formação editável "
            f"(use {', '.join(_VALIDADORES)} ou playbooks/<nome>.md)"
        )
    base = personas_dir.resolve()
    destino = (base / nome).resolve()
    if not destino.is_relative_to(base):   # cinto e suspensório pós-resolve
        raise FormacaoPathError(f"caminho fora de personas/: {arquivo!r}")
    return destino


def validar_conteudo_formacao(nome: str, conteudo: str, tmp_dir: Path) -> None:
    """Valida o conteúdo NOVO com o MESMO parser da camada de identidade.

    Escreve num arquivo temporário e roda o parser real (carregar_soul/user/
    playbook) — YAML malformado ou schema quebrado levanta PersonaParseError
    ANTES de tocarmos o arquivo verdadeiro.
    """
    from cortex.identity.parser import carregar_playbook, carregar_soul, carregar_user

    base = nome.split("/")[-1]
    if nome.startswith("playbooks/"):
        parser = carregar_playbook
    elif base == "SOUL.md":
        parser = carregar_soul
    elif base == "USER.md":
        parser = carregar_user
    else:
        return   # AGENTS.md: prosa livre, nada estruturado a validar
    alvo = tmp_dir / base
    alvo.write_text(conteudo, encoding="utf-8")
    parser(alvo)   # PersonaParseError sobe para o chamador


def _fazer_backup(personas_dir: Path, nome: str, destino: Path) -> str | None:
    """Copia a versão atual para personas/.historico/ antes de sobrescrever.

    Nome achatado (playbooks/x.md → playbooks_x.md) para tudo caber num
    diretório só, com timestamp UTC — histórico legível sem subpastas.
    """
    if not destino.is_file():
        return None
    hist = personas_dir / PASTA_HISTORICO
    hist.mkdir(parents=True, exist_ok=True)
    carimbo = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    plano = nome.replace("/", "_").removesuffix(".md")
    copia = hist / f"{plano}.{carimbo}.md"
    copia.write_text(destino.read_text(encoding="utf-8"), encoding="utf-8")
    return copia.name


def _diff_unificado(antes: str, depois: str, nome: str, max_linhas: int = 200) -> str:
    """Diff legível para o audit — truncado para não inchar a trilha."""
    linhas = list(
        difflib.unified_diff(
            antes.splitlines(), depois.splitlines(),
            fromfile=f"a/{nome}", tofile=f"b/{nome}", lineterm="", n=2,
        )
    )
    if len(linhas) > max_linhas:
        linhas = linhas[:max_linhas] + [f"... (+{len(linhas) - max_linhas} linhas)"]
    return "\n".join(linhas)


# --------------------------- serialização leve ----------------------------- #


def _belief_dict(b) -> dict:
    return {
        "key": b.key,
        "value": b.value,
        "dominio": b.domain,
        "confianca": round(b.confidence, 2),
        "saliencia": getattr(b, "salience", b.seen_count),
        "fonte": b.source.name,
        "procedencia": b.source.procedencia.value,
        "justificativa": b.justification.why,
        "verificavel": b.justification.verifiable,
    }


def _historico_dict(b) -> dict:
    return {
        "value": b.value,
        "status": b.status.value,
        "fonte": b.source.name,
        "procedencia": b.source.procedencia.value,
        "valido_de": b.valid_at.isoformat(),
        "valido_ate": b.invalid_at.isoformat() if b.invalid_at else None,
        "razao_mudanca": b.reason_for_change,
        "supersede": b.supersedes,
    }


def _proposta_dict(p) -> dict:
    externa = p.source.procedencia is Procedencia.EXTERNA
    return {
        "id": p.id,
        "tipo": p.kind.value,
        "chave": p.key,
        "vigente": p.current_value,
        "proposto": p.proposed_value,
        "risco": p.risk.value,
        "fonte": p.source.name,
        "procedencia": p.source.procedencia.value,
        "externa": externa,
        "porque": p.justification.why,
        "evidencia": p.justification.evidence,
        "razao_escalou": p.reason,
        "episodio_origem": p.origin_episode_id,
        "quando": p.created_at.isoformat(),
        "status": p.status.value,
    }


# ------------------------------- montagem ---------------------------------- #


def montar_painel(
    app: FastAPI,
    *,
    persona: Persona,
    engine: MemoryEngine,
    kb: KnowledgeBase,
    kb_path: Path,
    audit,
    lock,
    senha: str,
    operador: str,
    sessao_horas: int,
    toml_path: Path | None = None,
    senha_mestre: str | None = None,
    personas_dir: Path | None = None,
    chat_enviar=None,
    chat_historico=None,
    chat_novo=None,
) -> None:
    """Registra as rotas do painel no app. `operador` é o autor das decisões.

    Valida que o operador EXISTE no USER.md (órfão = erro de config, padrão da
    casa). A AUTORIDADE em si é enforçada por engine.aprovar (4b): um operador
    que existe mas não manda no domínio leva 409 na hora de decidir.

    `toml_path` é o cortex.toml do deploy — necessário para a troca de senha
    pelo painel (sem ele a rota responde 409 explicando como trocar à mão).

    MODO MESTRE (`senha_mestre`): senha SEPARADA que dá à sessão o papel
    'mestre', único que pode editar a FORMAÇÃO (SOUL/USER/playbooks). Fail-safe
    igual ao da senha do painel: vazia/ausente = o modo mestre NÃO EXISTE e as
    rotas de formação respondem 403 para todo mundo. A fronteira original segue
    valendo para o OPERADOR (cliente): ele configura, cura e aprova — nunca
    edita formação. O mestre é o criador/dev, e cada edição sua é auditada com
    diff e gera backup.
    """
    if papel_no_user_md(persona, operador) is None:
        raise ValueError(
            f"painel_operador '{operador}' não existe no USER.md — configure um "
            "nome de pessoa real (gestor ou colega) como operador do painel"
        )

    router = APIRouter(prefix="/painel")
    # A senha é MUTÁVEL em runtime (trocável pelo painel) — holder em vez de
    # closure sobre o valor, para a troca valer no ato, sem reiniciar o serviço.
    # É também o segredo que assina o cookie: trocar a senha invalida as
    # sessões abertas (inclusive as de quem roubou a senha antiga).
    estado = {"senha": senha, "senha_mestre": (senha_mestre or "").strip() or None}

    def sessao_papel(painel_sessao: str | None = Cookie(default=None)) -> str:
        papel = papel_do_cookie(painel_sessao, estado["senha"], estado["senha_mestre"])
        if papel is None:
            raise HTTPException(status_code=401, detail="sessão do painel ausente ou expirada")
        return papel

    def requer_painel(painel_sessao: str | None = Cookie(default=None)) -> None:
        sessao_papel(painel_sessao)

    def requer_mestre(painel_sessao: str | None = Cookie(default=None)) -> str:
        """Só o MESTRE passa. Sem senha mestre configurada, ninguém passa."""
        if not estado["senha_mestre"]:
            raise HTTPException(
                status_code=403,
                detail="modo mestre não está habilitado neste deploy (painel_senha_mestre vazia)",
            )
        if sessao_papel(painel_sessao) != PAPEL_MESTRE:
            raise HTTPException(
                status_code=403, detail="ação restrita ao modo mestre (edição de formação)"
            )
        return PAPEL_MESTRE

    def _emitir_cookie(response: Response, papel: str = PAPEL_OPERADOR) -> None:
        segredo = estado["senha_mestre"] if papel == PAPEL_MESTRE else estado["senha"]
        expira_em = int(time.time()) + sessao_horas * 3600
        response.set_cookie(
            COOKIE,
            _assinar(segredo, expira_em, papel),
            httponly=True,
            samesite="lax",
            max_age=sessao_horas * 3600,
        )

    # ---- página e login (não protegidos) --------------------------------- #

    @router.get("")
    def index() -> HTMLResponse:
        """Serve o HTML com CSS/JS VERSIONADOS pelo mtime (cache-busting).

        Sem isso, o navegador segura app.css/app.js antigos do cache e uma
        atualização do painel (tema novo, aba nova) só aparece com refresh
        forçado — que ninguém lembra de dar. O HTML em si vai com no-store:
        é pequeno, e é ele que carrega os ponteiros versionados.
        """
        html = (_STATIC_DIR / "index.html").read_text(encoding="utf-8")
        versao = 0
        for ativo in ("app.css", "app.js"):
            caminho = _STATIC_DIR / ativo
            if caminho.is_file():
                versao = max(versao, int(caminho.stat().st_mtime))
        html = html.replace("/painel/static/app.css", f"/painel/static/app.css?v={versao}")
        html = html.replace("/painel/static/app.js", f"/painel/static/app.js?v={versao}")
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    @router.post("/login")
    def login(body: dict, response: Response) -> dict:
        """Mesma porta para os dois papéis: a SENHA decide se é mestre ou operador."""
        oferecida = str(body.get("senha", ""))
        mestre = estado["senha_mestre"]
        if mestre and hmac.compare_digest(oferecida, mestre):
            _emitir_cookie(response, PAPEL_MESTRE)
            logger.info("login no painel em MODO MESTRE")
            return {"ok": True, "operador": operador, "papel": PAPEL_MESTRE}
        if not hmac.compare_digest(oferecida, estado["senha"]):
            raise HTTPException(status_code=401, detail="senha inválida")
        _emitir_cookie(response)
        return {"ok": True, "operador": operador, "papel": PAPEL_OPERADOR}

    @router.post("/logout")
    def logout(response: Response) -> dict:
        response.delete_cookie(COOKIE)
        return {"ok": True}

    # ---- leitura (JSON, protegida) --------------------------------------- #

    def _custo_tokens_hoje() -> int:
        if audit is None:
            return 0
        hoje = datetime.now(UTC).date().isoformat()
        total = 0
        for ln in audit.ultimos(2000):
            if ln.get("tipo") == "llm_request" and str(ln.get("ts", "")).startswith(hoje):
                total += int(ln.get("input_tokens", 0)) + int(ln.get("output_tokens", 0))
        return total

    @router.get("/api/resumo")
    def resumo(papel_sessao: str = _dep(sessao_papel)) -> dict:
        return {
            "persona": persona.soul.nome,
            "papel": persona.soul.papel,          # papel PROFISSIONAL da persona
            "modo": papel_sessao,                 # papel da SESSÃO: operador | mestre
            "gestor": persona.user.autoridade.gestor.nome,
            "operador": operador,
            "crencas_ativas": len(engine.beliefs_ativos()),
            "episodios": len(engine.store.all_episodes()),
            "pendentes": len(engine.pending_approvals),
            "custo_tokens_hoje": _custo_tokens_hoje(),
        }

    @router.get("/api/memoria", dependencies=[_dep(requer_painel)])
    def memoria(q: str = "", dominio: str = "") -> dict:
        beliefs = engine.beliefs_ativos()
        ql = q.strip().lower()
        out = []
        for b in beliefs:
            if dominio and b.domain != dominio:
                continue
            if ql and ql not in f"{b.key} {b.value} {b.source.name}".lower():
                continue
            out.append(_belief_dict(b))
        out.sort(key=lambda d: d["key"])
        return {"crencas": out, "total": len(out)}

    @router.get("/api/memoria/{key:path}/historico", dependencies=[_dep(requer_painel)])
    def historico(key: str) -> dict:
        linha = engine.history(key)
        return {"key": key, "historico": [_historico_dict(b) for b in linha]}

    @router.get("/api/fila", dependencies=[_dep(requer_painel)])
    def fila(status: str = "pendente") -> dict:
        from cortex.memory.learning import ProposalStatus

        try:
            st = ProposalStatus(status) if status else None
        except ValueError:
            st = ProposalStatus.PENDENTE
        propostas = engine.store.proposals(st)
        return {"propostas": [_proposta_dict(p) for p in propostas], "total": len(propostas)}

    @router.get("/api/kb", dependencies=[_dep(requer_painel)])
    def kb_listar() -> dict:
        docs = []
        if kb_path.is_dir():
            for arquivo in sorted(kb_path.glob("*.md")):
                if arquivo.name.lower() == "readme.md":
                    continue
                try:
                    doc = carregar_documento(arquivo)
                except KBParseError:
                    docs.append({"arquivo": arquivo.name, "erro": "frontmatter inválido"})
                    continue
                docs.append(
                    {
                        "arquivo": doc.arquivo,
                        "titulo": doc.titulo,
                        "autoridade": doc.autoridade,
                        "dominio": doc.dominio,
                        "vigente_desde": doc.vigente_desde.isoformat(),
                        "vigente_ate": doc.vigente_ate.isoformat() if doc.vigente_ate else None,
                        "revogado": not vigente(doc),
                    }
                )
        return {"documentos": docs, "total": len(docs)}

    @router.get("/api/audit", dependencies=[_dep(requer_painel)])
    def audit_listar(n: int = 50) -> dict:
        linhas = audit.ultimos(n) if audit is not None else []
        return {"linhas": linhas, "total": len(linhas)}

    # ---- ações GOVERNADAS (protegidas) ----------------------------------- #

    def _decidir(proposta_id: int, body: dict, aprovar: bool) -> dict:
        razao = str(body.get("razao", "")).strip()
        if not razao:
            raise HTTPException(status_code=400, detail="a decisão exige uma justificativa")
        with lock:  # serializa com os turnos (engine não é thread-safe)
            try:
                ep = (
                    engine.aprovar(proposta_id, operador, razao)
                    if aprovar
                    else engine.rejeitar(proposta_id, operador, razao)
                )
            except (AutoridadeInsuficienteError, PropostaJaDecididaError, ValueError) as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True, "por": operador, "resultado": ep.action}

    @router.post("/api/fila/{proposta_id}/aprovar", dependencies=[_dep(requer_painel)])
    def aprovar(proposta_id: int, body: dict) -> dict:
        return _decidir(proposta_id, body, aprovar=True)

    @router.post("/api/fila/{proposta_id}/rejeitar", dependencies=[_dep(requer_painel)])
    def rejeitar(proposta_id: int, body: dict) -> dict:
        return _decidir(proposta_id, body, aprovar=False)

    @router.post("/api/kb/upload", dependencies=[_dep(requer_painel)])
    def kb_upload(body: dict) -> dict:
        nome = str(body.get("nome", "")).strip()
        conteudo = body.get("conteudo", "")
        if not nome.endswith(".md") or "/" in nome or "\\" in nome:
            raise HTTPException(status_code=400, detail="nome inválido: use um arquivo .md simples")
        kb_path.mkdir(parents=True, exist_ok=True)
        destino = kb_path / nome
        destino.write_text(conteudo, encoding="utf-8")
        try:
            carregar_documento(destino)  # valida a CURADORIA (frontmatter)
        except KBParseError as exc:
            destino.unlink(missing_ok=True)  # não deixa doc inválido na KB
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        with lock:
            resumo_idx = kb.indexar()
        return {"ok": True, "arquivo": nome, **resumo_idx}

    @router.post("/api/kb/reindexar", dependencies=[_dep(requer_painel)])
    def kb_reindexar() -> dict:
        with lock:
            return {"ok": True, **kb.indexar()}

    # ---- chat com a persona (protegido) ---------------------------------- #

    @router.get("/api/chat", dependencies=[_dep(requer_painel)])
    def chat_ler() -> dict:
        """Conversa da sessão viva — permite recarregar a página sem perder o fio."""
        if chat_historico is None:
            raise HTTPException(status_code=503, detail="chat indisponível neste servidor")
        return {"mensagens": chat_historico(), "persona": persona.soul.nome}

    @router.post("/api/chat", dependencies=[_dep(requer_painel)])
    def chat_falar(body: dict) -> dict:
        """Envia um turno à persona pelo canal 'painel' (identidade = operador).

        Passa pelo MESMO pipeline dos bridges: lock, governança, promoção de
        memória e notificação de proposta nova. O que muda é só o canal.
        """
        if chat_enviar is None:
            raise HTTPException(status_code=503, detail="chat indisponível neste servidor")
        texto = str(body.get("texto", "")).strip()
        if not texto:
            raise HTTPException(status_code=400, detail="mensagem vazia")
        return {"resposta": chat_enviar(texto)}

    @router.post("/api/chat/novo", dependencies=[_dep(requer_painel)])
    def chat_reiniciar() -> dict:
        """Descarta a sessão: a próxima mensagem começa do zero (efemeridade)."""
        if chat_novo is None:
            raise HTTPException(status_code=503, detail="chat indisponível neste servidor")
        chat_novo()
        return {"ok": True}

    # ---- formação (MODO MESTRE — criador/dev, nunca o operador) ---------- #

    def _dir_personas() -> Path:
        if personas_dir is None:
            raise HTTPException(
                status_code=409,
                detail="deploy sem pasta personas/ conhecida — edite os arquivos direto no disco",
            )
        return Path(personas_dir)

    @router.get("/api/formacao", dependencies=[_dep(requer_mestre)])
    def formacao_listar() -> dict:
        """Lista os arquivos de formação editáveis (SOUL/USER/AGENTS + playbooks)."""
        base = _dir_personas()
        itens = []
        for nome in _VALIDADORES:
            alvo = base / nome
            if alvo.is_file():
                itens.append({"arquivo": nome, "bytes": alvo.stat().st_size})
        pb = base / "playbooks"
        if pb.is_dir():
            for alvo in sorted(pb.glob("*.md")):
                itens.append({"arquivo": f"playbooks/{alvo.name}", "bytes": alvo.stat().st_size})
        return {"arquivos": itens, "personas_dir": str(base)}

    @router.get("/api/formacao/{arquivo:path}", dependencies=[_dep(requer_mestre)])
    def formacao_ler(arquivo: str) -> dict:
        base = _dir_personas()
        try:
            alvo = resolver_arquivo_formacao(base, arquivo)
        except FormacaoPathError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not alvo.is_file():
            raise HTTPException(status_code=404, detail=f"não existe: {arquivo}")
        return {"arquivo": arquivo, "conteudo": alvo.read_text(encoding="utf-8")}

    @router.post("/api/formacao/{arquivo:path}", dependencies=[_dep(requer_mestre)])
    def formacao_salvar(arquivo: str, body: dict) -> dict:
        """Salva um arquivo de formação: valida → faz backup → grava → audita.

        Ordem deliberada: NADA toca o disco antes do parser aprovar o conteúdo
        novo (YAML quebrado deixaria a persona sem subir no próximo boot).
        """
        base = _dir_personas()
        try:
            alvo = resolver_arquivo_formacao(base, arquivo)
        except FormacaoPathError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        conteudo = body.get("conteudo")
        if not isinstance(conteudo, str) or not conteudo.strip():
            raise HTTPException(status_code=400, detail="conteúdo vazio")

        from cortex.identity.parser import PersonaParseError

        with tempfile.TemporaryDirectory() as tmp:
            try:
                validar_conteudo_formacao(arquivo, conteudo, Path(tmp))
            except PersonaParseError as exc:
                # Arquivo REAL intocado — o curador corrige e tenta de novo.
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        antes = alvo.read_text(encoding="utf-8") if alvo.is_file() else ""
        try:
            backup = _fazer_backup(base, arquivo, alvo)
            alvo.parent.mkdir(parents=True, exist_ok=True)
            alvo.write_text(conteudo, encoding="utf-8")
        except OSError as exc:
            raise HTTPException(status_code=409, detail=f"falha ao gravar: {exc}") from exc

        if audit is not None:
            audit.registrar(
                "edicao_formacao",
                arquivo=arquivo,
                papel=PAPEL_MESTRE,
                operador=operador,
                tamanho_antes=len(antes),
                tamanho_depois=len(conteudo),
                backup=backup,
                diff=_diff_unificado(antes, conteudo, arquivo),
            )
        logger.warning("FORMAÇÃO editada em modo mestre: %s (backup=%s)", arquivo, backup)
        return {
            "ok": True,
            "arquivo": arquivo,
            "backup": backup,
            # A persona é carregada UMA vez na subida (Session/loop/painel a
            # recebem pronta) — não há reload barato: reiniciar é o caminho.
            "requer_restart": True,
            "aviso": (
                "salvo e auditado. A persona em memória só muda no restart do "
                "serviço (ex.: systemctl restart cortex)."
            ),
        }

    # ---- senha do operador (protegida) ----------------------------------- #

    @router.post("/api/senha", dependencies=[_dep(requer_painel)])
    def trocar_senha(body: dict, response: Response) -> dict:
        """Troca a senha do painel: valida a atual, persiste no toml e reemite o cookie.

        A senha é config do DEPLOY (mora no cortex.toml), então a troca só vale
        se o arquivo for gravável — persistir primeiro e só então mudar em
        memória evita divergência entre o que vale agora e o que vale no
        próximo boot. Estar logado NÃO basta: exige a senha atual (defesa
        contra sessão esquecida aberta na máquina).
        """
        atual = str(body.get("senha_atual", ""))
        nova = str(body.get("nova_senha", ""))
        if not hmac.compare_digest(atual, estado["senha"]):
            raise HTTPException(status_code=401, detail="senha atual incorreta")
        if len(nova) < SENHA_MIN:
            raise HTTPException(
                status_code=400, detail=f"a nova senha precisa ter ao menos {SENHA_MIN} caracteres"
            )
        if nova == estado["senha"]:
            raise HTTPException(status_code=400, detail="a nova senha é igual à atual")
        if toml_path is None:
            raise HTTPException(
                status_code=409,
                detail="deploy sem cortex.toml conhecido — troque a senha no arquivo e reinicie",
            )
        try:
            atualizar_senha_no_toml(toml_path, nova)
        except SenhaTomlError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=409, detail=f"falha ao gravar {toml_path}: {exc}"
            ) from exc

        estado["senha"] = nova          # vale no ato — sem reiniciar o serviço
        _emitir_cookie(response)        # mantém ESTA sessão viva (as outras caem)
        if audit is not None:
            audit.registrar("painel_senha_trocada", operador=operador)
        logger.info("senha do painel trocada pelo operador %s", operador)
        return {"ok": True}

    app.include_router(router)
    app.mount("/painel/static", StaticFiles(directory=str(_STATIC_DIR)), name="painel_static")


def _dep(func):
    """Açúcar: embrulha uma função em fastapi.Depends sem importá-lo no topo."""
    from fastapi import Depends

    return Depends(func)
