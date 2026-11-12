"""Mount the private experiment without changing Flask routes or authentication."""

from contextlib import asynccontextmanager, AsyncExitStack

from a2wsgi import WSGIMiddleware
from starlette.applications import Starlette
from starlette.routing import Mount

from lms_mcp.probe import build_probe


def create_asgi_app(flask_app, settings=None, runtime=None):
    routes = []
    probe = None
    if settings:
        probe, _ = build_probe(settings)
        routes.append(Mount("/mcp-probe", app=probe))

    student = None
    if runtime:
        from lms_mcp.server import build_student_server
        student = build_student_server(runtime)
        # SDK routes must remain inside its auth/context middleware. Put the
        # existing Flask fallback last, inside that same app, then mount it.
        student.router.routes.append(Mount('/', app=WSGIMiddleware(flask_app, workers=8)))

    @asynccontextmanager
    async def lifespan(app):
        async with AsyncExitStack() as stack:
            if probe:
                await stack.enter_async_context(probe.router.lifespan_context(probe))
            if student:
                await stack.enter_async_context(student.router.lifespan_context(student))
            yield

    routes.append(Mount("/", app=student or WSGIMiddleware(flask_app, workers=8)))
    application = Starlette(routes=routes, lifespan=lifespan)
    if runtime:
        from lms_mcp.http_security import ConnectorHTTPGuard
        return ConnectorHTTPGuard(application, runtime.settings.origin)
    return application
