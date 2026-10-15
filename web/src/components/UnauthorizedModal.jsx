import React, { useEffect, useState } from 'react';
import { Key, Eye, EyeOff, CheckCircle2, AlertCircle } from 'lucide-react';
import { getApiKey, setApiKey, fetchHealth } from '../services/api';
import { Modal, Field } from './ui';

export default function UnauthorizedModal({ isOpen, onClose, onSuccess }) {
  const [keyInput, setKeyInput] = useState(() => getApiKey() || '');
  const [showKey, setShowKey] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null); // { success: boolean, message: string }

  // Cada vez que se abre, parte de la clave guardada y sin resultados anteriores
  useEffect(() => {
    if (isOpen) {
      setKeyInput(getApiKey() || '');
      setTestResult(null);
      setShowKey(false);
    }
  }, [isOpen]);

  const handleTestConnection = async () => {
    if (!keyInput.trim()) {
      setTestResult({ success: false, message: 'Introduce una clave de API antes de probar.' });
      return;
    }
    setTesting(true);
    setTestResult(null);

    // Se guarda de forma temporal para que fetchHealth use la clave en la petición
    const previousKey = getApiKey();
    setApiKey(keyInput.trim());

    try {
      const res = await fetchHealth();
      if (res && res.status !== 'offline') {
        setTestResult({ success: true, message: 'Autenticación correcta. El servidor responde.' });
      } else {
        setApiKey(previousKey);
        setTestResult({ success: false, message: 'Error de autenticación o servidor inalcanzable.' });
      }
    } catch (err) {
      setApiKey(previousKey);
      setTestResult({ success: false, message: `No se pudo verificar la clave: ${err.message}` });
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
    <Modal open={isOpen} title="Clave de API de PRAXEON" onClose={onClose} maxWidth={480}>
      <form onSubmit={handleSave} style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
        <p style={{ lineHeight: 1.55, color: 'var(--muted)' }}>
          El servidor PRAXEON necesita una <strong style={{ color: 'var(--text)', fontWeight: 500 }}>clave maestra de API</strong> (<code>PRAXEON_API_KEY</code>) para autenticar las peticiones REST y los canales de transmisión por WebSocket.
        </p>

        <Field label="Clave maestra de API" htmlFor="auth-key">
          <div style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
            <input
              id="auth-key"
              type={showKey ? 'text' : 'password'}
              value={keyInput}
              onChange={(e) => {
                setKeyInput(e.target.value);
                setTestResult(null);
              }}
              placeholder="Introduce tu PRAXEON_API_KEY…"
              autoComplete="off"
              style={{ width: '100%', paddingRight: '40px', fontFamily: 'var(--font-mono)' }}
            />
            <button
              type="button"
              className="btn-icon"
              onClick={() => setShowKey(!showKey)}
              style={{ position: 'absolute', right: '4px', width: '28px', height: '28px' }}
              aria-label={showKey ? 'Ocultar la clave' : 'Mostrar la clave'}
            >
              {showKey ? <EyeOff size={15} /> : <Eye size={15} />}
            </button>
          </div>
        </Field>

        {testResult && (
          <div
            role="status"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              color: testResult.success ? 'var(--ok)' : 'var(--bad)',
              fontSize: 'var(--fs-secondary)',
            }}
          >
            {testResult.success ? <CheckCircle2 size={15} /> : <AlertCircle size={15} />}
            <span>{testResult.message}</span>
          </div>
        )}

        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '10px', flexWrap: 'wrap' }}>
          <button type="button" className="btn btn-secondary" onClick={handleTestConnection} disabled={testing || !keyInput.trim()}>
            <Key size={13} />
            {testing ? 'Verificando…' : 'Probar la clave'}
          </button>
          <div style={{ display: 'flex', gap: '8px' }}>
            <button type="button" className="btn btn-secondary" onClick={onClose}>Cancelar</button>
            <button type="submit" className="btn btn-primary">Guardar y reconectar</button>
          </div>
        </div>
      </form>
    </Modal>
  );
}
