"""
Herramienta MCP: análisis de código con OpenCode (agente sobre repos locales).

Sigue el mismo patrón que ask_deepseek: una segunda opinión externa, de solo
consulta, registrada de forma aditiva desde server.py:

    from tools_opencode import register_opencode_tools
    register_opencode_tools(mcp)

A diferencia de ask_deepseek (que solo recibe texto), OpenCode corre como
agente DENTRO de un repo local y puede leer su código para responder.

Variables de entorno (ver .env.example), todas opcionales salvo
OPENCODE_REPOS_DIR, que es obligatoria para usar la herramienta:
    OPENCODE_REPOS_DIR        carpeta que contiene tus repos (raíz permitida)
    OPENCODE_BIN              ejecutable de OpenCode (por defecto "opencode")
    OPENCODE_MODEL            modelo, ej. "anthropic/claude-sonnet-4" (opcional)
    OPENCODE_TIMEOUT_S        timeout por defecto en segundos (por defecto 300)
    OPENCODE_MAX_OUTPUT_CHARS tope de caracteres devueltos (por defecto 20000)

Repos remotos (siempre última versión):
  `repo` puede ser una URL https de GitHub o Azure Repos. Si la carpeta no
  existe dentro de OPENCODE_REPOS_DIR se clona (superficial, --depth 1); si ya
  existe se actualiza con `git pull --ff-only` antes de analizar. Un repo con
  cambios locales NO se toca (se analiza tal cual y se avisa). La autenticación
  usa GITHUB_TOKEN / AZURE_DEVOPS_PAT por variables de entorno del proceso git:
  nunca se escribe en la URL, en argumentos ni en .git/config.

Seguridad:
  - El repo debe resolverse DENTRO de OPENCODE_REPOS_DIR (sin escapes con
    ".." ni enlaces simbólicos hacia afuera).
  - Se lanza con subprocess sin shell; el prompt viaja por stdin, no por
    argumentos, para que ningún carácter especial llegue a un intérprete
    (en Windows, opencode.cmd pasa por cmd.exe).
  - Timeout duro: al vencer se mata el proceso.
  - Guardia anti-recursión: el hijo recibe OPENCODE_MCP_NESTED=1; si OpenCode
    vuelve a llamar a este MCP, la llamada anidada se rechaza.
"""

import asyncio
import base64
import json
import os
import re
import shutil
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse

NESTED_ENV = "OPENCODE_MCP_NESTED"
DEFAULT_TIMEOUT_S = 300
MAX_TIMEOUT_S = 1800
DEFAULT_MAX_OUTPUT_CHARS = 20000
GIT_TIMEOUT_S = 180
ALLOWED_GIT_HOSTS = ("github.com", "dev.azure.com")  # + *.visualstudio.com
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SAFE_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, "") or default)
    except ValueError:
        return default
    return value if value > 0 else default


def _host_permitido(host: str) -> bool:
    host = (host or "").lower()
    return host in ALLOWED_GIT_HOSTS or host.endswith(".visualstudio.com")


def _parse_repo_url(repo: str) -> Optional[tuple[str, str]]:
    """Si `repo` es una URL devuelve (url_limpia, nombre_carpeta); si no, None."""
    texto = repo.strip()
    if not re.match(r"^[a-z][a-z0-9+.-]*://", texto, re.I):
        return None
    u = urlparse(texto)
    if u.scheme != "https":
        raise ValueError("Solo se aceptan URLs https de repos.")
    if u.username or u.password:
        raise ValueError("No incluyas credenciales en la URL; usa GITHUB_TOKEN o AZURE_DEVOPS_PAT.")
    if not _host_permitido(u.hostname or ""):
        raise ValueError(
            f"Host no permitido: {u.hostname}. Permitidos: GitHub, Azure Repos."
        )
    ruta = u.path.rstrip("/")
    if ruta.endswith(".git"):
        ruta = ruta[:-4]
    nombre = unquote(ruta.split("/")[-1]) if ruta else ""
    if not _SAFE_NAME.match(nombre):
        raise ValueError(f"No se pudo derivar un nombre de carpeta válido de la URL: '{nombre}'.")
    return f"https://{u.netloc}{ruta}", nombre


def _git_env(host: str) -> dict:
    """Entorno para git; el token va por GIT_CONFIG_*, no en argv ni en disco."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    host = (host or "").lower()
    cred = None
    if host == "github.com":
        token = os.environ.get("GITHUB_TOKEN", "")
        cred = f"x-access-token:{token}" if token else None
    elif _host_permitido(host):
        pat = os.environ.get("AZURE_DEVOPS_PAT", "")
        cred = f":{pat}" if pat else None
    if cred:
        b64 = base64.b64encode(cred.encode()).decode()
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "http.extraheader"
        env["GIT_CONFIG_VALUE_0"] = f"Authorization: Basic {b64}"
    return env


async def _git(args: list, cwd, env: dict) -> tuple[int, str]:
    exe = shutil.which("git")
    if not exe:
        raise RuntimeError("git no está instalado o no está en el PATH.")
    proc = await asyncio.create_subprocess_exec(
        exe, *args,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=GIT_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, f"git excedió el timeout de {GIT_TIMEOUT_S}s"
    return proc.returncode, out.decode("utf-8", errors="replace").strip()


async def _sincronizar(repo_path: Path, url: Optional[str], rama: str, actualizar: bool) -> str:
    """Clona o actualiza el repo. Devuelve un texto de estado; solo el clonado
    fallido lanza error (una actualización fallida no impide analizar)."""
    if rama and not _SAFE_BRANCH.match(rama):
        raise ValueError(f"Nombre de rama inválido: '{rama}'.")

    if not repo_path.exists():
        host = urlparse(url).hostname if url else ""
        args = ["clone", "--depth", "1"]
        if rama:
            args += ["--branch", rama]
        args += ["--", url, str(repo_path)]
        rc, out = await _git(args, None, _git_env(host))
        if rc != 0:
            raise RuntimeError(f"No se pudo clonar el repo: {out[-500:]}")
        return "clonado"

    if not actualizar:
        return "omitida (actualizar=false)"
    if not (repo_path / ".git").exists():
        return "omitida: la carpeta no es un repo git"

    rc, out = await _git(["status", "--porcelain"], repo_path, os.environ.copy())
    if rc != 0:
        return f"omitida: git status falló ({out[-200:]})"
    if out:
        return "omitida: hay cambios locales sin confirmar (se analiza tal cual)"

    rc, remoto = await _git(["remote", "get-url", "origin"], repo_path, os.environ.copy())
    if rc != 0:
        return "omitida: el repo no tiene remoto 'origin'"
    host = urlparse(remoto).hostname if remoto.startswith("https://") else ""
    env = _git_env(host)

    if rama:
        rc, out = await _git(["fetch", "origin", rama], repo_path, env)
        if rc == 0:
            rc, out = await _git(["checkout", rama], repo_path, env)
        if rc != 0:
            return f"no actualizada: no se pudo cambiar a la rama '{rama}' ({out[-200:]})"

    rc, out = await _git(["pull", "--ff-only"], repo_path, env)
    if rc != 0:
        return f"no actualizada: {out[-300:]}"
    return "ya estaba al día" if "up to date" in out.lower() else "actualizada"


def _resolve_repo(repo: str, debe_existir: bool = True) -> Path:
    """Valida que `repo` quede dentro de OPENCODE_REPOS_DIR y devuelve su ruta."""
    base_raw = os.environ.get("OPENCODE_REPOS_DIR", "").strip()
    if not base_raw:
        raise RuntimeError(
            "OPENCODE_REPOS_DIR no está configurado. Defínelo en .env con la "
            "carpeta que contiene tus repos."
        )
    base = Path(base_raw).expanduser().resolve()
    if not base.is_dir():
        raise RuntimeError(f"OPENCODE_REPOS_DIR no es una carpeta: {base}")
    if not repo or not repo.strip():
        raise ValueError("Indica el repo (nombre de carpeta dentro de OPENCODE_REPOS_DIR).")

    candidate = Path(repo.strip()).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    candidate = candidate.resolve()

    if candidate != base and base not in candidate.parents:
        raise ValueError(f"El repo '{repo}' queda fuera de OPENCODE_REPOS_DIR.")
    if candidate == base:
        raise ValueError("Indica un repo concreto, no la carpeta raíz completa.")
    if debe_existir and not candidate.is_dir():
        raise ValueError(f"El repo no existe: {candidate}")
    return candidate


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + f"\n... [salida recortada: {len(text) - limit} caracteres más]", True


def register_opencode_tools(mcp) -> None:
    """Registra analizar_codigo_opencode en la instancia de FastMCP."""

    @mcp.tool()
    async def analizar_codigo_opencode(
        repo: str,
        pregunta: str,
        contexto: str = "",
        timeout_s: int = 0,
        rama: str = "",
        actualizar: bool = True,
    ) -> str:
        """Analiza el código de un repo local con OpenCode (agente de solo
        consulta) y devuelve su respuesta.

        Úsala para diagnosticar un error leyendo el código real: pásale el
        stack trace o el resumen del incidente en `contexto` y qué quieres
        saber en `pregunta`. No debe usarse para modificar el repo.

        Siempre trabaja con la última versión: si `repo` es una URL (GitHub o
        Azure Repos) la clona en OPENCODE_REPOS_DIR si falta, y si ya existe
        la actualiza (git pull --ff-only) antes de analizar. Un repo con
        cambios locales no se modifica.

        Args:
            repo: URL https del repo (GitHub / Azure Repos), o nombre de la
                carpeta ya existente dentro de OPENCODE_REPOS_DIR.
            pregunta: qué quieres averiguar del código.
            contexto: información adicional (errores, logs, hipótesis).
            timeout_s: segundos máximos; 0 usa OPENCODE_TIMEOUT_S.
            rama: rama a clonar/usar (vacío = la rama por defecto del repo).
            actualizar: si es False no se hace pull de un repo existente.

        Requiere OPENCODE_REPOS_DIR y el binario de OpenCode instalado.
        """
        if os.environ.get(NESTED_ENV):
            raise RuntimeError(
                "Llamada anidada rechazada: analizar_codigo_opencode no puede "
                "invocarse desde una sesión de OpenCode lanzada por este mismo MCP."
            )
        if not pregunta or not pregunta.strip():
            raise ValueError("La pregunta no puede estar vacía.")

        parsed = _parse_repo_url(repo)
        if parsed:
            url, nombre = parsed
            repo_path = _resolve_repo(nombre, debe_existir=False)
        else:
            url = None
            repo_path = _resolve_repo(repo)
        actualizacion = await _sincronizar(repo_path, url, rama.strip(), actualizar)

        bin_name = os.environ.get("OPENCODE_BIN", "").strip() or "opencode"
        exe = shutil.which(bin_name)
        if not exe:
            raise RuntimeError(
                f"No se encontró el ejecutable de OpenCode ('{bin_name}'). "
                "Instálalo o define OPENCODE_BIN con su ruta."
            )

        timeout = timeout_s if timeout_s and timeout_s > 0 else _env_int(
            "OPENCODE_TIMEOUT_S", DEFAULT_TIMEOUT_S
        )
        timeout = min(timeout, MAX_TIMEOUT_S)
        max_chars = _env_int("OPENCODE_MAX_OUTPUT_CHARS", DEFAULT_MAX_OUTPUT_CHARS)

        args = [exe, "run"]
        model = os.environ.get("OPENCODE_MODEL", "").strip()
        if model:
            args += ["--model", model]

        prompt = pregunta.strip()
        if contexto and contexto.strip():
            prompt = f"{prompt}\n\nContexto:\n{contexto.strip()}"
        prompt = (
            "Analiza el código de este repo y responde. Es una consulta de solo "
            "lectura: NO modifiques archivos ni ejecutes comandos que cambien nada.\n\n"
            + prompt
        )

        env = dict(os.environ)
        env[NESTED_ENV] = "1"

        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(repo_path),
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(prompt.encode("utf-8")), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(f"OpenCode excedió el timeout de {timeout}s y fue detenido.")

        stdout = out.decode("utf-8", errors="replace").strip()
        stderr = err.decode("utf-8", errors="replace").strip()
        if proc.returncode != 0:
            detalle, _ = _truncate(stderr or stdout, min(max_chars, 4000))
            raise RuntimeError(f"OpenCode terminó con código {proc.returncode}: {detalle}")

        answer, truncated = _truncate(stdout, max_chars)
        return json.dumps(
            {
                "answer": answer,
                "repo": str(repo_path),
                "actualizacion": actualizacion,
                "model": model or "(por defecto de OpenCode)",
                "truncated": truncated,
            },
            ensure_ascii=False,
            indent=2,
        )
