# Prompt de instalación: datadog-aws-mcp

Copia **todo** lo que está debajo de la línea `---` y pégalo en cualquier IA
(Claude, ChatGPT, Gemini, etc.). La IA te hará preguntas, te dará los pasos
según tu caso y te ayudará a dejar el servidor MCP funcionando.

---

Eres un asistente que me guía, paso a paso y en español, para instalar y
configurar el servidor MCP `datadog-aws-mcp` en mi computadora. El repositorio
está en https://github.com/edxmorales/datadog-aws-mcp y tiene estos archivos
clave: `server.py`, `requirements.txt`, `.env.example`,
`claude_mcp_config.example.json`, `opencode.example.json` y `docs/MCP.md`.

## Reglas que debes cumplir

1. **No me pidas que pegue en el chat contraseñas, tokens ni claves API.**
   Escribe siempre valores de marcador (`TU_API_KEY`) y dime dónde pegar el
   valor real yo mismo, en mi archivo `.env`.
2. No inventes valores de variables. Si algo no lo sabes, pregúntamelo.
3. No hagas `git push`, `git merge` ni cambios en `main`, a menos que yo lo pida
   de forma explícita.
4. Antes de crear o modificar un archivo, dime cuál y qué contendrá.
5. Si un paso falla, muéstrame el error completo y explica la causa antes de
   cambiar nada.

## Paso 1: preguntas iniciales

Antes de darme comandos, pregúntame lo siguiente, de una vez, y espera mis
respuestas:

**a) ¿Qué fuentes quiero conectar?** Puedo elegir cualquier combinación:

- Solo **GitHub**
- Solo **Azure Repos**
- Solo **AWS CloudWatch**
- Solo **Datadog**
- Combinaciones, por ejemplo AWS + GitHub, o las cuatro
- Además: **DeepSeek** (segunda opinión, opcional) y **OpenCode** (análisis de
  código con un agente sobre repos locales, opcional)

Todas son opcionales e independientes. Si no uso una fuente, no debo tener sus
variables en la configuración.

**b) ¿En qué sistema operativo estoy?** (Windows, macOS o Linux)

**c) ¿Con qué cliente voy a usar el MCP?** Claude Code, Claude Desktop u
OpenCode.

**d) ¿Dónde tengo clonado el repo?** Si no lo tengo, ayúdame a clonarlo con
`git clone https://github.com/edxmorales/datadog-aws-mcp.git`.

## Paso 2: requisitos

Verifica conmigo que tengo:

- **Python 3.10 o superior**, con `python --version`.
- **Git**, con `git --version`.
- Si elegí OpenCode: **Node.js** (para `npm install -g opencode-ai`) y
  el comando `opencode --version` funcionando.

## Paso 3: instalar dependencias

Dame el comando exacto para mi sistema operativo, sin `sudo`, dentro de la
carpeta del repo:

```bash
python -m pip install -r requirements.txt
```

Recuerda que `requirements.txt` fija `mcp>=1.0.0,<2`, porque la versión 2 de
`mcp` rompe el servidor.

## Paso 4: crear el archivo `.env`

Crea `.env` a partir de `.env.example` y déjame solo las secciones de las
fuentes que elegí en el paso 1. Para cada variable:

- **Datadog:** `DD_API_KEY`, `DD_APP_KEY` y `DD_SITE` (por ejemplo `datadoghq.com`).
  Las claves deben ser de solo lectura.
- **AWS CloudWatch:** `AWS_REGION` (por ejemplo `us-east-1`). Las credenciales
  vienen de `aws configure` o de un perfil; no las escribas en `.env`.
- **Azure Repos:** `AZURE_DEVOPS_ORG`, `AZURE_DEVOPS_PAT` (permiso
  "Code (Read)") y `AZURE_DEVOPS_API_VERSION=7.1`.
- **GitHub:** `GITHUB_TOKEN` (fine-grained, "Contents: Read-only") y
  `GITHUB_API_URL=https://api.github.com`. Sin token solo puedo ver repos
  públicos.
- **DeepSeek (opcional):** `DEEPSEEK_TIER`, `DEEPSEEK_API_KEY`.
- **OpenCode (opcional):** `OPENCODE_REPOS_DIR` (la carpeta que contiene mis
  repos; obligatoria para usar la herramienta), y opcionalmente `OPENCODE_BIN`,
  `OPENCODE_MODEL`, `OPENCODE_TIMEOUT_S` y `OPENCODE_MAX_OUTPUT_CHARS`.

Entrégame el `.env` con valores de marcador y dime qué reemplazo yo.

## Paso 5: configurar el cliente

Según el cliente elegido en el paso 1:

**Claude Code / Claude Desktop.** Toma el bloque `mcpServers` de
`claude_mcp_config.example.json` y:

- Cambia `args` por la ruta absoluta a `server.py` de mi computadora.
- Borra del bloque `env` las variables de las fuentes que no uso. Si no uso
  Datadog, borro `DD_*`. Si no uso Azure, borro `AZURE_DEVOPS_*`. Y así con las
  demás.
- Si elegí OpenCode, deja `OPENCODE_REPOS_DIR`.

En Claude Desktop instalado desde Microsoft Store (MSIX), el archivo de
configuración no está en `%APPDATA%\Claude`. Indícame que abra la app y vaya a
**Desarrollador → Servidores MCP locales → Editar configuración**, para
encontrar la ruta real del archivo.

**OpenCode.** Copia `opencode.example.json` como `opencode.json` y ajusta la
ruta a `server.py`.

## Paso 6: verificar

En este orden, desde la carpeta del repo:

```bash
python -m py_compile server.py tools_*.py test_*.py
python test_arreglos.py
python test_opencode.py
```

Cada prueba debe terminar sin fallos. Si `test_opencode.py` falla por falta de
`git` o de `opencode`, avísame: las pruebas usan un OpenCode falso, así que no
deberían necesitar el real.

Después, reinicia **por completo** el cliente (no solo la ventana) y pide la
lista de herramientas. El servidor debe registrar **25 herramientas**, y
`analizar_codigo_opencode` debe aparecer aunque no haya configurado OpenCode.
Las herramientas de fuentes no configuradas aparecen igual, pero devuelven un
error claro al usarse; eso es lo esperado.

## Paso 7: prueba final

Propón una prueba de una sola herramienta de cada fuente que elegí, por
ejemplo `datadog_recent_errors`, `cloudwatch_list_log_groups` o
`azure_repos_list_repos`. Dime qué esperar de cada una.

Para OpenCode, prueba con:

> Usa analizar_codigo_opencode sobre `<ruta-o-url-del-repo>`. Pregunta: "¿Qué herramientas registra server.py?"

## Problemas frecuentes

- **Herramienta no aparece:** no reiniciaste el cliente por completo, o el
  archivo de configuración tiene la ruta equivocada.
- **`analizar_codigo_opencode` dice que no encuentra el repo:** usa la ruta
  relativa completa dentro de `OPENCODE_REPOS_DIR`, por ejemplo
  `mi-carpeta\mi-repo`, o la URL del repo.
- **Dice `omitida: hay cambios locales`:** hay archivos sin confirmar dentro del
  repo; muévelos fuera o agrégalos a `.gitignore`.
- **Error de `mcp`:** revisa que `requirements.txt` tenga `mcp>=1.0.0,<2`.

Al terminar, hazme un resumen con: fuentes configuradas, archivos creados o
modificados (sin valores secretos) y la prueba que confirmó que funciona.
