"""
tools_datadog_full.py — Consultas a Datadog Logs sin truncamiento, para el
MCP datadog-aws-integration.

Por qué existe este módulo
--------------------------
`datadog_recent_errors` devuelve solo `timestamp`, `service`, `message` y un
fingerprint. Todo lo que Datadog guarda como *atributos* del log —
`@email`, `playerId`, `requestHeaders`, `clientIp`, `x-forwarded-for` — se
descarta antes de llegar a la respuesta. Por eso varias investigaciones se
quedan a medias: el dato existe en Datadog, pero la herramienta no lo pasa.

Este módulo consulta la API v2 de Datadog directamente y devuelve los
atributos completos, con la opción de proyectar solo los campos que
interesan para que la respuesta no se dispare de tamaño.

No agrega dependencias: usa urllib de la librería estándar.

Variables de entorno requeridas (las mismas del .env que ya usas):
    DD_API_KEY, DD_APP_KEY
    DD_SITE  — opcional, por defecto 'datadoghq.com'.
               Usa 'datadoghq.eu', 'us3.datadoghq.com' o 'us5.datadoghq.com'
               según dónde esté tu organización.

Registro en el servidor:
    from tools_datadog_full import register_datadog_full_tools
    register_datadog_full_tools(mcp)
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Optional

_TIMEOUT_SECONDS = 45

# Cabeceras donde suele venir la IP real del cliente detrás del ingress.
_IP_HEADERS = ("x-forwarded-for", "x-real-ip", "cf-connecting-ip", "true-client-ip")

# Rangos privados RFC1918 y loopback: son IPs del propio clúster, no del cliente.
_PRIVATE_IP = re.compile(
    r"^(10\.|127\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|::1|fd)"
)


def _dd_site() -> str:
    return os.getenv("DD_SITE", "datadoghq.com").strip()


def _dd_headers() -> dict:
    api_key = os.getenv("DD_API_KEY")
    app_key = os.getenv("DD_APP_KEY")
    if not api_key or not app_key:
        raise RuntimeError(
            "Faltan DD_API_KEY o DD_APP_KEY en el entorno. "
            "Revisa el .env del proyecto."
        )
    return {
        "Content-Type": "application/json",
        "DD-API-KEY": api_key,
        "DD-APPLICATION-KEY": app_key,
    }


def _post_json(url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=_dd_headers(),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detalle = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Datadog respondió {exc.code}: {detalle}") from exc


def _flatten(obj: Any, prefix: str = "", salida: Optional[dict] = None) -> dict:
    """Aplana un dict anidado a claves tipo 'http.headers.user-agent'."""
    if salida is None:
        salida = {}
    if isinstance(obj, dict):
        for clave, valor in obj.items():
            _flatten(valor, f"{prefix}.{clave}" if prefix else str(clave), salida)
    elif isinstance(obj, list):
        # Las listas de pares {name, value} (típicas en headers) se colapsan.
        if obj and all(
            isinstance(item, dict) and "name" in item and "value" in item
            for item in obj
        ):
            for item in obj:
                _flatten(item["value"], f"{prefix}.{item['name']}", salida)
        else:
            for indice, item in enumerate(obj):
                _flatten(item, f"{prefix}.{indice}", salida)
    else:
        salida[prefix] = obj
    return salida


def _primera_ip_publica(valor: str) -> Optional[str]:
    """
    De un x-forwarded-for tipo '181.x.x.x, 10.0.3.44, 10.0.1.9' devuelve la
    primera IP que no sea privada: esa es la del cliente real.
    """
    for parte in str(valor).split(","):
        ip = parte.strip()
        if ip and not _PRIVATE_IP.match(ip):
            return ip
    return None


def register_datadog_full_tools(mcp) -> None:
    """Registra las herramientas de Datadog sin truncamiento."""

    @mcp.tool()
    def datadog_search_logs(
        query: str = "*",
        hours: int = 2,
        limit: int = 50,
        campos: str = "",
        max_message_chars: int = 0,
    ) -> dict:
        """
        Busca logs en Datadog y devuelve los ATRIBUTOS COMPLETOS de cada
        evento, no solo el mensaje.

        Úsala cuando necesites campos que datadog_recent_errors descarta:
        @email, playerId, clientIp, requestHeaders, status codes, etc.

        Args:
            query: query de Datadog (ej. 'service:back-security @email:x@y.com').
            hours: ventana hacia atrás, en horas.
            limit: máximo de eventos (tope 1000 por página en la API).
            campos: lista separada por comas de atributos a proyectar
                    (ej. 'playerId,http.url,network.client.ip'). Vacío = todos.
            max_message_chars: 0 = mensaje completo. Un número > 0 lo recorta
                    a esa cantidad de caracteres.
        """
        url = f"https://api.{_dd_site()}/api/v2/logs/events/search"
        payload = {
            "filter": {"query": query, "from": f"now-{hours}h", "to": "now"},
            "page": {"limit": min(int(limit), 1000)},
            "sort": "-timestamp",
        }
        respuesta = _post_json(url, payload)

        proyectar = [c.strip() for c in campos.split(",") if c.strip()]
        eventos = []

        for item in respuesta.get("data", []):
            atributos = item.get("attributes", {}) or {}
            personalizados = _flatten(atributos.get("attributes", {}) or {})

            if proyectar:
                personalizados = {
                    clave: valor
                    for clave, valor in personalizados.items()
                    if any(campo in clave for campo in proyectar)
                }

            mensaje = atributos.get("message", "") or ""
            if max_message_chars and len(mensaje) > max_message_chars:
                mensaje = mensaje[:max_message_chars]

            eventos.append(
                {
                    "timestamp": atributos.get("timestamp"),
                    "service": atributos.get("service"),
                    "status": atributos.get("status"),
                    "host": atributos.get("host"),
                    "message": mensaje,
                    "atributos": personalizados,
                }
            )

        return {
            "query": query,
            "ventana_horas": hours,
            "eventos_devueltos": len(eventos),
            "eventos": eventos,
        }

    @mcp.tool()
    def datadog_client_ips(
        query: str,
        hours: int = 24,
        limit: int = 200,
    ) -> dict:
        """
        Extrae la IP REAL del cliente de los logs que coincidan con la query,
        leyendo x-forwarded-for y descartando las IPs privadas del clúster.

        Resuelve el problema recurrente de que el ingress reescribe la IP de
        origen con direcciones RFC1918: la del cliente queda en la cabecera,
        y esta herramienta la saca y la agrupa.

        Args:
            query: query de Datadog (ej. '@email:alguien@correo.com').
            hours: ventana hacia atrás, en horas.
            limit: máximo de eventos a inspeccionar.
        """
        crudo = datadog_search_logs(query=query, hours=hours, limit=limit)

        por_ip: dict[str, dict] = {}
        sin_ip = 0

        for evento in crudo["eventos"]:
            encontrada = None
            for clave, valor in evento["atributos"].items():
                if any(cabecera in clave.lower() for cabecera in _IP_HEADERS):
                    encontrada = _primera_ip_publica(valor)
                    if encontrada:
                        break

            if not encontrada:
                sin_ip += 1
                continue

            registro = por_ip.setdefault(
                encontrada,
                {
                    "ip": encontrada,
                    "eventos": 0,
                    "servicios": set(),
                    "primera_vista": evento["timestamp"],
                    "ultima_vista": evento["timestamp"],
                },
            )
            registro["eventos"] += 1
            if evento["service"]:
                registro["servicios"].add(evento["service"])
            marca = evento["timestamp"]
            if marca:
                if not registro["primera_vista"] or marca < registro["primera_vista"]:
                    registro["primera_vista"] = marca
                if not registro["ultima_vista"] or marca > registro["ultima_vista"]:
                    registro["ultima_vista"] = marca

        resultados = sorted(
            (
                {**registro, "servicios": sorted(registro["servicios"])}
                for registro in por_ip.values()
            ),
            key=lambda r: r["eventos"],
            reverse=True,
        )

        return {
            "query": query,
            "ventana_horas": hours,
            "eventos_inspeccionados": len(crudo["eventos"]),
            "eventos_sin_ip_de_cliente": sin_ip,
            "ips_distintas": len(resultados),
            "ips": resultados,
            "nota": (
                "La geolocalización y el ASN no salen de Datadog. Para eso hay "
                "que consultar un servicio externo desde una máquina con salida "
                "a internet."
            ),
        }
