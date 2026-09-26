import React from 'react';
import { Shield, ShieldCheck, Key, Lock, Network, AlertTriangle, FileCode } from 'lucide-react';

export default function SecurityView() {
  const securityStats = [
    { label: 'Capabilities Emitidas', value: '8', icon: Key, color: '#f0f6fc' },
    { label: 'Consumos Únicos (Consume-once)', value: '8 / 8 (100%)', icon: Lock, color: '#3fb950' },
    { label: 'Intentos de Replay Bloqueados', value: '0', icon: ShieldCheck, color: '#3fb950' },
    { label: 'Egress Bloqueados (SSRF / Cloud)', value: '3', icon: Network, color: '#f85149' },
  ];

  const policies = [
    {
      name: 'EgressNetworkBarrier',
      status: 'Enforced',
      detail: 'Bloquea intentos de conexión outbound no autorizados e intercepta consultas a metadatos cloud (169.254.169.254).',
    },
    {
      name: 'PathTraversal & Symlink Containment',
      status: 'Enforced',
      detail: 'Resolución canónica mediante os.path.realpath. Anula escapes a directorios del sistema fuera del workspace.',
    },
    {
      name: 'Consume-Once Nonce Store',
      status: 'Enforced',
      detail: 'Almacenamiento atómico de nonces con TTL de 300 segundos y borrado tras consumo. Evita repetición de ejecuciones.',
    },
    {
      name: 'HMAC-SHA256 Cryptographic Receipts',
      status: 'Enforced',
      detail: 'Cada autorización emitida por el PolicyEngine va firmada y acoplada estrictamente con el action_hash y state_hash.',
    },
  ];

  return (
    <div style={{
      flex: 1,
      padding: '24px 32px',
      overflowY: 'auto',
      backgroundColor: '#0a0e16',
      display: 'flex',
      flexDirection: 'column',
      gap: '20px',
    }}>
      <div>
        <h1 style={{ fontSize: '18px', fontWeight: '700', color: '#f8fafc' }}>
          Seguridad, Capabilities y Aislamiento
        </h1>
        <p style={{ fontSize: '12px', color: '#73849c', marginTop: '4px' }}>
          Postura criptográfica, consumo único de tokens, defensas contra replay y barreras de contención.
        </p>
      </div>

      {/* KPI Cards */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
        gap: '14px',
      }}>
        {securityStats.map((st, idx) => {
          const Icon = st.icon;
          return (
            <div
              key={idx}
              style={{
                backgroundColor: '#121926',
                borderRadius: '8px',
                border: '1px solid #1e2a3c',
                padding: '16px',
                display: 'flex',
                alignItems: 'center',
                gap: '14px',
              }}
            >
              <div style={{
                width: '36px',
                height: '36px',
                borderRadius: '6px',
                backgroundColor: '#182436',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: st.color,
              }}>
                <Icon size={18} />
              </div>
              <div>
                <div style={{ fontSize: '16px', fontWeight: '700', color: '#f8fafc' }}>
                  {st.value}
                </div>
                <div style={{ fontSize: '11px', color: '#73849c', marginTop: '2px' }}>
                  {st.label}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Security Policies List */}
      <div style={{
        backgroundColor: '#121926',
        borderRadius: '8px',
        border: '1px solid #1e2a3c',
        padding: '20px',
      }}>
        <h3 style={{ fontSize: '14px', fontWeight: '700', color: '#f8fafc', marginBottom: '14px' }}>
          Barreras de Seguridad Activas
        </h3>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
          {policies.map((pol, idx) => (
            <div
              key={idx}
              style={{
                padding: '12px 16px',
                borderRadius: '6px',
                backgroundColor: '#0c111a',
                border: '1px solid #182232',
                display: 'flex',
                alignItems: 'flex-start',
                justifyContent: 'space-between',
                gap: '16px',
              }}
            >
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <ShieldCheck size={14} style={{ color: '#34d399' }} />
                  <span style={{ fontSize: '13px', fontWeight: '600', color: '#e2e8f0' }}>
                    {pol.name}
                  </span>
                </div>
                <p style={{ fontSize: '11.5px', color: '#8595a8', marginTop: '4px', lineHeight: '1.4' }}>
                  {pol.detail}
                </p>
              </div>
              <span className="badge badge-success">{pol.status}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
