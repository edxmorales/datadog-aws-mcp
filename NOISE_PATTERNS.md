# Ruido conocido y glosario del entorno

Este archivo es el equivalente de `bits.md` en Bits AI de Datadog: una
fuente de conocimiento **viva** sobre este entorno específico — no
código, no proceso, sino contexto que solo se aprende investigando
incidentes reales. Bits AI combina tres fuentes al investigar (runbooks,
`bits.md`, y feedback/memories de investigaciones pasadas); en este
repo, `PLAYBOOK.md` cubre el rol de runbook, `incident_history.json` el
de memories, y este archivo el de `bits.md`.

**Instrucción para el agente**: consúltalo ANTES de tratar un patrón de
`find_recurring_errors`/`datadog_recent_errors`/`cloudwatch_recent_errors`
como incidente real. Si descubres un patrón de ruido nuevo (algo que
parecía error pero no lo era) que no esté aquí, **agrégalo tú mismo** al
terminar la investigación — con `sed`/edición directa del archivo, igual
que actualizas `incident_history.json` — para que la próxima vez nadie
pierda tiempo re-investigándolo desde cero. Este archivo debe crecer con
el tiempo; una entrada nueva por cada falso positivo real que resuelvas.

## Patrones de ruido conocidos (no son incidentes reales)

| Patrón / fragmento | Fuente típica | Por qué es ruido |
|---|---|---|
| `_maxListeners: undefined` | logs Node.js | Objeto interno de EventEmitter serializado por accidente en un `console.log`/`print`, no un error de negocio. |
| `Symbol(kCapture): false` | logs Node.js | Mismo caso: fragmento de un objeto interno de Node serializado sin querer, sin mensaje ni stack trace coherente. |

_(agrega aquí cada patrón nuevo que confirmes como ruido, con la misma
estructura: patrón, dónde aparece, por qué no es un error real)_

## Convenciones de etiquetado / tagging

_(documenta aquí, a medida que las descubras investigando, cosas como:
qué tag identifica ambiente/namespace en Datadog, qué prefijo usan los
log groups de CloudWatch por servicio, qué convención de nombres usan
los NAT Gateways/Load Balancers, etc. — todo lo que hoy tienes que
redescubrir cada vez que investigas)_

## Glosario específico del equipo

_(nombres de servicios, siglas internas, o abreviaturas que aparecen en
logs/código y no son obvias para alguien nuevo — documéntalas aquí la
primera vez que las descifres)_
