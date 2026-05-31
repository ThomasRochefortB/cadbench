import contextlib

from starlette.applications import Starlette
from starlette.middleware.wsgi import WSGIMiddleware
from starlette.routing import Mount

from app import app as flask_app
from cadbench_mcp_server import cadbench_mcp


def create_asgi_app(mcp_server=cadbench_mcp) -> Starlette:
    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        async with mcp_server.session_manager.run():
            yield

    return Starlette(
        routes=[
            Mount("/mcp", app=mcp_server.streamable_http_app()),
            Mount("/", app=WSGIMiddleware(flask_app)),
        ],
        lifespan=lifespan,
    )


app = create_asgi_app()
