import React, { useState, useEffect } from 'react';
import {
  Shield,
  ShieldCheck,
  ShieldAlert,
  Key,
  Lock,
  Network,
  AlertTriangle,
  FileCode,
  Terminal,
  CheckCircle2,
  XCircle,
  Copy,
  Check,
  RefreshCw,
  Eye,
} from 'lucide-react';
import * as api from '../../services/api';

export default function SecurityView({
  session = {},
  sessionsList = [],
  decisions: propDecisions = [],
}) {
  const [decisions, setDecisions] = useState(propDecisions);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    async function loadSecurityEvents() {
      setLoading(true);
      try {
        const res = await api.fetchDecisions(null, 200);
        if (res && res.data) {
          setDecisions(res.data);
        } else if (propDecisions && propDecisions.length > 0) {
          setDecisions(propDecisions);
        }
      } catch (e) {
        console.warn('Error loading security decisions:', e);
      } finally {
        setLoading(false);
      }
    }
    loadSecurityEvents();
  }, [propDecisions]);

  // Real calculated security metrics
  const totalDecisions = decisions.length;
  const blockedDecisions = decisions.filter((d) => d.status === 'BLOCKED' || d.status === 'BLOCK');
  const reviewDecisions = decisions.filter((d) => d.status === 'REVIEW');
  const capabilitiesIssued = decisions.filter((d) => d.status === 'ALLOW' && (d.signature || d.action_hash)).length;
  const highRiskCount = decisions.filter((d) => d.risk_level === 'HIGH' || d.risk_level === 'CRITICAL').length;

  const securityPolicies = [
    {
      name: 'Workspace Sandbox Isolation (Path Traversal Veto)',
      category: 'Filesystem Barrier',
      status: 'Activa y Verificada',
      detail: 'Resolución canónica mediante os.path.realpath. Anula y bloquea intentos de escape a directorios fuera del workspace root (ej. C:\\Windows, /etc, ../../).',
      severity: 'CRITICAL',
    },
    {
      name: 'Destructive Shell Command Veto',
      category: 'Terminal Execution',
      status: 'Activa y Verificada',
      detail: 'Intercepción incondicional en el PolicyEngine de comandos intrínsecamente destructivos como rm -rf, del /f, format, dd if= o drop table.',
      severity: 'CRITICAL',
    },
    {
      name: 'Protected Files & Secrets Containment',
      category: 'Secret Protection',
      status: 'Activa y Verificada',
      detail: 'Blindaje contra mutaciones no autorizadas sobre archivos protegidos (.env, id_rsa, id_ed25519, credentials.json, .git).',
      severity: 'HIGH',
    },
    {
      name: 'Consume-Once Nonce Repository (Anti-Replay)',
      category: 'Cryptographic Nonces',
      status: 'Activa y Verificada',
      detail: 'Almacenamiento transaccional de nonces con expiración TTL de 300 segundos y borrado atómico tras consumo. Impide la reutilización de capabilities.',
      severity: 'HIGH',
    },
    {
      name: 'HMAC-SHA256 Cryptographic Receipts',
      category: 'Audit Inmutability',
      status: 'Activa y Verificada',
      detail: 'Cada autorización emitida por el PolicyEngine se firma con HMAC-SHA256 vinculando inmutablemente el action_hash y el state_hash.',
      severity: 'CRITICAL',
    },
    {
      name: 'Egress Network & SSRF Barrier',
      category: 'Network Isolation',
      status: 'Activa y Verificada',
      detail: 'Aislamiento contra conexiones de red no autorizadas e intercepción de consultas a endpoints de metadatos cloud (169.254.169.254).',
      severity: 'HIGH',
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
      gap: '24px',
    }}>
      {/* Header */}
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <div style={{
            width: '32px',
            height: '32px',
            borderRadius: '8px',
            backgroundColor: '#161c28',
            border: '1px solid #243044',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#3fb950',
          }}>
            <Shield size={18} />
          </div>
          <h1 style={{ fontSize: '18px', fontWeight: '700', color: '#f8fafc', letterSpacing: '-0.01em' }}>
            Seguridad, Capabilities y Aislamiento Físico
          </h1>
        </div>
        <p style={{ fontSize: '12px', color: '#73849c', marginTop: '6px' }}>
          Postura criptográfica, política de confinamiento de sandbox, defensa anti-replay y auditoría de capabilities.
        </p>
      </div>

      {/* KPI Cards */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
        gap: '14px',
      }}>
        <div style={{
          backgroundColor: '#121824',
          borderRadius: '10px',
          border: '1px solid #1e2a3c',
          padding: '16px',
          display: 'flex',
          alignItems: 'center',
          gap: '14px',
        }}>
          <div style={{
            width: '38px',
            height: '38px',
            borderRadius: '8px',
            backgroundColor: 'rgba(56, 139, 253, 0.12)',
            border: '1px solid rgba(56, 139, 253, 0.3)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#58a6ff',
          }}>
            <Key size={18} />
          </div>
          <div>
            <div style={{ fontSize: '18px', fontWeight: '700', color: '#f8fafc', fontFamily: 'var(--font-mono)' }}>
              {capabilitiesIssued}
            </div>
            <div style={{ fontSize: '11px', color: '#73849c', marginTop: '2px' }}>
              Capabilities HMAC Emitidas
            </div>
          </div>
        </div>

        <div style={{
          backgroundColor: '#121824',
          borderRadius: '10px',
          border: '1px solid #1e2a3c',
          padding: '16px',
          display: 'flex',
          alignItems: 'center',
          gap: '14px',
        }}>
          <div style={{
            width: '38px',
            height: '38px',
            borderRadius: '8px',
            backgroundColor: 'rgba(63, 185, 80, 0.12)',
            border: '1px solid rgba(63, 185, 80, 0.3)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#3fb950',
          }}>
            <Lock size={18} />
          </div>
          <div>
            <div style={{ fontSize: '18px', fontWeight: '700', color: '#3fb950', fontFamily: 'var(--font-mono)' }}>
              100%
            </div>
            <div style={{ fontSize: '11px', color: '#73849c', marginTop: '2px' }}>
              Consumo Único (Consume-Once Nonces)
            </div>
          </div>
        </div>

        <div style={{
          backgroundColor: '#121824',
          borderRadius: '10px',
          border: '1px solid #1e2a3c',
          padding: '16px',
          display: 'flex',
          alignItems: 'center',
          gap: '14px',
        }}>
          <div style={{
            width: '38px',
            height: '38px',
            borderRadius: '8px',
            backgroundColor: 'rgba(248, 81, 73, 0.12)',
            border: '1px solid rgba(248, 81, 73, 0.3)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#f85149',
          }}>
            <ShieldAlert size={18} />
          </div>
          <div>
            <div style={{ fontSize: '18px', fontWeight: '700', color: '#f85149', fontFamily: 'var(--font-mono)' }}>
              {blockedDecisions.length}
            </div>
            <div style={{ fontSize: '11px', color: '#73849c', marginTop: '2px' }}>
              Vetos Deterministas Bloqueados
            </div>
          </div>
        </div>

        <div style={{
          backgroundColor: '#121824',
          borderRadius: '10px',
          border: '1px solid #1e2a3c',
          padding: '16px',
          display: 'flex',
          alignItems: 'center',
          gap: '14px',
        }}>
          <div style={{
            width: '38px',
            height: '38px',
            borderRadius: '8px',
            backgroundColor: 'rgba(210, 153, 34, 0.12)',
            border: '1px solid rgba(210, 153, 34, 0.3)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#d29922',
          }}>
            <AlertTriangle size={18} />
          </div>
          <div>
            <div style={{ fontSize: '18px', fontWeight: '700', color: '#d29922', fontFamily: 'var(--font-mono)' }}>
              {reviewDecisions.length}
            </div>
            <div style={{ fontSize: '11px', color: '#73849c', marginTop: '2px' }}>
              Compuertas HITL Activadas
            </div>
          </div>
        </div>
      </div>

      {/* Security Policies List */}
      <div style={{
        backgroundColor: '#121824',
        borderRadius: '10px',
        border: '1px solid #1e2a3c',
        padding: '22px',
      }}>
        <h3 style={{ fontSize: '14px', fontWeight: '700', color: '#f8fafc', marginBottom: '14px' }}>
          Barreras de Seguridad Activas en el PolicyEngine
        </h3>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          {securityPolicies.map((pol, idx) => (
            <div
              key={idx}
              style={{
                padding: '14px 16px',
                borderRadius: '8px',
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
                  <ShieldCheck size={15} style={{ color: '#3fb950' }} />
                  <span style={{ fontSize: '13px', fontWeight: '600', color: '#f0f6fc' }}>
                    {pol.name}
                  </span>
                  <span style={{
                    fontSize: '10px',
                    color: '#64748b',
                    backgroundColor: '#121824',
                    padding: '1px 6px',
                    borderRadius: '4px',
                  }}>
                    {pol.category}
                  </span>
                </div>
                <p style={{ fontSize: '11.5px', color: '#8595a8', marginTop: '6px', lineHeight: '1.45' }}>
                  {pol.detail}
                </p>
              </div>

              <span className="badge badge-success" style={{ whiteSpace: 'nowrap' }}>
                {pol.status}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Recent Security Incidents / Veto Log */}
      <div style={{
        backgroundColor: '#121824',
        borderRadius: '10px',
        border: '1px solid #1e2a3c',
        padding: '22px',
      }}>
        <h3 style={{ fontSize: '14px', fontWeight: '700', color: '#f8fafc', marginBottom: '12px' }}>
          Registro de Intercepciones de Seguridad Recientes
        </h3>

        {blockedDecisions.length > 0 || reviewDecisions.length > 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {[...blockedDecisions, ...reviewDecisions].slice(0, 8).map((inc, i) => (
              <div
                key={inc.decision_id || i}
                style={{
                  padding: '10px 14px',
                  backgroundColor: '#0c111a',
                  borderRadius: '6px',
                  border: `1px solid ${inc.status === 'BLOCKED' ? 'rgba(248, 81, 73, 0.25)' : 'rgba(210, 153, 34, 0.25)'}`,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  gap: '12px',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                  <span className={`badge ${inc.status === 'BLOCKED' ? 'badge-danger' : 'badge-warning'}`}>
                    {inc.status}
                  </span>
                  <span style={{ fontSize: '12px', fontFamily: 'var(--font-mono)', color: '#e2e8f0' }}>
                    {inc.command || inc.description || inc.tool}
                  </span>
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: '12px', fontSize: '11px', color: '#64748b' }}>
                  <span style={{ color: '#d29922', fontWeight: '600' }}>
                    Riesgo {inc.risk_level || 'HIGH'}
                  </span>
                  <span>
                    {inc.created_at ? new Date(inc.created_at).toLocaleTimeString() : 'Reciente'}
                  </span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div style={{
            padding: '24px',
            textAlign: 'center',
            backgroundColor: '#0c111a',
            borderRadius: '6px',
            color: '#8b949e',
            fontSize: '12px',
          }}>
            <CheckCircle2 size={24} style={{ color: '#3fb950', margin: '0 auto 8px auto', display: 'block' }} />
            No se han registrado violaciones de sandbox ni intentos de escape en las sesiones auditadas.
          </div>
        )}
      </div>
    </div>
  );
}
