"""Registro Centralizado y Gestión de Ciclo de Vida de Agentes (AgentRegistry) (F4-03).

Implementa las operaciones completas del ciclo de vida de agentes en PRAXEON:
- CRUD tipado: register, get, list_all, update, delete (soft/hard delete).
- Control de estados: activate, deactivate, duplicate.
- Versionado determinista e incremento automático de versión al actualizar.
- Portabilidad: exportación e importación en formatos JSON y YAML.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional
import uuid
import yaml

from praxeon.agents.definition import AgentDefinition, AgentStatus

if TYPE_CHECKING:
    from praxeon.persistence.sqlite_store import SqlitePersistenceStore


class AgentRegistry:
    """Registro y gestor de ciclo de vida de agentes en memoria con persistencia transaccional SQLite o en disco."""

    def __init__(
        self,
        storage_dir: Optional[str] = None,
        store: Optional["SqlitePersistenceStore"] = None,
        db_path: Optional[str] = None,
    ):
        self._agents: Dict[str, AgentDefinition] = {}
        self._quarantined: List[Dict[str, Any]] = []
        self._version_history: Dict[str, List[Dict[str, Any]]] = {}
        self._in_memory_versions: Dict[str, Dict[int, AgentDefinition]] = {}
        self.storage_dir = Path(storage_dir) if storage_dir else None
        if store is not None:
            self.store: Optional["SqlitePersistenceStore"] = store
        elif db_path is not None:
            from praxeon.persistence.sqlite_store import SqlitePersistenceStore
            self.store = SqlitePersistenceStore(db_path=db_path)
        else:
            self.store = None

        if self.store is not None:
            self._load_from_store()
        elif self.storage_dir:
            self.storage_dir.mkdir(parents=True, exist_ok=True)
            self._load_from_storage()

    def _record_version_in_memory(self, agent: AgentDefinition) -> None:
        """Registra una versión de agente en la memoria interna para trazabilidad inmutable."""
        aid = agent.agent_id
        if aid not in self._version_history:
            self._version_history[aid] = []
        if aid not in self._in_memory_versions:
            self._in_memory_versions[aid] = {}

        self._in_memory_versions[aid][agent.version] = agent
        if not any(v.get("version") == agent.version for v in self._version_history[aid]):
            created_str = (
                agent.created_at.isoformat()
                if hasattr(agent.created_at, "isoformat")
                else str(agent.created_at)
            )
            self._version_history[aid].append({
                "agent_id": aid,
                "version": agent.version,
                "definition_hash": agent.definition_hash,
                "created_at": created_str,
            })

    def register(self, agent: AgentDefinition) -> AgentDefinition:
        """Registra un nuevo agente en el registry."""
        if agent.agent_id in self._agents:
            raise ValueError(f"El agente con ID '{agent.agent_id}' ya está registrado. Usa update() para modificarlo.")

        self._agents[agent.agent_id] = agent
        self._record_version_in_memory(agent)
        self._persist_if_configured(agent)
        return agent

    def get(self, agent_id: str) -> Optional[AgentDefinition]:
        """Obtiene un agente por su identificador único."""
        return self._agents.get(agent_id)

    def list_all(
        self,
        status: Optional[AgentStatus] = None,
        role: Optional[str] = None,
    ) -> List[AgentDefinition]:
        """Lista todos los agentes registrados aplicando filtros opcionales de estado o rol."""
        results = list(self._agents.values())
        if status is not None:
            results = [a for a in results if a.status == status]
        if role is not None:
            results = [a for a in results if a.role.lower() == role.lower()]
        return results

    def update(self, agent_id: str, updates: Dict[str, Any]) -> AgentDefinition:
        """Actualiza campos de un agente existente, incrementando su versión automáticamente."""
        current = self.get(agent_id)
        if not current:
            raise KeyError(f"Agente con ID '{agent_id}' no encontrado.")

        data = current.model_dump()
        for k, v in updates.items():
            if k in ("agent_id", "created_at", "definition_hash"):
                continue  # Identidad original protegida
            data[k] = v

        data["version"] = current.version + 1
        data["updated_at"] = datetime.now(timezone.utc)

        updated_agent = AgentDefinition(**data)
        self._agents[agent_id] = updated_agent
        self._record_version_in_memory(updated_agent)
        self._persist_if_configured(updated_agent)
        return updated_agent


    def delete(self, agent_id: str, hard_delete: bool = False) -> bool:
        """Elimina un agente. Por defecto aplica soft-delete marcándolo como TERMINATED."""
        if agent_id not in self._agents:
            return False

        if hard_delete:
            del self._agents[agent_id]
            if self.store:
                self.store.delete_agent(agent_id, hard_delete=True)
            if self.storage_dir:
                file_path = self.storage_dir / f"{agent_id}.json"
                if file_path.exists():
                    file_path.unlink()
            return True
        else:
            self.update(agent_id, {"status": AgentStatus.TERMINATED})
            return True

    def activate(self, agent_id: str) -> AgentDefinition:
        """Pone un agente en estado ACTIVE."""
        return self.update(agent_id, {"status": AgentStatus.ACTIVE})

    def deactivate(self, agent_id: str) -> AgentDefinition:
        """Pone un agente en estado INACTIVE."""
        return self.update(agent_id, {"status": AgentStatus.INACTIVE})

    def duplicate(
        self,
        source_agent_id: str,
        new_agent_id: str,
        new_name: Optional[str] = None,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> AgentDefinition:
        """Duplica un agente existente asignándole un nuevo ID y registrándolo."""
        source = self.get(source_agent_id)
        if not source:
            raise KeyError(f"Agente origen con ID '{source_agent_id}' no encontrado.")

        cloned = source.clone(new_agent_id=new_agent_id, new_name=new_name, overrides=overrides)
        return self.register(cloned)

    def export_to_json(self, agent_id: str, indent: int = 2) -> str:
        """Exporta la definición de un agente en formato JSON."""
        agent = self.get(agent_id)
        if not agent:
            raise KeyError(f"Agente con ID '{agent_id}' no encontrado.")
        return json.dumps(agent.to_dict(), indent=indent, ensure_ascii=False)

    def import_from_json(self, json_str: str) -> AgentDefinition:
        """Importa y registra un agente a partir de una cadena JSON."""
        data = json.loads(json_str)
        agent = AgentDefinition.from_dict(data)
        return self.register(agent)

    def export_to_yaml(self, agent_id: str) -> str:
        """Exporta la definición de un agente en formato YAML portable."""
        agent = self.get(agent_id)
        if not agent:
            raise KeyError(f"Agente con ID '{agent_id}' no encontrado.")
        return yaml.dump(agent.to_dict(), sort_keys=False, allow_unicode=True)

    def import_from_yaml(self, yaml_str: str) -> AgentDefinition:
        """Importa y registra un agente a partir de una cadena YAML."""
        data = yaml.safe_load(yaml_str)
        agent = AgentDefinition.from_dict(data)
        return self.register(agent)

    def export_bundle(self, filepath: str) -> None:
        """Exporta todos los agentes activos a un archivo JSON o YAML."""
        bundle = [a.to_dict() for a in self._agents.values()]
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        if filepath.endswith((".yaml", ".yml")):
            with open(path, "w", encoding="utf-8") as f:
                yaml.dump(bundle, f, sort_keys=False, allow_unicode=True)
        else:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(bundle, f, indent=2, ensure_ascii=False)

    def import_bundle(self, filepath: str) -> int:
        """Importa un conjunto de agentes desde un archivo JSON o YAML."""
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"Archivo de bundle '{filepath}' no existe.")

        with open(path, "r", encoding="utf-8") as f:
            if filepath.endswith((".yaml", ".yml")):
                items = yaml.safe_load(f)
            else:
                items = json.load(f)

        imported = 0
        for item in items:
            agent = AgentDefinition.from_dict(item)
            if agent.agent_id in self._agents:
                self.update(agent.agent_id, item)
            else:
                self.register(agent)
            imported += 1
        return imported

    def _persist_if_configured(self, agent: AgentDefinition) -> None:
        """Guarda en almacén SQLite o en disco si están configurados."""
        if self.store:
            self.store.save_agent(agent)
        if self.storage_dir:
            dest = self.storage_dir / f"{agent.agent_id}.json"
            with open(dest, "w", encoding="utf-8") as f:
                json.dump(agent.to_dict(), f, indent=2, ensure_ascii=False)

    def _load_from_store(self) -> None:
        """Carga agentes desde el almacén transaccional SQLite."""
        if not self.store:
            return
        loaded = self.store.list_agents()
        for agent in loaded:
            self._agents[agent.agent_id] = agent
        self._quarantined = self.store.list_quarantined(entity_type="agent")

    def _load_from_storage(self) -> None:
        """Carga agentes preexistentes del directorio de almacenamiento capturando registros corruptos en cuarentena."""
        if not self.storage_dir:
            return
        for file in self.storage_dir.glob("*.json"):
            raw_content = ""
            try:
                raw_content = file.read_text(encoding="utf-8")
                data = json.loads(raw_content)
                agent = AgentDefinition.from_dict(data)
                self._agents[agent.agent_id] = agent
            except Exception as exc:
                entry = {
                    "record_id": f"quarantine_{file.stem}_{uuid.uuid4().hex[:8]}",
                    "entity_type": "agent",
                    "entity_id": file.stem,
                    "raw_content": raw_content,
                    "error_message": str(exc),
                    "quarantined_at": datetime.now(timezone.utc).isoformat(),
                }
                self._quarantined.append(entry)

    def get_quarantined_agents(self) -> List[Dict[str, Any]]:
        """Lista los agentes que se encuentran en estado de cuarentena por errores de deserialización o integridad."""
        if self.store:
            return self.store.list_quarantined(entity_type="agent")
        return list(self._quarantined)

    def quarantine_agent(self, agent_id: str, reason: str) -> None:
        """Coloca un agente existente o corrupto en cuarentena explícita."""
        agent = self.get(agent_id)
        raw_content = ""
        if agent:
            raw_content = json.dumps(agent.to_dict(), ensure_ascii=False)
            self.update(agent_id, {"status": AgentStatus.QUARANTINED})
        if self.store:
            self.store.quarantine_record("agent", agent_id, raw_content, reason)
        else:
            self._quarantined.append({
                "record_id": f"quarantine_{agent_id}_{uuid.uuid4().hex[:8]}",
                "entity_type": "agent",
                "entity_id": agent_id,
                "raw_content": raw_content,
                "error_message": reason,
                "quarantined_at": datetime.now(timezone.utc).isoformat(),
            })

    def get_agent_history(self, agent_id: str) -> List[Dict[str, Any]]:
        """Obtiene el historial inmutable de versiones de un agente con sus hashes criptográficos."""
        if self.store:
            return self.store.get_agent_versions(agent_id)
        if agent_id in self._version_history:
            return list(self._version_history[agent_id])
        agent = self.get(agent_id)
        if not agent:
            return []
        return [{
            "agent_id": agent.agent_id,
            "version": agent.version,
            "definition_hash": agent.definition_hash,
            "created_at": agent.created_at.isoformat() if hasattr(agent.created_at, "isoformat") else str(agent.created_at),
        }]

    def get_agent_version(self, agent_id: str, version: int) -> Optional[AgentDefinition]:
        """Obtiene una versión histórica inmutable específica de un agente."""
        if self.store:
            return self.store.get_agent_version(agent_id, version)
        if agent_id in self._in_memory_versions:
            return self._in_memory_versions[agent_id].get(version)
        agent = self.get(agent_id)
        if agent and agent.version == version:
            return agent
        return None

