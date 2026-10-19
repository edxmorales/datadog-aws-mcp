"""
Pruebas de tools_opencode.analizar_codigo_opencode con un OpenCode falso.

    python test_opencode.py

No requiere OpenCode instalado. Crea un ejecutable falso multiplataforma:
  - Windows:     opencode.cmd que invoca un script Python.
  - Linux/macOS: script con shebang (no depende de /bin/sh).
Sale con código 1 si alguna prueba falla.
"""

import asyncio
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

FALLOS = []


def check(nombre: str, condicion: bool, detalle: str = "") -> None:
    if condicion:
        print(f"OK    {nombre}")
    else:
        print(f"FALLA {nombre}" + (f" -- {detalle}" if detalle else ""))
        FALLOS.append(nombre)


# Comportamiento del falso según FAKE_MODE: ok | fail | sleep | big
FAKE_SCRIPT = '''\
import json, os, sys, time
modo = os.environ.get("FAKE_MODE", "ok")
prompt = sys.stdin.buffer.read().decode("utf-8")
if modo == "fail":
    sys.stderr.write("boom")
    sys.exit(3)
if modo == "sleep":
    time.sleep(30)
if modo == "big":
    print("x" * 5000)
    sys.exit(0)
print(json.dumps({
    "argv": sys.argv[1:],
    "cwd": os.getcwd(),
    "nested": os.environ.get("OPENCODE_MCP_NESTED"),
    "prompt": prompt,
}))
'''


def crear_opencode_falso(carpeta: Path) -> Path:
    script = carpeta / "fake_opencode.py"
    script.write_text(FAKE_SCRIPT, encoding="utf-8")
    if os.name == "nt":
        exe = carpeta / "opencode.cmd"
        exe.write_text(
            f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8"
        )
    else:
        exe = carpeta / "opencode"
        exe.write_text(f"#!{sys.executable}\n" + FAKE_SCRIPT, encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return exe


def construir_tool():
    from mcp.server.fastmcp import FastMCP
    from tools_opencode import register_opencode_tools

    m = FastMCP("test-opencode")
    register_opencode_tools(m)
    return m


async def llamar(m, **args):
    """Devuelve (ok, texto). Los errores de la tool llegan como ToolError."""
    try:
        res = await m.call_tool("analizar_codigo_opencode", args)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    contenido = res[0] if isinstance(res, tuple) else res
    return True, "".join(getattr(c, "text", "") for c in contenido)


async def correr(tmp: Path):
    repos = tmp / "repos"
    (repos / "mi-repo").mkdir(parents=True)
    fuera = tmp / "fuera"
    fuera.mkdir()
    exe = crear_opencode_falso(tmp)

    os.environ["OPENCODE_REPOS_DIR"] = str(repos)
    os.environ["OPENCODE_BIN"] = str(exe)
    os.environ["OPENCODE_MODEL"] = "proveedor/modelo"
    os.environ.pop("OPENCODE_MCP_NESTED", None)
    os.environ["FAKE_MODE"] = "ok"

    m = construir_tool()

    nombres = [t.name for t in await m.list_tools()]
    check("la tool queda registrada", "analizar_codigo_opencode" in nombres)

    # Caso feliz: cwd, modelo, prompt por stdin (con metacaracteres de shell).
    pregunta = "¿Qué hace main? & echo inyectado | whoami"
    ok, txt = await llamar(m, repo="mi-repo", pregunta=pregunta, contexto="stack trace X")
    check("caso feliz responde", ok, txt)
    if ok:
        data = json.loads(txt)
        eco = json.loads(data["answer"])
        check("corre dentro del repo", Path(eco["cwd"]).resolve() == (repos / "mi-repo").resolve(), eco["cwd"])
        check("pasa el modelo", eco["argv"] == ["run", "--model", "proveedor/modelo"], str(eco["argv"]))
        check("el prompt viaja intacto por stdin", pregunta in eco["prompt"] and "stack trace X" in eco["prompt"])
        check("el hijo recibe la marca anti-recursión", eco["nested"] == "1")
        check("no marca truncado", data["truncated"] is False)

    # Validación de rutas.
    ok, txt = await llamar(m, repo="..", pregunta="x")
    check("rechaza '..' fuera de la raíz", not ok and "fuera de OPENCODE_REPOS_DIR" in txt, txt)
    ok, txt = await llamar(m, repo=str(fuera), pregunta="x")
    check("rechaza ruta absoluta fuera de la raíz", not ok and "fuera de OPENCODE_REPOS_DIR" in txt, txt)
    ok, txt = await llamar(m, repo="no-existe", pregunta="x")
    check("rechaza repo inexistente", not ok and "no existe" in txt, txt)
    ok, txt = await llamar(m, repo="mi-repo", pregunta="   ")
    check("rechaza pregunta vacía", not ok, txt)

    # Guardia de anidamiento.
    os.environ["OPENCODE_MCP_NESTED"] = "1"
    ok, txt = await llamar(m, repo="mi-repo", pregunta="x")
    check("rechaza llamadas anidadas", not ok and "anidada" in txt, txt)
    os.environ.pop("OPENCODE_MCP_NESTED")

    # Código de salida distinto de cero.
    os.environ["FAKE_MODE"] = "fail"
    ok, txt = await llamar(m, repo="mi-repo", pregunta="x")
    check("propaga fallo del proceso", not ok and "código 3" in txt and "boom" in txt, txt)

    # Timeout.
    os.environ["FAKE_MODE"] = "sleep"
    ok, txt = await llamar(m, repo="mi-repo", pregunta="x", timeout_s=1)
    check("aplica el timeout", not ok and "timeout" in txt, txt)

    # Recorte de salida.
    os.environ["FAKE_MODE"] = "big"
    os.environ["OPENCODE_MAX_OUTPUT_CHARS"] = "100"
    ok, txt = await llamar(m, repo="mi-repo", pregunta="x")
    if ok:
        data = json.loads(txt)
        check("recorta la salida larga", data["truncated"] is True and len(data["answer"]) < 300)
    else:
        check("recorta la salida larga", False, txt)
    os.environ.pop("OPENCODE_MAX_OUTPUT_CHARS")

    # Binario inexistente.
    os.environ["FAKE_MODE"] = "ok"
    os.environ["OPENCODE_BIN"] = str(tmp / "no-existe-opencode")
    ok, txt = await llamar(m, repo="mi-repo", pregunta="x")
    check("avisa si falta el binario", not ok and "ejecutable" in txt, txt)

    # Sin OPENCODE_REPOS_DIR.
    os.environ.pop("OPENCODE_REPOS_DIR")
    ok, txt = await llamar(m, repo="mi-repo", pregunta="x")
    check("exige OPENCODE_REPOS_DIR", not ok and "OPENCODE_REPOS_DIR" in txt, txt)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    try:
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(correr(Path(tmp)))
    except Exception as e:
        print(f"FALLA test_opencode -- excepción: {type(e).__name__}: {e}")
        FALLOS.append("test_opencode")

    print()
    if FALLOS:
        print(f"{len(FALLOS)} prueba(s) fallando: {', '.join(FALLOS)}")
        sys.exit(1)
    print("Todas las pruebas pasan.")
