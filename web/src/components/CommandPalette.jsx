import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Search } from 'lucide-react';

/**
 * Paleta de comandos (Ctrl K). `items`: [{ id, group, label, icon: Component, hint?, run }]
 * Teclado: ↑ ↓ navegan, Enter ejecuta, Esc cierra.
 */
export default function CommandPalette({ open, items = [], onClose }) {
  const [query, setQuery] = useState('');
  const [index, setIndex] = useState(0);
  const inputRef = useRef(null);
  const listRef = useRef(null);
  const returnFocusRef = useRef(null);

  useEffect(() => {
    if (open) {
      returnFocusRef.current = document.activeElement;
      setQuery('');
      setIndex(0);
      setTimeout(() => inputRef.current?.focus(), 0);
    } else if (returnFocusRef.current) {
      returnFocusRef.current.focus?.();
      returnFocusRef.current = null;
    }
  }, [open]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? items.filter((i) => `${i.label} ${i.group} ${i.hint || ''}`.toLowerCase().includes(q)) : items;
  }, [items, query]);

  useEffect(() => {
    listRef.current?.querySelector('[data-active="true"]')?.scrollIntoView({ block: 'nearest' });
  }, [index, filtered]);

  if (!open) return null;

  const run = (item) => {
    onClose?.();
    item?.run?.();
  };

  const onKeyDown = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); onClose?.(); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); setIndex((i) => Math.min(i + 1, filtered.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setIndex((i) => Math.max(i - 1, 0)); }
    else if (e.key === 'Enter') { e.preventDefault(); run(filtered[index]); }
  };

  let lastGroup = '';
  return (
    <div
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose?.(); }}
      style={{ position: 'fixed', inset: 0, zIndex: 80, background: 'var(--scrim)', display: 'flex', justifyContent: 'center' }}
    >
      <div
        role="dialog"
        aria-label="Paleta de comandos"
        onKeyDown={onKeyDown}
        className="rise"
        style={{
          marginTop: '14vh',
          width: 'min(520px, calc(100% - 32px))',
          height: 'fit-content',
          background: 'var(--surface)',
          border: '1px solid var(--line)',
          borderRadius: 'var(--r-panel)',
          boxShadow: 'var(--shadow-overlay)',
          overflow: 'hidden',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '14px 16px', borderBottom: '1px solid var(--line)', color: 'var(--muted)' }}>
          <Search size={16} strokeWidth={1.6} />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => { setQuery(e.target.value); setIndex(0); }}
            placeholder="Buscar vista o acción…"
            autoComplete="off"
            aria-label="Buscar vista o acción"
            style={{ flex: 1, border: 0, background: 'transparent', padding: 0, fontSize: 'var(--fs-chat)', outline: 'none' }}
          />
        </div>
        <div ref={listRef} style={{ padding: '6px', maxHeight: '360px', overflowY: 'auto' }} role="listbox">
          {filtered.length === 0 && (
            <div style={{ padding: '10px 12px', color: 'var(--muted)', fontSize: 'var(--fs-secondary)' }}>Sin resultados</div>
          )}
          {filtered.map((item, i) => {
            const Icon = item.icon;
            const header = item.group !== lastGroup;
            lastGroup = item.group;
            const active = i === index;
            return (
              <React.Fragment key={item.id}>
                {header && (
                  <div style={{ padding: '8px 12px 4px', fontSize: 'var(--fs-label)', letterSpacing: '0.07em', textTransform: 'uppercase', color: 'var(--muted)' }}>
                    {item.group}
                  </div>
                )}
                <button
                  type="button"
                  role="option"
                  aria-selected={active}
                  data-active={active}
                  onMouseMove={() => setIndex(i)}
                  onClick={() => run(item)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '12px',
                    width: '100%',
                    textAlign: 'left',
                    padding: '9px 12px',
                    border: 0,
                    borderRadius: 'var(--r-card)',
                    background: active ? 'var(--hover)' : 'transparent',
                    color: active ? 'var(--text)' : 'var(--muted)',
                    fontSize: 'var(--fs-ui)',
                  }}
                >
                  {Icon && <Icon size={16} strokeWidth={1.6} style={{ flexShrink: 0 }} />}
                  <span style={{ flex: 1, minWidth: 0 }}>{item.label}</span>
                  {item.hint && <span className="kbd">{item.hint}</span>}
                </button>
              </React.Fragment>
            );
          })}
        </div>
      </div>
    </div>
  );
}
