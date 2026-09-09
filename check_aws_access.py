"""
Diagnóstico rápido de acceso a AWS para datadog-aws-mcp.
Usa el mismo AWS_REGION/credenciales que carga server.py (via .env) y el
mismo timeout corto (5s conexión / 10s lectura) que ya le pusimos a los
clientes boto3, para que esto falle rápido y te diga exactamente qué está
mal: credenciales inválidas, permisos faltantes, o la red/VPN sin salida
a AWS.

Uso:
    python check_aws_access.py
"""
import os
import socket
import sys

from dotenv import load_dotenv

load_dotenv()

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, EndpointConnectionError

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
ACCESS_KEY = os.environ.get("AWS_ACCESS_KEY_ID", "")
CFG = Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 2, "mode": "standard"})


def line(title):
    print(f"\n--- {title} " + "-" * (60 - len(title)))


line("Config cargada")
print(f"AWS_REGION            = {AWS_REGION!r}")
print(f"AWS_ACCESS_KEY_ID     = {ACCESS_KEY[:4]}...{ACCESS_KEY[-4:] if len(ACCESS_KEY) > 8 else ''} "
      f"({len(ACCESS_KEY)} chars)" if ACCESS_KEY else "AWS_ACCESS_KEY_ID     = (vacío)")

line("1) Resolución DNS + conexión TCP al endpoint STS")
host = f"sts.{AWS_REGION}.amazonaws.com"
try:
    ip = socket.gethostbyname(host)
    print(f"DNS OK: {host} -> {ip}")
    s = socket.create_connection((host, 443), timeout=5)
    s.close()
    print(f"TCP 443 OK: se pudo conectar a {host}")
except socket.gaierror as e:
    print(f"FALLÓ resolución DNS de {host}: {e}")
    print("  -> Probable causa: sin internet, o DNS corporativo/VPN no resuelve dominios AWS.")
except OSError as e:
    print(f"FALLÓ conexión TCP a {host}:443: {e}")
    print("  -> Probable causa: firewall/VPN bloqueando salida a AWS (esto es lo que")
    print("     hace que boto3 se cuelgue largo rato en vez de fallar rápido).")

line("2) Credenciales: sts.get_caller_identity()")
try:
    sts = boto3.client("sts", region_name=AWS_REGION, config=CFG)
    identity = sts.get_caller_identity()
    print("Credenciales VÁLIDAS.")
    print(f"  Account : {identity.get('Account')}")
    print(f"  Arn     : {identity.get('Arn')}")
except (BotoCoreError, ClientError, EndpointConnectionError) as e:
    print(f"FALLÓ: {e}")

line("3) Permiso CloudWatch Logs: logs.describe_log_groups(limit=1)")
try:
    logs = boto3.client("logs", region_name=AWS_REGION, config=CFG)
    logs.describe_log_groups(limit=1)
    print("OK: puede listar log groups.")
except (BotoCoreError, ClientError, EndpointConnectionError) as e:
    print(f"FALLÓ: {e}")

line("4) Permiso EC2: ec2.describe_nat_gateways(MaxResults=5)")
try:
    ec2 = boto3.client("ec2", region_name=AWS_REGION, config=CFG)
    ec2.describe_nat_gateways(MaxResults=5)
    print("OK: puede listar NAT gateways.")
except (BotoCoreError, ClientError, EndpointConnectionError) as e:
    print(f"FALLÓ: {e}")

line("5) Permiso ELBv2: elbv2.describe_load_balancers(PageSize=5)")
try:
    elbv2 = boto3.client("elbv2", region_name=AWS_REGION, config=CFG)
    elbv2.describe_load_balancers(PageSize=5)
    print("OK: puede listar load balancers.")
except (BotoCoreError, ClientError, EndpointConnectionError) as e:
    print(f"FALLÓ: {e}")

print("\nListo. Cada bloque que diga FALLÓ es exactamente el punto a resolver.")
