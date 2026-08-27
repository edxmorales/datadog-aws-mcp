# datadog-aws-mcp

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Servidor MCP de solo lectura que expone herramientas para consultar
**Datadog** (monitores y logs), **AWS CloudWatch Logs** y, opcionalmente,
leer código fuente de **Azure Repos** (Azure DevOps) — y para agrupar
errores recurrentes por "fingerprint" (huella del mensaje de error).

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

Edita `.env` con tus API keys de Datadog (obligatorio), y configura
credenciales de AWS con `aws configure` o variables de entorno
(obligatorio). Azure Repos es opcional — ver su propia sección más abajo.

> **Nota:** si clonas el repo estando ya dentro de la carpeta destino
> (ej. `cd C:\Proyectos\mi-repo` y luego `git clone ...` ahí mismo), Git
> crea una subcarpeta anidada con el mismo nombre. Verifica con `dir` /
> `ls` que `server.py` y `requirements.txt` estén en tu carpeta actual
> antes de instalar — si no, entra un nivel más con `cd datadog-aws-mcp`.

## Herramientas expuestas

| Herramienta | Qué hace |
|---|---|
| `datadog_triggered_monitors` | Monitores de Datadog actualmente en Alert/Warn |
| `datadog_recent_errors` | Logs de error recientes en Datadog (por query) |
| `cloudwatch_list_log_groups` | Lista log groups de CloudWatch disponibles |
| `cloudwatch_recent_errors` | Logs de error recientes en un log group |
| `find_recurring_errors` | Combina Datadog + CloudWatch y agrupa errores repetidos |
| `check_known_incident` | Consulta si un error (por fingerprint) ya fue diagnosticado antes |
| `record_incident_resolution` | Registra diagnóstico + fix + resultado en el historial |
| `list_incident_history` | Lista el historial de incidentes registrados |
| `azure_devops_list_projects` | *(opcional)* Lista proyectos de tu organización de Azure DevOps |
| `azure_repos_list_repos` | *(opcional)* Lista repos Git dentro de un proyecto de Azure DevOps |
| `azure_repos_get_file` | *(opcional)* Lee el contenido de un archivo en un repo de Azure Repos |
| `azure_repos_search_code` | *(opcional)* Busca texto/código en los repos de Azure Repos |

Ninguna herramienta escribe, borra ni modifica nada en Datadog, AWS ni
Azure Repos — son de solo lectura a propósito. Las 3 herramientas de
historial de incidentes sí escriben, pero solo en un archivo local del
propio MCP (`incident_history.json`), nunca en Datadog/AWS/Azure/tu repo.

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

- Usa credenciales de **solo lectura** para Datadog, AWS y Azure DevOps
  (PAT con scope "Code (Read)" únicamente) en este servidor.
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
