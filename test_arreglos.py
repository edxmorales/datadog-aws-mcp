"""
Pruebas de los arreglos de encoding, filtros y fingerprint.

No hay framework de tests en el repo, así que esto es un script ejecutable:

    python test_arreglos.py

Cada prueba imprime OK o FALLA y el script sale con código 1 si algo falló.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

FALLOS = []


def check(nombre: str, condicion: bool, detalle: str = "") -> None:
    if condicion:
        print(f"OK    {nombre}")
    else:
        print(f"FALLA {nombre}" + (f" -- {detalle}" if detalle else ""))
        FALLOS.append(nombre)


# ---------------------------------------------------------------------------
# 1. Encoding del historial de incidentes
# ---------------------------------------------------------------------------
#
# El bug: _load_history/_save_history no declaraban encoding, así que en
# Windows resolvían a cp1252. En Linux (contenedor) read_text() asume UTF-8
# y lanza UnicodeDecodeError, que el `except` de al lado no capturaba: las
# tools de historial reventaban en vez de degradar.

def test_encoding_historial():
    import server

    entradas = [
        {
            "fingerprint": "abc123",
            "error_summary": "Fallo — con guión largo y acentuación: ñ á é",
            "resolution": "Ninguna",
        }
    ]

    with tempfile.TemporaryDirectory() as tmp:
        ruta = Path(tmp) / "hist.json"
        original = server.INCIDENT_HISTORY_PATH
        try:
            server.INCIDENT_HISTORY_PATH = ruta
            server._save_history(entradas)

            # Lo que se escribió debe ser UTF-8 válido, sin importar el
            # locale de la máquina que lo escribió.
            crudo = ruta.read_bytes()
            try:
                crudo.decode("utf-8")
                es_utf8 = True
            except UnicodeDecodeError:
                es_utf8 = False
            check("historial se escribe en UTF-8", es_utf8)

            recargado = server._load_history()
            check(
                "historial hace round-trip sin perder caracteres",
                recargado == entradas,
                f"esperado {entradas}, recibido {recargado}",
            )
        finally:
            server.INCIDENT_HISTORY_PATH = original


def test_encoding_historial_legado_cp1252():
    """Un historial viejo escrito en cp1252 debe seguir leyéndose."""
    import server

    entradas = [{"fingerprint": "legado", "error_summary": "Fallo — guión"}]

    with tempfile.TemporaryDirectory() as tmp:
        ruta = Path(tmp) / "hist.json"
        ruta.write_text(json.dumps(entradas, ensure_ascii=False), encoding="cp1252")

        original = server.INCIDENT_HISTORY_PATH
        try:
            server.INCIDENT_HISTORY_PATH = ruta
            recargado = server._load_history()
            check(
                "historial legado en cp1252 se recupera, no se pierde",
                recargado == entradas,
                f"recibido {recargado}",
            )
        finally:
            server.INCIDENT_HISTORY_PATH = original


# ---------------------------------------------------------------------------
# 2. Filtros que recortan respuestas
# ---------------------------------------------------------------------------

def test_triggered_monitors_sin_plantilla():
    """La plantilla Handlebars del monitor no debe viajar al contexto."""
    import server
    import inspect

    fuente = inspect.getsource(server.datadog_triggered_monitors)
    check(
        "datadog_triggered_monitors ya no devuelve el campo 'message'",
        '"message": m.get("message"' not in fuente,
        "sigue proyectando el mensaje completo del monitor",
    )


def test_list_repos_acepta_filtro():
    import server
    import inspect

    firma = inspect.signature(server.azure_repos_list_repos)
    check(
        "azure_repos_list_repos acepta name_filter",
        "name_filter" in firma.parameters,
        f"parámetros: {list(firma.parameters)}",
    )


# ---------------------------------------------------------------------------
# 3. Fingerprint: normalizar identificadores hex
# ---------------------------------------------------------------------------
#
# El bug real observado en Gamanza: cada log traía un xRequestId hex de 32
# caracteres. _fingerprint solo sustituía \d+, así que el hex con letras
# sobrevivía y 1000 eventos del MISMO error producían 1000 fingerprints.

def test_fingerprint_agrupa_pese_al_request_id():
    import server

    plantilla = (
        'Gamanza OAuth request failed — url: https://platform-api-grupojer'
        '.gamanzaengage.com/api/oauth/v1/token, status: 401, response: '
        '{{"message":"Invalid Credentials","request":{{"xRequestId":"{rid}"}}}}'
    )
    ids = [
        "e013702101a221b7fa378db5bd2c5b1e",
        "355fd9cd7c0437ba4cbe450fadf91fea",
        "ec7aad0fe9440a32f52d195a11c0587b",
    ]
    huellas = {server._fingerprint(plantilla.format(rid=r)) for r in ids}
    check(
        "mismo error con xRequestId distinto agrupa en una sola huella",
        len(huellas) == 1,
        f"produjo {len(huellas)} huellas distintas",
    )


def test_fingerprint_sigue_separando_errores_distintos():
    """La normalización no debe volverse tan agresiva que colapse todo."""
    import server

    a = server._fingerprint("Connection refused to database replica")
    b = server._fingerprint("Invalid credentials for OAuth token endpoint")
    check("errores genuinamente distintos no colapsan", a != b)


def test_fingerprint_no_colapsa_palabras_normales():
    """'deadbeef' es hex, pero 'accede' o 'facade' también lo parecen."""
    import server

    a = server._fingerprint("Error al procesar la facade del servicio")
    b = server._fingerprint("Error al procesar la cache del servicio")
    check(
        "palabras cortas que parecen hex no se normalizan",
        a != b,
        "'facade' y 'cache' colapsaron en la misma huella",
    )


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    for prueba in [
        test_encoding_historial,
        test_encoding_historial_legado_cp1252,
        test_triggered_monitors_sin_plantilla,
        test_list_repos_acepta_filtro,
        test_fingerprint_agrupa_pese_al_request_id,
        test_fingerprint_sigue_separando_errores_distintos,
        test_fingerprint_no_colapsa_palabras_normales,
    ]:
        try:
            prueba()
        except Exception as e:
            print(f"FALLA {prueba.__name__} -- excepción: {type(e).__name__}: {e}")
            FALLOS.append(prueba.__name__)

    print()
    if FALLOS:
        print(f"{len(FALLOS)} prueba(s) fallando: {', '.join(FALLOS)}")
        sys.exit(1)
    print("Todas las pruebas pasan.")
