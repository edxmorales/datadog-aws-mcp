# Documentación del servidor MCP

## Arquitectura

```
Claude / OpenCode  --stdio-->  server.py (FastMCP "datadog-aws-integration")
                                 |- herramientas propias de server.py
                                 |- tools_waf.py           (Logs Insights)
                                 |- tools_datadog_full.py  (API v2 de Datadog)
                                 `- tools_opencode.py      (análisis de código)
```

- `server.py` crea la instancia `FastMCP`, define las herramientas base y
  registra los módulos aditivos con `register_*_tools(mcp)` antes de `mcp.run()`.
- Todas las fuentes son **opcionales**: una fuente sin configurar devuelve un
  error claro en su herramienta y `find_recurring_errors` la omite.
- El servidor es de solo lectura; no crea ramas, commits ni despliegues.
- `tools_opencode.py` sigue el patrón de `ask_deepseek` (segunda opinión
  externa), pero lanza OpenCode como proceso hijo dentro de un repo local.

## Variables de entorno por fuente

Ver `.env.example` (nunca subas `.env`).

| Fuente | Variables |
|---|---|
| Datadog | `DD_API_KEY`, `DD_APP_KEY`, `DD_SITE` |
| AWS CloudWatch | `AWS_REGION` (+ credenciales estándar de AWS) |
| Azure Repos | `AZURE_DEVOPS_ORG`, `AZURE_DEVOPS_PAT`, `AZURE_DEVOPS_API_VERSION` |
| GitHub | `GITHUB_TOKEN`, `GITHUB_API_URL` |
| DeepSeek | `DEEPSEEK_TIER`, `DEEPSEEK_API_KEY`, `DEEPSEEK_API_URL`, `DEEPSEEK_MODEL` |
| OpenCode | `OPENCODE_REPOS_DIR`, `OPENCODE_BIN`, `OPENCODE_MODEL`, `OPENCODE_TIMEOUT_S`, `OPENCODE_MAX_OUTPUT_CHARS` |
| Historial | `INCIDENT_HISTORY_PATH` |

## Herramientas (25)

- **Datadog:** `datadog_recent_errors`, `datadog_search_logs`,
  `datadog_triggered_monitors`, `datadog_client_ips`
- **AWS:** `cloudwatch_list_log_groups`, `cloudwatch_recent_errors`,
  `logs_insights_query`, `waf_client_fingerprints`, `waf_request_cadence`,
  `aws_list_load_balancers`, `aws_network_egress_ips`
- **Azure Repos:** `azure_devops_list_projects`, `azure_repos_list_repos`,
  `azure_repos_get_file`, `azure_repos_search_code`
- **GitHub:** `github_list_repos`, `github_get_file`, `github_search_code`
- **Análisis y memoria:** `find_recurring_errors`, `check_known_incident`,
  `record_incident_resolution`, `list_incident_history`
- **IA:** `ask_deepseek`, `potenciar_respuesta`
- **OpenCode:** `analizar_codigo_opencode(repo, pregunta, contexto, timeout_s)`

### analizar_codigo_opencode

Ejecuta `opencode run` con el repo como directorio de trabajo y devuelve un
JSON con `answer`, `repo`, `model`, `actualizacion` y `truncated`.

`repo` acepta dos formas:

- **Nombre de carpeta** ya existente dentro de `OPENCODE_REPOS_DIR` (ej. `Sentinel`).
- **URL https** de GitHub o Azure Repos. Si la carpeta no existe, se clona
  (`--depth 1`, o la rama indicada en `rama`). Si ya existe, se actualiza con
  `git pull --ff-only` antes de analizar, así que siempre usa la última versión.
  Un repo con cambios locales sin confirmar **no se toca**: se analiza tal cual
  y `actualizacion` lo indica. `actualizar=false` omite la actualización.

La autenticación usa `GITHUB_TOKEN` o `AZURE_DEVOPS_PAT` pasados como cabecera
de git por variables de entorno: el token nunca va en la URL, en los argumentos
ni en `.git/config`. Solo se aceptan hosts de GitHub y Azure Repos, y URLs sin
credenciales. Salvaguardas: el repo debe
estar dentro de `OPENCODE_REPOS_DIR`; `subprocess` sin shell y con el prompt
por stdin; timeout (`timeout_s` o `OPENCODE_TIMEOUT_S`, tope 1800 s); salida
recortada a `OPENCODE_MAX_OUTPUT_CHARS`; y la guardia `OPENCODE_MCP_NESTED`,
que rechaza llamadas anidadas.

## Configuración

**Claude Code / Desktop:** ver `claude_mcp_config.example.json` y la sección
"Registrarlo en Claude Code / Claude Desktop" del `README.md`.

**OpenCode:** copia `opencode.example.json` a `opencode.json` (en el repo o en
`~/.config/opencode/`) y ajusta la ruta de `server.py`. Para que
`analizar_codigo_opencode` funcione hace falta OpenCode instalado y
`OPENCODE_REPOS_DIR` definido en `.env`.

## Pruebas

```bash
python -m py_compile server.py tools_*.py test_*.py
python test_arreglos.py
python test_opencode.py
```

`test_opencode.py` usa un OpenCode falso multiplataforma (`opencode.cmd` en
Windows, script con shebang en Linux/macOS); no necesita OpenCode real.

## Limitaciones conocidas

- `requirements.txt` fija `mcp>=1.0.0,<2`: la versión 2 renombró `FastMCP` a
  `MCPServer` y rompe el servidor.
- El prompt se envía a OpenCode por stdin; se asume que `opencode run` lo
  acepta así. Verifícalo con tu versión instalada.
- El modo de solo lectura de OpenCode se pide en el prompt, no está forzado
  por el MCP: limita sus permisos en su propia configuración.
- Un solo proceso por llamada, sin cola ni límite de concurrencia.
- La respuesta se recorta a `OPENCODE_MAX_OUTPUT_CHARS`.
- Clonar o actualizar necesita `git` en el PATH y acceso de red al remoto.
- El clon superficial (`--depth 1`) no tiene historial: `git log` y `git blame`
  no mostrarán commits anteriores.
- Un repo con cambios locales queda como está; no se actualiza hasta que
  confirmes o descartes esos cambios.
- Hay que tener cuidado con los repos privados: la URL y el código pasan por
  OpenCode, que usa el proveedor de modelo configurado en OpenCode.
