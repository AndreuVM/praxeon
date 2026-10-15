import React, { useState } from 'react';
import { Check, Copy, Maximize2, Minimize2 } from 'lucide-react';

const VERDICTS = {
  ALLOW: { label: 'Permitido', color: 'var(--ok)' },
  REVIEW: { label: 'Requiere revisión', color: 'var(--accent)' },
  REPLAN: { label: 'Replanificado (poda)', color: 'var(--muted)' },
  BLOCK: { label: 'Bloqueado', color: 'var(--bad)' },
  BLOCKED: { label: 'Bloqueado', color: 'var(--bad)' },
  PENDING: { label: 'Evaluando', color: 'var(--accent)' },
};

function Kv({ label, children, mono }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: '16px', padding: '7px 0', borderBottom: '1px solid var(--line)' }}>
      <span style={{ color: 'var(--muted)', flexShrink: 0 }}>{label}</span>
      <span style={{ textAlign: 'right', minWidth: 0, overflowWrap: 'anywhere', fontFamily: mono ? 'var(--font-mono)' : undefined }}>{children}</span>
    </div>
  );
}

function CopyText({ label, value, mono = true }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    try {
      navigator.clipboard?.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      /* sin portapapeles disponible */
    }
  };
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '3px', padding: '7px 0', borderBottom: '1px solid var(--line)' }}>
      <span style={{ color: 'var(--muted)' }}>{label}</span>
      <span style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0 }}>
        <span
          title={value}
          style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontFamily: mono ? 'var(--font-mono)' : undefined, fontSize: mono ? 'var(--fs-code)' : undefined, userSelect: 'text' }}
        >
          {value}
        </span>
        <button
          type="button"
          onClick={copy}
          className="btn-icon"
          style={{ width: '24px', height: '24px', flexShrink: 0 }}
          title={`Copiar ${label.toLowerCase()} completo`}
          aria-label={`Copiar ${label.toLowerCase()}`}
        >
          {copied ? <Check size={12} /> : <Copy size={12} />}
        </button>
      </span>
    </div>
  );
}

const textLink = { background: 'none', border: 0, padding: 0, color: 'var(--muted)', fontSize: 'var(--fs-secondary)', display: 'inline-flex', alignItems: 'center', gap: '4px' };

/**
 * Detalle de un paso (pestaña «Paso» del cajón). Primero lo que decide: veredicto, acción, motivo y valores clave.
 * Evidencia, política y recibo quedan plegados (RD-D1).
 */
export default function StepDetail({ decision = null, session = {}, title = '', onApprove, onReject, onExecute, nodeId = null }) {
  const [copied, setCopied] = useState(false);
  const [actionWrapped, setActionWrapped] = useState(true);
  const [actionExpanded, setActionExpanded] = useState(false);
  const [obsExpanded, setObsExpanded] = useState(false);
  const [copiedObs, setCopiedObs] = useState(false);
  const [isConfirming, setIsConfirming] = useState(false);
  const [isRejecting, setIsRejecting] = useState(false);
  const [rejectionReason, setRejectionReason] = useState('');
  const [rejectError, setRejectError] = useState('');

  if (!decision) {
    return (
      <p style={{ color: 'var(--muted)', fontSize: 'var(--fs-ui)' }}>
        Elige un paso de la conversación o del mapa para ver su decisión, su evidencia y su recibo firmado.
      </p>
    );
  }

  const {
    decisionId = 'd_desconocido',
    sequence = 1,
    status = 'PENDING',
    actionCommand = '',
    tool = '—',
    provider = '—',
    model: rawModel = null,
    riskLevel = 'LOW',
    semanticEvaluation = [],
    policyDecision = {},
    capability = { issued: false, statusText: 'No emitida', token: null },
    reason = '',
    evidenceTab = {},
    receiptTab = {},
    relatedDecisions = [],
    observationOutput = null,
  } = decision;

  const rawDecisionTab = decision.decisionTab || decision.decision_tab || {};
  const opAssessment = rawDecisionTab.operation_assessment || decision.operation_assessment || decision.operationAssessment || null;
  const opCategory = (rawDecisionTab.operation_category || decision.operation_category || opAssessment?.category || 'inspection').toLowerCase();
  const executionMode = rawDecisionTab.execution_mode || decision.execution_mode || decision.executionMode || session?.execution_mode || 'local_restricted';
  const isolation = rawDecisionTab.isolation || decision.isolation || (executionMode === 'full_access' ? 'Ninguno (SO anfitrión)' : 'Activo');
  const workingDirectory =
    rawDecisionTab.working_directory || decision.working_directory || decision.workingDirectory || session?.workspace_root || session?.working_directory ||
    (executionMode === 'full_access' ? '.' : '/workspace');
  const networkMode = rawDecisionTab.network_mode || decision.network_mode || (executionMode === 'full_access' ? 'Directa al host' : 'Aislada (restringida)');
  const executionBackend =
    rawDecisionTab.execution_backend || decision.execution_backend || decision.executionBackend ||
    (executionMode === 'full_access' ? 'FullAccessExecutor' : executionMode === 'container' ? 'DockerContainer' : 'LocalProcessSandbox');
  const effectiveModel =
    rawDecisionTab.model || rawModel || decision.model || session?.decision_model_effective?.model_id || session?.decisionModel?.model_id || session?.llm_model ||
    (session?.runtime?.provider ? session.runtime.provider.split('+')[0].trim() : null) || 'Automático';
  const effectiveProvider =
    rawDecisionTab.provider || decision.provider || session?.decision_model_effective?.provider || session?.decisionModel?.provider || provider;

  const verdict = VERDICTS[String(status).toUpperCase()] || VERDICTS.PENDING;
  const riskColor = riskLevel === 'HIGH' || riskLevel === 'CRITICAL' ? 'var(--bad)' : riskLevel === 'MEDIUM' ? 'var(--accent)' : 'var(--ok)';
  const riskLabel = { LOW: 'Bajo', MEDIUM: 'Medio', HIGH: 'Alto', CRITICAL: 'Crítico' }[riskLevel] || riskLevel;
  const grounding = Math.round((evidenceTab.groundingScore ?? 0.85) * 100);
  const claims = evidenceTab.claims || [];
  const requiresConfirmation = Boolean(policyDecision.requiresConfirmation);
  const yesNo = (v) => (v ? 'Sí' : 'No');

  const copyText = (text, setter) => {
    try {
      navigator.clipboard?.writeText(text);
      setter(true);
      setTimeout(() => setter(false), 1800);
    } catch {
      /* sin portapapeles disponible */
    }
  };

  const confirmReject = () => {
    if (!rejectionReason.trim()) {
      setRejectError('Escribe el motivo del rechazo para continuar.');
      return;
    }
    onReject?.(decisionId, rejectionReason.trim(), nodeId);
    setIsRejecting(false);
    setIsConfirming(false);
    setRejectionReason('');
    setRejectError('');
  };

  const sectionStyle = { borderBottom: '1px solid var(--line)' };
  const summaryStyle = { listStyle: 'none', cursor: 'pointer', display: 'flex', justifyContent: 'space-between', padding: '11px 0', color: 'var(--muted)' };
  const subTitle = { fontSize: 'var(--fs-label)', letterSpacing: '0.07em', textTransform: 'uppercase', color: 'var(--muted)', margin: '14px 0 2px' };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
        <h3 style={{ fontSize: 'var(--fs-chat)', fontWeight: 600 }}>{title || `Paso ${sequence}`}</h3>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: '12px' }}>
          <span style={{ fontSize: '17px', fontWeight: 600, color: verdict.color }}>{verdict.label}</span>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-code)', color: 'var(--muted)' }}>#{sequence}</span>
        </div>
      </div>

      {/* Acción propuesta */}
      <div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', marginBottom: '6px', flexWrap: 'wrap' }}>
          <span style={{ color: 'var(--muted)' }}>
            Acción{actionCommand ? <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-label)' }}> · {actionCommand.length} caracteres</span> : null}
          </span>
          <span style={{ display: 'inline-flex', gap: '12px' }}>
            <button type="button" onClick={() => setActionWrapped(!actionWrapped)} style={textLink} title={actionWrapped ? 'Mostrar en una línea con scroll horizontal' : 'Ajustar el texto al ancho'}>
              {actionWrapped ? 'Una línea' : 'Ajustar'}
            </button>
            <button type="button" onClick={() => setActionExpanded(!actionExpanded)} style={textLink}>
              {actionExpanded ? <Minimize2 size={11} /> : <Maximize2 size={11} />}
              {actionExpanded ? 'Reducir' : 'Expandir'}
            </button>
            <button type="button" onClick={() => copyText(actionCommand, setCopied)} style={textLink} title="Copiar el comando completo">
              {copied ? <Check size={11} /> : <Copy size={11} />}
              {copied ? 'Copiado' : 'Copiar'}
            </button>
          </span>
        </div>
        <div
          style={{
            background: 'var(--surface)',
            border: '1px solid var(--line)',
            borderRadius: 'var(--r-ui)',
            padding: '9px 11px',
            maxHeight: actionExpanded ? '380px' : '150px',
            overflow: 'auto',
            fontFamily: 'var(--font-mono)',
            fontSize: 'var(--fs-code)',
            whiteSpace: actionWrapped ? 'pre-wrap' : 'pre',
            overflowWrap: actionWrapped ? 'anywhere' : 'normal',
            userSelect: 'text',
          }}
        >
          {actionCommand || '(comando vacío)'}
        </div>
      </div>

      {reason && <p style={{ color: 'var(--muted)', lineHeight: 1.5 }}>{reason}</p>}

      <div>
        <Kv label="Herramienta" mono>{tool}</Kv>
        <Kv label="Proveedor">{effectiveProvider}</Kv>
        <Kv label="Modelo">{effectiveModel}</Kv>
        <Kv label="Riesgo">
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: '7px', color: riskColor }}>
            <span style={{ width: '7px', height: '7px', borderRadius: '50%', background: riskColor }} />
            {riskLabel}
            {decision.riskScore !== undefined && <span style={{ fontFamily: 'var(--font-mono)' }}> · {Number(decision.riskScore).toFixed(2)}</span>}
          </span>
        </Kv>
        {semanticEvaluation.map((ev, idx) => (
          <Kv key={idx} label={ev.provider}>
            <span style={{ fontFamily: 'var(--font-mono)' }}>{ev.score}</span>
            <span style={{ marginLeft: '8px', color: ev.verdict === 'ALLOW' ? 'var(--ok)' : ev.verdict === 'BLOCK' ? 'var(--bad)' : 'var(--accent)' }}>
              {VERDICTS[ev.verdict]?.label || ev.verdict}
            </span>
          </Kv>
        ))}
      </div>

      {/* Observación / respuesta final */}
      {observationOutput && (
        <div>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', marginBottom: '6px', flexWrap: 'wrap' }}>
            <span style={{ color: 'var(--muted)' }}>
              {tool === 'finish' ? 'Respuesta final de la misión' : 'Observación de la ejecución'}
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-label)' }}>
                {' · '}
                {observationOutput.length >= 1024 ? `${(observationOutput.length / 1024).toFixed(1)} KB` : `${observationOutput.length} B`}
                {' · '}
                {observationOutput.split('\n').length} líneas
              </span>
            </span>
            <span style={{ display: 'inline-flex', gap: '12px' }}>
              <button type="button" onClick={() => copyText(observationOutput, setCopiedObs)} style={textLink}>
                {copiedObs ? <Check size={11} /> : <Copy size={11} />}
                {copiedObs ? 'Copiado' : 'Copiar'}
              </button>
              <button type="button" onClick={() => setObsExpanded(!obsExpanded)} style={textLink}>
                {obsExpanded ? <Minimize2 size={11} /> : <Maximize2 size={11} />}
                {obsExpanded ? 'Compactar' : 'Ampliar'}
              </button>
            </span>
          </div>
          <div
            style={{
              padding: '9px 11px',
              background: 'var(--surface)',
              border: '1px solid var(--line)',
              borderRadius: 'var(--r-ui)',
              fontFamily: tool === 'finish' ? 'inherit' : 'var(--font-mono)',
              fontSize: tool === 'finish' ? 'var(--fs-ui)' : 'var(--fs-code)',
              maxHeight: obsExpanded ? '580px' : '260px',
              overflowY: 'auto',
              whiteSpace: 'pre-wrap',
              overflowWrap: 'anywhere',
              lineHeight: 1.45,
              userSelect: 'text',
            }}
          >
            {observationOutput}
          </div>
        </div>
      )}

      {/* Acciones del operador */}
      <div>
        {capability.issued ? (
          <button type="button" onClick={() => onExecute?.(decisionId, capability)} className="btn btn-primary" style={{ width: '100%' }}>
            Ejecutar en el sandbox
          </button>
        ) : isRejecting ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            <label htmlFor="step-reject-reason" style={{ color: 'var(--muted)', fontSize: 'var(--fs-secondary)' }}>Motivo del rechazo (obligatorio)</label>
            <input
              id="step-reject-reason"
              type="text"
              value={rejectionReason}
              onChange={(e) => { setRejectionReason(e.target.value); setRejectError(''); }}
              onKeyDown={(e) => { if (e.key === 'Enter') confirmReject(); }}
              placeholder="Por qué no debe ejecutarse"
              style={{ borderColor: rejectError ? 'var(--bad)' : undefined }}
            />
            {rejectError && <div style={{ color: 'var(--bad)', fontSize: 'var(--fs-secondary)' }}>{rejectError}</div>}
            <div style={{ display: 'flex', gap: '8px' }}>
              <button type="button" onClick={confirmReject} className="btn btn-danger" style={{ flex: 1 }}>Confirmar rechazo</button>
              <button type="button" onClick={() => { setIsRejecting(false); setRejectError(''); }} className="btn btn-secondary">Cancelar</button>
            </div>
          </div>
        ) : isConfirming ? (
          <div style={{ display: 'flex', gap: '8px' }}>
            <button type="button" onClick={() => { onApprove?.(decisionId, nodeId); setIsConfirming(false); }} className="btn btn-primary" style={{ flex: 1 }}>
              Aprobar
            </button>
            <button type="button" onClick={() => setIsRejecting(true)} className="btn btn-secondary" style={{ flex: 1 }}>
              Rechazar…
            </button>
          </div>
        ) : (
          <button type="button" onClick={() => setIsConfirming(true)} className="btn btn-secondary" style={{ width: '100%' }}>
            Solicitar aprobación humana
          </button>
        )}
      </div>

      {/* Evidencia, política y recibo: plegados por defecto */}
      <div style={{ borderTop: '1px solid var(--line)' }}>
        <details style={sectionStyle}>
          <summary style={summaryStyle}><span>Evidencia</span><span aria-hidden="true">+</span></summary>
          <div style={{ paddingBottom: '14px' }}>
            <Kv label="Grounding">
              <span style={{ fontFamily: 'var(--font-mono)', color: grounding >= 70 ? 'var(--ok)' : 'var(--bad)' }}>{grounding}%</span>
            </Kv>
            <div style={{ height: '4px', borderRadius: '99px', background: 'var(--line)', margin: '10px 0 4px', overflow: 'hidden' }}>
              <div style={{ width: `${grounding}%`, height: '100%', background: grounding >= 70 ? 'var(--ok)' : 'var(--bad)' }} />
            </div>
            <div style={subTitle}>Afirmaciones evaluadas ({evidenceTab.claimCount ?? claims.length})</div>
            {claims.length === 0 ? (
              <p style={{ color: 'var(--muted)', marginTop: '6px' }}>Sin afirmaciones registradas para este paso.</p>
            ) : (
              <ul style={{ listStyle: 'none', display: 'flex', flexDirection: 'column', gap: '6px', marginTop: '8px' }}>
                {claims.map((claim, idx) => (
                  <li key={idx} style={{ paddingLeft: '12px', position: 'relative', lineHeight: 1.5 }}>
                    <span style={{ position: 'absolute', left: 0, color: 'var(--muted)' }}>–</span>
                    {claim}
                  </li>
                ))}
              </ul>
            )}
            <Kv label="Frescura">{evidenceTab.freshness || 'en vivo'}</Kv>
          </div>
        </details>

        <details style={sectionStyle}>
          <summary style={summaryStyle}><span>Política</span><span aria-hidden="true">+</span></summary>
          <div style={{ paddingBottom: '14px' }}>
            <Kv label="Resultado">
              <span style={{ color: requiresConfirmation ? 'var(--accent)' : 'var(--ok)' }}>
                {requiresConfirmation ? 'Requiere confirmación' : policyDecision.status === 'Authorized by human operator' ? 'Autorizado por el operador' : policyDecision.status ? 'Autorizado por la política' : 'Sin evaluar'}
              </span>
            </Kv>
            <Kv label="Precedencia">{policyDecision.precedence === 'Deterministic Safety Precedence' || !policyDecision.precedence ? 'Seguridad determinista' : policyDecision.precedence}</Kv>
            <Kv label="Capacidad">
              <span style={{ color: capability.issued ? 'var(--ok)' : 'var(--muted)' }}>
                {capability.issued ? 'Emitida y firmada (HMAC)' : 'No emitida'}
              </span>
            </Kv>
            {(policyDecision.reasonCodes || []).length > 0 && <Kv label="Códigos de motivo" mono>{policyDecision.reasonCodes.join(', ')}</Kv>}
            <div style={subTitle}>Reglas activadas</div>
            <ul style={{ listStyle: 'none', display: 'flex', flexDirection: 'column', gap: '4px', marginTop: '6px', fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-code)' }}>
              {(policyDecision.rulesActivated || []).length === 0 && <li style={{ color: 'var(--muted)', fontFamily: 'var(--font-sans)' }}>Sin reglas registradas.</li>}
              {(policyDecision.rulesActivated || []).map((rule, idx) => <li key={idx}>{rule}</li>)}
            </ul>

            <div style={subTitle}>Clasificación del comando · {opCategory}</div>
            <Kv label="Solo lectura"><span style={{ color: opAssessment?.read_only ? 'var(--ok)' : 'var(--muted)' }}>{yesNo(opAssessment?.read_only)}</span></Kv>
            <Kv label="Reversible"><span style={{ color: opAssessment?.reversible ? 'var(--ok)' : 'var(--bad)' }}>{yesNo(opAssessment?.reversible)}</span></Kv>
            <Kv label="Acceso a red">{opAssessment?.network_access ? 'Necesario' : 'Ninguno'}</Kv>
            <Kv label="Confianza / clasificador">
              {opAssessment ? `${Math.round((opAssessment.confidence || 0) * 100)}% (${opAssessment.classifier || 'regla'})` : '100% (determinista)'}
            </Kv>

            <div style={subTitle}>Entorno de ejecución · {executionMode}</div>
            <Kv label="Aislamiento"><span style={{ color: executionMode === 'full_access' ? 'var(--bad)' : 'var(--ok)' }}>{isolation}</span></Kv>
            <Kv label="Directorio de trabajo" mono><span title={workingDirectory}>{workingDirectory.length > 28 ? `…${workingDirectory.slice(-26)}` : workingDirectory}</span></Kv>
            <Kv label="Red">{networkMode}</Kv>
            <Kv label="Motor de ejecución">{executionBackend}</Kv>
          </div>
        </details>

        <details style={sectionStyle}>
          <summary style={summaryStyle}><span>Recibo</span><span aria-hidden="true">+</span></summary>
          <div style={{ paddingBottom: '14px' }}>
            <div style={{ color: 'var(--ok)', padding: '7px 0', borderBottom: '1px solid var(--line)' }}>Firmado y verificado con HMAC</div>
            <CopyText label="Identificador de decisión" value={receiptTab.decisionId || decisionId} />
            <CopyText label="Hash de la acción" value={receiptTab.actionHash || '—'} />
            <CopyText label="Hash del estado" value={receiptTab.stateHash || '—'} />
            <CopyText label="Nonce" value={receiptTab.nonce || '—'} />
            <Kv label="Modo de ejecución (ligado al HMAC)" mono>{receiptTab.execution_mode || receiptTab.executionMode || executionMode}</Kv>
            <Kv label="Protección contra repetición">
              <span style={{ color: receiptTab.is_executed ? 'var(--accent)' : 'var(--ok)' }}>
                {receiptTab.is_executed ? 'Consumida (un solo uso)' : 'Disponible (pendiente de ejecutar)'}
              </span>
            </Kv>
            <CopyText label="Firma criptográfica" value={receiptTab.signature || '—'} />
          </div>
        </details>
      </div>

      {relatedDecisions.length > 0 && (
        <div>
          <div style={subTitle}>Decisiones relacionadas</div>
          <div style={{ marginTop: '4px' }}>
            {relatedDecisions.map((rd, idx) => (
              <Kv key={idx} label={rd.id}>
                <span>{rd.tool}</span>
                <span style={{ marginLeft: '8px', color: 'var(--ok)' }}>{VERDICTS[rd.verdict]?.label || rd.verdict}</span>
              </Kv>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
