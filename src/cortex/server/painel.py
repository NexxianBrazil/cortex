"""Painel HTML do operador (Fase 7d) — montado no MESMO servidor da 7a.

FRONTEIRA DE GOVERNANÇA (o ponto da fase): o painel é ALOCAÇÃO (do cliente). Ele
configura, cura a KB, OPERA a Learning Queue (aprova/rejeita com justificativa) e
VISUALIZA memória/audit. Ele NÃO edita o SOUL (formação é da Nexxian, via Git) e
NÃO escreve crença direto no banco — toda escrita passa pelo observe()/governança.
O painel PROPÕE e APROVA; nunca digita verdade. Não existe rota que crie belief.

Autenticação do OPERADOR (pessoa no navegador) por cookie assinado (HMAC stdlib,
HttpOnly) — separada do token de bridge das rotas /v1/* (clientes diferentes).
"""

import hashlib
import hmac
import logging
import re
import time
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Cookie, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
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


def _assinar(segredo: str, expira_em: int) -> str:
    sig = hmac.new(segredo.encode(), str(expira_em).encode(), hashlib.sha256).hexdigest()
    return f"{expira_em}.{sig}"


def _cookie_valido(segredo: str, valor: str | None) -> bool:
    if not valor or "." not in valor:
        return False
    exp_str, sig = valor.rsplit(".", 1)
    try:
        expira_em = int(exp_str)
    except ValueError:
        return False
    esperado = hmac.new(segredo.encode(), str(expira_em).encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, esperado) and expira_em > int(time.time())


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
) -> None:
    """Registra as rotas do painel no app. `operador` é o autor das decisões.

    Valida que o operador EXISTE no USER.md (órfão = erro de config, padrão da
    casa). A AUTORIDADE em si é enforçada por engine.aprovar (4b): um operador
    que existe mas não manda no domínio leva 409 na hora de decidir.

    `toml_path` é o cortex.toml do deploy — necessário para a troca de senha
    pelo painel (sem ele a rota responde 409 explicando como trocar à mão).
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
    estado = {"senha": senha}

    def requer_painel(painel_sessao: str | None = Cookie(default=None)) -> None:
        if not _cookie_valido(estado["senha"], painel_sessao):
            raise HTTPException(status_code=401, detail="sessão do painel ausente ou expirada")

    def _emitir_cookie(response: Response) -> None:
        expira_em = int(time.time()) + sessao_horas * 3600
        response.set_cookie(
            COOKIE,
            _assinar(estado["senha"], expira_em),
            httponly=True,
            samesite="lax",
            max_age=sessao_horas * 3600,
        )

    # ---- página e login (não protegidos) --------------------------------- #

    @router.get("")
    def index() -> FileResponse:
        return FileResponse(_STATIC_DIR / "index.html")

    @router.post("/login")
    def login(body: dict, response: Response) -> dict:
        if not hmac.compare_digest(str(body.get("senha", "")), estado["senha"]):
            raise HTTPException(status_code=401, detail="senha inválida")
        _emitir_cookie(response)
        return {"ok": True, "operador": operador}

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

    @router.get("/api/resumo", dependencies=[_dep(requer_painel)])
    def resumo() -> dict:
        return {
            "persona": persona.soul.nome,
            "papel": persona.soul.papel,
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
