import React from 'react';
import { Layers, Check, X, AlertTriangle, Zap } from 'lucide-react';

export default function SessionKPIs({ session }) {
  const {
    sessionId = '7f3a2c',
    status = 'Active',
    agent = 'CodingAgent',
    goal = 'Fix authentication bug in the API',
    metrics = {
      totalDecisions: 0,
      allowed: 0,
      blocked: 0,
      review: 0,
    },
  } = session || {};

  const isReady = status.toLowerCase() === 'ready';
  const isActive = status.toLowerCase().includes('active');
  const isCompleted = status.toLowerCase().includes('completed');
  const isReview = status.toLowerCase().includes('review');

  const getStatusBadgeStyle = () => {
    if (isReady) {
      return {
        bg: 'rgba(56, 189, 248, 0.12)',
        color: '#38bdf8',
        border: '1px solid rgba(56, 189, 248, 0.3)',
      };
    }
    if (isActive) {
      return {
        bg: 'rgba(63, 185, 80, 0.14)',
        color: '#3fb950',
        border: '1px solid rgba(63, 185, 80, 0.32)',
      };
    }
    if (isReview) {
      return {
        bg: 'rgba(210, 153, 34, 0.14)',
        color: '#d29922',
        border: '1px solid rgba(210, 153, 34, 0.32)',
      };
    }
    if (isCompleted) {
      return {
        bg: 'rgba(88, 166, 255, 0.14)',
        color: '#58a6ff',
        border: '1px solid rgba(88, 166, 255, 0.32)',
      };
    }
    return {
      bg: 'rgba(139, 148, 158, 0.14)',
      color: '#8b949e',
      border: '1px solid rgba(139, 148, 158, 0.25)',
    };
  };

  const badgeStyle = getStatusBadgeStyle();

  return (
    <div
      style={{
        height: '34px',
        minHeight: '34px',
        maxHeight: '34px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '0 14px',
        backgroundColor: '#0a0d14',
        borderBottom: '1px solid #1a202c',
        userSelect: 'none',
        flexShrink: 0,
        gap: '12px',
      }}
    >
      {/* Left: Session ID, Status Badge & Goal Context */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          minWidth: 0,
          overflow: 'hidden',
        }}
      >
        <span
          style={{
            fontSize: '12px',
            fontWeight: '700',
            color: '#f0f6fc',
            fontFamily: 'var(--font-mono, monospace)',
            whiteSpace: 'nowrap',
          }}
        >
          Session #{sessionId}
        </span>

        <span
          style={{
            fontSize: '10px',
            fontWeight: '600',
            padding: '1px 6px',
            borderRadius: '9999px',
            backgroundColor: badgeStyle.bg,
            color: badgeStyle.color,
            border: badgeStyle.border,
            whiteSpace: 'nowrap',
            display: 'inline-flex',
            alignItems: 'center',
            gap: '4px',
          }}
        >
          {isActive && (
            <span
              style={{
                width: '5px',
                height: '5px',
                borderRadius: '50%',
                backgroundColor: '#3fb950',
                display: 'inline-block',
                boxShadow: '0 0 6px #3fb950',
              }}
            />
          )}
          {status}
        </span>

        <span style={{ color: '#30363d', fontSize: '12px' }}>|</span>

        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            fontSize: '11.5px',
            color: '#8b949e',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          <span style={{ color: '#c9d1d9', fontWeight: '500', whiteSpace: 'nowrap' }}>
            {agent}
          </span>
          <span style={{ color: '#484f58' }}>·</span>
          <span
            style={{
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
            title={goal}
          >
            {goal}
          </span>
        </div>
      </div>

      {/* Right: Compact Inline Decision Metric Chips */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          flexShrink: 0,
        }}
      >
        {/* Total Decisions */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '5px',
            height: '22px',
            padding: '0 7px',
            borderRadius: '4px',
            backgroundColor: 'rgba(255, 255, 255, 0.03)',
            border: '1px solid #21262d',
            fontSize: '11px',
          }}
          title="Total de decisiones evaluadas"
        >
          <Layers size={11} style={{ color: '#8b949e' }} />
          <span style={{ color: '#8b949e', fontSize: '10.5px' }}>Total:</span>
          <strong style={{ color: '#f0f6fc', fontWeight: '700', fontFamily: 'var(--font-mono)' }}>
            {metrics.totalDecisions}
          </strong>
        </div>

        {/* Allowed */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '5px',
            height: '22px',
            padding: '0 7px',
            borderRadius: '4px',
            backgroundColor: 'rgba(63, 185, 80, 0.08)',
            border: '1px solid rgba(63, 185, 80, 0.25)',
            fontSize: '11px',
          }}
          title="Decisiones permitidas por política"
        >
          <Check size={11} strokeWidth={2.5} style={{ color: '#3fb950' }} />
          <span style={{ color: '#8b949e', fontSize: '10.5px' }}>Allowed:</span>
          <strong style={{ color: '#3fb950', fontWeight: '700', fontFamily: 'var(--font-mono)' }}>
            {metrics.allowed}
          </strong>
        </div>

        {/* Blocked */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '5px',
            height: '22px',
            padding: '0 7px',
            borderRadius: '4px',
            backgroundColor: 'rgba(248, 81, 73, 0.08)',
            border: '1px solid rgba(248, 81, 73, 0.25)',
            fontSize: '11px',
          }}
          title="Decisiones bloqueadas o prevenidas"
        >
          <X size={11} strokeWidth={2.5} style={{ color: '#f85149' }} />
          <span style={{ color: '#8b949e', fontSize: '10.5px' }}>Blocked:</span>
          <strong style={{ color: '#f85149', fontWeight: '700', fontFamily: 'var(--font-mono)' }}>
            {metrics.blocked}
          </strong>
        </div>

        {/* Review */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '5px',
            height: '22px',
            padding: '0 7px',
            borderRadius: '4px',
            backgroundColor: 'rgba(210, 153, 34, 0.08)',
            border: '1px solid rgba(210, 153, 34, 0.25)',
            fontSize: '11px',
          }}
          title="Decisiones requiriendo confirmación del operador"
        >
          <AlertTriangle size={11} strokeWidth={2.2} style={{ color: '#d29922' }} />
          <span style={{ color: '#8b949e', fontSize: '10.5px' }}>Review:</span>
          <strong style={{ color: '#d29922', fontWeight: '700', fontFamily: 'var(--font-mono)' }}>
            {metrics.review}
          </strong>
        </div>

        {/* Context Caching */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '5px',
            height: '22px',
            padding: '0 7px',
            borderRadius: '4px',
            backgroundColor: 'rgba(56, 189, 248, 0.08)',
            border: '1px solid rgba(56, 189, 248, 0.25)',
            fontSize: '11px',
          }}
          title={
            session?.contextMetrics?.actual_billed_tokens != null && session?.contextMetrics?.actual_billed_tokens > 0
              ? `Estimado ahorrado: ${session?.contextMetrics?.estimated_context_tokens_saved ?? session?.contextMetrics?.context_tokens_saved ?? 0} tok | Facturado real: ${session.contextMetrics.actual_billed_tokens} tok (Caché LLM: ${session.contextMetrics.actual_cached_tokens ?? 0} tok)`
              : "Optimización de tokens y Context Caching L1/L2 activo"
          }
        >
          <Zap size={11} style={{ color: '#38bdf8' }} />
          <span style={{ color: '#8b949e', fontSize: '10.5px' }}>Context Cache:</span>
          <strong style={{ color: '#38bdf8', fontWeight: '700', fontFamily: 'var(--font-mono)' }}>
            {(session?.contextMetrics?.estimated_context_tokens_saved ?? session?.contextMetrics?.context_tokens_saved) != null
              ? `${session.contextMetrics.estimated_context_tokens_saved ?? session.contextMetrics.context_tokens_saved} tok`
              : 'Active'}
          </strong>
          {session?.contextMetrics?.actual_billed_tokens > 0 && (
            <span style={{ color: '#a3e635', fontSize: '10px', marginLeft: '2px', borderLeft: '1px solid rgba(255,255,255,0.15)', paddingLeft: '4px' }}>
              {session.contextMetrics.actual_billed_tokens} billed
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

