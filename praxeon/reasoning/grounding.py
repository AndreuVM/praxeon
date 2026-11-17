"""Motor de Fundamentación Empírica (Grounding / EvidenceEngine) para JEV Reasoning Navigator v0.2.

Gestiona el almacén de evidencia empírica, evalúa precondiciones declaradas (requires_evidence),
verifica la solidez fáctica e invalida evidencias obsoletas cuando ocurren mutaciones de estado.
"""

import hashlib
import time
from typing import Any
import uuid

from praxeon.domain.interfaces import EvidenceProvider
from praxeon.domain.models import ActionCandidate, Claim, Evidence
from praxeon.reasoning.observations import is_failure_observation


class EvidenceEngine(EvidenceProvider):
    """Almacena, indexa, verifica e invalida evidencia empírica para groundedness formal."""

    def __init__(self) -> None:
        # Mapeo de claim_key normalizado -> Evidence
        self._evidence_pool: dict[str, Evidence] = {}
        # Mapeo de claim_id o statement -> Claim
        self._claims_pool: dict[str, Claim] = {}
        # Historial de evidencias invalidadas por mutaciones de estado
        self.invalidation_history: list[dict[str, Any]] = []

    def register_claim(
        self,
        statement: str,
        evidence_ids: list[str] | None = None,
        confidence: float = 1.0,
    ) -> Claim:
        """Registra explícitamente un aserto o Claim respaldado por evidencias."""
        clean_statement = statement.strip()
        claim_id = f"cl_{uuid.uuid4().hex[:8]}"
        claim = Claim(
            id=claim_id,
            statement=clean_statement,
            confidence=confidence,
            evidence_ids=evidence_ids or [],
        )
        self._claims_pool[claim_id] = claim
        self._claims_pool[clean_statement.lower()] = claim
        return claim

    def get_claim(self, identifier_or_statement: str) -> Claim | None:
        """Obtiene un Claim por su ID o por su texto normalizado."""
        return self._claims_pool.get(identifier_or_statement.strip().lower())

    def get_all_claims(self) -> list[Claim]:
        """Devuelve todos los asertos únicos registrados."""
        seen = set()
        unique = []
        for c in self._claims_pool.values():
            if c.id not in seen:
                seen.add(c.id)
                unique.append(c)
        return unique

    def ingest(
        self,
        claim: str,
        source_type: str = "tool_observation",
        source_step_id: str | None = None,
        confidence: float = 1.0,
        content: str = "",
    ) -> Evidence:
        """Registra una nueva pieza de evidencia empírica validada y su aserto correspondiente."""
        normalized_claim = claim.strip()
        key = normalized_claim.lower()

        content_hash = (
            hashlib.sha256(content.encode("utf-8")).hexdigest()
            if content
            else hashlib.sha256(normalized_claim.encode("utf-8")).hexdigest()
        )

        ev_id = f"ev_{uuid.uuid4().hex[:8]}"
        assoc_claim = self.register_claim(
            statement=normalized_claim,
            evidence_ids=[ev_id],
            confidence=confidence,
        )

        evidence = Evidence(
            id=ev_id,
            source_step_id=source_step_id,
            source_type=source_type,  # type: ignore
            claim=normalized_claim,
            content_hash=content_hash,
            confidence=confidence,
            claims=[assoc_claim],
        )
        self._evidence_pool[key] = evidence
        return evidence

    def ingest_from_observation(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        observation: str,
        step_id: str | None = None,
    ) -> list[Evidence]:
        """Extrae e ingesta evidencias automáticas a partir de la ejecución de una herramienta."""
        added: list[Evidence] = []
        # Solo un MENSAJE de fallo cuenta como error: leer un documento que dice «error» o «no existe» es una lectura correcta y es evidencia
        is_error = is_failure_observation(observation)

        if tool_name in ("read_file", "view_file") and not is_error:
            path = str(tool_args.get("path") or tool_args.get("AbsolutePath") or tool_args.get("file_path") or "").strip()
            if path:
                ev1 = self.ingest(
                    claim=f"file_exists:{path}",
                    source_step_id=step_id,
                    content=observation,
                )
                ev2 = self.ingest(
                    claim=f"file_read:{path}",
                    source_step_id=step_id,
                    content=observation,
                )
                added.extend([ev1, ev2])

        elif tool_name in ("edit_file", "write_to_file", "replace_file_content") and not is_error:
            path = str(tool_args.get("path") or tool_args.get("TargetFile") or tool_args.get("file_path") or "").strip()
            if path:
                # La edición muta el archivo: invalidar lecturas previas
                self.invalidate_resource(path, reason=f"Archivo '{path}' mutado por {tool_name}")
                ev = self.ingest(
                    claim=f"file_modified:{path}",
                    source_step_id=step_id,
                    content=observation,
                )
                added.append(ev)

        elif tool_name in ("delete_file", "remove_file") and not is_error:
            path = str(tool_args.get("path") or tool_args.get("TargetFile") or "").strip()
            if path:
                # El borrado invalida que el archivo exista
                self.invalidate_resource(path, reason=f"Archivo '{path}' eliminado por {tool_name}")
                ev = self.ingest(
                    claim=f"file_deleted:{path}",
                    source_step_id=step_id,
                    content=observation,
                )
                added.append(ev)

        return added

    def has_evidence(self, claim: str) -> bool:
        """Verifica si existe evidencia activa para un claim específico."""
        return claim.strip().lower() in self._evidence_pool

    def get_evidence(self, claim: str) -> Evidence | None:
        """Obtiene la evidencia para un claim si existe."""
        return self._evidence_pool.get(claim.strip().lower())

    def check_requirements(self, required_claims: list[str]) -> tuple[bool, list[str]]:
        """Verifica si se cumplen todas las precondiciones requeridas por una acción."""
        missing = [
            req for req in required_claims
            if req.strip().lower() not in self._evidence_pool
        ]
        return len(missing) == 0, missing

    def assess(
        self,
        state: Any,
        action: ActionCandidate,
    ) -> list[Evidence]:
        """Implementación de EvidenceProvider protocol: recupera evidencias asociadas a la acción."""
        matches: list[Evidence] = []
        for req in action.requires_evidence:
            ev = self._evidence_pool.get(req.strip().lower())
            if ev:
                matches.append(ev)
        return matches

    def invalidate_resource(self, resource_path: str, reason: str = "Recurso mutado") -> list[str]:
        """Invalida evidencias previas asociadas a un recurso cuando este es modificado o eliminado."""
        norm_path = resource_path.strip().lower()
        keys_to_remove = [
            k for k in self._evidence_pool.keys()
            if norm_path in k
        ]

        invalidated_ids: list[str] = []
        for k in keys_to_remove:
            ev = self._evidence_pool.pop(k)
            invalidated_ids.append(ev.id)
            self.invalidation_history.append({
                "timestamp": time.time(),
                "evidence_id": ev.id,
                "claim": ev.claim,
                "reason": reason,
            })

        return invalidated_ids

    def get_all_active_evidence(self) -> list[Evidence]:
        """Devuelve la lista de todas las evidencias activas en el pool."""
        return list(self._evidence_pool.values())

    def clear(self) -> None:
        """Limpia el almacén de evidencias."""
        self._evidence_pool.clear()
        self.invalidation_history.clear()
