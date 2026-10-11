"""Motor de Fundamentación Empírica (Grounding / EvidenceEngine) para JEV Reasoning Navigator v0.2.

Gestiona el almacén de evidencia empírica, evalúa precondiciones declaradas (requires_evidence),
verifica la solidez fáctica e invalida evidencias obsoletas cuando ocurren mutaciones de estado.
"""

import hashlib
import time
from typing import Any, Dict, List, Optional, Tuple
import uuid

from praxeon.domain.interfaces import EvidenceProvider
from praxeon.domain.models import ActionCandidate, Claim, Evidence


class EvidenceEngine(EvidenceProvider):
    """Almacena, indexa, verifica e invalida evidencia empírica para groundedness formal."""

    def __init__(self):
        # Mapeo de claim_key normalizado -> Evidence
        self._evidence_pool: Dict[str, Evidence] = {}
        # Mapeo de claim_id o statement -> Claim
        self._claims_pool: Dict[str, Claim] = {}
        # Historial de evidencias invalidadas por mutaciones de estado
        self.invalidation_history: List[Dict[str, Any]] = []

    def register_claim(
        self,
        statement: str,
        evidence_ids: Optional[List[str]] = None,
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

    def get_claim(self, identifier_or_statement: str) -> Optional[Claim]:
        """Obtiene un Claim por su ID o por su texto normalizado."""
        return self._claims_pool.get(identifier_or_statement.strip().lower())

    def get_all_claims(self) -> List[Claim]:
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
        source_step_id: Optional[str] = None,
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
        tool_args: Dict[str, Any],
        observation: str,
        step_id: Optional[str] = None,
    ) -> List[Evidence]:
        """Extrae e ingesta evidencias automáticas a partir de la ejecución de una herramienta."""
        added: List[Evidence] = []
        obs_lower = observation.lower()
        is_error = "error" in obs_lower or "no encontrado" in obs_lower or "no existe" in obs_lower

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

    def get_evidence(self, claim: str) -> Optional[Evidence]:
        """Obtiene la evidencia para un claim si existe."""
        return self._evidence_pool.get(claim.strip().lower())

    def check_requirements(self, required_claims: List[str]) -> Tuple[bool, List[str]]:
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
    ) -> List[Evidence]:
        """Implementación de EvidenceProvider protocol: recupera evidencias asociadas a la acción."""
        matches: List[Evidence] = []
        for req in action.requires_evidence:
            ev = self._evidence_pool.get(req.strip().lower())
            if ev:
                matches.append(ev)
        return matches

    def invalidate_resource(self, resource_path: str, reason: str = "Recurso mutado") -> List[str]:
        """Invalida evidencias previas asociadas a un recurso cuando este es modificado o eliminado."""
        norm_path = resource_path.strip().lower()
        keys_to_remove = [
            k for k in self._evidence_pool.keys()
            if norm_path in k
        ]

        invalidated_ids: List[str] = []
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

    def get_all_active_evidence(self) -> List[Evidence]:
        """Devuelve la lista de todas las evidencias activas en el pool."""
        return list(self._evidence_pool.values())

    def clear(self) -> None:
        """Limpia el almacén de evidencias."""
        self._evidence_pool.clear()
        self.invalidation_history.clear()
