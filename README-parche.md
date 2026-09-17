# Parche del MCP datadog-aws-integration

Resuelve dos límites que venían cortando las investigaciones:

1. **CloudWatch truncaba a 500 caracteres.** Los logs de AWS WAF ponen el bloque
   `httpRequest` (clientIp, User-Agent, uri) después de `ruleGroupList`, que es
   enorme, así que el dato útil siempre quedaba fuera del corte. Comprobado: de
   577 eventos de WAF descargados, cero traían `clientIp` visible.
2. **Datadog descartaba los atributos.** Solo devolvía `message`, y todo lo que
   está indexado como atributo (`@email`, `playerId`, `requestHeaders`,
   `x-forwarded-for`) nunca llegaba a la respuesta.

## Archivos

Los dos `.py` van en la misma carpeta que el archivo con el `FastMCP(...)`,
para que el import resuelva sin tocar rutas.

| Archivo | Qué hace |
|---|---|
| `tools_waf.py` | Consultas a CloudWatch Logs Insights con proyección de campos, sin truncamiento |
| `tools_datadog_full.py` | API v2 de Datadog devolviendo atributos completos |
| `iam-policy-mcp-logs.json` | Permisos que necesita el usuario `mcp-cloudwatch-readonly` |

## Cambios en el server.py

Arriba, con los demás imports:

```python
from tools_waf import register_waf_tools
from tools_datadog_full import register_datadog_full_tools
```

Después de crear la instancia `mcp` y antes del `mcp.run()`:

```python
register_waf_tools(mcp)
register_datadog_full_tools(mcp)
```

No hay dependencias nuevas: `tools_waf.py` usa `boto3`, que ya está en el
proyecto, y `tools_datadog_full.py` usa solo librería estándar.

## Permisos IAM

El usuario `mcp-cloudwatch-readonly` hoy no puede lanzar queries de Insights ni
describir load balancers — esto último ya había fallado antes. Aplica
`iam-policy-mcp-logs.json` como política inline o adjunta sobre ese usuario.
Todo es de solo lectura.

Para Datadog no hace falta nada nuevo: `DD_API_KEY` y `DD_APP_KEY` del `.env`
ya sirven. Si la organización no está en el sitio US1, agrega `DD_SITE` al
`.env` (`datadoghq.eu`, `us3.datadoghq.com` o `us5.datadoghq.com`).

## Herramientas nuevas

**`waf_client_fingerprints(log_group, hours, uri_pattern, min_requests)`**
Agrupa por IP + User-Agent. Devuelve volumen, rutas distintas, duración,
peticiones por minuto y las señales de automatización detectadas.

**`waf_request_cadence(log_group, client_ip, hours)`**
Coeficiente de variación de los intervalos entre peticiones de una IP. Es el
discriminador más fuerte: un humano navegando da CV alto (>0.8), un script con
sleep fijo da CV bajo (<0.2).

**`logs_insights_query(log_group, query, hours, limit)`**
Query libre de Insights. La salida de escape para cualquier caso que las dos
anteriores no cubran.

**`datadog_search_logs(query, hours, limit, campos)`**
Logs con atributos completos. El parámetro `campos` proyecta solo lo que pidas
para que la respuesta no se dispare.

**`datadog_client_ips(query, hours)`**
Saca la IP real del cliente de `x-forwarded-for` descartando las privadas del
clúster, y las agrupa por frecuencia.

## Verificación

Reinicia Claude y abre una conversación nueva — las herramientas registradas se
leen al iniciar la sesión.

Prueba de humo, sobre una marca cualquiera:

```
waf_client_fingerprints(
    log_group="aws-waf-logs-redcap-alb-shield-astrobet-ec",
    hours=2,
    min_requests=10
)
```

Si devuelve `status: "Complete"` y filas con `user_agent` poblado, el
truncamiento quedó resuelto. Si devuelve un `AccessDeniedException`, falta
aplicar la política IAM.

## Lo que esto sigue sin ver

Los logs de WAF solo capturan lo que pasa por el ALB de RedCap. Si la
sportsbook de Altenar se carga como widget desde un dominio del proveedor, el
navegador del jugador pega directo contra Altenar y ese tráfico nunca toca tu
WAF. En ese caso el canal y el dispositivo por apuesta hay que sacarlos del
backoffice de Altenar, no de aquí.

---

# Consumo de tokens

Todo lo que devuelve una tool entra en la ventana de contexto del modelo y se
vuelve a enviar en cada turno siguiente. Hay dos costos distintos:

## 1. Costo fijo: los esquemas de las tools

Las 24 tools registradas ocupan **~24.000 caracteres (~6.900 tokens)** de
descripciones y esquemas, y ese bloque viaja en *cada* request, se usen o no.
Es el piso del servidor. Si no usas DeepSeek (`ask_deepseek`,
`potenciar_respuesta`) o Azure Repos, no registrar esas tools baja el piso
alrededor de un 25%.

## 2. Costo variable: las respuestas

Aquí estaba el problema real. `cloudwatch_recent_errors` paginaba hasta 500
eventos de 500 caracteres y los devolvía todos, con `indent=2`: ~143.000
caracteres, del orden de 40.000 tokens en una sola llamada — y casi todo
repetido, porque el mismo stack trace aparece cientos de veces.

Cambios aplicados:

- **JSON compacto.** `_dump()` serializa sin sangrado ni espacios en los
  separadores. Se aplica a todas las respuestas (el `incident_history.json`
  en disco sigue indentado y legible).
- **Agrupación por fingerprint por defecto.** `datadog_recent_errors` y
  `cloudwatch_recent_errors` devuelven un ejemplo por huella con su conteo y
  su ventana de tiempo, en vez de cada repetición. Con `agrupar=False` vuelve
  la lista cruda.
- **Tope duro de respuesta.** `_cap()` recorta a `MCP_MAX_RESPONSE_CHARS`
  (60.000 por defecto) y avisa al modelo con qué parámetro acotar, en vez de
  reventar el contexto en silencio.
- **Lectura de archivos acotada.** `github_get_file` y `azure_repos_get_file`
  aceptan `max_chars` (40.000 por defecto) y `desde_linea`/`hasta_linea`, que
  es lo que quieres cuando vienes de un stack trace con número de línea.
- **`datadog_search_logs` con presupuesto.** Sigue sin truncar atributos —
  ese es su propósito — pero deja de devolver eventos cuando se agota
  `max_output_chars` y reporta cuántos quedaron fuera.
- **Sin ida y vuelta por JSON.** `find_recurring_errors` llama ahora a
  `_datadog_fetch` / `_cloudwatch_fetch` directamente. Antes serializaba
  cientos de eventos a JSON solo para volver a parsearlos dentro del proceso.

Medición con 500 eventos de CloudWatch y 3 errores distintos repetidos:

| Respuesta | Caracteres | Tokens aprox. |
|---|---:|---:|
| Antes (lista cruda, `indent=2`) | 143.450 | ~41.000 |
| Ahora, `agrupar=False` | 60.063 | ~17.000 |
| Ahora, por defecto | 1.005 | ~290 |

