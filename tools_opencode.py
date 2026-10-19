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
import json
import os
import shutil
from pathlib import Path

NESTED_ENV = "OPENCODE_MCP_NESTED"
DEFAULT_TIMEOUT_S = 300
MAX_TIMEOUT_S = 1800
DEFAULT_MAX_OUTPUT_CHARS = 20000


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, "") or default)
    except ValueError:
        return default
    return value if value > 0 else default


def _resolve_repo(repo: str) -> Path:
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
    if not candidate.is_dir():
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
    ) -> str:
        """Analiza el código de un repo local con OpenCode (agente de solo
        consulta) y devuelve su respuesta.

        Úsala para diagnosticar un error leyendo el código real: pásale el
        stack trace o el resumen del incidente en `contexto` y qué quieres
        saber en `pregunta`. No debe usarse para modificar el repo.

        Args:
            repo: nombre de la carpeta del repo dentro de OPENCODE_REPOS_DIR
                (o una ruta que se resuelva dentro de ella).
            pregunta: qué quieres averiguar del código.
            contexto: información adicional (errores, logs, hipótesis).
            timeout_s: segundos máximos; 0 usa OPENCODE_TIMEOUT_S.

        Requiere OPENCODE_REPOS_DIR y el binario de OpenCode instalado.
        """
        if os.environ.get(NESTED_ENV):
            raise RuntimeError(
                "Llamada anidada rechazada: analizar_codigo_opencode no puede "
                "invocarse desde una sesión de OpenCode lanzada por este mismo MCP."
            )
        if not pregunta or not pregunta.strip():
            raise ValueError("La pregunta no puede estar vacía.")

        repo_path = _resolve_repo(repo)

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
                "model": model or "(por defecto de OpenCode)",
                "truncated": truncated,
            },
            ensure_ascii=False,
            indent=2,
        )
