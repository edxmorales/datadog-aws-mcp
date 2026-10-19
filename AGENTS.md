# AGENTS.md

Instrucciones para agentes (OpenCode y similares) que trabajen en este repo
o lo consulten a través del MCP `datadog-aws-integration`.

## Qué es este repo

Servidor MCP (Python, FastMCP, transporte stdio) de **solo lectura** para
Datadog, AWS CloudWatch, Azure Repos, GitHub, DeepSeek y OpenCode.
Detalle en `README.md` y `docs/MCP.md`.

## Comandos

- Instalar: `pip install -r requirements.txt`
- Pruebas: `python test_arreglos.py` y `python test_opencode.py`
- Comprobar sintaxis: `python -m py_compile server.py tools_*.py test_*.py`

## Convenciones

- Código y comentarios en español, igual que el resto del repo.
- Cada grupo de herramientas nuevo vive en `tools_<nombre>.py` y expone
  `register_<nombre>_tools(mcp)`; `server.py` solo lo importa y lo registra.
- Las herramientas no escriben ni despliegan nada: crear ramas, commits y PRs
  se hace con git/gh, con revisión humana.

## Reglas

- Nunca leer, imprimir ni modificar `.env`; los secretos solo van por
  variables de entorno.
- No añadir `shell=True` ni construir comandos concatenando texto del usuario.
- No hacer push, merge ni tocar `main` sin petición explícita.
- Si analizas código por consulta (`analizar_codigo_opencode`): es de solo
  lectura. No modifiques archivos ni llames de nuevo a este MCP; la variable
  `OPENCODE_MCP_NESTED` bloquea las llamadas anidadas.
