import React, { useCallback, useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, RefreshCw } from 'lucide-react';
import * as api from '../../services/api';
import Markdown from '../Markdown';
import { ViewPage, ViewHeader, EmptyState } from '../ui';

const STATUS = {
  enabled: { label: 'Habilitada', color: 'var(--ok)' },
  disabled: { label: 'Deshabilitada', color: 'var(--muted)' },
  changed: { label: 'Cambió: revísala y vuelve a habilitarla', color: 'var(--bad)' },
  invalid: { label: 'Inválida', color: 'var(--bad)' },
};
const SOURCES = { user: 'Usuario', project: 'Proyecto' };
const card = { backgroundColor: 'var(--surface)', borderRadius: '9px', border: '1px solid var(--line)', padding: '14px 16px' };
const btn = {
  font: 'inherit', fontSize: 'var(--fs-secondary)', color: 'var(--text)', background: 'transparent',
  border: '1px solid var(--line)', borderRadius: '8px', padding: '6px 12px', cursor: 'pointer',
};

/** Skills (SK-001): carpetas con SKILL.md. Habilitar una skill añade sus instrucciones al prompt de los agentes. */
export default function SkillsView() {
  const [skills, setSkills] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [open, setOpen] = useState({});
  const [bodies, setBodies] = useState({});

  const load = useCallback(async () => {
    try {
      setSkills(await api.fetchSkills());
      setError('');
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Las instrucciones se leen al desplegar: se revisan antes de habilitar
  const toggleOpen = async (skill) => {
    const key = skill.path;
    setOpen((o) => ({ ...o, [key]: !o[key] }));
    if (skill.valid && !bodies[skill.name]) {
      try {
        const full = await api.fetchSkill(skill.name);
        setBodies((b) => ({ ...b, [skill.name]: full.body }));
      } catch (e) {
        setError(e.message);
      }
    }
  };

  const setEnabled = async (skill, enabled) => {
    setBusy(skill.name);
    try {
      await api.setSkillEnabled(skill.name, enabled);
      setBodies((b) => {
        const { [skill.name]: _drop, ...rest } = b;      // tras un cambio, se vuelve a leer lo que se habilita
        return rest;
      });
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy('');
    }
  };

  return (
    <ViewPage>
      <ViewHeader
        title="Skills"
        description="Instrucciones de trabajo reutilizables (carpetas con SKILL.md). Una skill solo llega a los agentes cuando la habilitas; si su archivo cambia, deja de aplicarse hasta que la vuelvas a habilitar."
        actions={<button type="button" style={btn} onClick={load}><RefreshCw size={13} style={{ verticalAlign: '-2px', marginRight: 6 }} />Actualizar</button>}
      />
      {error && <div role="alert" style={{ ...card, color: 'var(--bad)' }}>{error}</div>}
      {skills && skills.length === 0 && (
        <EmptyState title="No hay skills">
          Crea una carpeta con un archivo SKILL.md (frontmatter YAML con «name» y «description», y las instrucciones en Markdown) en
          {' '}<code>.praxeon_cache/skills</code>, <code>.praxeon/skills</code> o <code>.claude/skills</code> del workspace.
        </EmptyState>
      )}
      {(skills || []).map((skill) => {
        const st = STATUS[skill.status] || STATUS.disabled;
        const expanded = Boolean(open[skill.path]);
        return (
          <div key={skill.path} style={card}>
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
              <button
                type="button"
                onClick={() => toggleOpen(skill)}
                style={{ background: 'none', border: 'none', color: 'var(--text)', textAlign: 'left', padding: 0, cursor: 'pointer', minWidth: 0, flex: 1 }}
                aria-expanded={expanded}
              >
                <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontWeight: 600 }}>
                  {expanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                  {skill.valid ? skill.name : skill.folder}
                </span>
                <span style={{ display: 'block', color: 'var(--muted)', marginTop: 2, marginLeft: 21 }}>
                  {skill.valid ? skill.description : skill.error}
                </span>
              </button>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <span style={{ color: st.color, fontSize: 'var(--fs-secondary)' }}>{st.label}</span>
                {skill.valid && (
                  skill.status === 'enabled' ? (
                    <button type="button" style={btn} disabled={busy === skill.name} onClick={() => setEnabled(skill, false)}>Deshabilitar</button>
                  ) : (
                    <button type="button" style={{ ...btn, borderColor: 'var(--accent)' }} disabled={busy === skill.name} onClick={() => setEnabled(skill, true)}>
                      {skill.status === 'changed' ? 'Habilitar de nuevo' : 'Habilitar'}
                    </button>
                  )
                )}
              </div>
            </div>
            {expanded && (
              <div style={{ marginTop: 12, borderTop: '1px solid var(--line)', paddingTop: 12 }}>
                <div style={{ color: 'var(--muted)', fontSize: 'var(--fs-secondary)', marginBottom: 8 }}>
                  {SOURCES[skill.source] || skill.source} · <code>{skill.path}</code>{skill.size ? ` · ${skill.size} B` : ''}
                </div>
                {skill.valid ? (bodies[skill.name] ? <Markdown text={bodies[skill.name]} compact /> : <span style={{ color: 'var(--muted)' }}>Cargando…</span>) : null}
              </div>
            )}
          </div>
        );
      })}
    </ViewPage>
  );
}
