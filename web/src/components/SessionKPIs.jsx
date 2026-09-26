import React from 'react';
import { Layers, Check, X, AlertTriangle } from 'lucide-react';

export default function SessionKPIs({ session }) {
  const {
    sessionId = '7f3a2c',
    status = 'Active',
    agent = 'CodingAgent',
    goal = 'Fix authentication bug in the API',
    metrics = {
      totalDecisions: 12,
      allowed: 8,
      blocked: 3,
      review: 1,
    },
  } = session || {};

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      padding: '16px 20px',
      backgroundColor: '#0d121c',
      borderBottom: '1px solid #1a202c',
    }}>
      {/* Session Title & Subtitle */}
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <h1 style={{
            fontSize: '17px',
            fontWeight: '700',
            color: '#f0f6fc',
            fontFamily: 'var(--font-sans)',
            letterSpacing: '-0.01em',
          }}>
            Session #{sessionId}
          </h1>
          <span style={{
            fontSize: '11px',
            fontWeight: '600',
            padding: '2px 8px',
            borderRadius: '9999px',
            backgroundColor: 'rgba(63, 185, 80, 0.12)',
            color: '#3fb950',
            border: '1px solid rgba(63, 185, 80, 0.28)',
          }}>
            {status}
          </span>
        </div>
        <p style={{
          fontSize: '12px',
          color: '#8b949e',
          marginTop: '4px',
        }}>
          <span style={{ color: '#c9d1d9', fontWeight: '500' }}>{agent}</span>
          <span style={{ margin: '0 7px', color: '#484f58' }}>·</span>
          <span>{goal}</span>
        </p>
      </div>

      {/* 4 Summary KPI Cards */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
        {/* Total Decisions */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '12px',
          padding: '8px 14px',
          borderRadius: '8px',
          backgroundColor: '#141924',
          border: '1px solid #1e2636',
          boxShadow: 'var(--shadow-clay-sm)',
          minWidth: '130px',
        }}>
          <div style={{
            width: '28px',
            height: '28px',
            borderRadius: '6px',
            backgroundColor: '#1a2230',
            border: '1px solid #283244',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#8b949e',
          }}>
            <Layers size={14} />
          </div>
          <div>
            <div style={{ fontSize: '15px', fontWeight: '700', color: '#f0f6fc', lineHeight: 1.1 }}>
              {metrics.totalDecisions}
            </div>
            <div style={{ fontSize: '10.5px', color: '#8b949e', marginTop: '2px' }}>
              Total decisions
            </div>
          </div>
        </div>

        {/* Allowed */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '12px',
          padding: '8px 14px',
          borderRadius: '8px',
          backgroundColor: '#141924',
          border: '1px solid #1e2636',
          boxShadow: 'var(--shadow-clay-sm)',
          minWidth: '115px',
        }}>
          <div style={{
            width: '28px',
            height: '28px',
            borderRadius: '6px',
            backgroundColor: '#13231b',
            border: '1px solid #1e3a2b',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#3fb950',
          }}>
            <Check size={14} strokeWidth={2.5} />
          </div>
          <div>
            <div style={{ fontSize: '15px', fontWeight: '700', color: '#f0f6fc', lineHeight: 1.1 }}>
              {metrics.allowed}
            </div>
            <div style={{ fontSize: '10.5px', color: '#8b949e', marginTop: '2px' }}>
              Allowed
            </div>
          </div>
        </div>

        {/* Blocked */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '12px',
          padding: '8px 14px',
          borderRadius: '8px',
          backgroundColor: '#141924',
          border: '1px solid #1e2636',
          boxShadow: 'var(--shadow-clay-sm)',
          minWidth: '115px',
        }}>
          <div style={{
            width: '28px',
            height: '28px',
            borderRadius: '6px',
            backgroundColor: '#291418',
            border: '1px solid #441e25',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#f85149',
          }}>
            <X size={14} strokeWidth={2.5} />
          </div>
          <div>
            <div style={{ fontSize: '15px', fontWeight: '700', color: '#f0f6fc', lineHeight: 1.1 }}>
              {metrics.blocked}
            </div>
            <div style={{ fontSize: '10.5px', color: '#8b949e', marginTop: '2px' }}>
              Blocked
            </div>
          </div>
        </div>

        {/* Review */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '12px',
          padding: '8px 14px',
          borderRadius: '8px',
          backgroundColor: '#141924',
          border: '1px solid #1e2636',
          boxShadow: 'var(--shadow-clay-sm)',
          minWidth: '115px',
        }}>
          <div style={{
            width: '28px',
            height: '28px',
            borderRadius: '6px',
            backgroundColor: '#261d11',
            border: '1px solid #3f2f1a',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#d29922',
          }}>
            <AlertTriangle size={14} strokeWidth={2.3} />
          </div>
          <div>
            <div style={{ fontSize: '15px', fontWeight: '700', color: '#f0f6fc', lineHeight: 1.1 }}>
              {metrics.review}
            </div>
            <div style={{ fontSize: '10.5px', color: '#8b949e', marginTop: '2px' }}>
              Review
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

