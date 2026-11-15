import { describe, expect, it } from 'vitest';
import { DEFAULT_LIMITS, checkNewFiles, filesFromTransfer, formatSize, hasFiles, visionNotice } from './attachmentRules.js';

const file = (name, size) => ({ name, size });
const MB = 1024 * 1024;

describe('formatSize', () => {
  it('formatea bytes, KB y MB', () => {
    expect(formatSize(0)).toBe('0 B');
    expect(formatSize(1023)).toBe('1023 B');
    expect(formatSize(1536)).toBe('1.5 KB');
    expect(formatSize(3 * MB + MB / 2)).toBe('3.5 MB');
    expect(formatSize(undefined)).toBe('0 B');
  });
});

describe('checkNewFiles', () => {
  it('acepta lo normal', () => {
    const r = checkNewFiles([], [file('a.txt', 10), file('b.png', 5 * MB)]);
    expect(r.accepted.map((f) => f.name)).toEqual(['a.txt', 'b.png']);
    expect(r.rejected).toEqual([]);
  });
  it('rechaza vacíos y los que superan el tamaño, con el motivo', () => {
    const r = checkNewFiles([], [file('vacio.txt', 0), file('enorme.bin', 101 * MB), file('ok.txt', 1)]);
    expect(r.accepted.map((f) => f.name)).toEqual(['ok.txt']);
    expect(r.rejected[0]).toMatchObject({ name: 'vacio.txt', reason: expect.stringContaining('vacío') });
    expect(r.rejected[1].reason).toContain('100.0 MB');
  });
  it('el límite de un archivo es exacto', () => {
    expect(checkNewFiles([], [file('justo', DEFAULT_LIMITS.max_file_bytes)]).accepted).toHaveLength(1);
    expect(checkNewFiles([], [file('pasado', DEFAULT_LIMITS.max_file_bytes + 1)]).rejected).toHaveLength(1);
  });
  it('máximo de archivos por mensaje contando los que ya hay (los fallidos no cuentan)', () => {
    const existing = [
      ...Array.from({ length: DEFAULT_LIMITS.max_files - 2 }, (_, i) => ({ id: i, status: 'ready' })),
      { id: 'x', status: 'error' },
      { id: 'y', status: 'uploading' },
    ];
    const r = checkNewFiles(existing, [file('uno', 1), file('dos', 1), file('tres', 1)]);
    expect(r.accepted).toHaveLength(1);                           // (máximo - 2) listos + 1 subiendo + 1 nuevo = máximo
    expect(r.rejected.map((x) => x.name)).toEqual(['dos', 'tres']);
    expect(r.rejected[0].reason).toContain('Máximo 50');
  });
  it('respeta límites propios', () => {
    const r = checkNewFiles([], [file('a', 3), file('b', 1), file('c', 1)], { max_file_bytes: 2, max_files: 2 });
    expect(r.accepted.map((f) => f.name)).toEqual(['b', 'c']);
    expect(r.rejected).toHaveLength(1);
  });
  it('ignora entradas que no son archivos', () => {
    expect(checkNewFiles([], [null, undefined, {}, file('ok', 1)]).accepted).toHaveLength(1);
  });
});

describe('arrastrar y pegar', () => {
  it('toma los archivos de los items y, si no hay, de files', () => {
    const f1 = file('a', 1);
    const f2 = file('b', 1);
    expect(filesFromTransfer({ items: [{ kind: 'file', getAsFile: () => f1 }, { kind: 'string', getAsFile: () => null }] })).toEqual([f1]);
    expect(filesFromTransfer({ items: [], files: [f2] })).toEqual([f2]);
    expect(filesFromTransfer({ items: [{ kind: 'string' }], files: [] })).toEqual([]);
    expect(filesFromTransfer(null)).toEqual([]);
  });
  it('solo resalta si lo arrastrado incluye archivos', () => {
    expect(hasFiles({ types: ['text/plain', 'Files'] })).toBe(true);
    expect(hasFiles({ types: ['text/plain'] })).toBe(false);
    expect(hasFiles(undefined)).toBe(false);
  });
});

describe('visionNotice (FE-006)', () => {
  const img = (extra = {}) => ({ id: 1, kind: 'image', status: 'ready', ...extra });
  it('sin imágenes no hay aviso (los archivos normales no lo necesitan)', () => {
    expect(visionNotice([], null)).toBeNull();
    expect(visionNotice([{ kind: 'file', status: 'ready' }], { supported: false })).toBeNull();
    expect(visionNotice([img({ status: 'error' })], { supported: false })).toBeNull();
  });
  it('mientras no se sabe, avisa sin bloquear', () => {
    expect(visionNotice([img()], null)).toMatchObject({ blocked: false, level: 'info' });
  });
  it('un modelo sin visión bloquea el envío con el motivo del servidor', () => {
    const n = visionNotice([img()], { supported: false, reason: 'El modelo «x» no admite imágenes.' });
    expect(n).toEqual({ blocked: true, level: 'error', message: 'El modelo «x» no admite imágenes.' });
    expect(visionNotice([img()], { supported: false }).message).toContain('no admite imágenes');
  });
  it('un proveedor remoto avisa de que la imagen sale del equipo, sin bloquear', () => {
    const n = visionNotice([img()], { supported: true, remote: true, provider: 'openai' });
    expect(n.blocked).toBe(false);
    expect(n.level).toBe('warn');
    expect(n.message).toContain('openai');
    expect(n.message).toContain('sale de este equipo');
    expect(visionNotice([img(), img({ id: 2 })], { supported: true, remote: true, provider: 'groq' }).message).toContain('Las imágenes se enviarán');
  });
  it('un modelo local con visión no avisa de nada', () => {
    expect(visionNotice([img()], { supported: true, remote: false, provider: 'ollama' })).toBeNull();
  });
});
