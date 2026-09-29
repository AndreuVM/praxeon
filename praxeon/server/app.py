"""Punto de entrada y configuración de la aplicación FastAPI de PRAXEON 1.0 (praxeon/server/app.py)."""

import argparse
import os
from typing import List, Optional
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from praxeon.server.routes import api_router
from praxeon.server.websocket import ws_router


import logging

logger = logging.getLogger("praxeon.server.app")


def validate_security_profile(profile: Optional[str] = None) -> List[str]:
    """Valida el perfil de seguridad del runtime conforme a la Sección 8 y Sección 18 (Criterio 11)."""
    prof = (profile or os.environ.get("PRAXEON_PROFILE") or os.environ.get("PRAXEON_ENV") or "dev").lower().strip()

    if prof == "production":
        secret = os.environ.get("PRAXEON_SECRET_KEY", "")
        insecure_defaults = {"", "praxeon_secret_hmac_key_v1", "default", "secret", "change_me"}
        if secret in insecure_defaults or len(secret) < 32:
            raise ValueError(
                "Perfil de seguridad 'production' requiere que PRAXEON_SECRET_KEY esté configurada con al menos 32 caracteres y no use claves por defecto."
            )

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

        api_key = os.environ.get("PRAXEON_API_KEY", "")
        if api_key and (api_key in insecure_defaults or len(api_key) < 16):
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


def create_app(profile: Optional[str] = None) -> FastAPI:
    """Crea y configura la aplicación FastAPI con rutas y middleware."""
    origins = validate_security_profile(profile)

    app = FastAPI(
        title="PRAXEON Web Server",
        version="1.0.0",
        description="Runtime supervision for autonomous AI agents — REST API & WebSocket Streaming",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Registrar routers REST y WebSocket
    app.include_router(api_router)
    app.include_router(ws_router)

    # Servir interfaz web de supervisión si está construida o empaquetada
    static_packaged = os.path.abspath(os.path.join(os.path.dirname(__file__), "static"))
    frontend_dist = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "web", "dist"))
    target_static_dir = None
    if os.path.isdir(static_packaged) and os.path.isfile(os.path.join(static_packaged, "index.html")):
        target_static_dir = static_packaged
    elif os.path.isdir(frontend_dist) and os.path.isfile(os.path.join(frontend_dist, "index.html")):
        target_static_dir = frontend_dist

    if target_static_dir:
        from fastapi.staticfiles import StaticFiles
        app.mount("/", StaticFiles(directory=target_static_dir, html=True), name="frontend")

    return app


app = create_app()


def start():
    """Función de arranque del servidor Uvicorn vía script o CLI."""
    parser = argparse.ArgumentParser(description="PRAXEON Web Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host de escucha")
    parser.add_argument("--port", type=int, default=8000, help="Puerto HTTP")
    parser.add_argument("--reload", action="store_true", help="Recarga en caliente para desarrollo")
    args = parser.parse_args()

    print(f"[PRAXEON] Web Server iniciando en http://{args.host}:{args.port}")
    print(f"[PRAXEON] Documentacion OpenAPI disponible en http://{args.host}:{args.port}/docs")
    uvicorn.run("praxeon.server.app:app", host=args.host, port=args.port, reload=args.reload)


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

    url = f"http://{args.host}:{args.port}"
    print("=" * 68)
    print("PRAXEON 1.0 -- Runtime Supervision for Autonomous AI Agents")
    print(f"* Interfaz grafica: {url}")
    print(f"* API OpenAPI:     {url}/docs")
    print("=" * 68)

    if not args.no_browser:
        threading.Timer(1.2, webbrowser.open, [url]).start()

    uvicorn.run("praxeon.server.app:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    start()

