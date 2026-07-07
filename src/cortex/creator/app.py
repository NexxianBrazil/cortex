"""App FastAPI do Criador Visual (`cortex criar-visual`) — frontend + rotas JSON.

FRONTEIRA DE GOVERNANÇA: o criador é sobre NASCER um Cortex (alocação — nome,
função, gestor, provider, KB, subir/parar). Ele nunca edita SOUL/formação
(produto Nexxian) nem escreve memória — as únicas escritas são criar deploy,
subir/parar servidor e indexar KB; o teste-fronteira varre as rotas e garante.

Segurança: ferramenta de OPERADOR LOCAL — bind em 127.0.0.1 por padrão, sem
auth própria (mesma máquina = mesmo operador). Exposição em rede exigiria
TLS + autenticação e está fora de escopo (ver README).
"""

import importlib.util
import logging
import shutil
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from cortex.creator.deploys import (
    PROVIDERS,
    CriacaoInvalidaError,
    config_do_deploy,
    criar_cortex,
    listar_deploys,
    validar_nome_dir,
)
from cortex.creator.processos import GerenciadorProcessos, ProcessoError
from cortex.identity import carregar_persona
from cortex.knowledge import KnowledgeBase, criar_embedder
from cortex.runtime import LoopLimiteExcedidoError, Session, montar_runtime
from cortex.scaffold import ScaffoldError

logger = logging.getLogger("cortex.creator")

_STATIC_DIR = Path(__file__).parent / "static"
_GIB = 1024**3


class PedidoCriar(BaseModel):
    """Campos do formulário 'Criar um Cortex' — validação de negócio é 400, não 422."""

    nome: str = ""
    funcao: str = ""
    gestor: str = ""
    dominio: str = "geral"
    destino: str = ""
    provider: str = "stub"
    api_key: str | None = None
    base_url: str | None = None


def _diagnostico(base_dir: Path) -> list[dict]:
    """Checagens de pré-requisito da tela de instalação — diagnostica e ORIENTA.

    O criador nunca instala pacotes do sistema sozinho (frágil/inseguro por
    dentro de um web app); cada item reprovado traz o 'como resolver'.
    """
    itens = []

    v = sys.version_info
    itens.append({
        "id": "python",
        "rotulo": "Python 3.11+",
        "ok": v >= (3, 11),
        "obrigatorio": True,
        "detalhe": f"Python {v.major}.{v.minor}.{v.micro}",
        "como_resolver": "Instale Python 3.11 ou mais novo (python.org ou o gerenciador do SO).",
    })

    for modulo, rotulo, obrigatorio, resolver in (
        ("anthropic", "SDK Anthropic (provider Claude)", True, "pip install anthropic"),
        ("openai", "SDK OpenAI (provider Local/Ollama)", True, "pip install openai"),
        (
            "graphiti_core",
            "Memória persistente (graphiti-core[kuzu]) — opcional",
            False,
            'pip install "graphiti-core[kuzu]"  (só para store = "graphiti")',
        ),
    ):
        presente = importlib.util.find_spec(modulo) is not None
        itens.append({
            "id": modulo,
            "rotulo": rotulo,
            "ok": presente,
            "obrigatorio": obrigatorio,
            "detalhe": "instalado" if presente else "não encontrado",
            "como_resolver": resolver,
        })

    try:
        base_dir.mkdir(parents=True, exist_ok=True)
        (base_dir / ".escrita_teste").write_text("ok", encoding="utf-8")
        (base_dir / ".escrita_teste").unlink()
        base_ok, base_detalhe = True, str(base_dir)
    except OSError as exc:
        base_ok, base_detalhe = False, f"{base_dir}: {exc}"
    itens.append({
        "id": "diretorio_base",
        "rotulo": "Diretório dos deploys gravável",
        "ok": base_ok,
        "obrigatorio": True,
        "detalhe": base_detalhe,
        "como_resolver": "Ajuste as permissões da pasta ou rode com --base apontando para outra.",
    })

    livre = shutil.disk_usage(base_dir if base_dir.is_dir() else Path.home()).free
    itens.append({
        "id": "disco",
        "rotulo": "Espaço em disco (≥ 1 GiB livre)",
        "ok": livre >= _GIB,
        "obrigatorio": True,
        "detalhe": f"{livre / _GIB:.1f} GiB livres",
        "como_resolver": "Libere espaço em disco antes de criar deploys.",
    })
    return itens


def criar_app_criador(*, base_dir: Path, processos: GerenciadorProcessos) -> FastAPI:
    """Monta o app do criador sobre um diretório-base de deploys.

    `processos` é injetado (testes usam um dublê — nenhum teste sobe processo
    real). O app não guarda estado além de um cache de sessões de teste.
    """
    base_dir = Path(base_dir).expanduser().resolve()
    app = FastAPI(title="Cortex — Criador Visual", version="1")
    # Mini-chat: (loop, session) por deploy, vivos enquanto o criador roda. A
    # conversa é EFÊMERA por design — operação de verdade é no painel 7d.
    chats: dict[str, tuple] = {}

    def _dir_do_deploy(nome: str) -> Path:
        try:
            validar_nome_dir(nome)
        except CriacaoInvalidaError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        deploy = base_dir / nome
        if not (deploy / "cortex.toml").is_file():
            raise HTTPException(status_code=404, detail=f"deploy não encontrado: {nome}")
        return deploy

    # ------------------------------ diagnóstico ---------------------------- #

    @app.get("/api/ambiente")
    def ambiente() -> dict:
        itens = _diagnostico(base_dir)
        return {
            "itens": itens,
            "pronto": all(i["ok"] for i in itens if i["obrigatorio"]),
            "diretorio_base": str(base_dir),
        }

    # ------------------------------ deploys -------------------------------- #

    @app.get("/api/deploys")
    def deploys() -> dict:
        lista = listar_deploys(base_dir)
        for d in lista:
            st = processos.status(d["pasta"])
            d["no_ar"] = bool(st and st["vivo"])
            d["porta"] = st["porta"] if d["no_ar"] else None
            d["painel_url"] = f"http://127.0.0.1:{st['porta']}/painel" if d["no_ar"] else None
        return {"deploys": lista, "providers": PROVIDERS}

    @app.post("/api/criar")
    def criar(pedido: PedidoCriar) -> dict:
        try:
            caminho = criar_cortex(
                base_dir,
                nome=pedido.nome,
                funcao=pedido.funcao,
                gestor=pedido.gestor,
                dominio=pedido.dominio,
                destino=pedido.destino,
                provider=pedido.provider,
                api_key=pedido.api_key,
                base_url=pedido.base_url,
            )
        except (CriacaoInvalidaError, ScaffoldError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        logger.info("deploy criado em %s (provider=%s)", caminho, pedido.provider)
        return {
            "deploy": str(caminho),
            "pasta": caminho.name,
            "proximos_passos": [
                "Teste o Cortex no mini-chat (botão Testar) para sentir a persona.",
                f"Cure a KB dele em {caminho / 'kb'} e clique em 'Indexar KB'.",
                "Clique em 'Subir' e siga para o painel de operação (fila, memória, canais).",
                f"A formação (personas/) veio da Nexxian — curadoria fina é em {caminho}.",
            ],
        }

    # --------------------------- subir / parar ------------------------------ #

    @app.post("/api/deploys/{nome}/subir")
    def subir(nome: str) -> dict:
        _dir_do_deploy(nome)
        try:
            info = processos.subir(nome)
        except ProcessoError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "porta": info["porta"],
            "url": f"http://127.0.0.1:{info['porta']}",
            "painel_url": f"http://127.0.0.1:{info['porta']}/painel",
        }

    @app.post("/api/deploys/{nome}/parar")
    def parar(nome: str) -> dict:
        _dir_do_deploy(nome)
        try:
            processos.parar(nome)
        except ProcessoError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"parado": True}

    # ------------------------------- KB ------------------------------------- #

    @app.post("/api/deploys/{nome}/kb/indexar")
    def kb_indexar(nome: str) -> dict:
        deploy = _dir_do_deploy(nome)
        config = config_do_deploy(deploy)
        try:
            resumo = KnowledgeBase(config.kb_path, criar_embedder(config)).indexar()
        except Exception as exc:  # noqa: BLE001 — erro de curadoria/embedding vira 400 legível
            raise HTTPException(status_code=400, detail=f"falha ao indexar a KB: {exc}") from exc
        return resumo

    # ----------------------------- mini-chat -------------------------------- #

    @app.get("/api/deploys/{nome}/testar")
    def testar(nome: str, texto: str) -> dict:
        """Turno de teste no PRÓPRIO processo do criador (stub funciona offline).

        É o 'experimente aqui' — sessão efêmera, sem canal real. A operação de
        verdade (fila, memória, WhatsApp) vive no servidor/painel do deploy.
        """
        deploy = _dir_do_deploy(nome)
        if not texto.strip():
            raise HTTPException(status_code=400, detail="texto vazio")
        if nome not in chats:
            config = config_do_deploy(deploy)
            persona = carregar_persona(config.personas_dir)
            loop, _engine = montar_runtime(config, persona)
            chats[nome] = (loop, Session(persona), persona.soul.nome)
        loop, session, persona_nome = chats[nome]
        try:
            resposta = loop.executar_turno(session, texto)
        except LoopLimiteExcedidoError as exc:
            raise HTTPException(status_code=400, detail=f"turno abortado: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 — provider real pode falhar (chave, rede)
            raise HTTPException(
                status_code=502, detail=f"o provider do deploy falhou: {exc}"
            ) from exc
        return {"resposta": resposta, "persona": persona_nome}

    # ------------------------------ frontend -------------------------------- #

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(_STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    return app
