# CLAUDE.md

Instrucciones persistentes para Claude Code en este repositorio.
Este archivo se lee automáticamente al iniciar una sesión en este proyecto.

> Supuestos de este archivo (ajusta si no aplican a tu caso real):
> - Stack: Python
> - Runtime en AWS: ECS/Fargate (contenedor)
> - CI/CD: GitHub Actions

---

## Stack y convenciones del proyecto

- **Lenguaje**: Python (usa el intérprete/versión definido en
  `pyproject.toml` o `.python-version` del repo — verifícalo antes de
  asumir una versión).
- **Formato/lint**: usa `black` y `ruff` si están configurados en el
  repo (revisa `pyproject.toml` / `setup.cfg`). Corre el formateador
  antes de dar por terminado cualquier cambio.
- **Tests**: `pytest`. Corre la suite completa con `pytest -q` antes de
  abrir un PR, no solo los tests del módulo que tocaste.
- **Dependencias**: instala con
  `pip install -r requirements.txt --break-system-packages` (o
  `poetry install` / `pip install -e .` si el repo usa esas
  herramientas — revisa qué archivo de dependencias existe realmente).
- **Estilo de commits**: mensajes claros y en modo imperativo
  (ej. "Fix null pointer en checkout al validar cupón vacío"), un
  cambio lógico por commit.

## Infraestructura

- **Runtime**: ECS/Fargate. Los logs de aplicación van a CloudWatch
  Logs (log group por servicio — usa `cloudwatch_list_log_groups` del
  MCP si no conoces el nombre exacto).
- **Despliegue**: se dispara automáticamente al hacer merge a `main`
  vía GitHub Actions, que construye la imagen, la sube a ECR y
  actualiza el servicio de ECS. **Tú (Claude) nunca disparas un
  despliegue manualmente ni haces merge** — el pipeline existente se
  encarga después de que un humano aprueba el PR.
- **Monitoreo**: Datadog (monitores + logs). Usa las herramientas del
  MCP `datadog-aws-integration` para consultarlo.

## Agente de diagnóstico y corrección de errores

Cuando se te pida investigar errores de producción, o cuando detectes
que hay que revisar el estado de Datadog/CloudWatch, sigue el flujo
completo descrito en `PLAYBOOK.md` de este repo (o el que se te haya
compartido). En resumen:

1. **Recolectar** — `find_recurring_errors`, y `check_known_incident`
   para cada fingerprint relevante antes de investigar desde cero.
2. **Diagnosticar por escrito** — síntoma, hipótesis de causa raíz,
   evidencia del código, alternativas descartadas, y clasificación
   (`code_fix`, `infra_fix`, `config_fix`, `external_dependency`,
   `false_positive`, `needs_human_review`).
   - Si NO es `code_fix`, detente, registra el incidente con
     `record_incident_resolution` y notifica — no continúes a corregir
     código.
3. **Corregir** (solo si es `code_fix`) — rama `fix/<fingerprint>-<slug>`,
   cambio mínimo, tests completos con `pytest -q`, agrega test de
   regresión si no existe.
4. **Auto-revisar** — revisa tu propio diff buscando efectos
   secundarios en otros módulos, edge cases no cubiertos, y si
   realmente atacaste la causa raíz (no solo el síntoma).
5. **Entregar** — abre el PR con diagnóstico + cambios + resultado de
   tests + auto-revisión. Llama a `record_incident_resolution` con
   `outcome="pending_review"`. **No merges. No despliegas.**

## Reglas duras (no negociables)

- Nunca hagas `git push --force` a `main`.
- Nunca apruebes ni mergees tu propio PR.
- Nunca dispares el pipeline de despliegue manualmente.
- Si el diagnóstico no es claro o involucra algo fuera del código del
  repo, dilo explícitamente en vez de forzar un fix — es preferible un
  "no estoy seguro, esto necesita revisión humana" a un fix incorrecto
  en producción.
- Usa credenciales/herramientas de Datadog y AWS solo para lectura
  (así están configuradas en el MCP) — nunca intentes usar acciones de
  escritura sobre esos sistemas.
