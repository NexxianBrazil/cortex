"""Gerência de subprocessos do criador — sobe/para `cortex servir` por deploy.

Cada deploy no ar é um processo filho `cortex servir --deploy DIR --porta N`,
com porta própria (alocada a partir de 8420) e `cwd` no deploy (para o `.env`
local dele ser lido pela config). O registro PID/porta é persistido em
`~/.cortex-creator/processos.json` para sobreviver a um restart do criador.

Segurança: só sobe deploys DENTRO do diretório-base configurado, valida o
`cortex.toml` antes, e nunca passa entrada do usuário pelo shell (lista de
args, sem shell=True).
"""

import json
import os
import signal
import socket
import subprocess
import sys
from pathlib import Path

PORTA_INICIAL = 8420


class ProcessoError(Exception):
    """Falha de gerência de processo (deploy inválido, já no ar, não está no ar)."""


def _porta_livre(porta: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", porta)) != 0


def _vivo(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


class GerenciadorProcessos:
    """Sobe/para servidores de operação por deploy e persiste o registro."""

    def __init__(
        self,
        base_dir: Path | str,
        registro_dir: Path | str | None = None,
        porta_inicial: int = PORTA_INICIAL,
    ) -> None:
        self.base_dir = Path(base_dir).expanduser().resolve()
        self._dir = Path(registro_dir or Path.home() / ".cortex-creator").expanduser()
        self._registro = self._dir / "processos.json"
        self._porta_inicial = porta_inicial

    # ---- registro persistido ---------------------------------------------- #

    def _carregar(self) -> dict:
        if not self._registro.is_file():
            return {}
        try:
            return json.loads(self._registro.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _salvar(self, registro: dict) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._registro.write_text(
            json.dumps(registro, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ---- consulta ---------------------------------------------------------- #

    def status(self, nome: str) -> dict | None:
        """{'pid', 'porta', 'vivo'} do deploy, ou None se nunca subiu (ou já parou)."""
        info = self._carregar().get(nome)
        if info is None:
            return None
        return {**info, "vivo": _vivo(info["pid"])}

    def _proxima_porta(self, registro: dict) -> int:
        usadas = {info["porta"] for info in registro.values()}
        porta = self._porta_inicial
        while porta in usadas or not _porta_livre(porta):
            porta += 1
        return porta

    def _dir_do_deploy(self, nome: str) -> Path:
        """Resolve e VALIDA o deploy: dentro do base_dir e com cortex.toml."""
        deploy = (self.base_dir / nome).resolve()
        if deploy.parent != self.base_dir:
            raise ProcessoError(f"deploy fora do diretório-base: {nome}")
        if not (deploy / "cortex.toml").is_file():
            raise ProcessoError(f"não é um deploy de Cortex (sem cortex.toml): {deploy}")
        return deploy

    # ---- subir / parar ------------------------------------------------------ #

    def subir(self, nome: str) -> dict:
        """Sobe `cortex servir` para o deploy; devolve {'pid', 'porta'}."""
        deploy = self._dir_do_deploy(nome)
        registro = self._carregar()
        atual = registro.get(nome)
        if atual and _vivo(atual["pid"]):
            raise ProcessoError(f"'{nome}' já está no ar (porta {atual['porta']})")

        porta = self._proxima_porta(registro)
        log_dir = self._dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log = (log_dir / f"{nome}.log").open("ab")
        # Lista de args, sem shell — nada do usuário passa por um shell.
        proc = subprocess.Popen(
            [sys.executable, "-m", "cortex", "servir", "--deploy", str(deploy),
             "--porta", str(porta)],
            cwd=deploy,  # o .env do deploy é lido relativo ao CWD
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        registro[nome] = {"pid": proc.pid, "porta": porta}
        self._salvar(registro)
        return {"pid": proc.pid, "porta": porta}

    def parar(self, nome: str) -> None:
        """Encerra o servidor do deploy (SIGTERM) e limpa o registro."""
        registro = self._carregar()
        info = registro.pop(nome, None)
        if info is None:
            raise ProcessoError(f"'{nome}' não está no ar")
        if _vivo(info["pid"]):
            try:
                os.kill(info["pid"], signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        self._salvar(registro)
