/**
 * PRAXEON API & WebSocket Client
 * Conecta la UI React con el backend FastAPI (1.0-B).
 * Incluye gestión de autenticación, inyección de API Keys y handshake seguro para WebSockets.
 */

const API_BASE = '/v1';
const API_KEY_STORAGE_KEY = 'praxeon_api_key';

let inMemoryApiKey = (typeof window !== 'undefined' && window.__PRAXEON_API_KEY__) || null;

/**
 * Obtiene la API Key activa del almacén local o en memoria.
 */
export function getApiKey() {
  if (inMemoryApiKey) return inMemoryApiKey;
  if (typeof window !== 'undefined' && window.localStorage) {
    try {
      return window.localStorage.getItem(API_KEY_STORAGE_KEY) || null;
    } catch {
      return null;
    }
  }
  return null;
}

/**
 * Establece la API Key activa tanto en memoria como en almacenamiento local.
 */
export function setApiKey(key) {
  inMemoryApiKey = key ? key.trim() : null;
  if (typeof window !== 'undefined' && window.localStorage) {
    try {
      if (inMemoryApiKey) {
        window.localStorage.setItem(API_KEY_STORAGE_KEY, inMemoryApiKey);
      } else {
        window.localStorage.removeItem(API_KEY_STORAGE_KEY);
      }
    } catch {
      // Ignorar fallos de cuota o cookies restringidas
    }
  }
}

/**
 * Elimina las credenciales almacenadas.
 */
export function clearApiKey() {
  setApiKey(null);
}

/**
 * Indica si el cliente dispone de una clave de autenticación configurada.
 */
export function hasApiKey() {
  return Boolean(getApiKey());
}

/**
 * Envoltorio centralizado para peticiones HTTP con inyección automática de autenticación.
 */
export async function authFetch(url, options = {}) {
  const headers = new Headers(options.headers || {});
  const key = getApiKey();
  if (key) {
    if (!headers.has('X-API-Key')) headers.set('X-API-Key', key);
    if (!headers.has('Authorization')) headers.set('Authorization', `Bearer ${key}`);
  }
  if (!headers.has('Content-Type') && options.body && typeof options.body === 'string') {
    headers.set('Content-Type', 'application/json');
  }

  const res = await fetch(url, { ...options, headers });

  if (res.status === 401 || res.status === 403) {
    if (typeof window !== 'undefined') {
      window.dispatchEvent(
        new CustomEvent('praxeon:unauthorized', {
          detail: { url, status: res.status, timestamp: Date.now() },
        })
      );
    }
  }
  return res;
}

export async function fetchHealth() {
  try {
    const res = await authFetch(`${API_BASE}/health`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    return { data: { status: 'offline', error: err.message } };
  }
}

export async function fetchMetrics() {
  try {
    const res = await authFetch(`${API_BASE}/metrics`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch {
    return null;
  }
}

export async function fetchSessions() {
  try {
    const res = await authFetch(`${API_BASE}/sessions`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    console.warn('[PRAXEON API] Backend unreachable, fallback to local sessions:', err);
    return null;
  }
}

export async function fetchSession(sessionId) {
  try {
    const res = await authFetch(`${API_BASE}/sessions/${sessionId}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch {
    return null;
  }
}

export async function createSession(goal, sessionId = null) {
  const payload = { goal };
  if (sessionId) payload.session_id = sessionId;
  const res = await authFetch(`${API_BASE}/sessions`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return await res.json();
}

export async function deleteSession(sessionId) {
  let res = await authFetch(`${API_BASE}/sessions/${encodeURIComponent(sessionId)}`, {
    method: 'DELETE',
  });
  if (res.status === 405) {
    res = await authFetch(`${API_BASE}/sessions/${encodeURIComponent(sessionId)}/delete`, {
      method: 'POST',
    });
  }
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return await res.json();
}

export async function clearSessions(onlyCompleted = true, excludeSessionId = null) {
  let url = `${API_BASE}/sessions?only_completed=${onlyCompleted}`;
  if (excludeSessionId) {
    url += `&exclude_session_id=${encodeURIComponent(excludeSessionId)}`;
  }
  let res = await authFetch(url, {
    method: 'DELETE',
  });
  if (res.status === 405) {
    let fallbackUrl = `${API_BASE}/sessions/clear?only_completed=${onlyCompleted}`;
    if (excludeSessionId) {
      fallbackUrl += `&exclude_session_id=${encodeURIComponent(excludeSessionId)}`;
    }
    res = await authFetch(fallbackUrl, {
      method: 'POST',
    });
  }
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return await res.json();
}

export async function proposeAction(sessionId, proposal) {
  const res = await authFetch(`${API_BASE}/sessions/${sessionId}/actions`, {
    method: 'POST',
    body: JSON.stringify(proposal),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return await res.json();
}

export async function fetchDecisionDetail(decisionId) {
  try {
    const res = await authFetch(`${API_BASE}/decisions/${decisionId}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch {
    return null;
  }
}

export async function fetchDecisions(sessionId = null, limit = 150) {
  try {
    const url = sessionId
      ? `${API_BASE}/decisions?session_id=${encodeURIComponent(sessionId)}&limit=${limit}`
      : `${API_BASE}/decisions?limit=${limit}`;
    const res = await authFetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    console.warn('[PRAXEON API] Error fetching decisions:', err);
    return null;
  }
}

export async function fetchSessionDecisions(sessionId) {
  try {
    const res = await authFetch(`${API_BASE}/sessions/${sessionId}/decisions`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    console.warn('[PRAXEON API] Error fetching session decisions:', err);
    return null;
  }
}

export async function confirmDecision(decisionId, approved = true, reason = '', operatorId = 'operator_admin', role = 'operator') {
  const res = await authFetch(`${API_BASE}/decisions/${decisionId}/confirm`, {
    method: 'POST',
    body: JSON.stringify({
      approved,
      reason: reason || (approved ? 'Authorized from Decision Inspector' : 'Rechazado por operador'),
      operator_id: operatorId,
      role: role,
    }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return await res.json();
}

export async function rejectDecision(decisionId, reason = 'Rechazado por operador', operatorId = 'operator_admin', role = 'operator') {
  const res = await authFetch(`${API_BASE}/decisions/${decisionId}/reject`, {
    method: 'POST',
    body: JSON.stringify({
      reason: reason || 'Rechazado por operador',
      operator_id: operatorId,
      role: role,
    }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return await res.json();
}

export async function executeDecision(decisionId, capability = null, operatorId = 'operator_admin', role = 'operator') {
  const capToken = capability?.token || capability;
  const payload = {
    capability_token: capToken || undefined,
    operator_id: operatorId,
    role: role,
  };
  const res = await authFetch(`${API_BASE}/decisions/${decisionId}/execute`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return await res.json();
}

export async function fetchEvents(sessionId, page = 1, pageSize = 50, afterSequence = null) {
  try {
    let url = `${API_BASE}/sessions/${sessionId}/events?page=${page}&page_size=${pageSize}`;
    if (afterSequence !== null) url += `&after_sequence=${afterSequence}`;
    const res = await authFetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch {
    return null;
  }
}

/**
 * Solicita al servidor un ticket efímero de streaming para una sesión.
 */
export async function fetchWebSocketTicket(sessionId) {
  try {
    const res = await authFetch(`${API_BASE}/sessions/${encodeURIComponent(sessionId)}/ws-ticket`, {
      method: 'POST',
    });
    if (res.ok) {
      return await res.json();
    }
    return null;
  } catch (err) {
    console.warn('[PRAXEON API] Error obteniendo ticket de streaming WebSocket:', err);
    return null;
  }
}

/**
 * Establece conexión WebSocket con el canal de streaming de la sesión.
 * Soporta gap recovery transparente pasando after_sequence y autenticación por ticket efímero.
 */
export function createWebSocketStream(sessionId, onMessage, onStatusChange, getLastSequence = () => 0) {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  let ws = null;
  let isClosedManually = false;
  let reconnectTimer = null;

  async function connect() {
    try {
      const lastSeq = typeof getLastSequence === 'function' ? (getLastSequence() || 0) : (getLastSequence || 0);
      let wsUrl = `${protocol}//${window.location.host}/v1/sessions/${encodeURIComponent(sessionId)}/stream?after_sequence=${lastSeq}`;

      const key = getApiKey();
      if (key) {
        // Obtener ticket efímero de un solo uso para no exponer la API Key maestra en URLs ni logs
        const ticketData = await fetchWebSocketTicket(sessionId);
        if (ticketData && ticketData.ticket) {
          wsUrl += `&ticket=${encodeURIComponent(ticketData.ticket)}`;
        } else {
          // Fallback de retrocompatibilidad si el endpoint de ticket no respondiera
          wsUrl += `&token=${encodeURIComponent(key)}`;
        }
      }

      ws = new WebSocket(wsUrl);
      onStatusChange?.('connecting');

      ws.onopen = () => {
        onStatusChange?.('connected');
        // Mensaje de inicialización / sync compatible con el servidor
        ws.send(JSON.stringify({ action: 'sync', after_sequence: lastSeq }));
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

export async function runMission(missionConfig) {
  const res = await authFetch(`${API_BASE}/sessions/run`, {
    method: 'POST',
    body: JSON.stringify(missionConfig),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return await res.json();
}

export async function pauseMission(sessionId) {
  const res = await authFetch(`${API_BASE}/sessions/${sessionId}/pause`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return await res.json();
}

export async function resumeMission(sessionId) {
  const res = await authFetch(`${API_BASE}/sessions/${sessionId}/resume`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return await res.json();
}

export async function fetchContext(workspaceRoot = null) {
  try {
    const url = workspaceRoot ? `${API_BASE}/context?workspace_root=${encodeURIComponent(workspaceRoot)}` : `${API_BASE}/context`;
    const res = await authFetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch {
    return null;
  }
}

// ==========================================
// AGENTS REST API (PRAXEON 1.0)
// ==========================================

export async function fetchAgents(params = {}) {
  const query = new URLSearchParams();
  if (params.status) query.set('status', params.status);
  if (params.role) query.set('role', params.role);
  if (params.capability) query.set('capability', params.capability);
  if (params.skill) query.set('skill', params.skill);
  if (params.tag) query.set('tag', params.tag);
  const qStr = query.toString();
  const url = qStr ? `${API_BASE}/agents?${qStr}` : `${API_BASE}/agents`;
  const res = await authFetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return await res.json();
}

export async function fetchAgentDetail(agentId) {
  const res = await authFetch(`${API_BASE}/agents/${encodeURIComponent(agentId)}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return await res.json();
}

export async function createAgent(agentData) {
  const res = await authFetch(`${API_BASE}/agents`, {
    method: 'POST',
    body: JSON.stringify(agentData),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return await res.json();
}

export async function updateAgent(agentId, updates) {
  const res = await authFetch(`${API_BASE}/agents/${encodeURIComponent(agentId)}`, {
    method: 'PATCH',
    body: JSON.stringify(updates),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return await res.json();
}

export async function deleteAgent(agentId, hardDelete = false) {
  const url = `${API_BASE}/agents/${encodeURIComponent(agentId)}${hardDelete ? '?hard_delete=true' : ''}`;
  const res = await authFetch(url, { method: 'DELETE' });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return await res.json();
}

export async function createAgentVersion(agentId, versionData) {
  const res = await authFetch(`${API_BASE}/agents/${encodeURIComponent(agentId)}/versions`, {
    method: 'POST',
    body: JSON.stringify(versionData),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return await res.json();
}

export async function fetchAgentVersions(agentId) {
  const res = await authFetch(`${API_BASE}/agents/${encodeURIComponent(agentId)}/versions`);
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return await res.json();
}

// ==========================================
// WORKFLOWS REST API (PRAXEON 1.0)
// ==========================================

export async function fetchWorkflows() {
  const res = await authFetch(`${API_BASE}/workflows`);
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return await res.json();
}

export async function createWorkflow(workflowData) {
  const res = await authFetch(`${API_BASE}/workflows`, {
    method: 'POST',
    body: JSON.stringify(workflowData),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return await res.json();
}

export async function getWorkflowDetail(workflowId) {
  const res = await authFetch(`${API_BASE}/workflows/${encodeURIComponent(workflowId)}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return await res.json();
}

export async function addWorkflowNode(workflowId, nodeData) {
  const res = await authFetch(`${API_BASE}/workflows/${encodeURIComponent(workflowId)}/nodes`, {
    method: 'POST',
    body: JSON.stringify(nodeData),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return await res.json();
}

export async function updateWorkflowNodePosition(workflowId, nodeId, x, y) {
  const res = await authFetch(`${API_BASE}/workflows/${encodeURIComponent(workflowId)}/nodes/${encodeURIComponent(nodeId)}/position`, {
    method: 'PUT',
    body: JSON.stringify({ x, y }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return await res.json();
}

export async function deleteWorkflowNode(workflowId, nodeId) {
  const res = await authFetch(`${API_BASE}/workflows/${encodeURIComponent(workflowId)}/nodes/${encodeURIComponent(nodeId)}`, {
    method: 'DELETE',
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return await res.json();
}

export async function updateWorkflowNode(workflowId, nodeId, nodeData) {
  const res = await authFetch(`${API_BASE}/workflows/${encodeURIComponent(workflowId)}/nodes/${encodeURIComponent(nodeId)}`, {
    method: 'PUT',
    body: JSON.stringify(nodeData),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return await res.json();
}

export async function connectWorkflowNodes(workflowId, edgeData) {
  const res = await authFetch(`${API_BASE}/workflows/${encodeURIComponent(workflowId)}/edges`, {
    method: 'POST',
    body: JSON.stringify(edgeData),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return await res.json();
}

export async function updateWorkflowEdge(workflowId, edgeId, edgeData) {
  const res = await authFetch(`${API_BASE}/workflows/${encodeURIComponent(workflowId)}/edges/${encodeURIComponent(edgeId)}`, {
    method: 'PUT',
    body: JSON.stringify(edgeData),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return await res.json();
}

export async function deleteWorkflowEdge(workflowId, edgeId) {
  const res = await authFetch(`${API_BASE}/workflows/${encodeURIComponent(workflowId)}/edges/${encodeURIComponent(edgeId)}`, {
    method: 'DELETE',
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return await res.json();
}

export async function validateWorkflow(workflowId) {
  const res = await authFetch(`${API_BASE}/workflows/${encodeURIComponent(workflowId)}/validate`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return await res.json();
}

export async function executeWorkflow(workflowId, maxSteps = 100) {
  const res = await authFetch(`${API_BASE}/workflows/${encodeURIComponent(workflowId)}/execute?max_steps=${maxSteps}`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return await res.json();
}

export async function stepWorkflow(workflowId) {
  const res = await authFetch(`${API_BASE}/workflows/${encodeURIComponent(workflowId)}/step`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return await res.json();
}

export async function backtrackWorkflow(workflowId, targetNodeId) {
  const res = await authFetch(`${API_BASE}/workflows/${encodeURIComponent(workflowId)}/backtrack`, {
    method: 'POST',
    body: JSON.stringify({ target_node_id: targetNodeId }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return await res.json();
}

export async function resetWorkflow(workflowId) {
  const res = await authFetch(`${API_BASE}/workflows/${encodeURIComponent(workflowId)}/reset`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return await res.json();
}


