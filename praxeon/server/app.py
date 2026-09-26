"""Punto de entrada y configuración de la aplicación FastAPI de PRAXEON 1.0 (praxeon/server/app.py)."""

import argparse
import os
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from praxeon.server.routes import api_router
from praxeon.server.websocket import ws_router


def create_app() -> FastAPI:
    """Crea y configura la aplicación FastAPI con rutas y middleware."""
    app = FastAPI(
        title="PRAXEON Web Server",
        version="1.0.0",
        description="Runtime supervision for autonomous AI agents — REST API & WebSocket Streaming",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Configuración de CORS para permitir conexión desde la aplicación frontend (Vite/React)
    origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "*",
    ]
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

    return app


app = create_app()


def start():
    """Función de arranque del servidor Uvicorn vía script o CLI."""
    parser = argparse.ArgumentParser(description="PRAXEON Web Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host de escucha")
    parser.add_argument("--port", type=int, default=8000, help="Puerto HTTP")
    parser.add_argument("--reload", action="store_true", help="Recarga en caliente para desarrollo")
    args = parser.parse_args()

    print(f"⚡ PRAXEON Web Server iniciando en http://{args.host}:{args.port}")
    print(f"📖 Documentación OpenAPI disponible en http://{args.host}:{args.port}/docs")
    uvicorn.run("praxeon.server.app:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    start()
