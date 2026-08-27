# datadog-aws-mcp

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Servidor MCP de solo lectura que expone herramientas para consultar
**Datadog** (monitores y logs), **AWS CloudWatch Logs**, y leer código
fuente de **Azure Repos** (Azure DevOps) o **GitHub** — y para agrupar
errores recurrentes por "fingerprint" (huella del mensaje de error).

**Las 4 fuentes son independientes y 100% opcionales.** No necesitas
configurarlas todas: activa solo las que uses, en cualquier combinación
— solo AWS, solo Datadog, solo Git (Azure Repos y/o GitHub), AWS+GitHub,
AWS+Datadog, las 4 juntas, etc. Cada herramienta que dependa de una
fuente no configurada devuelve un error claro explicando qué falta, en
vez de fallar de forma confusa; y `find_recurring_errors` simplemente
omite las fuentes no configuradas (lo reporta en su respuesta) en vez de
fallar por completo.

Está pensado para usarse junto con **Claude Code**: Claude consulta estas
herramientas para diagnosticar qué está fallando, y luego usa sus
capacidades normales de git (crear rama, editar código, commit, push,
abrir PR) para proponer un fix — con revisión humana antes de mergear
o desplegar.

## Instalación

```bash
git clone https://github.com/edxmorales/datadog-aws-mcp.git
cd datadog-aws-mcp
pip install -r requirements.txt --break-system-packages
```

Crea tu archivo de variables de entorno a partir del ejemplo:

```bash
# macOS/Linux
cp .env.example .env

# Windows (PowerShell) — `cp` como en bash no siempre existe; usa:
Copy-Item .env.example .env
```

Edita `.env` y activa **solo las fuentes que vayas a usar** (ver tabla
abajo) — ninguna es obligatoria por sí sola, pero necesitas al menos una
para que el servidor tenga algo que consultar.

> **Nota:** si clonas el repo estando ya dentro de la carpeta destino
> (ej. `cd C:\Proyectos\mi-repo` y luego `git clone ...` ahí mismo), Git
> crea una subcarpeta anidada con el mismo nombre. Verifica con `dir` /
> `ls` que `server.py` y `requirements.txt` estén en tu carpeta actual
> antes de instalar — si no, entra un nivel más con `cd datadog-aws-mcp`.

## Fuentes: todas opcionales, cualquier combinación

| Fuente | Variables en `.env` | Qué necesitas |
|---|---|---|
| Datadog | `DD_API_KEY`, `DD_APP_KEY`, `DD_SITE` | API Key + Application Key de Datadog |
| AWS CloudWatch | `AWS_REGION` | Credenciales AWS estándar (`aws configure`, env vars, o rol) |
| Azure Repos | `AZURE_DEVOPS_ORG`, `AZURE_DEVOPS_PAT` | PAT de Azure DevOps, scope "Code (Read)" |
| GitHub | `GITHUB_TOKEN` (opcional), `GITHUB_API_URL` | Nada para repos públicos; PAT fine-grained "Contents: Read-only" para privados |

Deja vacías las variables de la fuente que no uses — el resto del
servidor sigue funcionando igual. Combina las que quieras: por ejemplo
solo AWS + GitHub (sin Datadog ni Azure), o solo Datadog, o las 4.

## Herramientas expuestas

| Herramienta | Fuente | Qué hace |
|---|---|---|
| `datadog_triggered_monitors` | Datadog | Monitores actualmente en Alert/Warn |
| `datadog_recent_errors` | Datadog | Logs de error recientes (por query) |
| `cloudwatch_list_log_groups` | AWS | Lista log groups de CloudWatch disponibles |
| `cloudwatch_recent_errors` | AWS | Logs de error recientes en un log group |
| `find_recurring_errors` | Datadog + AWS | Combina las fuentes configuradas y agrupa errores repetidos |
| `check_known_incident` | — (local) | Consulta si un error (por fingerprint) ya fue diagnosticado antes |
| `record_incident_resolution` | — (local) | Registra diagnóstico + fix + resultado en el historial |
| `list_incident_history` | — (local) | Lista el historial de incidentes registrados |
| `azure_devops_list_projects` | Azure Repos | Lista proyectos de tu organización de Azure DevOps |
| `azure_repos_list_repos` | Azure Repos | Lista repos Git dentro de un proyecto |
| `azure_repos_get_file` | Azure Repos | Lee el contenido de un archivo |
| `azure_repos_search_code` | Azure Repos | Busca texto/código (requiere extensión "Azure DevOps Search") |
| `github_list_repos` | GitHub | Lista repos de un usuario/organización |
| `github_get_file` | GitHub | Lee el contenido de un archivo |
| `github_search_code` | GitHub | Busca texto/código (requiere `GITHUB_TOKEN`) |

Ninguna herramienta escribe, borra ni modifica nada en Datadog, AWS,
Azure Repos ni GitHub — son de solo lectura a propósito. Las 3
herramientas de historial de incidentes sí escriben, pero solo en un
archivo local del propio MCP (`incident_history.json`), nunca en
Datadog/AWS/Azure/GitHub/tu repo.

## Azure Repos (opcional)

Si el código fuente que Claude necesita leer para diagnosticar un error
vive en **Azure Repos** (Azure DevOps) en vez de GitHub, agrega estas dos
variables a tu `.env`:

```bash
AZURE_DEVOPS_ORG=tu_organizacion       # el nombre en https://dev.azure.com/<org>
AZURE_DEVOPS_PAT=tu_personal_access_token
```

El PAT (Personal Access Token) se crea en
`https://dev.azure.com/<tu-org>/_usersSettings/tokens` con el scope de
**solo lectura "Code (Read)"** — nada más. Igual que con Datadog y AWS,
no le des permisos de escritura a este servidor.

Es completamente opcional y genérico: no está atado a ninguna
organización/proyecto/repo en particular, así que cualquiera que instale
este MCP lo apunta a su propia cuenta de Azure DevOps. Si dejas
`AZURE_DEVOPS_ORG` / `AZURE_DEVOPS_PAT` vacíos, el resto del servidor
sigue funcionando normal — esas 4 herramientas simplemente devuelven un
error explicando qué falta si Claude intenta usarlas.

Flujo típico: `find_recurring_errors` para detectar el error → si el
repo está en Azure DevOps, `azure_devops_list_projects` y
`azure_repos_list_repos` para ubicar dónde vive el código → 
`azure_repos_get_file` (o `azure_repos_search_code`, si tu organización
tiene habilitada la extensión "Azure DevOps Search") para leer el
archivo relevante antes de proponer el fix.

## GitHub (opcional)

Si el código fuente vive en **GitHub** en vez de (o además de) Azure
Repos, agrega a tu `.env`:

```bash
GITHUB_TOKEN=tu_personal_access_token   # opcional para repos públicos
# GITHUB_API_URL=https://api.github.com  # cambia solo si usas GitHub Enterprise Server
```

Sin `GITHUB_TOKEN`, `github_list_repos` y `github_get_file` funcionan
igual pero limitados a repos **públicos** y con límites de rate más
bajos (la API de GitHub lo permite así). `github_search_code` sí exige
un token siempre — la Search API de GitHub no acepta búsquedas sin
autenticación, ni en repos públicos.

Para repos privados, crea un token fine-grained en
`https://github.com/settings/tokens?type=beta` con el scope de solo
lectura **"Contents: Read-only"** sobre los repos que necesites — nada
más. Igual que las demás fuentes, es completamente opcional y genérico:
no está atado a ningún usuario/organización en particular.

## Modo experto: el archivo PLAYBOOK.md

`PLAYBOOK.md` contiene las instrucciones que convierten esto en un
agente de triage nivel senior/experto, no un script ciego: diagnóstico
documentado antes de tocar código, clasificación explícita de la causa,
auto-revisión del propio fix, y memoria de incidentes pasados vía
`check_known_incident` / `record_incident_resolution`.

Pega el contenido de `PLAYBOOK.md` en el `CLAUDE.md` de tu repo (o
pásaselo como instrucción al inicio de la sesión de Claude Code) para
que Claude siga ese flujo por defecto.

## Registrarlo en Claude Code / Claude Desktop

Copia el contenido de `claude_mcp_config.example.json` (la clave
`mcpServers`) al archivo de configuración de MCP que use tu instalación
de Claude Code/Desktop, y ajusta la ruta absoluta a `server.py`. Si el
archivo de configuración ya tiene contenido (otros servidores MCP, u
otras preferencias de la app), **fusiona** tu bloque `mcpServers` dentro
de lo que ya existe — no reemplaces el archivo completo.

> **Dónde está ese archivo — ojo con esto en Windows:** el nombre y ruta
> exactos pueden variar según versión (revisa la doc oficial en
> docs.claude.com), pero además, si tu Claude Desktop está instalado como
> **app empaquetada de Windows** (Microsoft Store / MSIX — se ve como
> `Claude_<id-aleatorio>` en `AppData\Local\Packages`), el archivo **no**
> está en el `%APPDATA%\Claude\` clásico, sino en una carpeta virtualizada
> tipo:
> ```
> C:\Users\<tu_usuario>\AppData\Local\Packages\Claude_<id>\LocalCache\Roaming\Claude\claude_desktop_config.json
> ```
> La forma confiable de encontrarlo sin adivinar: dentro de la app, ve a
> **Desarrollador → Servidores MCP locales → "Editar configuración"** —
> ese botón abre el archivo real que la app está leyendo, y ahí puedes
> confirmar la ruta exacta.
>
> Después de guardar el archivo, cierra Claude Desktop **por completo**
> (verifica que no quede el proceso corriendo en la bandeja del sistema —
> en PowerShell: `Stop-Process -Name "Claude" -Force`) y vuelve a abrirlo
> para que cargue el servidor nuevo.

Una vez registrado, en Desarrollador → Servidores MCP locales deberías
ver `datadog-aws-integration` en estado **running**. En una sesión de
Claude Code (o Claude Desktop) podrías pedir algo como:

> "Revisa `find_recurring_errors` con datadog_query='service:checkout
> status:error' y log_group='/aws/lambda/checkout-prod' de las últimas
> 24 horas. Si hay algún error que se repita 5+ veces y parezca originarse
> en nuestro código (no en un servicio externo), crea una rama
> `fix/<descripcion-corta>`, corrígelo, corre los tests, y abre un PR
> con la explicación del diagnóstico. No hagas merge ni despliegues."

## Flujo recomendado de extremo a extremo

1. **Disparo**: un cron, un webhook de Datadog, o tú manualmente, inician
   una sesión de Claude Code.
2. **Diagnóstico**: Claude llama a `find_recurring_errors` para ver qué se
   repite y con qué frecuencia.
3. **Decisión**: le indicas (o Claude decide según tus instrucciones) si
   el patrón corresponde a un bug de código del repo vs. un problema de
   infraestructura/datos externos.
4. **Fix propuesto**: Claude crea una rama, edita el código, corre tests
   localmente.
5. **Revisión humana**: Claude abre un Pull Request con el diagnóstico y
   el fix. Un humano revisa y aprueba.
6. **Despliegue**: lo dispara tu pipeline de CI/CD existente (GitHub
   Actions, GitLab CI, CodePipeline, etc.) tras el merge — no el MCP.

## Seguridad

- Usa credenciales de **solo lectura** en cada fuente que actives: AWS
  (rol/política read-only), Datadog (API/App key read-only), Azure
  DevOps (PAT scope "Code (Read)") y GitHub (token fine-grained
  "Contents: Read-only").
- Nunca subas tu archivo `.env` real a git (ya debería estar en
  `.gitignore` de tu proyecto).
- El paso de despliegue automático queda deliberadamente fuera de este
  MCP; añádelo solo si tu equipo decide asumir ese riesgo, y hazlo con
  aprobaciones explícitas (ej. un "approve" manual en el PR o en el
  pipeline) en vez de un despliegue directo sin supervisión.

## Contribuir

Este proyecto está en etapa temprana y las contribuciones son
bienvenidas — desde reportar bugs hasta agregar soporte para otros
proveedores de observabilidad. Revisa [`CONTRIBUTING.md`](CONTRIBUTING.md)
para la guía de cómo participar.

## Licencia

[MIT](LICENSE) © 2026 Edixon Morales
