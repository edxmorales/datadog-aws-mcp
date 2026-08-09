# datadog-aws-mcp

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Servidor MCP de solo lectura que expone herramientas para consultar
**Datadog** (monitores y logs) y **AWS CloudWatch Logs**, y para agrupar
errores recurrentes por "fingerprint" (huella del mensaje de error).

Está pensado para usarse junto con **Claude Code**: Claude consulta estas
herramientas para diagnosticar qué está fallando, y luego usa sus
capacidades normales de git (crear rama, editar código, commit, push,
abrir PR) para proponer un fix — con revisión humana antes de mergear
o desplegar.

## Instalación

```bash
cd datadog-aws-mcp
pip install -r requirements.txt --break-system-packages
cp .env.example .env
# edita .env con tus API keys de Datadog
# configura credenciales de AWS con `aws configure` o variables de entorno
```

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

Ninguna herramienta escribe, borra ni modifica nada en Datadog o AWS —
son de solo lectura a propósito. Las 3 herramientas de historial de
incidentes sí escriben, pero solo en un archivo local del propio MCP
(`incident_history.json`), nunca en Datadog/AWS/tu repo.

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

Copia `claude_mcp_config.example.json` a la ubicación de configuración de
MCP que use tu instalación de Claude Code/Desktop (revisa la doc oficial
en docs.claude.com, el nombre y ruta exactos del archivo de config puede
variar según versión) y ajusta la ruta absoluta a `server.py`.

Una vez registrado, en una sesión de Claude Code podrías pedir algo como:

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

- Usa credenciales de **solo lectura** para Datadog y AWS en este servidor.
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
