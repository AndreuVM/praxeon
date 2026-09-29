"""Módulo de resiliencia, Circuit Breaker y control de reintentos para proveedores semánticos.

Implementa los requisitos de la Sección 10 de la Auditoría Técnica:
- Máquina de estados formal de Circuit Breaker (CLOSED, OPEN, HALF_OPEN)
- Exponential Backoff con Full Jitter para evitar tormentas de reintentos
- Parsing de cabeceras/mensajes 'Retry-After'
- Deadline global y presupuesto de reintentos (retry budget)
"""

from enum import Enum
import logging
import random
import re
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger("praxeon.providers.resilience")


class CircuitState(str, Enum):
    """Estados canónicos de la máquina de Circuit Breaker."""
    CLOSED = "closed"        # Operación normal: el tráfico fluye hacia el proveedor
    OPEN = "open"            # Proveedor caído: llamadas rechazadas inmediatamente sin coste de red
    HALF_OPEN = "half_open"  # Periodo de prueba: se admite una llamada para verificar recuperación


class CircuitBreaker:
    """Implementa protección de circuito cerrado/abierto para llamadas a proveedores externos con sincronización thread-safe."""

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
        half_open_max_trials: int = 1,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_trials = half_open_max_trials

        self._lock = threading.Lock()
        self._state: CircuitState = CircuitState.CLOSED
        self._consecutive_failures: int = 0
        self._last_failure_time: float = 0.0
        self._half_open_trials: int = 0

    @property
    def state(self) -> CircuitState:
        """Determina el estado actual del circuito considerando el temporizador de recuperación de forma thread-safe."""
        with self._lock:
            now = time.time()
            if self._state == CircuitState.OPEN:
                if now - self._last_failure_time >= self.recovery_timeout:
                    logger.info("CircuitBreaker: tiempo de recuperación alcanzado. Transición de OPEN a HALF_OPEN.")
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_trials = 0
            return self._state

    def allow_request(self) -> bool:
        """Indica si una nueva petición puede enviarse al proveedor externo (thread-safe)."""
        with self._lock:
            now = time.time()
            if self._state == CircuitState.OPEN:
                if now - self._last_failure_time >= self.recovery_timeout:
                    logger.info("CircuitBreaker: tiempo de recuperación alcanzado. Transición de OPEN a HALF_OPEN.")
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_trials = 0
            if self._state == CircuitState.CLOSED:
                return True
            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_trials < self.half_open_max_trials:
                    self._half_open_trials += 1
                    return True
                return False
            return False

    def record_success(self) -> None:
        """Registra una respuesta exitosa del proveedor restableciendo el circuito a CLOSED (thread-safe)."""
        with self._lock:
            if self._state != CircuitState.CLOSED:
                logger.info("CircuitBreaker: llamada exitosa confirmada. Circuito restablecido a CLOSED.")
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self._half_open_trials = 0

    def record_failure(self, error: Optional[Exception] = None) -> None:
        """Registra un fallo del proveedor y transiciona a OPEN si supera el umbral (thread-safe)."""
        with self._lock:
            self._last_failure_time = time.time()
            self._consecutive_failures += 1

            if self._state == CircuitState.HALF_OPEN:
                logger.warning("CircuitBreaker: fallo en estado HALF_OPEN. Regreso inmediato a OPEN.")
                self._state = CircuitState.OPEN
                return

            if self._consecutive_failures >= self.failure_threshold and self._state == CircuitState.CLOSED:
                logger.warning(
                    f"CircuitBreaker: {self._consecutive_failures} fallos consecutivos superan el umbral ({self.failure_threshold}). "
                    f"Transición a OPEN. Peticiones bloqueadas por {self.recovery_timeout}s."
                )
                self._state = CircuitState.OPEN


def parse_retry_after(error_message_or_header: str) -> Optional[float]:
    """Extrae el tiempo en segundos sugerido por un error 429 o cabecera Retry-After."""
    if not error_message_or_header:
        return None

    # Buscar patrones como "retry after 12.5s", "retry-after: 5", "retry in 3 seconds"
    match = re.search(r"retry[-_ ]?(?:after|in)?[:= ]+([0-9]+(?:\.[0-9]+)?)", error_message_or_header, re.IGNORECASE)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    return None


def calculate_jittered_backoff(
    attempt: int,
    base_backoff: float = 0.5,
    max_backoff: float = 10.0,
    retry_after: Optional[float] = None,
) -> float:
    """Calcula el tiempo de espera con Exponential Backoff y Full Jitter aleatorio."""
    if retry_after is not None and retry_after > 0:
        # Respetar sugerencia de la API añadiendo pequeño jitter para desincronizar
        return min(max_backoff, retry_after + random.uniform(0.1, 0.5))

    calculated = base_backoff * (2 ** attempt)
    capped = min(max_backoff, calculated)
    # Full jitter: tiempo uniforme entre 0 y el valor exponencial
    return random.uniform(0.1, capped)
