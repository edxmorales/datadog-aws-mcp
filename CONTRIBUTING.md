# Contribuir a datadog-aws-mcp

¡Gracias por tu interés en contribuir! Este proyecto es joven, así que
las contribuciones —de código, documentación, o simplemente reportar
un bug— son muy bienvenidas.

## Cómo reportar un bug

Abre un Issue con:
- Qué esperabas que pasara vs. qué pasó realmente.
- Versión de Python y del paquete `mcp` que estás usando.
- Logs relevantes (¡sin pegar tus API keys ni datos sensibles!).

## Cómo proponer una mejora / feature

Abre un Issue primero describiendo la idea antes de invertir tiempo en
un PR grande — así evitamos que trabajes en algo que no encaje con la
dirección del proyecto.

## Cómo enviar un Pull Request

1. Haz fork del repo y crea una rama descriptiva:
   `git checkout -b feature/nombre-corto` o `fix/nombre-corto`.
2. Instala dependencias de desarrollo:
   ```bash
   pip install -r requirements.txt --break-system-packages
   ```
3. Sigue el estilo del código existente (nombres descriptivos,
   docstrings en las herramientas MCP explicando qué hace cada una —
   Claude las usa para decidir cuándo llamarlas, así que la claridad
   importa más de lo habitual).
4. Si agregas una herramienta nueva, documéntala también en la tabla
   del `README.md`.
5. Verifica que el archivo compile sin errores:
   ```bash
   python3 -m py_compile server.py
   ```
6. Abre el Pull Request describiendo el cambio y por qué es necesario.

## Ideas de contribución bienvenidas

- Soporte para otros proveedores de observabilidad (New Relic, Grafana,
  Sentry).
- Soporte para otras fuentes de AWS además de CloudWatch Logs (X-Ray,
  RDS Performance Insights).
- Soporte para otros hosts de código además de Azure Repos/GitHub
  (GitLab, Bitbucket) — sigue el mismo patrón: fuente 100% opcional,
  activada solo por sus propias variables de entorno, con un
  `_require_*_config()` que da un error claro si falta algo, sin afectar
  al resto del servidor si no se usa.
- Un modo "dry-run" que solo reporte sin nunca sugerir cambios de código.
- Integración con Slack/Discord para notificar cuando se abre un PR
  generado por el agente.

## Código de conducta

Sé respetuoso. Este es un proyecto pequeño mantenido por la comunidad,
no una empresa con equipo de moderación — tratemos bien a quien dedique
su tiempo a mejorarlo.
