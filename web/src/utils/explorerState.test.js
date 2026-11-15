import { describe, expect, it } from 'vitest';
import {
  MAX_OPEN, MAX_WORKSPACES, STORAGE_KEY, ancestors, createExplorerStore, emptyState, isInside, isRelPath, moveRel, removed, renamed, reveal, sanitize, setOpen,
} from './explorerState.js';

const memoryStorage = (initial = {}) => {
  const data = { ...initial };
  return {
    data,
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => { data[k] = String(v); },
  };
};

describe('isRelPath', () => {
  it('acepta rutas relativas normales', () => {
    for (const p of ['a', 'src/app', 'a/b/c.txt', '.github/workflows']) expect(isRelPath(p)).toBe(true);
  });
  it('rechaza raíces, escapes y basura', () => {
    for (const p of ['', '/etc', 'C:/x', 'c:\\x', 'a\\b', '../x', 'a/../b', 'a//b', 'a/', './a', 'a/./b', 'a\0b', null, 5, {}, 'x'.repeat(1025)]) {
      expect(isRelPath(p)).toBe(false);
    }
  });
});

describe('sanitize', () => {
  it('devuelve un estado vacío para entradas no válidas', () => {
    for (const raw of [null, undefined, 5, 'x', []]) expect(sanitize(raw)).toEqual(emptyState());
  });
  it('descarta rutas inválidas y duplicados, y limita a MAX_OPEN conservando las más recientes', () => {
    const open = ['a', '../x', 'a', ...Array.from({ length: MAX_OPEN + 5 }, (_, i) => `d${i}`)];
    const s = sanitize({ open, currentDir: '/abs', selected: 'f.txt', scroll: 12.6 });
    expect(s.open).toHaveLength(MAX_OPEN);
    expect(s.open.at(-1)).toBe(`d${MAX_OPEN + 4}`);
    expect(s.open).not.toContain('../x');
    expect(s.currentDir).toBe('');
    expect(s.selected).toBe('f.txt');
    expect(s.scroll).toBe(13);
  });
  it('ignora desplazamientos no válidos', () => {
    for (const scroll of [-1, NaN, Infinity, '5', null]) expect(sanitize({ scroll }).scroll).toBe(0);
  });
});

describe('isInside y moveRel', () => {
  it('comparan por segmentos, no por prefijo de texto', () => {
    expect(isInside('a', 'a')).toBe(true);
    expect(isInside('a/b', 'a')).toBe(true);
    expect(isInside('ab', 'a')).toBe(false);
    expect(isInside('', 'a')).toBe(false);
  });
  it('moveRel solo mueve lo que cuelga de la ruta renombrada', () => {
    expect(moveRel('a/b/f', 'a', 'x')).toBe('x/b/f');
    expect(moveRel('a', 'a', 'x')).toBe('x');
    expect(moveRel('ab/f', 'a', 'x')).toBe('ab/f');
  });
});

describe('ancestors y reveal', () => {
  it('lista las carpetas que contienen a una ruta', () => {
    expect(ancestors('a/b/c.txt')).toEqual(['a', 'a/b']);
    expect(ancestors('top.txt')).toEqual([]);
    expect(ancestors('../x')).toEqual([]);
  });
  it('reveal abre las carpetas contenedoras sin duplicar', () => {
    const s = reveal({ ...emptyState(), open: ['a'] }, 'a/b/c.txt');
    expect(s.open).toEqual(['a', 'a/b']);
  });
});

describe('setOpen', () => {
  it('abre, cierra y no duplica', () => {
    let s = setOpen(emptyState(), 'a', true);
    s = setOpen(s, 'a', true);
    expect(s.open).toEqual(['a']);
    s = setOpen(s, 'a', false);
    expect(s.open).toEqual([]);
  });
  it('cerrar algo que no estaba abierto no cambia el estado (misma referencia)', () => {
    const s = emptyState();
    expect(setOpen(s, 'a', false)).toBe(s);
  });
  it('al superar MAX_OPEN olvida las más antiguas', () => {
    let s = emptyState();
    for (let i = 0; i < MAX_OPEN + 3; i += 1) s = setOpen(s, `d${i}`, true);
    expect(s.open).toHaveLength(MAX_OPEN);
    expect(s.open[0]).toBe('d3');
  });
  it('ignora rutas inválidas', () => {
    const s = emptyState();
    expect(setOpen(s, '../x', true)).toBe(s);
  });
});

describe('removed', () => {
  const base = { open: ['a', 'a/b', 'ab', 'z'], currentDir: 'a/b', selected: 'a/b/f.txt', scroll: 40 };
  it('quita la carpeta y todo lo que cuelga, sin tocar nombres que solo comparten prefijo', () => {
    const s = removed(base, 'a');
    expect(s.open).toEqual(['ab', 'z']);
    expect(s.currentDir).toBe('');
    expect(s.selected).toBeNull();
    expect(s.scroll).toBe(40);
  });
  it('quitar un archivo seleccionado limpia la selección y nada más', () => {
    const s = removed(base, 'a/b/f.txt');
    expect(s.selected).toBeNull();
    expect(s.open).toEqual(base.open);
    expect(s.currentDir).toBe('a/b');
  });
});

describe('renamed', () => {
  it('mueve lo recordado dentro de la ruta renombrada', () => {
    const s = renamed({ open: ['a', 'a/b', 'ab'], currentDir: 'a/b', selected: 'a/b/f.txt', scroll: 0 }, 'a', 'x');
    expect(s.open).toEqual(['x', 'x/b', 'ab']);
    expect(s.currentDir).toBe('x/b');
    expect(s.selected).toBe('x/b/f.txt');
  });
  it('sin cambios si las rutas no son válidas o son iguales', () => {
    const s = emptyState();
    expect(renamed(s, 'a', 'a')).toBe(s);
    expect(renamed(s, '../a', 'b')).toBe(s);
  });
});

describe('createExplorerStore', () => {
  it('guarda y recupera por workspace sin mezclar', () => {
    const store = createExplorerStore(memoryStorage());
    store.save('/w/one', (s) => setOpen(s, 'src', true));
    store.save('/w/two', (s) => ({ ...s, selected: 'readme.md' }));
    expect(store.load('/w/one').open).toEqual(['src']);
    expect(store.load('/w/one').selected).toBeNull();
    expect(store.load('/w/two').selected).toBe('readme.md');
    expect(store.load('/w/three')).toEqual(emptyState());
  });
  it('una instancia nueva sobre el mismo almacenamiento ve lo guardado (recarga)', () => {
    const storage = memoryStorage();
    createExplorerStore(storage).save('/w', (s) => ({ ...setOpen(s, 'a/b', true), scroll: 120 }));
    const again = createExplorerStore(storage).load('/w');
    expect(again.open).toEqual(['a/b']);
    expect(again.scroll).toBe(120);
  });
  it('JSON corrupto o con forma inesperada se trata como vacío', () => {
    for (const bad of ['{no json', '[]', '"x"', '{"order":1,"states":{}}', '{"order":[],"states":5}']) {
      const store = createExplorerStore(memoryStorage({ [STORAGE_KEY]: bad }));
      expect(store.load('/w')).toEqual(emptyState());
      expect(store.save('/w', (s) => setOpen(s, 'a', true)).open).toEqual(['a']);
    }
  });
  it('un estado manipulado en el almacenamiento se normaliza al leer', () => {
    const raw = JSON.stringify({ order: ['/w'], states: { '/w': { open: ['../etc', 'ok'], currentDir: 'C:/x', selected: '/abs', scroll: -3 } } });
    expect(createExplorerStore(memoryStorage({ [STORAGE_KEY]: raw })).load('/w')).toEqual({ open: ['ok'], currentDir: '', selected: null, scroll: 0 });
  });
  it('si el almacenamiento falla al escribir o no existe, no lanza', () => {
    const throwing = { getItem: () => null, setItem: () => { throw new Error('QuotaExceededError'); } };
    expect(() => createExplorerStore(throwing).save('/w', (s) => setOpen(s, 'a', true))).not.toThrow();
    expect(() => createExplorerStore(null).save('/w', (s) => setOpen(s, 'a', true))).not.toThrow();
    expect(createExplorerStore(null).load('/w')).toEqual(emptyState());
  });
  it('workspace vacío o no cadena no escribe nada', () => {
    const storage = memoryStorage();
    const store = createExplorerStore(storage);
    expect(store.save('', (s) => setOpen(s, 'a', true))).toEqual(emptyState());
    expect(store.save(null, (s) => s)).toEqual(emptyState());
    expect(storage.data).toEqual({});
  });
  it('olvida los workspaces más antiguos al superar MAX_WORKSPACES', () => {
    const store = createExplorerStore(memoryStorage());
    for (let i = 0; i < MAX_WORKSPACES + 2; i += 1) store.save(`/w${i}`, (s) => setOpen(s, 'a', true));
    expect(store.load('/w0')).toEqual(emptyState());
    expect(store.load('/w1')).toEqual(emptyState());
    expect(store.load('/w2').open).toEqual(['a']);
    expect(store.load(`/w${MAX_WORKSPACES + 1}`).open).toEqual(['a']);
  });
  it('un workspace usado de nuevo pasa a ser el más reciente', () => {
    const store = createExplorerStore(memoryStorage());
    for (let i = 0; i < MAX_WORKSPACES; i += 1) store.save(`/w${i}`, (s) => setOpen(s, 'a', true));
    store.save('/w0', (s) => s);
    store.save('/new', (s) => setOpen(s, 'a', true));
    expect(store.load('/w0').open).toEqual(['a']);
    expect(store.load('/w1')).toEqual(emptyState());
  });
  it('clear borra un workspace', () => {
    const store = createExplorerStore(memoryStorage());
    store.save('/w', (s) => setOpen(s, 'a', true));
    store.clear('/w');
    expect(store.load('/w')).toEqual(emptyState());
  });
  it('no guarda contenido: solo rutas y números', () => {
    const storage = memoryStorage();
    createExplorerStore(storage).save('/w', (s) => ({ ...setOpen(s, 'a', true), selected: 'a/f.txt', content: 'SECRETO', extra: { x: 1 } }));
    expect(storage.data[STORAGE_KEY]).not.toContain('SECRETO');
    expect(storage.data[STORAGE_KEY]).not.toContain('extra');
  });
});
