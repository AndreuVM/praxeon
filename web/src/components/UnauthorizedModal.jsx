import React, { useState } from 'react';
import { Key, Lock, Eye, EyeOff, CheckCircle2, AlertCircle, X } from 'lucide-react';
import { getApiKey, setApiKey, fetchHealth } from '../services/api';

export default function UnauthorizedModal({
  isOpen,
  onClose,
  onSuccess,
}) {
  const [keyInput, setKeyInput] = useState(() => getApiKey() || '');
  const [showKey, setShowKey] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null); // { success: boolean, message: string }

  if (!isOpen) return null;

  const handleTestConnection = async () => {
    if (!keyInput.trim()) {
      setTestResult({ success: false, message: 'Por favor introduce una API Key antes de probar.' });
      return;
    }
    setTesting(true);
    setTestResult(null);

    // Guardar temporalmente para que fetchHealth use la key en authFetch
    const previousKey = getApiKey();
    setApiKey(keyInput.trim());

    try {
      const res = await fetchHealth();
      if (res && res.status !== 'offline') {
        setTestResult({ success: true, message: 'Autenticación exitosa. Servidor online.' });
      } else {
        // Si falló, restaurar
        setApiKey(previousKey);
        setTestResult({ success: false, message: 'Error de autenticación o servidor inalcanzable.' });
      }
    } catch (err) {
      setApiKey(previousKey);
      setTestResult({ success: false, message: `Fallo al verificar clave: ${err.message}` });
    } finally {
      setTesting(false);
    }
  };

  const handleSave = (e) => {
    e.preventDefault();
    const trimmed = keyInput.trim();
    setApiKey(trimmed || null);
    if (onSuccess) {
      onSuccess(trimmed);
    }
    onClose();
  };

  return (
    <div style={{
      position: 'fixed',
      top: 0,
      left: 0,
      right: 0,
      bottom: 0,
      backgroundColor: 'rgba(0, 0, 0, 0.75)',
      backdropFilter: 'blur(4px)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      zIndex: 9999,
      padding: '16px',
    }}>
      <div style={{
        backgroundColor: '#121824',
        border: '1px solid #243044',
        borderRadius: '12px',
        width: '100%',
        maxWidth: '480px',
        boxShadow: '0 20px 40px rgba(0, 0, 0, 0.6)',
        overflow: 'hidden',
      }}>
        {/* Header */}
        <div style={{
          padding: '18px 24px',
          borderBottom: '1px solid #1e293b',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <div style={{
              width: '32px',
              height: '32px',
              borderRadius: '8px',
              backgroundColor: 'rgba(235, 179, 56, 0.15)',
              border: '1px solid rgba(235, 179, 56, 0.3)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#ebb338',
            }}>
              <Lock size={18} />
            </div>
            <div>
              <h2 style={{ fontSize: '15px', fontWeight: '700', color: '#f8fafc', margin: 0 }}>
                Autenticación Requerida
              </h2>
              <span style={{ fontSize: '11.5px', color: '#73849c' }}>
                PRAXEON Runtime Profile: Production Security
              </span>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            style={{
              background: 'none',
              border: 'none',
              color: '#64748b',
              cursor: 'pointer',
              padding: '4px',
              display: 'flex',
            }}
          >
            <X size={18} />
          </button>
        </div>

        {/* Form Body */}
        <form onSubmit={handleSave} style={{ padding: '24px' }}>
          <p style={{ fontSize: '12.5px', color: '#94a3b8', lineHeight: '1.5', margin: '0 0 16px 0' }}>
            El servidor PRAXEON requiere una <strong>Clave Maestra de API</strong> (<code>PRAXEON_API_KEY</code>) para autenticar las peticiones REST y los canales de streaming WebSocket.
          </p>

          <div style={{ marginBottom: '16px' }}>
            <label style={{ display: 'block', fontSize: '12px', fontWeight: '600', color: '#cbd5e1', marginBottom: '6px' }}>
              Clave Maestra de PRAXEON (API Key)
            </label>
            <div style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
              <input
                type={showKey ? 'text' : 'password'}
                value={keyInput}
                onChange={(e) => {
                  setKeyInput(e.target.value);
                  setTestResult(null);
                }}
                placeholder="Introduce tu PRAXEON_API_KEY..."
                autoFocus
                style={{
                  width: '100%',
                  padding: '9px 40px 9px 12px',
                  borderRadius: '6px',
                  backgroundColor: '#0c111a',
                  border: '1px solid #1e293b',
                  color: '#f8fafc',
                  fontSize: '12.5px',
                  fontFamily: 'var(--font-mono)',
                  outline: 'none',
                  boxSizing: 'border-box',
                }}
              />
              <button
                type="button"
                onClick={() => setShowKey(!showKey)}
                style={{
                  position: 'absolute',
                  right: '10px',
                  background: 'none',
                  border: 'none',
                  color: '#64748b',
                  cursor: 'pointer',
                  padding: '2px',
                  display: 'flex',
                }}
              >
                {showKey ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
          </div>

          {/* Test Status Feedback */}
          {testResult && (
            <div style={{
              padding: '10px 14px',
              borderRadius: '6px',
              backgroundColor: testResult.success ? 'rgba(63, 185, 80, 0.12)' : 'rgba(248, 81, 73, 0.12)',
              border: `1px solid ${testResult.success ? 'rgba(63, 185, 80, 0.3)' : 'rgba(248, 81, 73, 0.3)'}`,
              color: testResult.success ? '#3fb950' : '#f85149',
              fontSize: '12px',
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              marginBottom: '16px',
            }}>
              {testResult.success ? <CheckCircle2 size={16} /> : <AlertCircle size={16} />}
              <span>{testResult.message}</span>
            </div>
          )}

          {/* Actions */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '10px', marginTop: '20px' }}>
            <button
              type="button"
              onClick={handleTestConnection}
              disabled={testing || !keyInput.trim()}
              style={{
                padding: '8px 14px',
                fontSize: '12px',
                borderRadius: '6px',
                backgroundColor: '#161c28',
                border: '1px solid #243044',
                color: '#cbd5e1',
                cursor: (testing || !keyInput.trim()) ? 'not-allowed' : 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                opacity: (testing || !keyInput.trim()) ? 0.6 : 1,
              }}
            >
              <Key size={13} />
              {testing ? 'Verificando...' : 'Probar Clave'}
            </button>

            <div style={{ display: 'flex', gap: '8px' }}>
              <button
                type="button"
                onClick={onClose}
                style={{
                  padding: '8px 14px',
                  fontSize: '12px',
                  borderRadius: '6px',
                  backgroundColor: 'transparent',
                  border: '1px solid #334155',
                  color: '#94a3b8',
                  cursor: 'pointer',
                }}
              >
                Cancelar
              </button>
              <button
                type="submit"
                style={{
                  padding: '8px 18px',
                  fontSize: '12px',
                  fontWeight: '600',
                  borderRadius: '6px',
                  backgroundColor: '#238636',
                  border: 'none',
                  color: '#ffffff',
                  cursor: 'pointer',
                }}
              >
                Guardar y Reconectar
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
}
