/**
 * Cliente HTTP y WebSocket canónico para PRAXEON (@praxeon/sdk).
 */

import type {
  ActionProposal,
  ClientOptions,
  DecisionReceipt,
  ExecutionResult,
  SessionConfig,
  SessionState,
  SystemEvent,
  WorkflowDefinition,
} from './types.js';

export class PraxeonClient {
  private readonly baseUrl: string;
  private readonly apiKey?: string;
  private readonly timeoutMs: number;

  constructor(options: ClientOptions = {}) {
    this.baseUrl = (options.baseUrl || 'http://127.0.0.1:8000').replace(/\/+$/, '');
    this.apiKey = options.apiKey;
    this.timeoutMs = options.timeoutMs || 30_000;
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const url = `${this.baseUrl}${path.startsWith('/') ? path : `/${path}`}`;
    const headers = new Headers(init.headers);

    headers.set('Accept', 'application/json');
    if (this.apiKey) {
      headers.set('X-API-Key', this.apiKey);
    }
    if (init.body && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json');
    }

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.timeoutMs);

    try {
      const response = await fetch(url, {
        ...init,
        headers,
        signal: controller.signal,
      });

      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`[PRAXEON API ERROR ${response.status}]: ${errorText || response.statusText}`);
      }

      const json = await response.json();
      return (json && typeof json === 'object' && 'data' in json ? json.data : json) as T;
    } finally {
      clearTimeout(timeout);
    }
  }

  // ==========================================
  // GESTIÓN DE SESIONES
  // ==========================================

  async createSession(config: SessionConfig): Promise<SessionState> {
    return this.request<SessionState>('/v1/sessions/run', {
      method: 'POST',
      body: JSON.stringify(config),
    });
  }

  async listSessions(): Promise<SessionState[]> {
    return this.request<SessionState[]>('/v1/sessions');
  }

  async getSession(sessionId: string): Promise<SessionState> {
    return this.request<SessionState>(`/v1/sessions/${encodeURIComponent(sessionId)}`);
  }

  // ==========================================
  // SUPERVISIÓN Y DECISIONES
  // ==========================================

  async proposeAction(sessionId: string, proposal: ActionProposal): Promise<DecisionReceipt> {
    return this.request<DecisionReceipt>(`/v1/sessions/${encodeURIComponent(sessionId)}/actions`, {
      method: 'POST',
      body: JSON.stringify({
        action_id: proposal.actionId,
        parent_id: proposal.parentId,
        tool: proposal.tool,
        operation: proposal.operation,
        arguments: proposal.arguments,
        thought_rationale: proposal.thoughtRationale,
        provenance: proposal.provenance,
        context: proposal.context,
      }),
    });
  }

  async confirmDecision(decisionId: string, approved: boolean, reason?: string, actor?: string): Promise<DecisionReceipt> {
    return this.request<DecisionReceipt>(`/v1/decisions/${encodeURIComponent(decisionId)}/confirm`, {
      method: 'POST',
      body: JSON.stringify({
        approved,
        reason: reason || 'Confirmado por SDK cliente',
        actor: actor || 'sdk_operator',
      }),
    });
  }

  async executeDecision(decisionId: string): Promise<ExecutionResult> {
    return this.request<ExecutionResult>(`/v1/decisions/${encodeURIComponent(decisionId)}/execute`, {
      method: 'POST',
    });
  }

  // ==========================================
  // WORKFLOWS MULTIAGENTE
  // ==========================================

  async listWorkflows(): Promise<WorkflowDefinition[]> {
    return this.request<WorkflowDefinition[]>('/v1/workflows');
  }

  async getWorkflow(workflowId: string): Promise<WorkflowDefinition> {
    return this.request<WorkflowDefinition>(`/v1/workflows/${encodeURIComponent(workflowId)}`);
  }

  async executeWorkflow(workflowId: string, maxSteps = 100): Promise<{ executionId: string; status: string }> {
    return this.request<{ executionId: string; status: string }>(
      `/v1/workflows/${encodeURIComponent(workflowId)}/execute?max_steps=${maxSteps}`,
      { method: 'POST' }
    );
  }

  // ==========================================
  // OBSERVABILIDAD Y TELEMETRÍA
  // ==========================================

  async getHealth(): Promise<{ status: string; version: string; profile: string }> {
    return this.request<{ status: string; version: string; profile: string }>('/health');
  }

  async getMetricsJson(): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>('/v1/metrics');
  }

  async getMetricsPrometheus(): Promise<string> {
    const url = `${this.baseUrl}/metrics`;
    const headers: Record<string, string> = { Accept: 'text/plain' };
    if (this.apiKey) {
      headers['X-API-Key'] = this.apiKey;
    }
    const res = await fetch(url, { headers });
    if (!res.ok) {
      throw new Error(`[PRAXEON METRICS ERROR ${res.status}]: ${await res.text()}`);
    }
    return res.text();
  }

  // ==========================================
  // EVENT STREAMING (WEBSOCKET)
  // ==========================================

  createEventStreamUrl(sessionId: string, afterSequence = 0): string {
    const wsProto = this.baseUrl.startsWith('https') ? 'wss' : 'ws';
    const host = this.baseUrl.replace(/^https?:\/\//, '');
    let url = `${wsProto}://${host}/v1/sessions/${encodeURIComponent(sessionId)}/stream?after_sequence=${afterSequence}`;
    if (this.apiKey) {
      url += `&api_key=${encodeURIComponent(this.apiKey)}`;
    }
    return url;
  }

  parseEvent(rawData: string): SystemEvent {
    return JSON.parse(rawData) as SystemEvent;
  }
}
