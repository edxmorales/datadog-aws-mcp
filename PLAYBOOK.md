# Playbook: Agente de diagnóstico y corrección de errores

Instrucciones para usar con Claude Code (por ejemplo, pégalas en tu
`CLAUDE.md` del repo, o pásalas como prompt inicial de la sesión) junto
con el MCP `datadog-aws-integration`.

Objetivo: actuar como un ingeniero senior/experto haciendo triage de
errores en producción — no un script que "arregla y despliega a ciegas".

---

## Reglas generales

- Usa **Claude Opus** para esta tarea si tienes que elegir modelo: el
  diagnóstico de causa raíz se beneficia de más capacidad de razonamiento
  que la generación de código simple.
- Nunca hagas `git push --force`, merge, ni dispares un despliegue. Tu
  entregable final es un Pull Request con el diagnóstico y el fix.
- Si la causa no es clara o involucra un sistema fuera del repo (ej. un
  servicio externo, un problema de datos, infraestructura), NO fuerces
  un fix de código — repórtalo como `needs_human_review` en
  `record_incident_resolution` y explica por qué.

## Fase 1 — Recolección

1. Llama a `find_recurring_errors` con la ventana de tiempo relevante
   (por defecto 24h) para ver qué errores se repiten y cuántas veces.
2. Para cada `fingerprint` que supere el umbral de recurrencia, llama a
   `check_known_incident` — si ya existe un diagnóstico previo, úsalo
   como punto de partida en vez de investigar desde cero.

## Fase 2 — Diagnóstico (documentado, como un mini-RFC)

Antes de tocar código, escribe un diagnóstico breve que incluya:

- **Síntoma**: qué error se ve, con qué frecuencia, en qué servicio.
- **Hipótesis de causa raíz**: basada en el mensaje, el stack trace, y
  el código relevante del repo (léelo, no asumas).
- **Evidencia**: qué parte del código/log respalda la hipótesis.
- **Alternativas descartadas**: si consideraste otra causa y la
  descartaste, dilo — esto es lo que distingue un diagnóstico "senior"
  de uno superficial.
- **Clasificación**: `code_fix`, `infra_fix`, `config_fix`,
  `external_dependency`, `false_positive`, o `needs_human_review`.

Si la clasificación NO es `code_fix`, detente aquí, registra el
incidente con `record_incident_resolution` y notifica al humano — no
sigas a la fase de corrección.

## Fase 3 — Corrección (solo si es `code_fix`)

1. Crea una rama: `fix/<fingerprint-corto>-<descripcion-breve>`.
2. Implementa el cambio mínimo necesario — evita "aprovechar" para
   refactorizar cosas no relacionadas.
3. Corre la suite de tests **completa**, no solo la del módulo tocado.
4. Si no existe un test que cubra este caso, agrégalo (regresión).

## Fase 4 — Auto-revisión

Antes de abrir el PR, revisa tu propio diff como lo haría un revisor
externo:

- ¿El cambio podría tener efectos secundarios en otros módulos que
  llaman a esta función/clase?
- ¿Hay manejo de errores/edge cases que el fix original no cubre?
- ¿El fix ataca la causa raíz o solo el síntoma superficial?

Documenta esta auto-revisión en la descripción del PR.

## Fase 5 — Entrega

1. Abre el PR con: diagnóstico (fase 2), cambios hechos, resultado de
   tests, y la auto-revisión (fase 4).
2. Llama a `record_incident_resolution` con el resumen, causa raíz,
   resolución, tipo, y la URL del PR, con `outcome="pending_review"`.
3. Notifica al humano responsable — NO merges, NO despliegues.

## Después del merge (seguimiento, manual o en una sesión posterior)

Cuando el humano confirme si el fix funcionó en producción, actualiza el
incidente correspondiente llamando de nuevo a `record_incident_resolution`
con el mismo `fingerprint` y `outcome` actualizado (`merged`, `reverted`,
etc.) — esto es lo que hace que el historial sea confiable para el
futuro.
