"""Política de control de salida de red (egress) y contención perimetral (policy/egress.py).

Garantiza la prevención de:
1. Exfiltración inadvertida hacia servidores externos no autorizados.
2. Ataques de Server-Side Request Forgery (SSRF) dirigidos a servicios internos (localhost, 127.0.0.1, 0.0.0.0).
3. Acceso a endpoints de metadatos de proveedores cloud (169.254.169.254, metadata.google.internal).
4. Evasión de proxy o apertura de sockets arbitrarios.
"""

from enum import Enum
import ipaddress
import re
from typing import List, Optional, Set, Tuple
from pydantic import BaseModel, ConfigDict, Field


class EgressMode(str, Enum):
    """Modos operativos de la política de egress."""
    BLOCK_ALL = "block_all"          # Aislamiento total de red
    ALLOWLIST = "allowlist"          # Solo destinos explícitamente autorizados
    AUDITED = "audited"              # Permite con registro estricto
    ALLOW_ALL = "allow_all"          # Sin restricciones externas


class EgressViolation(Exception):
    """Excepción lanzada cuando una acción viola la política de salida de red."""
    pass


class EgressPolicy(BaseModel):
    """Configuración formal de control de egress y fronteras de red."""
    model_config = ConfigDict(frozen=True)

    mode: EgressMode = EgressMode.BLOCK_ALL
    allowed_hosts: Set[str] = Field(default_factory=set)
    allowed_ports: Set[int] = Field(default_factory=lambda: {80, 443})
    block_cloud_metadata: bool = True
    block_localhost: bool = True
    block_private_ips: bool = True

    # Endpoints de metadatos cloud conocidos (AWS, GCP, Azure, DigitalOcean)
    CLOUD_METADATA_IPS: Set[str] = {"169.254.169.254", "metadata.google.internal"}

    # Patrones para extraer hosts o URLs de cadenas de comandos
    URL_PATTERN: re.Pattern = re.compile(
        r'(?:https?|ftp|tcp|udp)://([a-zA-Z0-9_\-\.]+)(?::([0-9]+))?',
        re.IGNORECASE,
    )
    IP_PORT_PATTERN: re.Pattern = re.compile(
        r'\b((?:[0-9]{1,3}\.){3}[0-9]{1,3})(?::([0-9]+))?\b'
    )

    def is_cloud_metadata(self, host: str) -> bool:
        """Determina si el host corresponde a una IP o nombre de metadatos cloud."""
        norm = host.lower().strip()
        return norm in self.CLOUD_METADATA_IPS or norm.startswith("169.254.")

    def is_loopback(self, host: str) -> bool:
        """Determina si el host apunta a localhost o la interfaz de bucle local."""
        norm = host.lower().strip()
        if norm in {"localhost", "localhost.localdomain", "127.0.0.1", "::1", "0.0.0.0"}:  # nosec B104
            return True
        try:
            ip = ipaddress.ip_address(norm)
            return ip.is_loopback or ip.is_unspecified
        except ValueError:
            return False

    def is_private_ip(self, host: str) -> bool:
        """Determina si una dirección IP pertenece a un rango privado RFC 1918."""
        try:
            ip = ipaddress.ip_address(host)
            return ip.is_private
        except ValueError:
            return False

    def check_destination(self, host: str, port: Optional[int] = None) -> Tuple[bool, Optional[str]]:
        """Evalúa un destino (host, puerto) contra las reglas de la política.
        
        Devuelve (allowed, reason_if_blocked).
        """
        norm_host = host.lower().strip()

        # 1. Protección de metadatos cloud
        if self.block_cloud_metadata and self.is_cloud_metadata(norm_host):
            return False, f"Acceso prohibido a endpoint de metadatos cloud: '{norm_host}'"

        # 2. Protección de bucle local (SSRF / Localhost)
        if self.block_localhost and self.is_loopback(norm_host):
            return False, f"Acceso prohibido a interfaz localhost/loopback: '{norm_host}'"

        # 3. Protección de IPs privadas si está habilitado
        if self.block_private_ips and self.is_private_ip(norm_host):
            return False, f"Acceso prohibido a dirección IP privada RFC1918: '{norm_host}'"

        # 4. Evaluación según modo operativo
        if self.mode == EgressMode.BLOCK_ALL:
            return False, f"Salida de red deshabilitada globalmente (mode={self.mode.value})"

        if self.mode == EgressMode.ALLOWLIST:
            if norm_host not in {h.lower() for h in self.allowed_hosts}:
                return False, f"Host '{norm_host}' no presente en la lista blanca de egress autorizados"

        if port is not None and self.allowed_ports:
            if port not in self.allowed_ports:
                return False, f"Puerto {port} no autorizado en la política de salida (permitidos: {self.allowed_ports})"

        return True, None

    def evaluate_command_egress(self, command: str) -> Tuple[bool, Optional[str]]:
        """Inspecciona una línea de comando en busca de llamadas o conexiones de red.
        
        Devuelve (allowed, reason_if_blocked).
        """
        cmd = command.strip()
        if not cmd:
            return True, None

        # Si el modo es BLOCK_ALL, cualquier utilidad de red conocida es rechazada
        network_binaries = {
            "curl", "wget", "nc", "netcat", "ncat", "socat", "ssh", "scp", "sftp",
            "ftp", "telnet", "ping", "tracert", "traceroute", "nslookup", "dig",
            "invoke-webrequest", "invoke-restmethod", "irm", "iwr",
        }

        tokens = cmd.replace(";", " ").replace("|", " ").replace("&", " ").split()
        for token in tokens:
            base = token.lower().replace(".exe", "").split("/")[-1].split("\\")[-1]
            if base in network_binaries:
                if self.mode == EgressMode.BLOCK_ALL:
                    return False, f"Comando de red '{base}' bloqueado por política de egress ({self.mode.value})"

        # Analizar URLs explícitas dentro del comando
        for match in self.URL_PATTERN.finditer(cmd):
            host = match.group(1)
            port_str = match.group(2)
            port = int(port_str) if port_str else None
            allowed, reason = self.check_destination(host, port)
            if not allowed:
                return False, reason

        # Analizar IPs directas
        for match in self.IP_PORT_PATTERN.finditer(cmd):
            ip_str = match.group(1)
            port_str = match.group(2)
            port = int(port_str) if port_str else None
            # Evitar falsos positivos con versiones numéricas (ej. 3.11.2)
            try:
                ipaddress.ip_address(ip_str)
                allowed, reason = self.check_destination(ip_str, port)
                if not allowed:
                    return False, reason
            except ValueError:
                pass

        return True, None
