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
