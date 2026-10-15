import React from 'react';

// Renderizador de Markdown mínimo y seguro: construye elementos React (nunca HTML en crudo) y solo enlaza http(s)/mailto.
// Cubre lo que producen los modelos: títulos, párrafos, listas, citas, código, tablas, negrita, cursiva y enlaces.

const INLINE = /(`[^`\n]+`|\*\*[^*\n]+\*\*|__[^_\n]+__|\[[^\]\n]+\]\([^)\s]+\)|\*[^*\s][^*\n]*\*|_[^_\s][^_\n]*_)/g;

const codeStyle = {
  fontFamily: 'var(--font-mono)',
  fontSize: 'var(--fs-code)',
  background: 'var(--surface)',
  border: '1px solid var(--line)',
  borderRadius: '5px',
  padding: '1px 5px',
};

function safeHref(url) {
  return /^(https?:\/\/|mailto:)/i.test(url) ? url : null;
}

function inline(text, keyBase = 'i') {
  const out = [];
  let last = 0;
  let n = 0;
  for (const m of text.matchAll(INLINE)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    const key = `${keyBase}-${n++}`;
    if (tok.startsWith('`')) {
      out.push(<code key={key} style={codeStyle}>{tok.slice(1, -1)}</code>);
    } else if (tok.startsWith('**') || tok.startsWith('__')) {
      out.push(<strong key={key} style={{ fontWeight: 600 }}>{inline(tok.slice(2, -2), key)}</strong>);
    } else if (tok.startsWith('[')) {
      const [, label, url] = tok.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      const href = safeHref(url);
      out.push(href
        ? <a key={key} href={href} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--accent)', textUnderlineOffset: '3px' }}>{label}</a>
        : label);
    } else {
      out.push(<em key={key}>{inline(tok.slice(1, -1), key)}</em>);
    }
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const isTableRow = (l) => /^\s*\|.*\|\s*$/.test(l);
const isTableSep = (l) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(l);
const cells = (l) => l.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
const BLOCK_START = /^(\s*(```|#{1,6}\s|>|[-*+]\s|\d+[.)]\s|(-{3,}|\*{3,})\s*$))/;

function parse(src) {
  const lines = src.replace(/\r\n/g, '\n').split('\n');
  const blocks = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }

    if (/^\s*```/.test(line)) {
      const lang = line.trim().slice(3).trim();
      const body = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) body.push(lines[i++]);
      i++;
      blocks.push({ type: 'code', lang, text: body.join('\n') });
      continue;
    }
    const h = line.match(/^\s*(#{1,6})\s+(.*)$/);
    if (h) { blocks.push({ type: 'h', level: h[1].length, text: h[2].replace(/\s*#+\s*$/, '') }); i++; continue; }
    if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) { blocks.push({ type: 'hr' }); i++; continue; }

    if (/^\s*>/.test(line)) {
      const body = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) body.push(lines[i++].replace(/^\s*>\s?/, ''));
      blocks.push({ type: 'quote', text: body.join('\n') });
      continue;
    }
    if (isTableRow(line) && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      const head = cells(line);
      i += 2;
      const rows = [];
      while (i < lines.length && isTableRow(lines[i])) rows.push(cells(lines[i++]));
      blocks.push({ type: 'table', head, rows });
      continue;
    }
    const ul = /^\s*[-*+]\s+/;
    const ol = /^\s*\d+[.)]\s+/;
    if (ul.test(line) || ol.test(line)) {
      const ordered = ol.test(line);
      const re = ordered ? ol : ul;
      const items = [];
      while (i < lines.length && re.test(lines[i])) {
        let item = lines[i++].replace(re, '');
        // continuación con sangría de la misma entrada
        while (i < lines.length && lines[i].trim() && /^\s{2,}\S/.test(lines[i]) && !re.test(lines[i])) item += ' ' + lines[i++].trim();
        items.push(item);
      }
      blocks.push({ type: ordered ? 'ol' : 'ul', items });
      continue;
    }
    const para = [];
    while (i < lines.length && lines[i].trim() && !BLOCK_START.test(lines[i]) && !(isTableRow(lines[i]) && isTableSep(lines[i + 1] || ''))) para.push(lines[i++]);
    if (!para.length) para.push(lines[i++]);
    blocks.push({ type: 'p', text: para.join('\n') });
  }
  return blocks;
}

export default function Markdown({ text, compact = false }) {
  const blocks = React.useMemo(() => parse(String(text || '')), [text]);
  const gap = compact ? '0.55em' : '0.85em';
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap, overflowWrap: 'anywhere' }}>
      {blocks.map((b, idx) => {
        const key = `b${idx}`;
        switch (b.type) {
          case 'h':
            return (
              <div key={key} role="heading" aria-level={b.level} style={{ fontWeight: 600, fontSize: b.level <= 2 ? '1.12em' : '1.02em', marginTop: idx ? '0.35em' : 0 }}>
                {inline(b.text, key)}
              </div>
            );
          case 'code':
            return (
              <pre key={key} style={{ ...codeStyle, padding: '10px 12px', margin: 0, overflowX: 'auto', whiteSpace: 'pre', lineHeight: 1.5, borderRadius: 'var(--r-card)' }}>
                <code>{b.text}</code>
              </pre>
            );
          case 'ul':
          case 'ol': {
            const Tag = b.type;
            return (
              <Tag key={key} style={{ margin: 0, paddingLeft: '1.4em', display: 'flex', flexDirection: 'column', gap: '0.3em' }}>
                {b.items.map((it, j) => <li key={j}>{inline(it, `${key}-${j}`)}</li>)}
              </Tag>
            );
          }
          case 'quote':
            return (
              <blockquote key={key} style={{ margin: 0, paddingLeft: '12px', borderLeft: '2px solid var(--line)', color: 'var(--muted)' }}>
                <Markdown text={b.text} compact />
              </blockquote>
            );
          case 'hr':
            return <hr key={key} style={{ border: 0, borderTop: '1px solid var(--line)', width: '100%', margin: '0.2em 0' }} />;
          case 'table':
            return (
              <div key={key} style={{ overflowX: 'auto' }}>
                <table style={{ borderCollapse: 'collapse', fontSize: 'var(--fs-secondary)' }}>
                  <thead>
                    <tr>{b.head.map((c, j) => <th key={j} style={{ textAlign: 'left', padding: '5px 12px 5px 0', borderBottom: '1px solid var(--line)', fontWeight: 600 }}>{inline(c, `${key}h${j}`)}</th>)}</tr>
                  </thead>
                  <tbody>
                    {b.rows.map((r, j) => (
                      <tr key={j}>{r.map((c, k) => <td key={k} style={{ padding: '5px 12px 5px 0', borderBottom: '1px solid var(--line)', verticalAlign: 'top' }}>{inline(c, `${key}r${j}c${k}`)}</td>)}</tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          default:
            return <div key={key} style={{ whiteSpace: 'pre-wrap' }}>{inline(b.text, key)}</div>;
        }
      })}
    </div>
  );
}
