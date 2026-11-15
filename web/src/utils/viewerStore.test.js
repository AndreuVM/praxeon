import { describe, expect, it } from 'vitest';
import { MAX_VIEWER_TABS, STORAGE_KEY, createExplorerStore, sanitize } from './explorerState.js';
import {
  closeTab, dirtyPaths, isDirty, loadingTab, openTab, patchTab, persistable, removePath, renamePath, restore, setActive,
} from './viewerState.js';
import { createViewerStore } from './viewerStore.js';

const text = (path, content = `contenido de ${path}`, extra = {}) => ({ ...loadingTab(path), kind: 'text', content, original: content, etag: 'e1', ...extra });
const withTabs = (tabs, active = tabs.at(-1)?.path ?? null) => ({ tabs, active });
const memoryStorage = () => {
  const data = {};
  return { data, getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); } };
};
const flush = () => new Promise((r) => setTimeout(r, 0));

describe('viewerState: pestañas', () => {
  it('abrir añade una pestaña en carga y la activa; abrir otra vez solo la activa', () => {
    let s = openTab({ tabs: [], active: null }, 'a.txt');
    expect(s.tabs.map((t) => [t.path, t.kind])).toEqual([['a.txt', 'loading']]);
    s = openTab(s, 'b.txt');
    expect(s.active).toBe('b.txt');
    const again = openTab(s, 'a.txt');
    expect(again.tabs).toHaveLength(2);
    expect(again.active).toBe('a.txt');
    expect(openTab(again, 'a.txt')).toBe(again);
  });

  it('un SVG se abre en vista previa y el resto en edición', () => {
    expect(loadingTab('x.svg').mode).toBe('preview');
    expect(loadingTab('x.js').mode).toBe('edit');
  });

  it('patchTab no resucita una pestaña cerrada y no cambia nada si el valor es igual', () => {
    const s = withTabs([text('a.txt')]);
    expect(patchTab(s, 'zzz', { content: 'x' })).toBe(s);
    expect(patchTab(s, 'a.txt', { content: s.tabs[0].content })).toBe(s);
    expect(patchTab(s, 'a.txt', { content: 'nuevo' }).tabs[0].content).toBe('nuevo');
  });

  it('cerrar la activa deja activa la última; cerrar otra no cambia la activa', () => {
    const s = withTabs([text('a'), text('b'), text('c')], 'b');
    expect(closeTab(s, 'b').active).toBe('c');
    expect(closeTab(s, 'a').active).toBe('b');
    expect(closeTab(withTabs([text('a')]), 'a')).toEqual({ tabs: [], active: null });
    expect(closeTab(s, 'nope')).toBe(s);
  });

  it('setActive ignora rutas sin pestaña', () => {
    const s = withTabs([text('a'), text('b')], 'a');
    expect(setActive(s, 'zzz')).toBe(s);
    expect(setActive(s, 'b').active).toBe('b');
  });

  it('eliminar una carpeta cierra las pestañas de dentro, pero no las de un nombre parecido', () => {
    const s = withTabs([text('src/a.js'), text('srcx/b.js'), text('src/deep/c.js'), text('README.md')], 'src/a.js');
    const r = removePath(s, 'src');
    expect(r.tabs.map((t) => t.path)).toEqual(['srcx/b.js', 'README.md']);
    expect(r.active).toBe('README.md');
    expect(removePath(s, 'otra')).toBe(s);
  });

  it('renombrar mueve las pestañas (y su texto sin guardar) a la ruta nueva', () => {
    const s = withTabs([text('src/a.js', 'editado', { original: 'viejo' }), text('srcx/b.js')], 'src/a.js');
    const r = renamePath(s, 'src', 'lib');
    expect(r.tabs.map((t) => t.path)).toEqual(['lib/a.js', 'srcx/b.js']);
    expect(r.active).toBe('lib/a.js');
    expect(isDirty(r.tabs[0])).toBe(true);
    expect(r.tabs[0].content).toBe('editado');
    expect(renamePath(s, 'nada', 'x')).toBe(s);
  });

  it('dirtyPaths solo cuenta texto modificado', () => {
    const s = withTabs([text('a', 'x', { original: 'y' }), text('b'), { ...loadingTab('c'), content: 'z' }]);
    expect(dirtyPaths(s)).toEqual(['a']);
  });
});

describe('viewerState: lo que se recuerda', () => {
  it('persistable guarda rutas y pestaña activa, NUNCA contenido', () => {
    const s = withTabs([text('a.txt', 'SECRETO-sin-guardar', { original: 'otro' }), text('b.txt')], 'a.txt');
    const p = persistable(s);
    expect(p).toEqual({ tabs: ['a.txt', 'b.txt'], active: 'a.txt' });
    expect(JSON.stringify(p)).not.toContain('SECRETO');
  });

  it('las pestañas con error no se recuerdan y la activa vuelve a null si ya no está', () => {
    const s = withTabs([text('a'), { ...loadingTab('roto'), kind: 'error', error: 'x' }], 'roto');
    expect(persistable(s)).toEqual({ tabs: ['a'], active: null });
  });

  it('restore reabre en carga, descarta rutas inválidas y repetidas, y respeta el tope', () => {
    const tabs = ['a', '../x', 'a', '/abs', ...Array.from({ length: MAX_VIEWER_TABS + 4 }, (_, i) => `d/f${i}`)];
    const r = restore({ tabs, active: 'a' });
    expect(r.tabs.every((t) => t.kind === 'loading')).toBe(true);
    expect(r.tabs).toHaveLength(MAX_VIEWER_TABS);
    expect(r.tabs.at(-1).path).toBe(`d/f${MAX_VIEWER_TABS + 3}`);
    expect(r.tabs.map((t) => t.path)).not.toContain('../x');
    expect(r.active).toBe(r.tabs.at(-1).path);          // 'a' quedó fuera por el tope: pasa a la última
  });

  it('restore conserva la activa si sigue ahí y tolera basura', () => {
    expect(restore({ tabs: ['a', 'b', 'c'], active: 'b' }).active).toBe('b');
    for (const raw of [null, undefined, 5, 'x', {}, { tabs: 'a' }]) expect(restore(raw)).toEqual({ tabs: [], active: null });
  });
});

describe('explorerState: sección del visor', () => {
  it('sanitize normaliza las pestañas recordadas y descarta una activa que no está', () => {
    const s = sanitize({ viewer: { tabs: ['a', '../b', 'a', 'c'], active: 'zzz' } });
    expect(s.viewer).toEqual({ tabs: ['a', 'c'], active: null });
    expect(sanitize({ viewer: { tabs: ['a', 'b'], active: 'b' } }).viewer.active).toBe('b');
    expect(sanitize({ open: ['x'] }).viewer).toEqual({ tabs: [], active: null });
  });

  it('un estado anterior (sin visor) se sigue leyendo', () => {
    const storage = memoryStorage();
    storage.setItem(STORAGE_KEY, JSON.stringify({ order: ['w'], states: { w: { open: ['a'], currentDir: '', selected: 'a/f', scroll: 0 } } }));
    const loaded = createExplorerStore(storage).load('w');
    expect(loaded.selected).toBe('a/f');
    expect(loaded.viewer).toEqual({ tabs: [], active: null });
  });

  it('guardar el visor no pisa el resto del estado del explorador y viceversa', () => {
    const store = createExplorerStore(memoryStorage());
    store.save('w', (s) => ({ ...s, selected: 'a/f', open: ['a'] }));
    store.save('w', (s) => ({ ...s, viewer: { tabs: ['a/f'], active: 'a/f' } }));
    store.save('w', (s) => ({ ...s, scroll: 40 }));
    expect(store.load('w')).toMatchObject({ selected: 'a/f', open: ['a'], scroll: 40, viewer: { tabs: ['a/f'], active: 'a/f' } });
  });
});

// ------------------------------------------------------------------------------------------------------------- almacén

const makeStore = ({ files = {}, delay = 0, storage = memoryStorage() } = {}) => {
  const disk = { ...files };                    // ruta -> { content, etag }
  const calls = { read: [], write: [] };
  const explorer = createExplorerStore(storage);
  const notFound = (path) => Object.assign(new Error(`No existe: ${path}`), { status: 404 });
  const store = createViewerStore({
    read: async (path) => {
      calls.read.push(path);
      if (delay) await new Promise((r) => setTimeout(r, delay));
      if (!(path in disk)) throw notFound(path);
      return { kind: 'text', content: disk[path].content, etag: disk[path].etag, encoding: 'utf-8' };
    },
    write: async (path, content, etag) => {
      calls.write.push({ path, content, etag });
      if (!(path in disk)) throw notFound(path);
      if (etag !== null && etag !== disk[path].etag) throw Object.assign(new Error('cambió en disco'), { status: 409 });
      disk[path] = { content, etag: `${disk[path].etag}+` };
      return { etag: disk[path].etag, encoding: 'utf-8' };
    },
    load: (ws) => explorer.load(ws).viewer,
    persist: (ws, viewer) => explorer.save(ws, (s) => ({ ...s, viewer })),
  });
  return { store, disk, calls, storage, explorer };
};
const fileMap = (names) => Object.fromEntries(names.map((n) => [n, { content: `contenido de ${n}`, etag: 'e1' }]));

describe('viewerStore: cambiar de vista', () => {
  it('abrir varios archivos y editar uno: tras desmontar y volver, pestañas y texto siguen intactos', async () => {
    const { store, calls } = makeStore({ files: fileMap(['a.txt', 'b.txt', 'c.txt']) });
    store.ensure('W');
    store.open('W', 'a.txt');
    store.open('W', 'b.txt');
    store.open('W', 'c.txt');
    await flush();
    store.select('W', 'b.txt');
    store.patch('W', 'b.txt', { content: 'texto editado sin guardar' });

    // «cambiar de vista»: el componente se desmonta (nadie suscrito) y al volver llama a activate
    const before = calls.read.length;
    store.activate('W');
    await flush();
    const s = store.snapshot('W');
    expect(s.tabs.map((t) => t.path)).toEqual(['a.txt', 'b.txt', 'c.txt']);
    expect(s.active).toBe('b.txt');
    expect(s.tabs[1].content).toBe('texto editado sin guardar');
    expect(isDirty(s.tabs[1])).toBe(true);
    expect(calls.read.slice(before).sort()).toEqual(['a.txt', 'b.txt', 'c.txt']);   // se comprueban todas…
    expect(s.tabs[1].content).toBe('texto editado sin guardar');                  // …pero lo editado no se pisa
  });

  it('una pestaña sin cambios se actualiza si el archivo cambió mientras no se miraba; la modificada nunca se pisa', async () => {
    const { store, disk } = makeStore({ files: fileMap(['a.txt', 'b.txt']) });
    store.open('W', 'a.txt');
    store.open('W', 'b.txt');
    await flush();
    store.patch('W', 'b.txt', { content: 'mi edición' });
    disk['a.txt'] = { content: 'lo cambió un agente', etag: 'e2' };
    disk['b.txt'] = { content: 'lo cambió un agente', etag: 'e2' };
    store.activate('W');
    await flush();
    const [a, b] = store.snapshot('W').tabs;
    expect(a.content).toBe('lo cambió un agente');
    expect(a.etag).toBe('e2');
    expect(b.content).toBe('mi edición');
    expect(b.etag).toBe('e1');
    await store.save('W');                                                   // la activa es b: el etag viejo → conflicto, no sobrescribe
    expect(store.snapshot('W').tabs[1].conflict).toBe(true);
    expect(disk['b.txt'].content).toBe('lo cambió un agente');
  });

  it('un archivo que desapareció se descarta si estaba limpio y avisa (sin perder el texto) si estaba modificado', async () => {
    const { store, disk } = makeStore({ files: fileMap(['a.txt', 'b.txt']) });
    store.open('W', 'a.txt');
    store.open('W', 'b.txt');
    await flush();
    store.patch('W', 'b.txt', { content: 'mi edición' });
    delete disk['a.txt'];
    delete disk['b.txt'];
    store.activate('W');
    await flush();
    const s = store.snapshot('W');
    expect(s.tabs.map((t) => t.path)).toEqual(['b.txt']);
    expect(s.tabs[0].content).toBe('mi edición');
    expect(s.tabs[0].error).toMatch(/ya no existe/);
  });

  it('una carga en vuelo al desmontar termina igualmente y no se duplica al volver', async () => {
    const { store, calls } = makeStore({ files: fileMap(['a.txt']), delay: 30 });
    store.open('W', 'a.txt');                                                // sigue cargando…
    store.activate('W');                                                     // …y la vista se vuelve a montar
    await new Promise((r) => setTimeout(r, 80));
    expect(store.snapshot('W').tabs[0]).toMatchObject({ kind: 'text', content: 'contenido de a.txt' });
    expect(calls.read.filter((p) => p === 'a.txt')).toHaveLength(1);
  });

  it('cerrar una pestaña mientras cargaba no la resucita', async () => {
    const { store } = makeStore({ files: fileMap(['a.txt']), delay: 20 });
    store.open('W', 'a.txt');
    store.close('W', 'a.txt');
    await new Promise((r) => setTimeout(r, 60));
    expect(store.snapshot('W').tabs).toEqual([]);
  });
});

describe('viewerStore: varios workspaces', () => {
  it('cada workspace conserva sus pestañas y su texto sin guardar al cambiar y volver', async () => {
    const { store } = makeStore({ files: fileMap(['a.txt', 'b.txt']) });
    store.open('A', 'a.txt');
    store.open('B', 'b.txt');
    await flush();
    store.patch('A', 'a.txt', { content: 'sin guardar en A' });
    expect(store.snapshot('A').tabs.map((t) => t.path)).toEqual(['a.txt']);
    expect(store.snapshot('B').tabs.map((t) => t.path)).toEqual(['b.txt']);
    expect(store.snapshot('A').tabs[0].content).toBe('sin guardar en A');
    expect(store.snapshot('B').tabs[0].content).toBe('contenido de b.txt');
    expect(store.unsaved()).toEqual([{ workspace: 'A', path: 'a.txt' }]);
  });

  it('guardar en un workspace no toca el estado del otro', async () => {
    const { store } = makeStore({ files: fileMap(['x.txt']) });
    store.open('A', 'x.txt');
    store.open('B', 'x.txt');
    await flush();
    store.patch('A', 'x.txt', { content: 'cambio en A' });
    store.patch('B', 'x.txt', { content: 'cambio en B' });
    await store.save('A');
    expect(isDirty(store.snapshot('A').tabs[0])).toBe(false);
    expect(isDirty(store.snapshot('B').tabs[0])).toBe(true);
  });
});

describe('viewerStore: recarga de la página', () => {
  it('las pestañas y la activa se reabren desde las rutas recordadas, sin contenido en el almacenamiento', async () => {
    const storage = memoryStorage();
    const first = makeStore({ files: fileMap(['a.txt', 'b.txt']), storage });
    first.store.open('W', 'a.txt');
    first.store.open('W', 'b.txt');
    await flush();
    first.store.select('W', 'a.txt');
    first.store.patch('W', 'b.txt', { content: 'SECRETO sin guardar' });
    expect(JSON.stringify(storage.data)).not.toContain('SECRETO');
    expect(JSON.stringify(storage.data)).not.toContain('contenido de');

    // recarga: almacén nuevo (memoria vacía) sobre el mismo sessionStorage
    const second = makeStore({ files: fileMap(['a.txt', 'b.txt']), storage });
    second.store.ensure('W');
    expect(second.store.snapshot('W').tabs.map((t) => t.kind)).toEqual(['loading', 'loading']);
    second.store.activate('W');
    await flush();
    const s = second.store.snapshot('W');
    expect(s.tabs.map((t) => [t.path, t.kind])).toEqual([['a.txt', 'text'], ['b.txt', 'text']]);
    expect(s.active).toBe('a.txt');
    expect(s.tabs[1].content).toBe('contenido de b.txt');                    // el texto sin guardar NO vuelve: se pierde con aviso
  });

  it('un archivo borrado entre recargas se descarta sin error ni pestaña rota', async () => {
    const storage = memoryStorage();
    const first = makeStore({ files: fileMap(['a.txt', 'b.txt']), storage });
    first.store.open('W', 'a.txt');
    first.store.open('W', 'b.txt');
    await flush();
    const second = makeStore({ files: fileMap(['b.txt']), storage });
    second.store.activate('W');
    await flush();
    const s = second.store.snapshot('W');
    expect(s.tabs.map((t) => [t.path, t.kind, t.error])).toEqual([['b.txt', 'text', null]]);
    expect(s.active).toBe('b.txt');
    expect(second.explorer.load('W').viewer.tabs).toEqual(['b.txt']);          // y deja de recordarse
  });

  it('un fallo que no es «no existe» deja la pestaña con su error y se reintenta al volver', async () => {
    const storage = memoryStorage();
    makeStore({ files: fileMap(['a.txt']), storage }).store.open('W', 'a.txt');
    await flush();
    let fail = true;
    const explorer = createExplorerStore(storage);
    const store = createViewerStore({
      read: async () => { if (fail) throw Object.assign(new Error('HTTP 500'), { status: 500 }); return { kind: 'text', content: 'ok', etag: 'e1' }; },
      write: async () => ({ etag: 'e2' }),
      load: (ws) => explorer.load(ws).viewer,
      persist: (ws, viewer) => explorer.save(ws, (s) => ({ ...s, viewer })),
    });
    store.activate('W');
    await flush();
    expect(store.snapshot('W').tabs[0]).toMatchObject({ kind: 'error', error: 'HTTP 500' });
    fail = false;
    store.activate('W');
    await flush();
    expect(store.snapshot('W').tabs[0]).toMatchObject({ kind: 'text', content: 'ok', error: null });
  });
});

describe('viewerStore: renombrar y borrar desde el explorador', () => {
  it('la pestaña sigue al archivo renombrado, con su texto sin guardar, y se recuerda con la ruta nueva', async () => {
    const { store, explorer } = makeStore({ files: fileMap(['src/a.js']) });
    store.open('W', 'src/a.js');
    await flush();
    store.patch('W', 'src/a.js', { content: 'cambios' });
    store.entryChanged('W', { action: 'rename', path: 'src', newPath: 'lib' });
    const s = store.snapshot('W');
    expect(s.tabs[0]).toMatchObject({ path: 'lib/a.js', content: 'cambios' });
    expect(s.active).toBe('lib/a.js');
    expect(explorer.load('W').viewer.tabs).toEqual(['lib/a.js']);
  });

  it('borrar la carpeta cierra sus pestañas y las deja de recordar', async () => {
    const { store, explorer } = makeStore({ files: fileMap(['src/a.js', 'r.md']) });
    store.open('W', 'r.md');
    store.open('W', 'src/a.js');
    await flush();
    store.entryChanged('W', { action: 'delete', path: 'src' });
    expect(store.snapshot('W').tabs.map((t) => t.path)).toEqual(['r.md']);
    expect(store.snapshot('W').active).toBe('r.md');
    expect(explorer.load('W').viewer).toEqual({ tabs: ['r.md'], active: 'r.md' });
    store.entryChanged('W', { action: 'delete' });                              // sin ruta: se ignora
    store.entryChanged('W', undefined);
  });
});

describe('viewerStore: guardar', () => {
  it('guardar marca como guardado lo enviado; si se siguió escribiendo durante el guardado, lo nuevo sigue pendiente', async () => {
    const { store, disk } = makeStore({ files: fileMap(['a.txt']) });
    store.open('W', 'a.txt');
    await flush();
    store.patch('W', 'a.txt', { content: 'versión 1' });
    const saving = store.save('W');
    store.patch('W', 'a.txt', { content: 'versión 2' });                        // se sigue escribiendo mientras se guarda
    await saving;
    const t = store.snapshot('W').tabs[0];
    expect(disk['a.txt'].content).toBe('versión 1');
    expect(t.original).toBe('versión 1');
    expect(isDirty(t)).toBe(true);
    expect(store.unsaved()).toHaveLength(1);
  });

  it('un conflicto se puede resolver sobrescribiendo (force) y entonces ya no hay nada pendiente', async () => {
    const { store, disk } = makeStore({ files: fileMap(['a.txt']) });
    store.open('W', 'a.txt');
    await flush();
    disk['a.txt'] = { content: 'externo', etag: 'e9' };
    store.patch('W', 'a.txt', { content: 'mío' });
    await store.save('W');
    expect(store.snapshot('W').tabs[0].conflict).toBe(true);
    await store.save('W', true);
    expect(disk['a.txt'].content).toBe('mío');
    expect(store.snapshot('W').tabs[0]).toMatchObject({ conflict: false });
    expect(store.unsaved()).toEqual([]);
  });
});

describe('viewerStore: aviso al recargar la página', () => {
  const fakeWindow = () => {
    const handlers = [];
    return { handlers, addEventListener: (type, fn) => handlers.push([type, fn]) };
  };

  it('con texto sin guardar bloquea la salida; sin él, no', async () => {
    const win = fakeWindow();
    globalThis.window = win;
    try {
      const store = createViewerStore({ read: async () => ({ kind: 'text', content: 'x', etag: 'e' }), write: async () => ({ etag: 'e2' }), guardUnload: true });
      const [type, onUnload] = win.handlers[0];
      expect(type).toBe('beforeunload');
      const ev = () => ({ prevented: false, returnValue: undefined, preventDefault() { this.prevented = true; } });
      let e = ev();
      onUnload(e);
      expect(e.prevented).toBe(false);

      store.open('W', 'a.txt');
      await flush();
      store.patch('W', 'a.txt', { content: 'cambiado' });
      e = ev();
      onUnload(e);
      expect(e.prevented).toBe(true);
      expect(e.returnValue).toBe('');

      await store.save('W');
      e = ev();
      onUnload(e);
      expect(e.prevented).toBe(false);
    } finally {
      delete globalThis.window;
    }
  });
});
