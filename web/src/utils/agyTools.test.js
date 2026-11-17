import { describe, expect, it } from 'vitest';
import { agyNotice, agySelection, confirmGroup, imageFromEvent, isAgy, missionFields, slowModelNotice, toggleGroup, withoutAgyTools } from './agyTools.js';
import { CONFIG_FIELDS } from './chatStore.js';
import { pickConfig } from './panelLayout.js';

const CATALOG = [{ id: 'images', title: 'Generación de imágenes' }, { id: 'web_search', title: 'Búsqueda web' }];
const cfg = (extra = {}) => ({ llmProvider: 'agy', agyTools: [], agyToolsAck: [], ...extra });

describe('agySelection', () => {
  it('solo cuenta con el proveedor agy', () => {
    expect(agySelection({ llmProvider: 'groq', agyTools: ['images'], agyToolsAck: ['images'] })).toEqual({ tools: [], ack: [], pending: [] });
    expect(agySelection(undefined)).toEqual({ tools: [], ack: [], pending: [] });
    expect(isAgy('Antigravity') && isAgy('agy') && !isAgy('claude_cli')).toBe(true);
  });
  it('separa lo confirmado de lo pendiente y descarta basura y repetidos', () => {
    const s = agySelection(cfg({ agyTools: ['images', 'web_search', 'images', 5, '', null], agyToolsAck: ['web_search', 'otra'] }));
    expect(s).toEqual({ tools: ['images', 'web_search'], ack: ['web_search'], pending: ['images'] });
  });
  it('una confirmación sin su grupo activado no cuenta', () => {
    expect(agySelection(cfg({ agyTools: [], agyToolsAck: ['images'] }))).toEqual({ tools: [], ack: [], pending: [] });
  });
});

describe('toggleGroup y confirmGroup', () => {
  it('activar deja el grupo sin confirmar; confirmar y desactivar se encadenan', () => {
    let c = cfg();
    c = { ...c, ...toggleGroup(c, 'images', true) };
    expect(agySelection(c).pending).toEqual(['images']);
    c = { ...c, ...confirmGroup(c, 'images', true) };
    expect(agySelection(c)).toMatchObject({ ack: ['images'], pending: [] });
    c = { ...c, ...toggleGroup(c, 'images', false) };
    expect(c.agyTools).toEqual([]);                                              // desactivar quita también la confirmación
    expect(c.agyToolsAck).toEqual([]);
    c = { ...c, ...toggleGroup(c, 'images', true) };
    expect(agySelection(c).pending).toEqual(['images']);                         // y al reactivarlo hay que confirmar de nuevo
  });
  it('no se puede confirmar un grupo que no está activado', () => {
    expect(confirmGroup(cfg(), 'images', true).agyToolsAck).toEqual([]);
  });
  it('retirar la confirmación vuelve a bloquear el envío', () => {
    const c = cfg({ agyTools: ['web_search'], agyToolsAck: ['web_search'] });
    const after = { ...c, ...confirmGroup(c, 'web_search', false) };
    expect(agyNotice(agySelection(after), CATALOG)).toMatchObject({ blocked: true, level: 'error' });
  });
  it('activar varias veces el mismo grupo no lo duplica', () => {
    let c = cfg();
    for (let i = 0; i < 3; i += 1) c = { ...c, ...toggleGroup(c, 'images', true) };
    expect(c.agyTools).toEqual(['images']);
  });
});

describe('agyNotice', () => {
  it('sin herramientas no hay aviso', () => {
    expect(agyNotice(agySelection(cfg()), CATALOG)).toBeNull();
  });
  it('con alguna sin confirmar bloquea el envío y dice cuáles', () => {
    const n = agyNotice(agySelection(cfg({ agyTools: ['images', 'web_search'], agyToolsAck: ['web_search'] })), CATALOG);
    expect(n.blocked).toBe(true);
    expect(n.message).toContain('Generación de imágenes');
    expect(n.message).not.toContain('Búsqueda web');
    expect(n.message).toContain('sin la política de PRAXEON');
  });
  it('con todo confirmado avisa pero no bloquea', () => {
    const n = agyNotice(agySelection(cfg({ agyTools: ['images'], agyToolsAck: ['images'] })), CATALOG);
    expect(n).toMatchObject({ blocked: false, level: 'warn' });
    expect(n.message).toContain('sin pasar por la política, el supervisor ni el sandbox');
  });
});

describe('missionFields', () => {
  it('manda solo lo confirmado, y nada sin confirmar ni sin agy', () => {
    expect(missionFields(cfg({ agyTools: ['images', 'web_search'], agyToolsAck: ['web_search'] }))).toEqual({ agy_tools: ['web_search'], agy_tools_ack: ['web_search'] });
    expect(missionFields(cfg({ agyTools: ['images'], agyToolsAck: [] }))).toEqual({});
    expect(missionFields({ llmProvider: 'openai', agyTools: ['images'], agyToolsAck: ['images'] })).toEqual({});
    expect(missionFields(cfg())).toEqual({});
  });
});

describe('withoutAgyTools', () => {
  it('quita herramientas y confirmaciones y respeta el resto', () => {
    expect(withoutAgyTools({ llmProvider: 'agy', maxSteps: 5, agyTools: ['images'], agyToolsAck: ['images'] })).toEqual({ llmProvider: 'agy', maxSteps: 5 });
    expect(withoutAgyTools(null)).toBeNull();
  });
});

describe('nada se recuerda', () => {
  it('ni la configuración que se guarda en el navegador ni la de los paneles incluye las herramientas de agy ni su confirmación', () => {
    expect(CONFIG_FIELDS.filter((k) => /agy/i.test(k))).toEqual([]);
    const picked = pickConfig({ llmProvider: 'agy', maxSteps: 5, agyTools: ['images'], agyToolsAck: ['images'], agy_tools: ['images'] });
    expect(picked).toMatchObject({ llmProvider: 'agy', maxSteps: 5 });
    expect(Object.keys(picked).filter((k) => /agy_?tools/i.test(k))).toEqual([]);
  });
});

describe('imageFromEvent', () => {
  it('acepta solo rutas de la carpeta de imágenes generadas', () => {
    expect(imageFromEvent({ rel_path: '.praxeon/generated/s-1/circulo.jpg', name: 'circulo.jpg', width: 1024, height: 1024 })).toEqual({ rel_path: '.praxeon/generated/s-1/circulo.jpg', name: 'circulo.jpg', width: 1024, height: 1024 });
    for (const bad of ['.praxeon/attachments/s/a.png', '../x.png', '.praxeon/generated/../../x.png', '/etc/passwd', 'C:/x.png', 5, null, undefined]) {
      expect(imageFromEvent({ rel_path: bad })).toBeNull();
    }
    expect(imageFromEvent(undefined)).toBeNull();
  });
});

describe('slowModelNotice', () => {
  it('avisa de los modelos lentos medidos y no de los rápidos', () => {
    for (const slow of ['gemini-4-hydrogen-xhigh', 'gemini-3.8-flash-high', 'claude-opus-5-5-high', 'gemini-4-argon-low', 'gemini-4-carbon-medium', 'GEMINI-4-HYDROGEN-XHIGH']) {
      expect(slowModelNotice(slow), slow).toMatch(/3-5 veces más/);
    }
    for (const fast of ['gemini-3.8-flash-medium', 'gemini-3.8-flash-low', 'gemini-4-hydrogen-low', 'claude-sonnet-5-5-low', 'gpt-oss-120b-medium', '', null, undefined]) {
      expect(slowModelNotice(fast), String(fast)).toBeNull();
    }
    expect(slowModelNotice('gemini-4-hydrogen-xhigh')).toContain('gemini-3.8-flash-medium');
  });
  it('la lista de la interfaz empieza por el predeterminado del servidor y deja los lentos al final', async () => {
    const { BUILTIN_MODELS } = await import('./modelCatalog.js');
    const list = BUILTIN_MODELS.agy;
    expect(list[0]).toBe('gemini-3.8-flash-medium');
    const firstSlow = list.findIndex((m) => slowModelNotice(m));
    expect(list.slice(0, firstSlow).every((m) => !slowModelNotice(m))).toBe(true);
    expect(list.slice(firstSlow).every((m) => slowModelNotice(m))).toBe(true);
  });
});
