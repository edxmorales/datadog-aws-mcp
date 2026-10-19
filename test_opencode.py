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


def git(cwd, *args):
    import subprocess

    r = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd, capture_output=True, text=True, encoding="utf-8",
    )
    if r.returncode != 0:
        raise RuntimeError(f"git {args} falló: {r.stderr}")
    return r.stdout.strip()


async def correr_git(tmp: Path):
    """Clonado y actualización a la última versión, con un remoto local real."""
    import tools_opencode

    remoto = tmp / "remoto.git"
    trabajo = tmp / "trabajo"
    repos = tmp / "repos_git"
    repos.mkdir()
    git(tmp, "init", "--bare", "-b", "main", str(remoto))
    git(tmp, "clone", str(remoto), str(trabajo))
    (trabajo / "a.txt").write_text("v1", encoding="utf-8")
    git(trabajo, "add", ".")
    git(trabajo, "commit", "-m", "v1")
    git(trabajo, "push", "origin", "HEAD:main")

    exe = crear_opencode_falso(tmp)
    os.environ.update({
        "OPENCODE_REPOS_DIR": str(repos), "OPENCODE_BIN": str(exe), "FAKE_MODE": "ok",
    })
    os.environ.pop("OPENCODE_MODEL", None)
    m = construir_tool()

    # Parseo de URLs (sin red).
    pu = tools_opencode._parse_repo_url
    check("URL de GitHub deriva el nombre", pu("https://github.com/org/mi-repo.git/") == ("https://github.com/org/mi-repo", "mi-repo"))
    check("URL de Azure deriva el nombre", pu("https://dev.azure.com/o/p/_git/api")[1] == "api")
    check("un nombre simple no es URL", pu("mi-repo") is None)
    for malo in ["http://github.com/o/r", "https://evil.com/o/r", "https://u:p@github.com/o/r",
                 "file:///c:/x", "https://github.com/o/.."]:
        try:
            pu(malo)
            check(f"rechaza URL {malo}", False)
        except ValueError:
            check(f"rechaza URL {malo}", True)

    # Clonado (se parchea el parseo para apuntar al remoto local).
    original = tools_opencode._parse_repo_url
    tools_opencode._parse_repo_url = lambda r: (str(remoto), "clon") if r == "URL-FALSA" else original(r)
    try:
        ok, txt = await llamar(m, repo="URL-FALSA", pregunta="x")
        check("clona si no existe", ok and json.loads(txt)["actualizacion"] == "clonado", txt)
        check("el clon tiene los archivos", (repos / "clon" / "a.txt").exists())

        # Llega un commit nuevo al remoto: se debe traer.
        (trabajo / "a.txt").write_text("v2", encoding="utf-8")
        git(trabajo, "commit", "-am", "v2")
        git(trabajo, "push", "origin", "HEAD:main")
        ok, txt = await llamar(m, repo="URL-FALSA", pregunta="x")
        check("actualiza a la última versión", ok and json.loads(txt)["actualizacion"] == "actualizada", txt)
        check("el contenido es el nuevo", (repos / "clon" / "a.txt").read_text(encoding="utf-8") == "v2")

        ok, txt = await llamar(m, repo="URL-FALSA", pregunta="x")
        check("sin novedades dice que está al día", ok and json.loads(txt)["actualizacion"] == "ya estaba al día", txt)

        # Cambios locales: no se tocan.
        (repos / "clon" / "a.txt").write_text("mio", encoding="utf-8")
        (trabajo / "b.txt").write_text("nuevo", encoding="utf-8")
        git(trabajo, "add", ".")
        git(trabajo, "commit", "-m", "v3")
        git(trabajo, "push", "origin", "HEAD:main")
        ok, txt = await llamar(m, repo="clon", pregunta="x")
        check("no pisa cambios locales", ok and "cambios locales" in json.loads(txt)["actualizacion"], txt)
        check("conserva el archivo editado", (repos / "clon" / "a.txt").read_text(encoding="utf-8") == "mio")
        check("no trajo el commit nuevo", not (repos / "clon" / "b.txt").exists())

        # actualizar=false.
        ok, txt = await llamar(m, repo="clon", pregunta="x", actualizar=False)
        check("respeta actualizar=false", ok and "omitida" in json.loads(txt)["actualizacion"], txt)

        # Clonado fallido es un error claro.
        tools_opencode._parse_repo_url = lambda r: (str(tmp / "no-existe.git"), "roto") if r == "URL-ROTA" else original(r)
        ok, txt = await llamar(m, repo="URL-ROTA", pregunta="x")
        check("clonado fallido da error", not ok and "No se pudo clonar" in txt, txt)
    finally:
        tools_opencode._parse_repo_url = original

    # Rama inválida.
    ok, txt = await llamar(m, repo="clon", pregunta="x", rama="--upload-pack=x")
    check("rechaza rama sospechosa", not ok and "Nombre de rama inválido" in txt, txt)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    try:
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(correr(Path(tmp)))
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(correr_git(Path(tmp)))
    except Exception as e:
        print(f"FALLA test_opencode -- excepción: {type(e).__name__}: {e}")
        FALLOS.append("test_opencode")

    print()
    if FALLOS:
        print(f"{len(FALLOS)} prueba(s) fallando: {', '.join(FALLOS)}")
        sys.exit(1)
    print("Todas las pruebas pasan.")
