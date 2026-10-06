"""Pruebas unitarias para el orquestador unificado de arranque run_praxeon.py (DEUDA-OPS-01).

Valida:
1. Detección de puertos libres y ocupados (is_port_in_use y find_available_port).
2. Configuración y validación del entorno de seguridad (configure_security_environment).
3. Construcción y validación del analizador de argumentos de línea de comandos (build_parser).
4. Verificación de puertos vía flag --check-ports.
"""

import socket
import sys
import pytest
from unittest.mock import patch

from run_praxeon import (
    build_parser,
    configure_security_environment,
    find_available_port,
    is_port_in_use,
    main,
)


def test_is_port_in_use_detects_bound_and_unbound_ports():
    """Valida que is_port_in_use detecta correctamente sockets activos y puertos libres."""
    # 1. Crear socket temporal escuchando en localhost
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.bind(("127.0.0.1", 0))
    server_sock.listen(1)
    bound_port = server_sock.getsockname()[1]

    try:
        assert is_port_in_use("127.0.0.1", bound_port) is True
    finally:
        server_sock.close()

    # Tras cerrar el socket, el puerto queda libre
    assert is_port_in_use("127.0.0.1", bound_port) is False


def test_find_available_port():
    """Valida que find_available_port retorna un puerto utilizable."""
    free_port = find_available_port("127.0.0.1", start_port=18000, max_attempts=10)
    assert 18000 <= free_port < 18010
    assert is_port_in_use("127.0.0.1", free_port) is False


def test_configure_security_environment(monkeypatch):
    """Valida la configuración del perfil de seguridad en variables de entorno."""
    monkeypatch.delenv("PRAXEON_PROFILE", raising=False)
    prof = configure_security_environment("test")
    assert prof == "test"

    monkeypatch.setenv("PRAXEON_PROFILE", "dev")
    prof2 = configure_security_environment(None)
    assert prof2 == "dev"


def test_build_parser_options():
    """Valida que todas las opciones canónicas estén disponibles en el parser."""
    parser = build_parser()

    args = parser.parse_args(["--mode", "api", "--port", "9000", "--host", "0.0.0.0", "--auto-port", "--reload"])
    assert args.mode == "api"
    assert args.port == 9000
    assert args.host == "0.0.0.0"
    assert args.auto_port is True
    assert args.reload is True

    args_web = parser.parse_args(["-m", "web", "--no-browser"])
    assert args_web.mode == "web"
    assert args_web.no_browser is True


def test_main_check_ports_exits_cleanly_on_free_port(capsys):
    """Valida que --check-ports informe el estado y termine con código 0 si está libre."""
    free_port = find_available_port("127.0.0.1", start_port=29000)
    test_args = ["run_praxeon.py", "--check-ports", "--port", str(free_port)]

    with patch.object(sys, "argv", test_args):
        with pytest.raises(SystemExit) as excinfo:
            main()
        assert excinfo.value.code == 0

    captured = capsys.readouterr()
    assert "[LIBRE]" in captured.out
