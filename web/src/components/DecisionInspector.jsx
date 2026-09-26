import React, { useState } from 'react';
import {
  Copy,
  Check,
  X,
  AlertTriangle,
  Shield,
  ShieldCheck,
  FileCheck,
  UserCheck,
  Terminal,
  ExternalLink,
  ChevronRight,
  Play,
} from 'lucide-react';

export default function DecisionInspector({
  decision = null,
  onApprove,
  onReject,
  onExecute,
}) {
  const [activeTab, setActiveTab] = useState('decision');
  const [copied, setCopied] = useState(false);
  const [isConfirming, setIsConfirming] = useState(false);

  if (!decision) {
    return (
      <aside style={{
        width: '330px',
        backgroundColor: '#0c111a',
        borderLeft: '1px solid #1a2333',
        padding: '24px',
        color: '#64748b',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        textAlign: 'center',
      }}>
        Selecciona un nodo del Decision Tree para inspeccionar su trazabilidad.
      </aside>
    );
  }

  const {
    decisionId = 'd_unknown',
    sequence = 5,
    status = 'BLOCKED',
    actionCommand = 'git push origin main',
    tool = 'git',
    provider = 'JEV',
    model = 'Claude-3.5-sonnet',
    riskLevel = 'HIGH',
    riskScore = 0.82,
    semanticEvaluation = [
      { provider: 'LAYA', score: 0.81, verdict: 'ALLOW' },
      { provider: 'TypeSafe', score: 0.64, verdict: 'REVIEW' },
    ],
    policyDecision = {
      status: 'Requires confirmation',
      requiresConfirmation: true,
      rulesActivated: ['EgressPolicy', 'PathContainment', 'DoubleVerification'],
      reasonCodes: ['REQUIRE_HUMAN_CONFIRMATION'],
      precedence: 'Deterministic Safety Precedence',
    },
    capability = {
      issued: false,
      statusText: 'Not issued',
      token: null,
    },
    reason = 'High risk action requires confirmation according to policy rules.',
    evidenceTab = {},
    receiptTab = {},
    relatedDecisions = [],
  } = decision;

  const handleCopyCommand = () => {
    navigator.clipboard?.writeText(actionCommand);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const getStatusBadge = () => {
    if (status === 'ALLOW') {
      return (
        <span className="badge badge-success">
          <Check size={11} strokeWidth={2.5} />
          ALLOW
        </span>
      );
    }
    if (status === 'REVIEW') {
      return (
        <span className="badge badge-warning">
          <AlertTriangle size={11} strokeWidth={2.3} />
          REVIEW
        </span>
      );
    }
    return (
      <span className="badge badge-danger">
        <X size={11} strokeWidth={2.5} />
        BLOCKED
      </span>
    );
  };

  return (
    <aside style={{
      width: '335px',
      backgroundColor: '#0c111a',
      borderLeft: '1px solid #1a2333',
      display: 'flex',
      flexDirection: 'column',
      flexShrink: 0,
      userSelect: 'none',
    }}>
      {/* 4 Tabs Header */}
      <div style={{
        height: '42px',
        backgroundColor: '#090d14',
        borderBottom: '1px solid #17202e',
        display: 'flex',
        alignItems: 'center',
        padding: '0 16px',
        justifyContent: 'space-between',
      }}>
        {['decision', 'evidence', 'policy', 'receipt'].map((tab) => {
          const isActive = activeTab === tab;
          return (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              style={{
                background: 'none',
                border: 'none',
                padding: '10px 0',
                fontSize: '12px',
                fontWeight: isActive ? '600' : '400',
                color: isActive ? '#f8fafc' : '#73849c',
                cursor: 'pointer',
                borderBottom: isActive ? '2px solid #38bdf8' : '2px solid transparent',
                textTransform: 'capitalize',
                transition: 'all 0.15s ease',
              }}
            >
              {tab}
            </button>
          );
        })}
      </div>

      {/* Main Tab Content */}
      <div style={{
        flex: 1,
        overflowY: 'auto',
        padding: '18px',
        display: 'flex',
        flexDirection: 'column',
        gap: '18px',
      }}>
        {activeTab === 'decision' && (
          <>
            {/* Status & Sequence */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              {getStatusBadge()}
              <span style={{ fontSize: '12px', color: '#64748b', fontFamily: 'var(--font-mono)' }}>
                # {sequence}
              </span>
            </div>

            {/* Action Box */}
            <div>
              <span style={{ fontSize: '11px', color: '#73849c', fontWeight: '500' }}>
                Action
              </span>
              <div style={{
                marginTop: '6px',
                backgroundColor: '#111722',
                borderRadius: '6px',
                border: '1px solid #1e2a3c',
                padding: '9px 12px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
              }}>
                <div style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: '11.5px',
                  color: '#e2e8f0',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}>
                  <span style={{ color: '#64748b' }}>&gt;_</span>
                  <span>{actionCommand}</span>
                </div>
                <button
                  onClick={handleCopyCommand}
                  style={{
                    background: 'none',
                    border: 'none',
                    color: copied ? '#34d399' : '#64748b',
                    cursor: 'pointer',
                    padding: '2px',
                  }}
                  title="Copiar comando"
                >
                  {copied ? <Check size={13} /> : <Copy size={13} />}
                </button>
              </div>
            </div>

            {/* Attributes Grid */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', fontSize: '11.5px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: '#73849c' }}>Tool</span>
                <span style={{ color: '#e2e8f0', fontFamily: 'var(--font-mono)' }}>{tool}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: '#73849c' }}>Provider</span>
                <span style={{ color: '#e2e8f0' }}>{provider}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: '#73849c' }}>Model</span>
                <span style={{ color: '#e2e8f0' }}>{model}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ color: '#73849c' }}>Risk Level</span>
                <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                  <span style={{
                    width: '7px',
                    height: '7px',
                    borderRadius: '50%',
                    backgroundColor: riskLevel === 'HIGH' ? '#ef4444' : riskLevel === 'MEDIUM' ? '#f59e0b' : '#10b981',
                  }} />
                  <span style={{
                    fontSize: '11px',
                    fontWeight: '700',
                    color: riskLevel === 'HIGH' ? '#f87171' : riskLevel === 'MEDIUM' ? '#fbbf24' : '#34d399',
                  }}>
                    {riskLevel}
                  </span>
                </div>
              </div>
            </div>

            {/* Semantic Evaluation */}
            <div style={{
              backgroundColor: '#0f1520',
              borderRadius: '6px',
              padding: '12px',
              border: '1px solid #1a2434',
            }}>
              <span style={{ fontSize: '11px', color: '#8595a8', fontWeight: '600' }}>
                Semantic Evaluation
              </span>
              <div style={{ marginTop: '8px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
                {semanticEvaluation.map((ev, idx) => (
                  <div key={idx} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11.5px' }}>
                    <span style={{ color: '#94a3b8' }}>{ev.provider}</span>
                    <div style={{ display: 'flex', gap: '8px' }}>
                      <span style={{ color: ev.verdict === 'ALLOW' ? '#34d399' : '#fbbf24', fontWeight: '600' }}>
                        {ev.score}
                      </span>
                      <span style={{
                        color: ev.verdict === 'ALLOW' ? '#34d399' : '#fbbf24',
                        fontWeight: '700',
                        fontSize: '10.5px',
                      }}>
                        {ev.verdict}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Policy Decision */}
            <div>
              <span style={{ fontSize: '11px', color: '#73849c', fontWeight: '500' }}>
                Policy Decision
              </span>
              <div style={{
                marginTop: '6px',
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                color: policyDecision.requiresConfirmation ? '#fbbf24' : '#34d399',
                fontSize: '11.5px',
                fontWeight: '600',
              }}>
                <AlertTriangle size={14} />
                <span>{policyDecision.status}</span>
              </div>
            </div>

            {/* Capability */}
            <div>
              <span style={{ fontSize: '11px', color: '#73849c', fontWeight: '500' }}>
                Capability
              </span>
              <div style={{
                marginTop: '6px',
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                color: capability.issued ? '#34d399' : '#64748b',
                fontSize: '11.5px',
              }}>
                <Shield size={14} />
                <span>{capability.statusText || (capability.issued ? 'Issued & Signed' : 'Not issued')}</span>
              </div>
            </div>

            {/* Reason */}
            <div>
              <span style={{ fontSize: '11px', color: '#73849c', fontWeight: '500' }}>
                Reason
              </span>
              <p style={{
                marginTop: '6px',
                fontSize: '11.5px',
                color: '#94a3b8',
                lineHeight: '1.5',
              }}>
                {reason}
              </p>
            </div>

            {/* Action Buttons: Request Human Approval or Approve/Reject */}
            <div>
              {capability.issued ? (
                <button
                  onClick={() => onExecute?.(decisionId, capability)}
                  className="btn btn-primary"
                  style={{ width: '100%', padding: '9px 12px' }}
                >
                  <Play size={13} />
                  Execute in Sandbox
                </button>
              ) : isConfirming ? (
                <div style={{ display: 'flex', gap: '8px' }}>
                  <button
                    onClick={() => {
                      onApprove?.(decisionId);
                      setIsConfirming(false);
                    }}
                    className="btn btn-success"
                    style={{ flex: 1, padding: '8px' }}
                  >
                    <Check size={13} />
                    Approve
                  </button>
                  <button
                    onClick={() => {
                      onReject?.(decisionId);
                      setIsConfirming(false);
                    }}
                    className="btn btn-danger"
                    style={{ flex: 1, padding: '8px' }}
                  >
                    <X size={13} />
                    Reject
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => setIsConfirming(true)}
                  className="btn btn-approval"
                >
                  <UserCheck size={14} />
                  Request Human Approval
                </button>
              )}
            </div>

            {/* Related Decisions */}
            {relatedDecisions.length > 0 && (
              <div style={{ borderTop: '1px solid #1a2333', paddingTop: '14px' }}>
                <span style={{ fontSize: '11px', color: '#73849c', fontWeight: '600' }}>
                  Related Decisions
                </span>
                <div style={{ marginTop: '8px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
                  {relatedDecisions.map((rd, idx) => (
                    <div
                      key={idx}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        padding: '6px 8px',
                        borderRadius: '6px',
                        backgroundColor: '#101622',
                        fontSize: '11.5px',
                      }}
                    >
                      <span style={{ color: '#64748b', fontFamily: 'var(--font-mono)' }}>{rd.id}</span>
                      <span style={{ color: '#cbd5e1' }}>{rd.tool}</span>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                        <span style={{ color: '#34d399', fontWeight: '600', fontSize: '10.5px' }}>
                          ✓ {rd.verdict}
                        </span>
                        <ChevronRight size={12} style={{ color: '#475569' }} />
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}

        {/* Tab 2: Evidence */}
        {activeTab === 'evidence' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '14px', fontSize: '11.5px' }}>
            <div style={{
              padding: '12px',
              borderRadius: '8px',
              backgroundColor: '#111722',
              border: '1px solid #1e2a3c',
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: '#73849c' }}>Grounding Score</span>
                <span style={{ color: '#34d399', fontWeight: '700' }}>
                  {Math.round((evidenceTab.groundingScore || 0.85) * 100)}%
                </span>
              </div>
              <div style={{
                height: '6px',
                borderRadius: '9999px',
                backgroundColor: '#1e293b',
                marginTop: '8px',
                overflow: 'hidden',
              }}>
                <div style={{
                  width: `${Math.round((evidenceTab.groundingScore || 0.85) * 100)}%`,
                  height: '100%',
                  backgroundColor: '#10b981',
                }} />
              </div>
            </div>

            <div>
              <span style={{ color: '#73849c', fontWeight: '600' }}>
                Empirical Claims ({evidenceTab.claimCount || 0})
              </span>
              <ul style={{ marginTop: '8px', paddingLeft: '16px', color: '#cbd5e1', lineHeight: '1.6' }}>
                {(evidenceTab.claims || [
                  'Git branch HEAD is verified against remote origin.',
                  'No uncommitted conflicting unstaged files in tree.'
                ]).map((claim, idx) => (
                  <li key={idx}>{claim}</li>
                ))}
              </ul>
            </div>

            <div style={{ borderTop: '1px solid #1a2333', paddingTop: '10px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', color: '#73849c' }}>
                <span>Freshness</span>
                <span style={{ color: '#e2e8f0' }}>{evidenceTab.freshness || 'live'}</span>
              </div>
            </div>
          </div>
        )}

        {/* Tab 3: Policy */}
        {activeTab === 'policy' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '14px', fontSize: '11.5px' }}>
            <div>
              <span style={{ color: '#73849c', fontWeight: '600' }}>Activated Policy Rules</span>
              <div style={{ marginTop: '8px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
                {(policyDecision.rulesActivated || ['EgressPolicy', 'PathContainment']).map((rule, idx) => (
                  <div
                    key={idx}
                    style={{
                      padding: '6px 10px',
                      borderRadius: '6px',
                      backgroundColor: '#111722',
                      border: '1px solid #1e2a3c',
                      color: '#cbd5e1',
                    }}
                  >
                    🛡️ {rule}
                  </div>
                ))}
              </div>
            </div>

            <div>
              <span style={{ color: '#73849c', fontWeight: '600' }}>Precedence Chain</span>
              <p style={{ marginTop: '6px', color: '#94a3b8', lineHeight: '1.5' }}>
                {policyDecision.precedence || 'Deterministic Safety Precedence (Hard Block > Human Gate > Auto Allow)'}
              </p>
            </div>
          </div>
        )}

        {/* Tab 4: Receipt */}
        {activeTab === 'receipt' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', fontSize: '11px' }}>
            <div style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '6px 10px',
              borderRadius: '6px',
              backgroundColor: 'rgba(16, 185, 129, 0.12)',
              border: '1px solid rgba(16, 185, 129, 0.3)',
              color: '#34d399',
              fontWeight: '600',
            }}>
              <ShieldCheck size={14} />
              <span>HMAC Signed & Verified</span>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
              <span style={{ color: '#64748b' }}>Decision ID</span>
              <span style={{ fontFamily: 'var(--font-mono)', color: '#cbd5e1' }}>
                {receiptTab.decisionId || decisionId}
              </span>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
              <span style={{ color: '#64748b' }}>Action Hash</span>
              <span style={{
                fontFamily: 'var(--font-mono)',
                color: '#cbd5e1',
                wordBreak: 'break-all',
              }}>
                {receiptTab.actionHash || 'sha256:7e9b04fc41a7d6568297b83321588632a488c0352ef2bc560ec0a8c27e852d43'}
              </span>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
              <span style={{ color: '#64748b' }}>State Hash</span>
              <span style={{
                fontFamily: 'var(--font-mono)',
                color: '#cbd5e1',
                wordBreak: 'break-all',
              }}>
                {receiptTab.stateHash || 'sha256:4b81c201a096180373ad412e8473e6a71e8bfb510ca1c1696a60db9372179b02'}
              </span>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
              <span style={{ color: '#64748b' }}>Nonce</span>
              <span style={{ fontFamily: 'var(--font-mono)', color: '#cbd5e1' }}>
                {receiptTab.nonce || 'non_89a01f7c11'}
              </span>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
              <span style={{ color: '#64748b' }}>Cryptographic Signature</span>
              <span style={{
                fontFamily: 'var(--font-mono)',
                color: '#94a3b8',
                wordBreak: 'break-all',
              }}>
                {receiptTab.signature || 'hmac-sha256:39a7b212f008cb042aaefc32986423a884efbb5c'}
              </span>
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}
