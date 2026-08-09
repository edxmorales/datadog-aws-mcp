"""
Servidor MCP: integración con Datadog y AWS CloudWatch/Logs.

Expone herramientas que Claude (vía Claude Code, la API con mcp_servers,
o Claude Desktop) puede usar para:
  - Consultar errores/monitores disparados en Datadog.
  - Consultar logs de error en CloudWatch.
  - Agrupar/detectar errores recurrentes (misma huella / fingerprint).

IMPORTANTE (seguridad):
  - Las credenciales se leen de variables de entorno, nunca hardcodeadas.
  - El rol IAM y las API keys usadas aquí deben tener SOLO permisos de
    lectura (CloudWatch Logs read-only, Datadog read-only). Este servidor
    NO expone herramientas de escritura/despliegue a propósito: esa parte
    (crear rama, hacer commit, abrir PR, deploy) se recomienda dejarla en
    manos de Claude Code usando git/gh directamente, con revisión humana
    antes de mergear o desplegar.

Requisitos:
    pip install mcp boto3 httpx python-dotenv --break-system-packages

Variables de entorno esperadas (ver .env.example):
    DD_API_KEY, DD_APP_KEY, DD_SITE (por defecto datadoghq.com)
    AWS_REGION (usa las credenciales estándar de AWS: perfil, rol, etc.)
"""

import os
import time
import json
import hashlib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
import boto3
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

DD_API_KEY = os.environ.get("DD_API_KEY", "")
DD_APP_KEY = os.environ.get("DD_APP_KEY", "")
DD_SITE = os.environ.get("DD_SITE", "datadoghq.com")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

# Archivo local donde se guarda el historial de incidentes ya diagnosticados
# y resueltos. Esto es lo que le da al agente "memoria" de casos pasados,
# como la experiencia acumulada de un ingeniero senior.
INCIDENT_HISTORY_PATH = Path(
    os.environ.get("INCIDENT_HISTORY_PATH", str(Path(__file__).parent / "incident_history.json"))
)

mcp = FastMCP("datadog-aws-integration")


def _load_history() -> list:
    if not INCIDENT_HISTORY_PATH.exists():
        return []
    try:
        return json.loads(INCIDENT_HISTORY_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _save_history(history: list) -> None:
    INCIDENT_HISTORY_PATH.write_text(json.dumps(history, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fingerprint(message: str) -> str:
    """Genera una huella simple para agrupar errores similares.

    Quita números, IDs y rutas variables para que el mismo tipo de error
    con datos distintos (ej. distinto user_id) agrupe igual.
    """
    import re
    normalized = re.sub(r"\d+", "#", message)
    normalized = re.sub(r"0x[0-9a-fA-F]+", "0x#", normalized)
    normalized = normalized.strip().lower()
    return hashlib.sha1(normalized.encode()).hexdigest()[:12]


def _dd_headers() -> dict:
    return {
        "DD-API-KEY": DD_API_KEY,
        "DD-APPLICATION-KEY": DD_APP_KEY,
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Herramientas: Datadog
# ---------------------------------------------------------------------------

@mcp.tool()
async def datadog_triggered_monitors() -> str:
    """Lista los monitores de Datadog que están actualmente en estado
    'Alert' o 'Warn'. Útil para saber qué está fallando ahora mismo.
    """
    url = f"https://api.{DD_SITE}/api/v1/monitor"
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            url, headers=_dd_headers(), params={"group_states": "alert,warn"}
        )
        resp.raise_for_status()
        monitors = resp.json()

    triggered = [
        {
            "id": m["id"],
            "name": m["name"],
            "state": m.get("overall_state"),
            "message": m.get("message", "")[:300],
            "tags": m.get("tags", []),
        }
        for m in monitors
        if m.get("overall_state") in ("Alert", "Warn")
    ]
    return json.dumps(triggered, indent=2, ensure_ascii=False)


@mcp.tool()
async def datadog_recent_errors(query: str = "status:error", hours: int = 1, limit: int = 100) -> str:
    """Busca logs de error recientes en Datadog Log Management.

    Args:
        query: query de búsqueda de Datadog (ej. "status:error service:checkout").
        hours: ventana de tiempo hacia atrás, en horas.
        limit: máximo de logs a devolver.
    """
    url = f"https://api.{DD_SITE}/api/v2/logs/events/search"
    now_ms = int(time.time() * 1000)
    from_ms = now_ms - hours * 3600 * 1000

    body = {
        "filter": {
            "query": query,
            "from": str(from_ms),
            "to": str(now_ms),
        },
        "page": {"limit": limit},
        "sort": "-timestamp",
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, headers=_dd_headers(), json=body)
        resp.raise_for_status()
        data = resp.json()

    logs = data.get("data", [])
    results = []
    for log in logs:
        attrs = log.get("attributes", {})
        message = attrs.get("message", "")
        results.append({
            "timestamp": attrs.get("timestamp"),
            "service": attrs.get("service"),
            "message": message[:500],
            "fingerprint": _fingerprint(message),
        })
    return json.dumps(results, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Herramientas: AWS CloudWatch
# ---------------------------------------------------------------------------

@mcp.tool()
def cloudwatch_recent_errors(log_group: str, hours: int = 1, filter_pattern: str = "?ERROR ?Error ?error") -> str:
    """Busca eventos de error recientes en un log group de CloudWatch Logs.

    Args:
        log_group: nombre del log group (ej. "/aws/lambda/mi-funcion").
        hours: ventana de tiempo hacia atrás, en horas.
        filter_pattern: patrón de filtro de CloudWatch Logs Insights/Filter.
    """
    client = boto3.client("logs", region_name=AWS_REGION)
    start_time = int((time.time() - hours * 3600) * 1000)

    events = []
    kwargs = {
        "logGroupName": log_group,
        "startTime": start_time,
        "filterPattern": filter_pattern,
    }
    while True:
        resp = client.filter_log_events(**kwargs)
        events.extend(resp.get("events", []))
        next_token = resp.get("nextToken")
        if not next_token or len(events) >= 500:
            break
        kwargs["nextToken"] = next_token

    results = []
    for e in events:
        message = e.get("message", "")
        results.append({
            "timestamp": e.get("timestamp"),
            "message": message[:500],
            "fingerprint": _fingerprint(message),
        })
    return json.dumps(results, indent=2, ensure_ascii=False)


@mcp.tool()
def cloudwatch_list_log_groups(prefix: Optional[str] = None) -> str:
    """Lista los log groups disponibles en CloudWatch (útil para saber
    qué nombre exacto pasarle a cloudwatch_recent_errors).
    """
    client = boto3.client("logs", region_name=AWS_REGION)
    kwargs = {}
    if prefix:
        kwargs["logGroupNamePrefix"] = prefix
    resp = client.describe_log_groups(**kwargs)
    groups = [g["logGroupName"] for g in resp.get("logGroups", [])]
    return json.dumps(groups, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Herramienta combinada: detección de recurrencia
# ---------------------------------------------------------------------------

@mcp.tool()
async def find_recurring_errors(
    datadog_query: str = "status:error",
    log_group: Optional[str] = None,
    hours: int = 24,
    min_occurrences: int = 3,
) -> str:
    """Combina Datadog + CloudWatch (opcional) y agrupa errores por
    'fingerprint' para detectar cuáles son recurrentes en la ventana dada.

    Devuelve solo los grupos de error que ocurrieron min_occurrences veces
    o más, ordenados de más a menos frecuentes. Esto es lo que normalmente
    quieres revisar antes de decidir si vale la pena crear un fix.
    """
    all_messages = []

    dd_raw = await datadog_recent_errors(query=datadog_query, hours=hours, limit=500)
    for item in json.loads(dd_raw):
        all_messages.append((item["fingerprint"], item["message"], "datadog"))

    if log_group:
        cw_raw = cloudwatch_recent_errors(log_group=log_group, hours=hours)
        for item in json.loads(cw_raw):
            all_messages.append((item["fingerprint"], item["message"], "cloudwatch"))

    counter = Counter(fp for fp, _, _ in all_messages)
    examples = {}
    sources = {}
    for fp, msg, src in all_messages:
        examples.setdefault(fp, msg)
        sources.setdefault(fp, set()).add(src)

    recurring = [
        {
            "fingerprint": fp,
            "count": count,
            "example_message": examples[fp],
            "sources": list(sources[fp]),
        }
        for fp, count in counter.items()
        if count >= min_occurrences
    ]
    recurring.sort(key=lambda x: x["count"], reverse=True)
    return json.dumps(recurring, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Herramientas: memoria de incidentes (historial de diagnósticos/fixes)
# ---------------------------------------------------------------------------

@mcp.tool()
def check_known_incident(fingerprint: str) -> str:
    """Busca si un error con este 'fingerprint' ya fue diagnosticado antes.

    Úsala ANTES de investigar desde cero: si el error ya se vio antes,
    esto te ahorra tiempo y te da el diagnóstico/fix que funcionó
    (o que se intentó y no funcionó) la vez anterior.

    Args:
        fingerprint: huella del error (la que devuelven las herramientas
            de datadog_recent_errors / cloudwatch_recent_errors /
            find_recurring_errors).
    """
    history = _load_history()
    matches = [inc for inc in history if inc.get("fingerprint") == fingerprint]
    if not matches:
        return json.dumps({"found": False, "message": "Sin incidentes previos con este fingerprint."})
    return json.dumps({"found": True, "incidents": matches}, indent=2, ensure_ascii=False)


@mcp.tool()
def record_incident_resolution(
    fingerprint: str,
    error_summary: str,
    root_cause: str,
    resolution: str,
    resolution_type: str,
    pr_url: Optional[str] = None,
    outcome: str = "pending_review",
) -> str:
    """Registra el diagnóstico y la resolución de un incidente en el
    historial, para que sirva de referencia en futuros errores similares.

    Llama a esta herramienta SIEMPRE que termines de diagnosticar y
    proponer un fix, incluso si el PR aún no fue aprobado — así queda
    trazabilidad de qué se intentó.

    Args:
        fingerprint: huella del error (ver check_known_incident).
        error_summary: descripción breve del error observado.
        root_cause: causa raíz identificada (tu diagnóstico).
        resolution: qué se hizo para corregirlo (resumen del fix).
        resolution_type: uno de "code_fix", "infra_fix", "config_fix",
            "external_dependency", "false_positive", "needs_human_review".
        pr_url: URL del Pull Request si se creó uno.
        outcome: "pending_review", "merged", "rejected", "reverted".
    """
    history = _load_history()
    entry = {
        "fingerprint": fingerprint,
        "error_summary": error_summary,
        "root_cause": root_cause,
        "resolution": resolution,
        "resolution_type": resolution_type,
        "pr_url": pr_url,
        "outcome": outcome,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    history.append(entry)
    _save_history(history)
    return json.dumps({"saved": True, "entry": entry}, indent=2, ensure_ascii=False)


@mcp.tool()
def list_incident_history(resolution_type: Optional[str] = None, limit: int = 50) -> str:
    """Lista el historial completo de incidentes registrados, opcionalmente
    filtrado por tipo de resolución. Útil para revisar patrones generales
    o para auditar qué ha estado corrigiendo el agente.
    """
    history = _load_history()
    if resolution_type:
        history = [h for h in history if h.get("resolution_type") == resolution_type]
    history = sorted(history, key=lambda h: h.get("recorded_at", ""), reverse=True)[:limit]
    return json.dumps(history, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
