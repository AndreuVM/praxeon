import React, { useState } from 'react';
import { Settings, Save, Check, RefreshCw } from 'lucide-react';

export default function SettingsView() {
  const [strictness, setStrictness] = useState('standard');
  const [failClosed, setFailClosed] = useState(true);
  const [capabilityTtl, setCapabilityTtl] = useState(300);
  const [logLevel, setLogLevel] = useState('INFO');
  const [saved, setSaved] = useState(false);

  const handleSave = (e) => {
    e.preventDefault();
    setSaved(true);
    setTimeout(() => setSaved(false), 2500);
  };

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
          Configuración del Runtime de Supervisión
        </h1>
        <p style={{ fontSize: '12px', color: '#73849c', marginTop: '4px' }}>
          Parámetros operativos, umbrales de riesgo, políticas de intervención y tiempos de expiración de capabilities.
        </p>
      </div>

      <form onSubmit={handleSave} style={{
        backgroundColor: '#121926',
        borderRadius: '8px',
        border: '1px solid #1e2a3c',
        padding: '24px',
        maxWidth: '680px',
        display: 'flex',
        flexDirection: 'column',
        gap: '20px',
      }}>
        {/* Strictness Level */}
        <div>
          <label style={{ fontSize: '13px', fontWeight: '600', color: '#f8fafc', display: 'block', marginBottom: '6px' }}>
            Nivel de Rigor de Políticas (Policy Strictness)
          </label>
          <p style={{ fontSize: '11.5px', color: '#73849c', marginBottom: '10px' }}>
            Determina cuándo se exige confirmación humana obligatoria (*Human-in-the-loop*).
          </p>
          <div style={{ display: 'flex', gap: '10px' }}>
            {[
              { id: 'standard', label: 'Estándar', desc: 'Riesgo alto exige confirmación' },
              { id: 'strict', label: 'Estricto', desc: 'Toda mutación exige confirmación' },
              { id: 'permissive', label: 'Permisivo', desc: 'Solo acciones críticas se bloquean' },
            ].map((opt) => (
              <button
                type="button"
                key={opt.id}
                onClick={() => setStrictness(opt.id)}
                style={{
                  flex: 1,
                  padding: '10px 12px',
                  borderRadius: '6px',
                  textAlign: 'left',
                  backgroundColor: strictness === opt.id ? '#182438' : '#0c111a',
                  border: `1px solid ${strictness === opt.id ? '#38bdf8' : '#1e293b'}`,
                  cursor: 'pointer',
                  transition: 'all 0.15s ease',
                }}
              >
                <div style={{ fontSize: '12px', fontWeight: '600', color: '#f8fafc' }}>{opt.label}</div>
                <div style={{ fontSize: '10.5px', color: '#73849c', marginTop: '3px' }}>{opt.desc}</div>
              </button>
            ))}
          </div>
        </div>

        {/* Fail-Closed Toggle */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '12px 14px',
          borderRadius: '6px',
          backgroundColor: '#0c111a',
          border: '1px solid #1a2333',
        }}>
          <div>
            <div style={{ fontSize: '12.5px', fontWeight: '600', color: '#e2e8f0' }}>
              Modo Fail-Closed (Seguridad por Defecto)
            </div>
            <div style={{ fontSize: '11px', color: '#73849c', marginTop: '2px' }}>
              Si los proveedores semánticos fallan o caducan, bloquear automáticamente acciones destructivas.
            </div>
          </div>
          <input
            type="checkbox"
            checked={failClosed}
            onChange={(e) => setFailClosed(e.target.checked)}
            style={{ width: '18px', height: '18px', cursor: 'pointer' }}
          />
        </div>

        {/* Capability TTL */}
        <div>
          <label style={{ fontSize: '13px', fontWeight: '600', color: '#f8fafc', display: 'block', marginBottom: '6px' }}>
            Tiempo de Vida de Capability (TTL en segundos)
          </label>
          <input
            type="number"
            value={capabilityTtl}
            onChange={(e) => setCapabilityTtl(Number(e.target.value))}
            min={30}
            max={3600}
            style={{
              width: '180px',
              padding: '8px 12px',
              borderRadius: '6px',
              backgroundColor: '#0c111a',
              border: '1px solid #1e293b',
              color: '#f8fafc',
              fontSize: '12px',
              fontFamily: 'var(--font-mono)',
              outline: 'none',
            }}
          />
          <span style={{ fontSize: '11px', color: '#73849c', marginLeft: '10px' }}>
            Por defecto: 300 segundos (5 minutos)
          </span>
        </div>

        {/* Log Level */}
        <div>
          <label style={{ fontSize: '13px', fontWeight: '600', color: '#f8fafc', display: 'block', marginBottom: '6px' }}>
            Nivel de Registro del Terminal (Log Level)
          </label>
          <select
            value={logLevel}
            onChange={(e) => setLogLevel(e.target.value)}
            style={{
              padding: '8px 12px',
              borderRadius: '6px',
              backgroundColor: '#0c111a',
              border: '1px solid #1e293b',
              color: '#f8fafc',
              fontSize: '12px',
              outline: 'none',
              width: '180px',
            }}
          >
            <option value="DEBUG">DEBUG (Detallado)</option>
            <option value="INFO">INFO (Normal)</option>
            <option value="WARN">WARN (Solo alertas)</option>
          </select>
        </div>

        {/* Save button */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginTop: '10px' }}>
          <button type="submit" className="btn btn-primary" style={{ padding: '8px 16px' }}>
            <Save size={14} />
            Guardar Configuración
          </button>
          {saved && (
            <span style={{ color: '#34d399', fontSize: '12px', display: 'flex', alignItems: 'center', gap: '5px' }}>
              <Check size={14} />
              Configuración aplicada al runtime
            </span>
          )}
        </div>
      </form>
    </div>
  );
}
