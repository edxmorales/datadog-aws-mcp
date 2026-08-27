"""
Servidor MCP: integración con Datadog, AWS CloudWatch/Logs, Azure Repos
y GitHub — TODAS las fuentes son independientes y opcionales.

Expone herramientas que Claude (vía Claude Code, la API con mcp_servers,
o Claude Desktop) puede usar para:
  - Consultar errores/monitores disparados en Datadog (opcional).
  - Consultar logs de error en CloudWatch (opcional).
  - Agrupar/detectar errores recurrentes (misma huella / fingerprint)
    combinando las fuentes que estén configuradas.
  - Leer código fuente de repos en Azure Repos (opcional) o GitHub
    (opcional), para diagnosticar errores antes de proponer un fix.

DISEÑO "TODO OPCIONAL, TODO COMBINABLE":
  No hace falta tener las 4 fuentes configuradas. Cada una se activa
  independientemente según qué variables de entorno definas — puedes
  usar solo AWS, solo Datadog, solo GitHub, AWS+GitHub, AWS+Datadog,
  las 4 juntas, etc. Si una fuente no está configurada:
    - Sus herramientas individuales (ej. datadog_recent_errors,
      azure_repos_get_file) devuelven un RuntimeError explicando
      exactamente qué variable falta, en vez de fallar de forma confusa.
    - find_recurring_errors simplemente omite esa fuente (lo reporta en
      "skipped_sources" de su respuesta) en vez de fallar por completo.

IMPORTANTE (seguridad):
  - Las credenciales se leen de variables de entorno, nunca hardcodeadas.
  - El rol IAM, las API keys de Datadog, el PAT de Azure DevOps y el
    token de GitHub usados aquí deben tener SOLO permisos de lectura
    (CloudWatch Logs read-only, Datadog read-only, Azure DevOps
    "Code (Read)", GitHub fine-grained "Contents: Read-only"). Este
    servidor NO expone herramientas de escritura/despliegue a propósito:
    esa parte (crear rama, hacer commit, abrir PR, deploy) se recomienda
    dejarla en manos de Claude Code usando git/gh directamente, con
    revisión humana antes de mergear o desplegar.

Requisitos:
    pip install mcp boto3 httpx python-dotenv --break-system-packages

Variables de entorno esperadas (ver .env.example) — TODAS opcionales
salvo que quieras usar esa fuente en particular:
    DD_API_KEY, DD_APP_KEY, DD_SITE (Datadog; por defecto datadoghq.com)
    AWS_REGION (AWS; usa las credenciales estándar: perfil, rol, env vars)
    AZURE_DEVOPS_ORG, AZURE_DEVOPS_PAT (Azure Repos)
    GITHUB_TOKEN, GITHUB_API_URL (GitHub; sin token solo ve repos públicos)
"""

import base64
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
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

# --- Datadog (opcional) ---
DD_API_KEY = os.environ.get("DD_API_KEY", "")
DD_APP_KEY = os.environ.get("DD_APP_KEY", "")
DD_SITE = os.environ.get("DD_SITE", "datadoghq.com")

# --- AWS CloudWatch (opcional) ---
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

# --- Azure Repos / Azure DevOps (opcional) ---
# Genérico a propósito: no asume una organización/proyecto/repo fijo,
# para que cualquiera pueda apuntarlo a su propia cuenta vía .env.
AZURE_DEVOPS_ORG = os.environ.get("AZURE_DEVOPS_ORG", "")
AZURE_DEVOPS_PAT = os.environ.get("AZURE_DEVOPS_PAT", "")
AZURE_DEVOPS_API_VERSION = os.environ.get("AZURE_DEVOPS_API_VERSION", "7.1")

# --- GitHub (opcional) ---
# Igual de genérico: sin GITHUB_TOKEN, las herramientas de solo-lectura
# de contenido funcionan igual pero limitadas a repos públicos y con
# límites de rate más bajos. Con un token (fine-grained, "Contents:
# Read-only") también ves repos privados a los que tengas acceso.
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_API_URL = os.environ.get("GITHUB_API_URL", "https://api.github.com")

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


def _cloudwatch_client():
    """Crea el cliente de boto3 para CloudWatch Logs, traduciendo errores
    de credenciales/permisos a un mensaje claro (AWS es una fuente
    opcional — boto3 por sí solo da errores crípticos como "Unable to
    locate credentials" que no dicen qué hacer al respecto).
    """
    try:
        client = boto3.client("logs", region_name=AWS_REGION)
        # boto3 no valida credenciales al crear el cliente; forzamos una
        # llamada barata para detectar el problema aquí, en un solo lugar.
        client.describe_log_groups(limit=1)
        return client
    except (BotoCoreError, ClientError) as e:
        raise RuntimeError(
            "No se pudo conectar a AWS CloudWatch Logs: "
            f"{e}. Verifica tus credenciales de AWS — vía `aws configure`, "
            "variables de entorno AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY, "
            "o un rol/perfil — y que AWS_REGION sea la región correcta. "
            "Nota: si tu Claude Desktop está instalado como app empaquetada "
            "de Windows, puede no ver tu ~/.aws/credentials — en ese caso "
            "define AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY directamente en "
            "el bloque \"env\" de la config del servidor MCP. Esta fuente es "
            "opcional — si no la necesitas, usa Datadog/Azure Repos/GitHub."
        ) from e


def _ec2_client():
    """Crea el cliente de boto3 para EC2 (NAT Gateways / Elastic IPs / VPCs).

    Requiere, además de las credenciales de AWS ya usadas por
    _cloudwatch_client, permisos de solo lectura de red que el usuario/rol
    de AWS puede no tener todavía: 'ec2:DescribeNatGateways',
    'ec2:DescribeAddresses' y 'ec2:DescribeVpcs' (por ejemplo, la política
    administrada AmazonEC2ReadOnlyAccess, o una política mínima con solo
    esas tres acciones). Sin ellos, AWS devuelve AccessDenied/
    UnauthorizedOperation — lo traducimos aquí a un mensaje claro.
    """
    try:
        client = boto3.client("ec2", region_name=AWS_REGION)
        client.describe_nat_gateways(MaxResults=5)
        return client
    except (BotoCoreError, ClientError) as e:
        raise RuntimeError(
            "No se pudo consultar EC2 (NAT Gateways / IPs de red): "
            f"{e}. Si el error es de permisos (AccessDenied / "
            "UnauthorizedOperation), el usuario de AWS en tu .env necesita "
            "además de CloudWatchLogsReadOnlyAccess los permisos de solo "
            "lectura 'ec2:DescribeNatGateways', 'ec2:DescribeAddresses' y "
            "'ec2:DescribeVpcs' (ej. la política administrada "
            "AmazonEC2ReadOnlyAccess). Esta fuente es opcional — si no la "
            "necesitas, usa Datadog/Azure Repos/GitHub."
        ) from e


def _elbv2_client():
    """Crea el cliente de boto3 para Elastic Load Balancing v2 (ALB/NLB).

    Requiere el permiso de solo lectura
    'elasticloadbalancing:DescribeLoadBalancers' (ej. la política
    administrada ElasticLoadBalancingReadOnly), que el usuario/rol de AWS
    puede no tener todavía.
    """
    try:
        client = boto3.client("elbv2", region_name=AWS_REGION)
        client.describe_load_balancers(PageSize=5)
        return client
    except (BotoCoreError, ClientError) as e:
        raise RuntimeError(
            "No se pudo consultar Elastic Load Balancing: "
            f"{e}. El usuario de AWS en tu .env necesita el permiso de "
            "solo lectura 'elasticloadbalancing:DescribeLoadBalancers' "
            "(ej. la política administrada ElasticLoadBalancingReadOnly). "
            "Esta fuente es opcional — si no la necesitas, usa Datadog/"
            "Azure Repos/GitHub."
        ) from e


def _require_datadog_config() -> None:
    if not DD_API_KEY or not DD_APP_KEY:
        raise RuntimeError(
            "Datadog no está configurado en este servidor MCP: define "
            "DD_API_KEY y DD_APP_KEY en tu .env para usar las herramientas "
            "de Datadog. Esta fuente es opcional — si no la necesitas, "
            "usa las de AWS/Azure Repos/GitHub en su lugar."
        )


def _github_headers() -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers


def _require_azure_config() -> None:
    if not AZURE_DEVOPS_ORG or not AZURE_DEVOPS_PAT:
        raise RuntimeError(
            "Azure DevOps no está configurado en este servidor MCP: define "
            "AZURE_DEVOPS_ORG y AZURE_DEVOPS_PAT en tu .env (PAT con scope "
            "de solo lectura 'Code (Read)') para usar las herramientas de "
            "Azure Repos. Ver README.md, sección 'Azure Repos (opcional)'."
        )


def _azure_headers() -> dict:
    token = base64.b64encode(f":{AZURE_DEVOPS_PAT}".encode()).decode()
    return {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
    }


def _azure_base_url(project: Optional[str] = None) -> str:
    if project:
        return f"https://dev.azure.com/{AZURE_DEVOPS_ORG}/{project}/_apis"
    return f"https://dev.azure.com/{AZURE_DEVOPS_ORG}/_apis"


# ---------------------------------------------------------------------------
# Herramientas: Datadog
# ---------------------------------------------------------------------------

@mcp.tool()
async def datadog_triggered_monitors() -> str:
    """Lista los monitores de Datadog que están actualmente en estado
    'Alert' o 'Warn'. Útil para saber qué está fallando ahora mismo.

    Requiere DD_API_KEY y DD_APP_KEY configurados en el .env (fuente
    opcional — si no usas Datadog, usa las herramientas de AWS/Azure
    Repos/GitHub en su lugar).
    """
    _require_datadog_config()
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

    Requiere DD_API_KEY y DD_APP_KEY configurados en el .env (fuente
    opcional).
    """
    _require_datadog_config()
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

    Requiere credenciales de AWS configuradas (fuente opcional).
    """
    client = _cloudwatch_client()
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

    Requiere credenciales de AWS configuradas (fuente opcional).
    """
    client = _cloudwatch_client()
    kwargs = {}
    if prefix:
        kwargs["logGroupNamePrefix"] = prefix
    resp = client.describe_log_groups(**kwargs)
    groups = [g["logGroupName"] for g in resp.get("logGroups", [])]
    return json.dumps(groups, indent=2, ensure_ascii=False)


@mcp.tool()
def aws_network_egress_ips(name_filter: Optional[str] = None) -> str:
    """Lista las IPs públicas de salida (Elastic IPs de los NAT Gateways)
    de la cuenta de AWS, agrupadas por VPC.

    Útil para responder "¿qué IP ve un tercero (ej. un partner que hace
    whitelist) cuando el tráfico sale desde tal ambiente/VPC?" sin tener
    que esperar a que esa IP aparezca de rebote en un log de error.

    Args:
        name_filter: si se indica, filtra por el tag "Name" del NAT
            Gateway o de su VPC (búsqueda parcial, sin distinguir
            mayúsculas — ej. "prod", "dev", "neat").

    Requiere permisos de solo lectura de EC2 — ver el docstring de
    _ec2_client para el detalle exacto. Fuente opcional.
    """
    client = _ec2_client()
    nats = client.describe_nat_gateways().get("NatGateways", [])

    vpc_ids = sorted({n.get("VpcId") for n in nats if n.get("VpcId")})
    vpc_names = {}
    if vpc_ids:
        vpcs = client.describe_vpcs(VpcIds=vpc_ids).get("Vpcs", [])
        for v in vpcs:
            tags = {t["Key"]: t["Value"] for t in v.get("Tags", [])}
            vpc_names[v["VpcId"]] = tags.get("Name", v["VpcId"])

    results = []
    for n in nats:
        if n.get("State") != "available":
            continue
        nat_tags = {t["Key"]: t["Value"] for t in n.get("Tags", [])}
        nat_name = nat_tags.get("Name", n.get("NatGatewayId"))
        vpc_id = n.get("VpcId")
        vpc_name = vpc_names.get(vpc_id, vpc_id)

        if name_filter:
            haystack = f"{nat_name} {vpc_name}".lower()
            if name_filter.lower() not in haystack:
                continue

        public_ips = [
            addr.get("PublicIp")
            for addr in n.get("NatGatewayAddresses", [])
            if addr.get("PublicIp")
        ]
        results.append({
            "nat_gateway_id": n.get("NatGatewayId"),
            "name": nat_name,
            "vpc_id": vpc_id,
            "vpc_name": vpc_name,
            "public_ips": public_ips,
        })

    return json.dumps(results, indent=2, ensure_ascii=False)


@mcp.tool()
def aws_list_load_balancers(name_filter: Optional[str] = None) -> str:
    """Lista los Load Balancers (ALB/NLB) de la cuenta de AWS: nombre,
    tipo, si son públicos o internos, DNS name y VPC.

    Útil para ubicar el front de un operador. OJO: un ALB normal NO tiene
    IP pública fija — su DNS name puede resolver a varias IPs que AWS
    rota sin aviso. Solo un NLB con Elastic IP asignada tiene IP fija.
    Para una IP estable, usa aws_network_egress_ips (tráfico saliente) o
    resuelve este DNS name en el momento que lo necesites.

    Args:
        name_filter: si se indica, filtra por nombre del load balancer
            (búsqueda parcial, sin distinguir mayúsculas).

    Requiere el permiso de solo lectura
    'elasticloadbalancing:DescribeLoadBalancers'. Fuente opcional.
    """
    client = _elbv2_client()
    lbs = client.describe_load_balancers().get("LoadBalancers", [])

    results = []
    for lb in lbs:
        name = lb.get("LoadBalancerName", "")
        if name_filter and name_filter.lower() not in name.lower():
            continue
        results.append({
            "name": name,
            "type": lb.get("Type"),
            "scheme": lb.get("Scheme"),
            "dns_name": lb.get("DNSName"),
            "vpc_id": lb.get("VpcId"),
            "state": lb.get("State", {}).get("Code"),
        })

    return json.dumps(results, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Herramientas: Azure Repos (Azure DevOps) — opcional
# ---------------------------------------------------------------------------
#
# Genéricas a propósito: no asumen una organización, proyecto o repo fijo.
# Cualquiera que instale este MCP las activa apuntándolas a su propia
# cuenta de Azure DevOps vía AZURE_DEVOPS_ORG / AZURE_DEVOPS_PAT en el
# .env. Si no se configuran, cada herramienta devuelve un error claro en
# vez de fallar de forma confusa.
#
# Útiles cuando el código fuente del servicio que está fallando (visto en
# find_recurring_errors) vive en Azure Repos en vez de GitHub: primero
# ubica el proyecto/repo, y luego lee el archivo relevante para diagnosticar
# antes de proponer un fix.

@mcp.tool()
async def azure_devops_list_projects() -> str:
    """Lista los proyectos disponibles en tu organización de Azure DevOps.

    Úsala primero para saber qué nombre exacto de 'project' pasarle a
    azure_repos_list_repos / azure_repos_get_file / azure_repos_search_code.

    Requiere AZURE_DEVOPS_ORG y AZURE_DEVOPS_PAT configurados en el .env
    (ver README.md, sección 'Azure Repos (opcional)').
    """
    _require_azure_config()
    url = f"{_azure_base_url()}/projects"
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            url, headers=_azure_headers(), params={"api-version": AZURE_DEVOPS_API_VERSION}
        )
        resp.raise_for_status()
        data = resp.json()

    projects = [{"id": p["id"], "name": p["name"]} for p in data.get("value", [])]
    return json.dumps(projects, indent=2, ensure_ascii=False)


@mcp.tool()
async def azure_repos_list_repos(project: str) -> str:
    """Lista los repositorios Git dentro de un proyecto de Azure DevOps.

    Args:
        project: nombre o ID del proyecto (ver azure_devops_list_projects).
    """
    _require_azure_config()
    url = f"{_azure_base_url(project)}/git/repositories"
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            url, headers=_azure_headers(), params={"api-version": AZURE_DEVOPS_API_VERSION}
        )
        resp.raise_for_status()
        data = resp.json()

    repos = [
        {
            "id": r["id"],
            "name": r["name"],
            "default_branch": r.get("defaultBranch"),
            "web_url": r.get("webUrl"),
        }
        for r in data.get("value", [])
    ]
    return json.dumps(repos, indent=2, ensure_ascii=False)


@mcp.tool()
async def azure_repos_get_file(
    project: str, repository: str, path: str, branch: Optional[str] = None
) -> str:
    """Obtiene el contenido de un archivo de un repo de Azure Repos.

    Útil para leer el código fuente real al diagnosticar un error (ej.
    después de find_recurring_errors) cuando el repo vive en Azure DevOps
    en vez de GitHub.

    Args:
        project: nombre o ID del proyecto.
        repository: nombre o ID del repositorio (ver azure_repos_list_repos).
        path: ruta del archivo dentro del repo (ej. "/src/checkout/handler.py").
        branch: rama a consultar (por defecto, la rama por defecto del repo).
    """
    _require_azure_config()
    url = f"{_azure_base_url(project)}/git/repositories/{repository}/items"
    params = {
        "path": path,
        "api-version": AZURE_DEVOPS_API_VERSION,
        "includeContent": "true",
    }
    if branch:
        params["versionDescriptor.version"] = branch
        params["versionDescriptor.versionType"] = "branch"

    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=_azure_headers(), params=params)
        resp.raise_for_status()
        # La API de Items de Azure Repos devuelve el contenido crudo del
        # archivo en el body (no un JSON con un campo "content") cuando se
        # pide sin "$format=json". Antes este código hacía resp.json() y
        # buscaba data["content"], que nunca existe: si el archivo es JSON
        # válido (ej. package.json) el archivo entero se parseaba como el
        # "sobre" y content quedaba en "" por defecto; si no es JSON (ej.
        # README.md) resp.json() lanzaba "Expecting value: line 1 column 1".
        try:
            content = resp.text
        except UnicodeDecodeError:
            content = "<archivo binario, no se puede mostrar como texto>"

    return json.dumps(
        {"path": path, "content": content},
        indent=2,
        ensure_ascii=False,
    )


@mcp.tool()
async def azure_repos_search_code(
    project: str, search_text: str, repository: Optional[str] = None, top: int = 20
) -> str:
    """Busca texto/código dentro de los repos de Azure Repos.

    Requiere que la extensión gratuita "Azure DevOps Search" esté
    habilitada en tu organización (si no lo está, la API devuelve error;
    en ese caso usa azure_repos_get_file con la ruta exacta en su lugar).

    Útil para ubicar en qué archivo vive el código relacionado con un
    mensaje de error antes de proponer un fix.

    Args:
        project: nombre o ID del proyecto donde buscar.
        search_text: texto o snippet de código a buscar.
        repository: si se indica, limita la búsqueda a ese repo.
        top: máximo de resultados a devolver.
    """
    _require_azure_config()
    url = f"https://almsearch.dev.azure.com/{AZURE_DEVOPS_ORG}/{project}/_apis/search/codesearchresults"
    body = {"searchText": search_text, "$top": top}
    if repository:
        body["filters"] = {"Repository": [repository]}

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            url,
            headers=_azure_headers(),
            params={"api-version": AZURE_DEVOPS_API_VERSION},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()

    results = [
        {
            "file": r.get("path"),
            "repository": r.get("repository", {}).get("name"),
        }
        for r in data.get("results", [])
    ]
    return json.dumps(results, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Herramientas: GitHub — opcional
# ---------------------------------------------------------------------------
#
# Igual de genérico que Azure Repos: sin GITHUB_TOKEN configurado, estas
# herramientas funcionan igual pero limitadas a repos públicos y con
# límites de rate más bajos (la API de GitHub lo permite sin auth para
# lectura de contenido). Con un token (fine-grained, scope de solo
# lectura "Contents: Read-only") también ves repos privados.
#
# No confundas esto con Azure Repos: son dos fuentes de código
# independientes y ambas opcionales — usa la que corresponda a dónde
# vive tu código (o ambas, si distintos servicios están en distintos
# hosts).

@mcp.tool()
async def github_list_repos(owner: str) -> str:
    """Lista los repositorios de un usuario o de una organización de GitHub.

    Args:
        owner: nombre de usuario u organización en GitHub (ej. "octocat").

    Sin GITHUB_TOKEN configurado, solo ve repos públicos. Con un token
    (fine-grained, "Contents: Read-only"), también ve repos privados a
    los que ese token tenga acceso.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{GITHUB_API_URL}/orgs/{owner}/repos",
            headers=_github_headers(),
            params={"per_page": 100},
        )
        if resp.status_code >= 400:
            # No es una organización (o el endpoint de orgs la rechazó por
            # otra razón, ej. sin auth) — probablemente es un usuario.
            resp = await client.get(
                f"{GITHUB_API_URL}/users/{owner}/repos",
                headers=_github_headers(),
                params={"per_page": 100},
            )
        resp.raise_for_status()
        data = resp.json()

    repos = [
        {
            "name": r["name"],
            "full_name": r["full_name"],
            "default_branch": r.get("default_branch"),
            "private": r.get("private"),
            "html_url": r.get("html_url"),
        }
        for r in data
    ]
    return json.dumps(repos, indent=2, ensure_ascii=False)


@mcp.tool()
async def github_get_file(owner: str, repo: str, path: str, ref: Optional[str] = None) -> str:
    """Obtiene el contenido de un archivo de un repo de GitHub.

    Útil para leer el código fuente real al diagnosticar un error (ej.
    después de find_recurring_errors) cuando el repo vive en GitHub.

    Args:
        owner: usuario u organización dueño del repo.
        repo: nombre del repositorio.
        path: ruta del archivo dentro del repo (ej. "src/checkout/handler.py").
        ref: rama, tag o commit SHA a consultar (por defecto la rama por
            defecto del repo).
    """
    url = f"{GITHUB_API_URL}/repos/{owner}/{repo}/contents/{path}"
    params = {"ref": ref} if ref else {}

    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=_github_headers(), params=params)
        resp.raise_for_status()
        data = resp.json()

    content = data.get("content", "")
    if data.get("encoding") == "base64" and content:
        content = base64.b64decode(content).decode("utf-8", errors="replace")

    return json.dumps(
        {"path": data.get("path", path), "content": content}, indent=2, ensure_ascii=False
    )


@mcp.tool()
async def github_search_code(
    search_text: str, owner: Optional[str] = None, repo: Optional[str] = None
) -> str:
    """Busca texto/código dentro de repos de GitHub.

    Requiere GITHUB_TOKEN configurado — la Search API de GitHub exige
    autenticación incluso para repos públicos.

    Args:
        search_text: texto o snippet de código a buscar.
        owner: si se indica (y no `repo`), limita la búsqueda a esa
            organización/usuario.
        repo: si se indica junto a `owner`, limita la búsqueda a ese repo
            específico (formato interno: "owner/repo").
    """
    if not GITHUB_TOKEN:
        raise RuntimeError(
            "github_search_code requiere GITHUB_TOKEN configurado: la "
            "Search API de GitHub no acepta búsquedas de código sin "
            "autenticación, ni siquiera en repos públicos. Define "
            "GITHUB_TOKEN en tu .env (fine-grained, 'Contents: Read-only')."
        )

    query = search_text
    if owner and repo:
        query += f" repo:{owner}/{repo}"
    elif owner:
        query += f" org:{owner}"

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{GITHUB_API_URL}/search/code", headers=_github_headers(), params={"q": query}
        )
        resp.raise_for_status()
        data = resp.json()

    results = [
        {"file": item.get("path"), "repository": item.get("repository", {}).get("full_name")}
        for item in data.get("items", [])
    ]
    return json.dumps(results, indent=2, ensure_ascii=False)


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
    """Combina Datadog + CloudWatch — las fuentes que estén configuradas —
    y agrupa errores por 'fingerprint' para detectar cuáles son
    recurrentes en la ventana dada.

    NO requiere tener ambas fuentes configuradas: si Datadog no está
    configurado, simplemente se omite (igual si no pasas `log_group`, o
    si AWS no tiene credenciales válidas). La respuesta incluye
    "skipped_sources" con el detalle de qué se omitió y por qué, para que
    sepas si el resultado es parcial.

    Devuelve solo los grupos de error que ocurrieron min_occurrences veces
    o más, ordenados de más a menos frecuentes. Esto es lo que normalmente
    quieres revisar antes de decidir si vale la pena crear un fix.
    """
    all_messages = []
    skipped_sources = []

    if DD_API_KEY and DD_APP_KEY:
        try:
            dd_raw = await datadog_recent_errors(query=datadog_query, hours=hours, limit=500)
            for item in json.loads(dd_raw):
                all_messages.append((item["fingerprint"], item["message"], "datadog"))
        except Exception as e:
            skipped_sources.append({"source": "datadog", "reason": str(e)})
    else:
        skipped_sources.append({"source": "datadog", "reason": "no configurado (DD_API_KEY/DD_APP_KEY)"})

    if log_group:
        try:
            cw_raw = cloudwatch_recent_errors(log_group=log_group, hours=hours)
            for item in json.loads(cw_raw):
                all_messages.append((item["fingerprint"], item["message"], "cloudwatch"))
        except Exception as e:
            skipped_sources.append({"source": "cloudwatch", "reason": str(e)})
    else:
        skipped_sources.append({"source": "cloudwatch", "reason": "no se indicó log_group"})

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
    return json.dumps(
        {"recurring": recurring, "skipped_sources": skipped_sources},
        indent=2,
        ensure_ascii=False,
    )


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
