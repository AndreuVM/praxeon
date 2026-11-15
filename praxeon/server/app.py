"""Punto de entrada y configuración de la aplicación FastAPI de PRAXEON 1.0 (praxeon/server/app.py)."""

import argparse
import os
import secrets
from typing import Any
import uvicorn
from fastapi import Depends, FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from praxeon import __version__
from praxeon.env import env_setting
from praxeon.runtime.keys import production_keys_problem
from praxeon.server.host_guard import HostGuardMiddleware, configured_hosts
from praxeon.server.routes import api_router
from praxeon.server.websocket import ws_router


import logging

logger = logging.getLogger("praxeon.server.app")


INSECURE_API_KEYS = frozenset({"", "praxeon_secret_hmac_key_v1", "default", "secret", "change_me"})


def validate_security_profile(profile: str | None = None) -> list[str]:
    """Valida el perfil de seguridad del runtime conforme a la Sección 8 y Sección 18 (Criterio 11)."""
    prof = (profile or os.environ.get("PRAXEON_PROFILE") or os.environ.get("PRAXEON_ENV") or "dev").lower().strip()

    if prof == "production":
        problem = production_keys_problem()
        if problem:
            raise ValueError(problem)

        cors_env = os.environ.get("PRAXEON_CORS_ORIGINS", "")
        if not cors_env or "*" in cors_env:
            raise ValueError(
                "El comodín '*' o lista vacía en CORS está prohibido en perfil 'production'. Configure dominios explícitos en PRAXEON_CORS_ORIGINS."
            )
        origins = [o.strip() for o in cors_env.split(",") if o.strip()]
        if not origins or "*" in origins:
            raise ValueError(
                "Lista de orígenes CORS inválida para producción. Especifique dominios explícitos sin comodines."
            )

        api_key = env_setting("API_KEY", "")  # también el nombre histórico JEV_API_KEY, que auth_service acepta
        if api_key and (api_key in INSECURE_API_KEYS or len(api_key) < 16):
            raise ValueError(
                "Perfil de seguridad 'production' requiere que PRAXEON_API_KEY esté configurada con al menos 16 caracteres y no use claves por defecto."
            )
        return origins

    cors_env = os.environ.get("PRAXEON_CORS_ORIGINS")
    if cors_env:
        return [o.strip() for o in cors_env.split(",") if o.strip()]
    return [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]


def create_app(
    profile: str | None = None,
    service: Any | None = None,
) -> FastAPI:
    """Crea y configura la aplicación FastAPI con rutas y middleware."""
    from praxeon.server.auth_deps import require_permission
    from praxeon.server.bootstrap import resolve_security_profile
    from praxeon.server.dependencies import get_runtime_service
    from praxeon.server.services.auth_service import Permission

    prof = resolve_security_profile(profile)

    origins = validate_security_profile(prof)

    app = FastAPI(
        title="PRAXEON Web Server",
        version=__version__,
        description="Runtime supervision for autonomous AI agents — REST API & WebSocket Streaming",
        docs_url=None if prof == "production" else "/docs",
        redoc_url=None if prof == "production" else "/redoc",
        openapi_url=None if prof == "production" else "/openapi.json",
    )
    app.state.security_profile = prof
    # Cada aplicación atiende con su servicio: el indicado o el del proceso, construido en praxeon.server.bootstrap
    app.state.service = service if service is not None else get_runtime_service()
    app.state.service.security_profile = prof

    is_prod = (prof == "production")
    cors_methods = ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"] if is_prod else ["*"]
    cors_headers = ["Authorization", "X-API-Key", "Content-Type", "Accept"] if is_prod else ["*"]

    app.add_middleware(HostGuardMiddleware)      # la última en añadirse es la primera en ejecutarse: antes que CORS y las rutas
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=cors_methods,
        allow_headers=cors_headers,
    )

    # Registrar routers REST y WebSocket
    app.include_router(api_router)
    app.include_router(ws_router)

    # Endpoint canónico raíz /metrics compatible con Prometheus y OpenTelemetry Collector
    @app.get("/metrics", response_class=Response, include_in_schema=True)
    def prometheus_metrics_root(
        service_instance: Any = Depends(get_runtime_service),
        _tenant: Any = Depends(require_permission(Permission.METRICS_READ)),
    ) -> Response:
        """Endpoint canónico de métricas en formato texto plano Prometheus (0.0.4)."""
        from praxeon.telemetry.prometheus import get_prometheus_exporter
        exporter = get_prometheus_exporter()
        return Response(
            content=exporter.generate_metrics(service_instance),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    # Endpoint raíz /health para balanceadores de carga y sondas Kubernetes
    @app.get("/health", include_in_schema=True)
    def health_root():
        """Healthcheck raíz para balanceadores de carga y sondas Kubernetes."""
        return {"status": "healthy", "version": __version__}

    # Servir interfaz web de supervisión si está construida o empaquetada
    static_packaged = os.path.abspath(os.path.join(os.path.dirname(__file__), "static"))
    frontend_dist = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "web", "dist"))
    target_static_dir = None
    if os.path.isdir(frontend_dist) and os.path.isfile(os.path.join(frontend_dist, "index.html")):
        target_static_dir = frontend_dist
    elif os.path.isdir(static_packaged) and os.path.isfile(os.path.join(static_packaged, "index.html")):
        target_static_dir = static_packaged

    if target_static_dir:
        from fastapi.staticfiles import StaticFiles

        class FrontendFiles(StaticFiles):
            """Interfaz web con caché correcta.

            Sin `Cache-Control`, el navegador embebido (que guarda su caché entre arranques) aplica caché heurística y puede servir
            un `index.html` de hace horas: apunta a archivos con hash que ya no existen y la pantalla se queda en blanco.
            El HTML se revalida siempre; los archivos con hash en su nombre (`assets/`) no cambian nunca, así que se cachean.
            """

            async def get_response(self, path, scope):
                response = await super().get_response(path, scope)
                if response.status_code in (200, 304):
                    hashed = path.replace("\\", "/").startswith("assets/")
                    response.headers["Cache-Control"] = "public, max-age=31536000, immutable" if hashed else "no-cache"
                return response

        app.mount("/", FrontendFiles(directory=target_static_dir, html=True), name="frontend")

    # Las rutas piden el servicio con Depends(get_runtime_service): se resuelve al de esta aplicación
    app.dependency_overrides[get_runtime_service] = lambda: app.state.service

    return app


_default_app: FastAPI | None = None


def __getattr__(name: str) -> Any:
    """`praxeon.server.app.app` se crea al pedirlo por primera vez, no al importar el módulo (auditoría F-03).

    Importar ya no valida el perfil ni registra rutas como efecto secundario. Los arranques propios usan
    `uvicorn ...:create_app --factory`; este atributo se mantiene para quien lance `uvicorn praxeon.server.app:app`.
    """
    global _default_app
    if name == "app":
        if _default_app is None:
            _default_app = create_app()
        return _default_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def validate_network_binding(host: str) -> None:
    """Valida que si el servidor se vincula a interfaces de red externas, se exija y esté configurada autenticación."""
    loopback_hosts = {"127.0.0.1", "localhost", "::1", "testclient"}
    normalized_host = (host or "").lower().strip()
    if normalized_host not in loopback_hosts:
        # PRAXEON_SECRET_KEY es un secreto maestro de firma, no una credencial de cliente: no basta para exponerse en red
        has_auth = bool(env_setting("API_KEY", "").strip())
        if has_auth and not configured_hosts():
            raise ValueError(
                f"Vincular el servidor a '{host}' exige declarar los nombres con que se le llamará en PRAXEON_ALLOWED_HOSTS "
                "(p. ej. 'praxeon.midominio.com'); sin ella la validación de Host rechazaría todas las peticiones externas."
            )
        if not has_auth:
            raise ValueError(
                f"Fallo de seguridad: Vincular el servidor a la interfaz de red '{host}' sin autenticación "
                "activa está prohibido. Configure PRAXEON_API_KEY o use 127.0.0.1."
            )


def ensure_local_token(host: str) -> str | None:
    """En local sin PRAXEON_API_KEY se genera un token aleatorio por arranque (como la app de escritorio) y se devuelve para
    mostrarlo. Se desactiva con PRAXEON_ALLOW_INSECURE_DEV=1 (modo antiguo: loopback sin autenticación)."""
    if os.environ.get("PRAXEON_API_KEY", "").strip():
        return None
    if os.environ.get("PRAXEON_ALLOW_INSECURE_DEV", "").strip().lower() in ("1", "true", "yes"):
        return None
    token = secrets.token_urlsafe(32)
    os.environ["PRAXEON_API_KEY"] = token
    return token


def start():
    """Función de arranque del servidor Uvicorn vía script o CLI."""
    parser = argparse.ArgumentParser(description="PRAXEON Web Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host de escucha")
    parser.add_argument("--port", type=int, default=8000, help="Puerto HTTP")
    parser.add_argument("--reload", action="store_true", help="Recarga en caliente para desarrollo")
    args = parser.parse_args()

    validate_network_binding(args.host)
    token = ensure_local_token(args.host)
    print(f"[PRAXEON] Web Server iniciando en http://{args.host}:{args.port}")
    if token:
        print(f"[PRAXEON] Token de esta ejecución (X-API-Key / interfaz): http://{args.host}:{args.port}/#token={token}")
    print(f"[PRAXEON] Documentacion OpenAPI disponible en http://{args.host}:{args.port}/docs")
    uvicorn.run("praxeon.server.app:create_app", factory=True, host=args.host, port=args.port, reload=args.reload)


def launch_web():
    """Lanzador interactivo de la aplicación web y servidor de supervisión PRAXEON."""
    import threading
    import webbrowser

    parser = argparse.ArgumentParser(description="PRAXEON Web Supervisor & Application")
    parser.add_argument("--host", default="127.0.0.1", help="Host de escucha")
    parser.add_argument("--port", type=int, default=8000, help="Puerto HTTP")
    parser.add_argument("--no-browser", action="store_true", help="No abrir el navegador automáticamente")
    parser.add_argument("--reload", action="store_true", help="Recarga en caliente para desarrollo")
    args = parser.parse_args()

    validate_network_binding(args.host)
    token = ensure_local_token(args.host)
    url = f"http://{args.host}:{args.port}"
    open_url = f"{url}/#token={token}" if token else url      # el fragmento no viaja al servidor ni queda en sus logs
    print("=" * 68)
    print("PRAXEON 1.0 -- Runtime Supervision for Autonomous AI Agents")
    print(f"* Interfaz grafica: {url}")
    print(f"* API OpenAPI:     {url}/docs")
    print("=" * 68)

    if not args.no_browser:
        threading.Timer(1.2, webbrowser.open, [open_url]).start()

    uvicorn.run("praxeon.server.app:create_app", factory=True, host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    start()

