"""``praxeon sandbox status | revoke``: qué aislamiento hay y retirada de los permisos concedidos (AUD-19)."""

from __future__ import annotations

import sys

from praxeon.runtime import isolation


def status_lines() -> list[str]:
    st = isolation.refresh()
    lines = [
        f"Aislamiento pedido: {st.requested} (PRAXEON_ISOLATION)",
        f"Backend: {st.backend or 'ninguno'} — {st.reason}",
        "Sesiones sin modo explícito: " + ("aisladas" if st.available else "sandbox local (sin aislamiento fuerte)"),
    ]
    if sys.platform == "win32":
        from praxeon.runtime.isolation.appcontainer import granted_containers

        grants = granted_containers()
        lines.append(f"AppContainer con permisos concedidos: {len(grants)}")
        for name, entry in sorted(grants.items()):
            lines.append(f"  {name}: {len(entry.get('paths', []))} carpetas")
    return lines


def revoke_lines() -> list[str]:
    if sys.platform == "win32":
        from praxeon.runtime.isolation.appcontainer import revoke_all

        revoked = revoke_all()
        return [f"Permisos retirados en {len(revoked)} carpetas; perfiles de AppContainer de PRAXEON borrados."]
    return ["Nada que revocar: solo el backend AppContainer (Windows) concede permisos sobre carpetas."]


def main(action: str) -> int:
    lines = revoke_lines() if action == "revoke" else status_lines()
    print("\n".join(lines))
    return 0
