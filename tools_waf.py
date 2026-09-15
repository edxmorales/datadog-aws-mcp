"""
tools_waf.py — Herramientas de CloudWatch Logs Insights para el MCP
datadog-aws-integration.

Por qué existe este módulo
--------------------------
`cloudwatch_recent_errors` usa FilterLogEvents, que devuelve el evento
completo y luego se recorta a 500 caracteres. En los logs de AWS WAF el
bloque `httpRequest` (clientIp, country, uri y headers) va DESPUES de
`ruleGroupList`, que es larguísimo, así que siempre queda fuera del corte.

Logs Insights resuelve el problema de raíz: se proyectan solo los campos
que interesan, así que lo que vuelve son cuatro columnas cortas en vez de
un JSON de 8 KB. No hace falta truncar nada.

Registro en el servidor
-----------------------
En server.py (o donde tengas el FastMCP):

    from tools_waf import register_waf_tools
    register_waf_tools(mcp)

Requisitos: boto3 y credenciales AWS con permisos
logs:StartQuery, logs:GetQueryResults, logs:StopQuery.
"""

from __future__ import annotations

import os
import re
import statistics
import time
from datetime import datetime, timezone
from typing import Any, Optional

import boto3

# --------------------------------------------------------------------------
# Configuración
# --------------------------------------------------------------------------

_MAX_WAIT_SECONDS = 90
_POLL_SECONDS = 1.0

# User-Agents que delatan automatización de forma directa.
_UA_AUTOMATION = re.compile(
    r"python-requests|python-urllib|aiohttp|httpx|Go-http-client|okhttp|"
    r"curl/|Wget/|libwww-perl|Java/|Apache-HttpClient|axios/|node-fetch|"
    r"PostmanRuntime|Insomnia|HeadlessChrome|PhantomJS|Puppeteer|Playwright|"
    r"Selenium|scrapy|bot|crawler|spider",
    re.IGNORECASE,
)


def _logs_client():
    """Cliente de CloudWatch Logs. Respeta AWS_REGION / AWS_PROFILE del .env."""
    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
    return boto3.client("logs", region_name=region)


def _to_epoch_seconds(value: Any) -> Optional[float]:
    """
    Logs Insights devuelve @timestamp como 'YYYY-MM-DD HH:MM:SS.mmm' cuando se
    proyecta directo, y como epoch en milisegundos cuando pasa por min()/max().
    Esta función normaliza los dos casos a segundos epoch.
    """
    if value is None or value == "":
        return None
    try:
        num = float(value)
        # Heurística: por encima de 1e11 es milisegundos.
        return num / 1000.0 if num > 1e11 else num
    except (TypeError, ValueError):
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(str(value), fmt).replace(
                tzinfo=timezone.utc
            ).timestamp()
        except ValueError:
            continue
    return None


def _iso(epoch_seconds: Optional[float]) -> Optional[str]:
    if epoch_seconds is None:
        return None
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).isoformat()


def _run_insights_query(
    log_group: str,
    query: str,
    hours: int = 2,
    limit: int = 1000,
) -> dict:
    """
    Ejecuta una query de Logs Insights y espera el resultado.

    Devuelve un dict con:
      status          — 'Complete', 'Timeout' o el estado que reporte AWS
      records         — lista de dicts {campo: valor}
      records_matched — cuántos eventos escaneó la query
      query           — la query efectivamente ejecutada (útil para depurar)
    """
    client = _logs_client()
    end = int(time.time())
    start = end - hours * 3600

    started = client.start_query(
        logGroupName=log_group,
        startTime=start,
        endTime=end,
        queryString=query,
        limit=limit,
    )
    query_id = started["queryId"]

    waited = 0.0
    while waited < _MAX_WAIT_SECONDS:
        result = client.get_query_results(queryId=query_id)
        status = result["status"]
        if status in ("Complete", "Failed", "Cancelled"):
            records = [
                {field["field"]: field["value"] for field in row}
                for row in result.get("results", [])
            ]
            return {
                "status": status,
                "log_group": log_group,
                "ventana_horas": hours,
                "records_matched": result.get("statistics", {}).get("recordsMatched"),
                "records": records,
                "query": query,
            }
        time.sleep(_POLL_SECONDS)
        waited += _POLL_SECONDS

    # Se pasó del tiempo máximo: cancelamos para no dejar la query colgada.
    try:
        client.stop_query(queryId=query_id)
    except Exception:
        pass
    return {
        "status": "Timeout",
        "log_group": log_group,
        "ventana_horas": hours,
        "records": [],
        "query": query,
        "nota": (
            f"La query superó {_MAX_WAIT_SECONDS}s. Reduce 'hours' o agrega "
            "un filtro más específico."
        ),
    }


# --------------------------------------------------------------------------
# Registro de herramientas MCP
# --------------------------------------------------------------------------

def register_waf_tools(mcp) -> None:
    """Registra las herramientas de Logs Insights en la instancia de FastMCP."""

    @mcp.tool()
    def logs_insights_query(
        log_group: str,
        query: str,
        hours: int = 2,
        limit: int = 1000,
    ) -> dict:
        """
        Ejecuta una query arbitraria de CloudWatch Logs Insights y devuelve
        los campos proyectados SIN truncar.

        Es la salida de escape para cuando cloudwatch_recent_errors se queda
        corto: en vez de traer el evento entero y recortarlo, proyectas solo
        las columnas que necesitas.

        Args:
            log_group: nombre exacto del log group.
            query: query de Logs Insights (fields / filter / parse / stats).
            hours: ventana hacia atrás, en horas.
            limit: máximo de filas a devolver.
        """
        return _run_insights_query(log_group, query, hours, limit)

    @mcp.tool()
    def waf_client_fingerprints(
        log_group: str,
        hours: int = 2,
        uri_pattern: str = "",
        min_requests: int = 5,
        limit: int = 100,
    ) -> dict:
        """
        Agrupa el tráfico de un log group de AWS WAF por IP de cliente y
        User-Agent, para separar tráfico humano de automatizado.

        Cada fila trae volumen de peticiones, rutas distintas tocadas,
        ventana de actividad, tasa por minuto y las señales de automatización
        detectadas.

        Args:
            log_group: ej. 'aws-waf-logs-redcap-alb-shield-astrobet-ec'.
            hours: ventana hacia atrás, en horas.
            uri_pattern: regex opcional para filtrar rutas (ej. 'bet|wallet').
            min_requests: descarta huellas con menos peticiones que esto.
            limit: máximo de huellas a devolver.
        """
        filtro = f"| filter uri like /{uri_pattern}/\n" if uri_pattern else ""
        query = (
            "fields httpRequest.clientIp as ip, httpRequest.country as pais, "
            "httpRequest.uri as uri\n"
            '| parse @message /"name":"[Uu]ser-[Aa]gent","value":"(?<ua>[^"]+)"/\n'
            f"{filtro}"
            "| stats count() as reqs, count_distinct(uri) as rutas, "
            "min(@timestamp) as primera, max(@timestamp) as ultima by ip, pais, ua\n"
            "| sort reqs desc\n"
            f"| limit {limit}"
        )

        raw = _run_insights_query(log_group, query, hours, limit)
        huellas = []

        for row in raw.get("records", []):
            try:
                reqs = int(float(row.get("reqs", 0)))
            except (TypeError, ValueError):
                continue
            if reqs < min_requests:
                continue

            primera = _to_epoch_seconds(row.get("primera"))
            ultima = _to_epoch_seconds(row.get("ultima"))
            duracion_min = (
                round((ultima - primera) / 60.0, 2)
                if primera and ultima and ultima > primera
                else 0.0
            )
            ua = row.get("ua", "") or "(sin User-Agent)"

            try:
                rutas = int(float(row.get("rutas", 0)))
            except (TypeError, ValueError):
                rutas = 0

            señales = []
            if _UA_AUTOMATION.search(ua):
                señales.append("User-Agent de librería o headless")
            if ua == "(sin User-Agent)":
                señales.append("petición sin User-Agent")
            if rutas <= 2 and reqs >= 50:
                señales.append("mucho volumen sobre muy pocas rutas")
            if duracion_min >= 60:
                señales.append("actividad sostenida más de una hora")
            if duracion_min > 0 and (reqs / duracion_min) > 30:
                señales.append("más de 30 peticiones por minuto")

            huellas.append(
                {
                    "ip": row.get("ip"),
                    "pais": row.get("pais"),
                    "user_agent": ua,
                    "peticiones": reqs,
                    "rutas_distintas": rutas,
                    "primera_vista": _iso(primera),
                    "ultima_vista": _iso(ultima),
                    "duracion_minutos": duracion_min,
                    "peticiones_por_minuto": (
                        round(reqs / duracion_min, 2) if duracion_min else None
                    ),
                    "señales_automatizacion": señales,
                }
            )

        return {
            "status": raw.get("status"),
            "log_group": log_group,
            "ventana_horas": hours,
            "huellas_devueltas": len(huellas),
            "huellas": huellas,
            "query": raw.get("query"),
        }

    @mcp.tool()
    def waf_request_cadence(
        log_group: str,
        client_ip: str,
        hours: int = 6,
        uri_pattern: str = "",
        max_events: int = 5000,
    ) -> dict:
        """
        Analiza el ritmo de las peticiones de UNA IP: intervalos entre
        peticiones, media, desviación y coeficiente de variación.

        El coeficiente de variación es la señal más fuerte para distinguir
        un bot de una persona. Un humano navegando produce intervalos muy
        irregulares (CV típicamente > 0.8). Un script con sleep fijo produce
        intervalos casi idénticos (CV < 0.2). Interpretar siempre junto con
        el User-Agent y el ASN de la IP, no de forma aislada.

        Args:
            log_group: log group de WAF de la marca.
            client_ip: IP exacta a analizar.
            hours: ventana hacia atrás, en horas.
            uri_pattern: regex opcional para limitar a ciertas rutas.
            max_events: tope de eventos a traer.
        """
        filtro = f"| filter uri like /{uri_pattern}/\n" if uri_pattern else ""
        query = (
            "fields @timestamp, httpRequest.uri as uri\n"
            f'| filter httpRequest.clientIp = "{client_ip}"\n'
            f"{filtro}"
            "| sort @timestamp asc\n"
            f"| limit {max_events}"
        )

        raw = _run_insights_query(log_group, query, hours, max_events)
        marcas = [
            ts
            for ts in (_to_epoch_seconds(r.get("@timestamp")) for r in raw.get("records", []))
            if ts is not None
        ]
        marcas.sort()

        if len(marcas) < 3:
            return {
                "status": raw.get("status"),
                "ip": client_ip,
                "peticiones": len(marcas),
                "veredicto": "Datos insuficientes para evaluar cadencia.",
                "query": raw.get("query"),
            }

        intervalos = [b - a for a, b in zip(marcas, marcas[1:])]
        media = statistics.mean(intervalos)
        desviacion = statistics.pstdev(intervalos)
        cv = (desviacion / media) if media > 0 else 0.0

        # Proporción de intervalos que caen a menos de 10% de la media:
        # un scheduler fijo los concentra casi todos ahí.
        cerca = (
            sum(1 for i in intervalos if media and abs(i - media) <= 0.1 * media)
            / len(intervalos)
        )

        if cv < 0.2 or cerca > 0.7:
            veredicto = "Cadencia muy regular. Compatible con automatización."
        elif cv < 0.5:
            veredicto = "Cadencia parcialmente regular. Revisar junto con User-Agent y ASN."
        else:
            veredicto = "Cadencia irregular. Compatible con navegación humana."

        return {
            "status": raw.get("status"),
            "ip": client_ip,
            "log_group": log_group,
            "ventana_horas": hours,
            "peticiones": len(marcas),
            "primera_vista": _iso(marcas[0]),
            "ultima_vista": _iso(marcas[-1]),
            "intervalo_medio_seg": round(media, 3),
            "desviacion_seg": round(desviacion, 3),
            "coeficiente_variacion": round(cv, 3),
            "proporcion_intervalos_regulares": round(cerca, 3),
            "veredicto": veredicto,
            "query": raw.get("query"),
        }
