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
      backgroundColor: '#0d131d',
      borderBottom: '1px solid #1a2333',
    }}>
      {/* Session Title & Subtitle */}
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <h1 style={{
            fontSize: '17px',
            fontWeight: '700',
            color: '#f8fafc',
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
            backgroundColor: 'rgba(16, 185, 129, 0.15)',
            color: '#34d399',
            border: '1px solid rgba(16, 185, 129, 0.3)',
          }}>
            {status}
          </span>
        </div>
        <p style={{
          fontSize: '12px',
          color: '#8292a8',
          marginTop: '4px',
        }}>
          <span style={{ color: '#94a3b8', fontWeight: '500' }}>{agent}</span>
          <span style={{ margin: '0 7px', color: '#475569' }}>·</span>
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
          backgroundColor: '#121926',
          border: '1px solid #1e2a3c',
          minWidth: '130px',
        }}>
          <div style={{
            width: '28px',
            height: '28px',
            borderRadius: '6px',
            backgroundColor: '#1a2436',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#94a3b8',
          }}>
            <Layers size={14} />
          </div>
          <div>
            <div style={{ fontSize: '15px', fontWeight: '700', color: '#f8fafc', lineHeight: 1.1 }}>
              {metrics.totalDecisions}
            </div>
            <div style={{ fontSize: '10.5px', color: '#73849c', marginTop: '2px' }}>
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
          backgroundColor: '#121926',
          border: '1px solid #1e2a3c',
          minWidth: '115px',
        }}>
          <div style={{
            width: '28px',
            height: '28px',
            borderRadius: '6px',
            backgroundColor: 'rgba(16, 185, 129, 0.15)',
            border: '1px solid rgba(16, 185, 129, 0.3)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#34d399',
          }}>
            <Check size={14} strokeWidth={2.5} />
          </div>
          <div>
            <div style={{ fontSize: '15px', fontWeight: '700', color: '#f8fafc', lineHeight: 1.1 }}>
              {metrics.allowed}
            </div>
            <div style={{ fontSize: '10.5px', color: '#73849c', marginTop: '2px' }}>
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
          backgroundColor: '#121926',
          border: '1px solid #1e2a3c',
          minWidth: '115px',
        }}>
          <div style={{
            width: '28px',
            height: '28px',
            borderRadius: '6px',
            backgroundColor: 'rgba(239, 68, 68, 0.15)',
            border: '1px solid rgba(239, 68, 68, 0.3)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#f87171',
          }}>
            <X size={14} strokeWidth={2.5} />
          </div>
          <div>
            <div style={{ fontSize: '15px', fontWeight: '700', color: '#f8fafc', lineHeight: 1.1 }}>
              {metrics.blocked}
            </div>
            <div style={{ fontSize: '10.5px', color: '#73849c', marginTop: '2px' }}>
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
          backgroundColor: '#121926',
          border: '1px solid #1e2a3c',
          minWidth: '115px',
        }}>
          <div style={{
            width: '28px',
            height: '28px',
            borderRadius: '6px',
            backgroundColor: 'rgba(245, 158, 11, 0.15)',
            border: '1px solid rgba(245, 158, 11, 0.3)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#fbbf24',
          }}>
            <AlertTriangle size={14} strokeWidth={2.3} />
          </div>
          <div>
            <div style={{ fontSize: '15px', fontWeight: '700', color: '#f8fafc', lineHeight: 1.1 }}>
              {metrics.review}
            </div>
            <div style={{ fontSize: '10.5px', color: '#73849c', marginTop: '2px' }}>
              Review
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
