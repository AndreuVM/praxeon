/**
 * PRAXEON API & WebSocket Client
 * Conecta la UI React con el backend FastAPI (1.0-B).
 */

const API_BASE = '/v1';

export async function fetchHealth() {
  try {
    const res = await fetch(`${API_BASE}/health`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    return { data: { status: 'offline', error: err.message } };
  }
}

export async function fetchMetrics() {
  try {
    const res = await fetch(`${API_BASE}/metrics`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    return null;
  }
}

export async function fetchSessions() {
  try {
    const res = await fetch(`${API_BASE}/sessions`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    console.warn('[PRAXEON API] Backend unreachable, fallback to local sessions:', err);
    return null;
  }
}

export async function fetchSession(sessionId) {
  try {
    const res = await fetch(`${API_BASE}/sessions/${sessionId}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    return null;
  }
}

export async function createSession(goal, sessionId = null) {
  const payload = { goal };
  if (sessionId) payload.session_id = sessionId;
  const res = await fetch(`${API_BASE}/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return await res.json();
}

export async function proposeAction(sessionId, proposal) {
  const res = await fetch(`${API_BASE}/sessions/${sessionId}/actions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(proposal),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return await res.json();
}

export async function fetchDecisionDetail(decisionId) {
  try {
    const res = await fetch(`${API_BASE}/decisions/${decisionId}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    return null;
  }
}

export async function confirmDecision(decisionId, approved = true, operatorId = 'operator_ui', notes = '') {
  const res = await fetch(`${API_BASE}/decisions/${decisionId}/confirm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      approved,
      operator_id: operatorId,
      notes: notes || (approved ? 'Authorized from Decision Inspector' : 'Rejected from Decision Inspector'),
    }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return await res.json();
}

export async function executeDecision(decisionId, capability = null) {
  const payload = capability ? { capability } : {};
  const res = await fetch(`${API_BASE}/decisions/${decisionId}/execute`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return await res.json();
}

export async function fetchEvents(sessionId, page = 1, pageSize = 50, afterSequence = null) {
  try {
    let url = `${API_BASE}/sessions/${sessionId}/events?page=${page}&page_size=${pageSize}`;
    if (afterSequence !== null) url += `&after_sequence=${afterSequence}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    return null;
  }
}

/**
 * Establece conexión WebSocket con el canal de streaming de la sesión.
 */
export function createWebSocketStream(sessionId, onMessage, onStatusChange) {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/v1/sessions/${sessionId}/stream`;

  let ws = null;
  let isClosedManually = false;
  let reconnectTimer = null;

  function connect() {
    try {
      ws = new WebSocket(wsUrl);
      onStatusChange?.('connecting');

      ws.onopen = () => {
        onStatusChange?.('connected');
        // Mensaje de inicialización / sync
        ws.send(JSON.stringify({ type: 'sync', last_sequence: 0 }));
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          onMessage?.(data);
        } catch (e) {
          console.error('[WS Parse Error]', e, event.data);
        }
      };

      ws.onerror = (err) => {
        console.warn('[WS Error]', err);
        onStatusChange?.('error');
      };

      ws.onclose = () => {
        onStatusChange?.('disconnected');
        if (!isClosedManually) {
          reconnectTimer = setTimeout(connect, 3000);
        }
      };
    } catch (e) {
      console.warn('[WS Connection Failed]', e);
      onStatusChange?.('failed');
    }
  }

  connect();

  return {
    send: (msg) => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(typeof msg === 'string' ? msg : JSON.stringify(msg));
      }
    },
    close: () => {
      isClosedManually = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (ws) ws.close();
    },
  };
}
