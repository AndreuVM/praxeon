import React, { useState } from 'react';
import { Cpu, Clock, Terminal, Activity } from 'lucide-react';

export default function ConsolePanel({
  logs = [],
  events = [],
  runtime = {
    provider: 'JEV + LAYA',
    version: 'v1.0.0',
    latencyP50: '93ms',
    executionTime: '—',
  },
}) {
  const [activeTab, setActiveTab] = useState('terminal');

  return (
    <div
      style={{
        height: '185px',
        backgroundColor: '#0c1017',
        borderTop: '1px solid #1a2333',
        display: 'flex',
        overflow: 'hidden',
        flexShrink: 0,
      }}
    >
      {/* Left Log / Event Area */}
      <div
        style={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          borderRight: '1px solid #1a2333',
          overflow: 'hidden',
        }}
      >
        {/* Tabs Bar */}
        <div
          style={{
            height: '32px',
            backgroundColor: '#0c0f14',
            borderBottom: '1px solid #1a202c',
            display: 'flex',
            alignItems: 'center',
            padding: '0 16px',
            gap: '16px',
          }}
        >
          <button
            onClick={() => setActiveTab('terminal')}
            style={{
              background: 'none',
              border: 'none',
              padding: '6px 0',
              color: activeTab === 'terminal' ? '#f0f6fc' : '#8b949e',
              fontSize: '11.5px',
              fontWeight: activeTab === 'terminal' ? '600' : '400',
              cursor: 'pointer',
              borderBottom: activeTab === 'terminal' ? '2px solid #f0f6fc' : '2px solid transparent',
              transition: 'all 0.15s ease',
            }}
          >
            Terminal
          </button>
          <button
            onClick={() => setActiveTab('events')}
            style={{
              background: 'none',
              border: 'none',
              padding: '6px 0',
              color: activeTab === 'events' ? '#f0f6fc' : '#8b949e',
              fontSize: '11.5px',
              fontWeight: activeTab === 'events' ? '600' : '400',
              cursor: 'pointer',
              borderBottom: activeTab === 'events' ? '2px solid #f0f6fc' : '2px solid transparent',
              transition: 'all 0.15s ease',
            }}
          >
            Events ({events.length})
          </button>
        </div>

        {/* Content Stream */}
        <div
          style={{
            flex: 1,
            overflowY: 'auto',
            padding: '10px 16px',
            fontFamily: 'var(--font-mono)',
            fontSize: '11.5px',
            lineHeight: '1.7',
            backgroundColor: '#0a0e16',
          }}
        >
          {activeTab === 'terminal' ? (
            <div>
              {logs.map((log, idx) => {
                const isWarn = log.level === 'WARN';
                const isError = log.level === 'ERROR';
                return (
                  <div key={idx} style={{ display: 'flex', gap: '10px', whiteSpace: 'pre-wrap' }}>
                    <span style={{ color: '#6e7681', userSelect: 'none' }}>{log.time}</span>
                    <span
                      style={{
                        color: isWarn ? '#d29922' : isError ? '#f85149' : '#8b949e',
                        fontWeight: '600',
                      }}
                    >
                      [{log.level}]
                    </span>
                    <span style={{ color: isWarn ? '#e3b341' : isError ? '#ffa198' : '#c9d1d9' }}>
                      {log.message.includes('ALLOW') ? (
                        <>
                          {log.message.split('ALLOW')[0]}
                          <span style={{ color: '#3fb950', fontWeight: '700' }}>ALLOW</span>
                          {log.message.split('ALLOW')[1]}
                        </>
                      ) : log.message.includes('REVIEW') ? (
                        <>
                          {log.message.split('REVIEW')[0]}
                          <span style={{ color: '#d29922', fontWeight: '700' }}>REVIEW</span>
                          {log.message.split('REVIEW')[1]}
                        </>
                      ) : log.message.includes('BLOCK') ? (
                        <>
                          {log.message.split('BLOCK')[0]}
                          <span style={{ color: '#f85149', fontWeight: '700' }}>BLOCK</span>
                          {log.message.split('BLOCK')[1]}
                        </>
                      ) : (
                        log.message
                      )}
                    </span>
                  </div>
                );
              })}
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
              {events.map((ev, idx) => (
                <div
                  key={idx}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '10px',
                    padding: '4px 8px',
                    borderRadius: '4px',
                    backgroundColor: '#131822',
                    border: '1px solid #1a202c',
                  }}
                >
                  <span style={{ color: '#6e7681' }}>#{ev.seq || idx + 1}</span>
                  <span style={{ color: '#8b949e' }}>{ev.time}</span>
                  <span
                    style={{
                      padding: '1px 6px',
                      borderRadius: '4px',
                      backgroundColor: 'rgba(240, 246, 252, 0.08)',
                      color: '#c9d1d9',
                      border: '1px solid #283244',
                      fontSize: '10.5px',
                    }}
                  >
                    {ev.type}
                  </span>
                  <span style={{ color: '#f0f6fc', flex: 1 }}>{ev.detail}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Right Metadata Overview Card */}
      <div
        style={{
          width: '260px',
          padding: '16px 20px',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'center',
          gap: '11px',
          backgroundColor: '#0c0f14',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#8b949e', fontSize: '11.5px' }}>
            <Cpu size={14} />
            <span>LLM Provider</span>
          </div>
          <span style={{ fontSize: '11.5px', fontWeight: '600', color: '#f0f6fc', fontFamily: 'var(--font-mono)' }}>
            {runtime.provider}
          </span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#8b949e', fontSize: '11.5px' }}>
            <Terminal size={14} />
            <span>Runtime</span>
          </div>
          <span style={{ fontSize: '11.5px', fontWeight: '600', color: '#f0f6fc', fontFamily: 'var(--font-mono)' }}>
            {runtime.version}
          </span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#8b949e', fontSize: '11.5px' }}>
            <Activity size={14} />
            <span>Latency (p50)</span>
          </div>
          <span style={{ fontSize: '11.5px', fontWeight: '600', color: '#f0f6fc', fontFamily: 'var(--font-mono)' }}>
            {runtime.latencyP50}
          </span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#8b949e', fontSize: '11.5px' }}>
            <Clock size={14} />
            <span>Execution Time</span>
          </div>
          <span style={{ fontSize: '11.5px', fontWeight: '600', color: '#f0f6fc', fontFamily: 'var(--font-mono)' }}>
            {runtime.executionTime}
          </span>
        </div>
      </div>
    </div>
  );
}
