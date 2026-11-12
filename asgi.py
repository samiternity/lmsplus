"""Opt-in testing entry point: MCP probe and existing Flask in one process."""

from app import create_app
from lms_mcp.asgi import create_asgi_app
from lms_mcp.probe import ProbeSettings

flask_app = create_app()
app = create_asgi_app(flask_app, ProbeSettings.from_environment(), flask_app.extensions.get('lms_mcp'))
