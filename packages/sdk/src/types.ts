/**
 * Contratos y tipos canónicos de TypeScript para PRAXEON (@praxeon/sdk).
 */

export type ExecutionMode = 'container' | 'local_restricted' | 'full_access';

export type DecisionStatus = 'ALLOW' | 'BLOCK' | 'REVIEW' | 'REPLAN' | 'PENDING';

export interface ActionProposal {
  actionId: string;
  parentId?: string | null;
  tool: string;
  operation?: string;
  arguments: Record<string, unknown>;
  thoughtRationale?: string;
  provenance?: {
    source: string;
    step?: number;
    [key: string]: unknown;
  };
  context?: Record<string, unknown>;
}

export interface CapabilityToken {
  decisionId: string;
  sessionId: string;
  actionHash: string;
  stateHash: string;
  nonce: string;
  status: DecisionStatus;
  expiresAt: number;
  executionMode: ExecutionMode;
  signature: string;
}

export interface DecisionReceipt {
  decisionId: string;
  sessionId: string;
  actionId: string;
  status: DecisionStatus;
  capability?: CapabilityToken | null;
  policy: {
    requiresConfirmation: boolean;
    reasonCodes: string[];
    riskScore?: number;
    [key: string]: unknown;
  };
  latencyMs?: number;
  createdAt: string;
}

export interface ExecutionResult {
  decisionId: string;
  actionId: string;
  output: string;
  success: boolean;
  isError: boolean;
  exitCode: number;
  executionTimeMs: number;
}

export interface SessionConfig {
  goal: string;
  executionMode?: ExecutionMode;
  autonomous?: boolean;
  allowUnattendedExecution?: boolean;
  maxSteps?: number;
  provider?: string;
  model?: string;
  workspaceRoot?: string;
}

export interface SessionState {
  id: string;
  goal: string;
  status: 'Active' | 'Completed' | 'Intervened' | 'Failed' | 'Paused';
  executionMode: ExecutionMode;
  totalDecisions: number;
  allowedCount: number;
  blockedCount: number;
  reviewCount: number;
  eventCount: number;
  createdAt: string;
  finalAnswer?: string | null;
}

export interface WorkflowNode {
  id: string;
  type: 'start' | 'end' | 'task' | 'agent' | 'decision' | 'while' | 'delegate' | 'human_approval' | 'fork' | 'join';
  label: string;
  position: { x: number; y: number };
  config?: Record<string, unknown>;
}

export interface WorkflowEdge {
  id: string;
  source: string;
  target: string;
  condition?: string | Record<string, unknown>;
}

export interface WorkflowDefinition {
  id: string;
  name: string;
  description?: string;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  status?: string;
}

export interface SystemEvent {
  sequence: number;
  sessionId: string;
  type: string;
  nodeId?: string;
  decisionId?: string;
  timestamp: string;
  payload: Record<string, unknown>;
}

export interface ClientOptions {
  baseUrl?: string;
  apiKey?: string;
  timeoutMs?: number;
}
