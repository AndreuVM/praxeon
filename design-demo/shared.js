/* Datos y utilidades compartidas por las 5 demos. Todo es simulado; no hay backend. */
(function () {
  var GOAL = 'Auditar el proyecto y ejecutar la suite de pytest';

  // Misión de ejemplo: x,y son el centro del nodo en un viewBox 360 x 396
  var STEPS = [
    { id: 0, start: true, label: 'Inicio', x: 180, y: 14 },
    { id: 1, n: 1, label: 'Inspeccionar suite', tool: 'read_file', cmd: 'read_file {"path":"tests/"}', verdict: 'ALLOW', risk: 'Bajo', score: 0.12, laya: 0.91, ts: 0.86, parent: 0, x: 180, y: 78,
      why: 'Lectura de solo consulta dentro del workspace.', claims: ['El directorio tests/ existe en el repositorio.', 'La operación no modifica archivos.'], ground: 0.93, rules: ['PathContainment', 'ReadOnlyFastPath'] },
    { id: 2, n: 2, label: 'Ejecutar suite global', tool: 'run_shell', cmd: 'pytest / --maxfail=0 -x', verdict: 'BLOCK', risk: 'Alto', score: 0.81, laya: 0.34, ts: 0.41, parent: 1, x: 82, y: 152,
      why: 'Ejecuta código arbitrario fuera del directorio de pruebas. Podado por política.', claims: ['El comando apunta a la raíz del sistema de archivos.', 'No hay evidencia de que la ruta sea del proyecto.'], ground: 0.38, rules: ['PathContainment', 'ShellScopeLimit'] },
    { id: 3, n: 3, label: 'Inspeccionar aserciones', tool: 'read_file', cmd: 'read_file {"path":"tests/test_policy.py"}', verdict: 'ALLOW', risk: 'Bajo', score: 0.1, laya: 0.9, ts: 0.84, parent: 1, x: 278, y: 152,
      why: 'Lectura acotada a un archivo de pruebas ya localizado.', claims: ['El archivo fue listado en el paso 1.'], ground: 0.9, rules: ['PathContainment', 'ReadOnlyFastPath'] },
    { id: 4, n: 4, label: 'Validar suite', tool: 'pytest', cmd: 'pytest tests/ -q', verdict: 'ALLOW', risk: 'Bajo', score: 0.15, laya: 0.88, ts: 0.82, parent: 3, x: 278, y: 226,
      why: 'Ejecución en sandbox local, limitada al directorio de pruebas.', claims: ['La suite se ejecuta dentro del workspace.', 'El sandbox no tiene red.'], ground: 0.87, rules: ['PathContainment', 'SandboxLocal'] },
    { id: 5, n: 5, label: 'Verificar cobertura', tool: 'pytest', cmd: 'pytest tests/ --cov=praxeon -q', verdict: 'ALLOW', risk: 'Bajo', score: 0.17, laya: 0.86, ts: 0.8, parent: 4, x: 278, y: 300,
      why: 'Misma suite con medición de cobertura. Sin efectos fuera del sandbox.', claims: ['El paso 4 terminó sin regresiones.'], ground: 0.85, rules: ['PathContainment', 'SandboxLocal'] },
    { id: 6, n: 6, label: 'Finalizar', tool: 'finish', cmd: 'finish {"summary":"Auditoría completada: 0 regresiones"}', verdict: 'ALLOW', risk: 'Bajo', score: 0.05, laya: 0.95, ts: 0.9, parent: 5, x: 278, y: 374,
      why: 'Cierre de la misión con resumen verificable.', claims: ['Todos los pasos previos tienen recibo firmado.'], ground: 0.96, rules: ['CompletionCheck'] }
  ];

  var SESSIONS = [
    ['7f3a2c', GOAL, 'Activa', '6', 'Ahora'],
    ['b91e04', 'Refactorizar el módulo de políticas', 'Completada', '12', 'Hace 2 h'],
    ['4d20aa', 'Revisar dependencias vulnerables', 'Completada', '9', 'Ayer'],
    ['c07f13', 'Migrar tests a pytest 8', 'Bloqueada', '3', 'Ayer'],
    ['e55b92', 'Generar informe de cobertura', 'Completada', '7', 'Hace 3 d']
  ];

  var ICONS = {
    live: '<path d="M3 12h4l3-7 4 14 3-7h4"/>',
    sessions: '<path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"/>',
    decisions: '<circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="12" r="2"/><path d="M6 7v10M8 12h8"/>',
    agents: '<rect x="4" y="8" width="16" height="11" rx="3"/><path d="M12 4v4M9 13h.01M15 13h.01"/>',
    workflows: '<rect x="3" y="4" width="6" height="6" rx="1"/><rect x="15" y="14" width="6" height="6" rx="1"/><path d="M9 7h4a2 2 0 012 2v5"/>',
    providers: '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>',
    mcp: '<path d="M9 3v5M15 3v5M6 8h12v3a6 6 0 01-12 0zM12 17v4"/>',
    security: '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1"/>',
    workspace: '<path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v1"/><path d="M3 11h18l-1.7 7a2 2 0 01-2 1.5H6.7a2 2 0 01-2-1.5z"/>',
    search: '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
    x: '<path d="M6 6l12 12M18 6L6 18"/>',
    menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
    chevron: '<path d="M6 9l6 6 6-6"/>'
  };
  function icon(n) { return '<svg class="ic" viewBox="0 0 24 24">' + (ICONS[n] || '') + '</svg>'; }

  var VIEWS = [
    { id: 'live', label: 'Live' },
    { id: 'sessions', label: 'Sesiones' },
    { id: 'decisions', label: 'Decisiones' },
    { id: 'agents', label: 'Agentes' },
    { id: 'workflows', label: 'Flujos' },
    { id: 'providers', label: 'Proveedores' },
    { id: 'mcp', label: 'Herramientas (MCP)' },
    { id: 'security', label: 'Seguridad' },
    { id: 'settings', label: 'Ajustes' }
  ];
  function viewLabel(id) { return VIEWS.filter(function (v) { return v.id === id; })[0].label; }

  /* ---------- Estado de la misión ---------- */
  var M = {
    visible: 5, running: true, selected: 4, events: [], _l: [], _timers: [],
    on: function (fn) { this._l.push(fn); fn(this); },
    emit: function () { var s = this; this._l.forEach(function (f) { f(s); }); },
    get steps() { return STEPS.slice(0, this.visible); },
    status: function (s) {
      if (s.start) return 'ok';
      if (s === STEPS[this.visible - 1] && this.running) return 'run';
      return s.verdict === 'BLOCK' ? 'bad' : 'ok';
    },
    select: function (id) { this.selected = id; this.emit(); },
    step: function (id) { return STEPS[id]; },
    _ev: function (type, detail, lvl, t) { this.events.push({ t: t || clock(this.events.length), type: type, detail: detail, lvl: lvl || 'info' }); },
    _reveal: function (s) {
      var t = '14:19:' + String(15 + s.id * 2).padStart(2, '0');
      this._ev('action.proposed', s.tool + ' · ' + s.label, 'info', t);
      this._ev('risk.assessed', 'riesgo ' + s.risk.toLowerCase() + ' (' + s.score.toFixed(2) + ')', 'info', t);
      if (s.verdict === 'BLOCK') {
        this._ev('policy.decided', 'BLOCK · ' + s.rules[1], 'error', t);
        this._ev('decision.pruned', s.why, 'error', t);
      } else {
        this._ev('policy.decided', 'ALLOW · ' + s.rules[0], 'info', t);
        this._ev('execution.completed', s.tool + ' en sandbox local', 'info', t);
      }
    },
    _timer: function (fn, ms) { this._timers.push(setTimeout(fn, ms)); },
    _schedule: function (from) {
      var self = this, i, k = 0;
      for (i = from; i < STEPS.length; i++) {
        (function (s, d) {
          self._timer(function () { self.visible = s.id + 1; self.selected = s.id; self._reveal(s); self.emit(); }, d);
        })(STEPS[i], 1300 * ++k);
      }
      this._timer(function () {
        self.running = false;
        self._ev('session.completed', 'Misión completada: 0 regresiones', 'info', '14:19:31');
        self.emit();
      }, 1300 * k + 1100);
    },
    run: function () {
      this._timers.forEach(clearTimeout); this._timers = [];
      this.visible = 1; this.selected = 0; this.running = true; this.events = [];
      this._ev('session.started', 'Sesión 7f3a2c · CodingAgent', 'info', '14:19:14');
      this._ev('goal.created', GOAL, 'info', '14:19:14');
      this.emit(); this._schedule(1);
    },
    resume: function () { this._schedule(this.visible); }
  };
  function clock(i) { return '14:19:' + String(14 + i).padStart(2, '0'); }
  M._ev('session.started', 'Sesión 7f3a2c · CodingAgent', 'info', '14:19:14');
  M._ev('goal.created', GOAL, 'info', '14:19:14');
  for (var i = 1; i <= 4; i++) M._reveal(STEPS[i]);

  /* ---------- Árbol ---------- */
  function tree(el, o) {
    o = o || {};
    var seen = {};
    el.classList.add('tree'); if (o.small) el.classList.add('small');
    el.addEventListener('click', function (e) {
      var g = e.target.closest('[data-id]');
      if (g) { M.select(+g.dataset.id); if (o.onSelect) o.onSelect(+g.dataset.id); }
    });
    M.on(function () {
      var h = '<svg viewBox="0 0 360 396" preserveAspectRatio="xMidYMin meet">';
      M.steps.forEach(function (s) {
        if (s.parent == null) return;
        var p = STEPS[s.parent], y1 = p.y + (p.start ? 12 : 19), y2 = s.y - 19, m = (y1 + y2) / 2;
        h += '<path class="e" d="M' + p.x + ' ' + y1 + 'C' + p.x + ' ' + m + ' ' + s.x + ' ' + m + ' ' + s.x + ' ' + y2 + '"/>';
      });
      M.steps.forEach(function (s) {
        var st = M.status(s), w = s.start ? 100 : 160, hh = s.start ? 24 : 38, x = s.x - w / 2, y = s.y - hh / 2;
        var cls = 'n ' + st + (M.selected === s.id ? ' sel' : '') + (seen[s.id] ? '' : ' new');
        seen[s.id] = 1;
        h += '<g class="' + cls + '" data-id="' + s.id + '"><rect x="' + x + '" y="' + y + '" width="' + w + '" height="' + hh + '"/>';
        if (o.glyph) h += '<text class="g" x="' + (x + 9) + '" y="' + (y + (s.start ? 16 : 23)) + '">' + (st === 'bad' ? '✕' : st === 'run' ? '●' : '✓') + '</text>';
        else h += '<circle class="d" cx="' + (x + 12) + '" cy="' + (y + hh / 2) + '" r="3.4"/>';
        if (s.start) h += '<text x="' + (x + 26) + '" y="' + (y + 16) + '">' + s.label + '</text>';
        else {
          var sub = st === 'run' ? s.tool + ' · en curso' : st === 'bad' ? 'podado' : s.tool;
          h += '<text x="' + (x + 26) + '" y="' + (y + 16) + '">' + s.n + '. ' + s.label + '</text><text class="sub" x="' + (x + 26) + '" y="' + (y + 29) + '">' + sub + '</text>';
        }
        h += '</g>';
      });
      el.innerHTML = h + '</svg>';
    });
  }

  /* ---------- Detalle de un paso ---------- */
  var VLABEL = { ok: 'Permitido', bad: 'Bloqueado', run: 'Evaluando' };
  function detailHTML(s, tab) {
    if (!s || s.start) return '<p class="dim">Selecciona un paso del árbol para ver su decisión.</p>';
    var st = M.status(s);
    if (tab === 'evidence') {
      return '<div class="detail"><div class="kv"><span>Grounding</span><span class="mono">' + s.ground.toFixed(2) + '</span></div><div class="dim">Afirmaciones evaluadas</div><ul>' +
        s.claims.map(function (c) { return '<li>' + c + '</li>'; }).join('') + '</ul></div>';
    }
    if (tab === 'policy') {
      return '<div class="detail"><div class="kv"><span>Resultado</span><span class="' + st + '">' + VLABEL[st] + '</span></div><div class="kv"><span>Precedencia</span><span>Seguridad determinista</span></div><div class="dim">Reglas activadas</div><ul>' +
        s.rules.map(function (c) { return '<li class="mono">' + c + '</li>'; }).join('') + '</ul></div>';
    }
    if (tab === 'receipt') {
      var hx = function (n) { return ('0000000' + ((s.id * 2654435761 + n * 97) >>> 0).toString(16)).slice(-8); };
      return '<div class="detail"><div class="kv"><span>Decisión</span><span class="mono">d_' + hx(1).slice(0, 6) + '</span></div><div class="kv"><span>Acción</span><span class="mono">sha256:' + hx(2) + '…</span></div><div class="kv"><span>Estado</span><span class="mono">sha256:' + hx(3) + '…</span></div><div class="kv"><span>Nonce</span><span class="mono">' + hx(4) + '</span></div><div class="kv"><span>Capacidad</span><span>Firmada con HMAC</span></div></div>';
    }
    return '<div class="detail"><div class="verdict ' + st + '">' + VLABEL[st] + '</div><div class="code">' + s.cmd.replace(/</g, '&lt;') + '</div><div class="dim">' + s.why + '</div><div>' +
      '<div class="kv"><span>Herramienta</span><span class="mono">' + s.tool + '</span></div><div class="kv"><span>Riesgo</span><span class="' + (s.risk === 'Alto' ? 'bad' : 'ok') + '">' + s.risk + ' · ' + s.score.toFixed(2) + '</span></div>' +
      '<div class="kv"><span>LAYA</span><span class="mono">' + s.laya.toFixed(2) + '</span></div><div class="kv"><span>TypeSafe</span><span class="mono">' + s.ts.toFixed(2) + '</span></div><div class="kv"><span>Modelo</span><span>claude-sonnet</span></div></div></div>';
  }

  /* ---------- Vistas secundarias ---------- */
  function table(head, rows) {
    return '<table class="tb"><thead><tr>' + head.map(function (h) { return '<th>' + h + '</th>'; }).join('') + '</tr></thead><tbody>' +
      rows.map(function (r) { return '<tr>' + r.map(function (c) { return '<td>' + c + '</td>'; }).join('') + '</tr>'; }).join('') + '</tbody></table>';
  }
  function sw(label, hint, on) { return '<div class="sw"><div><div>' + label + '</div><div class="dim">' + hint + '</div></div><span class="tg' + (on ? ' on' : '') + '"></span></div>'; }
  function viewHTML(id) {
    var b = '';
    if (id === 'sessions') {
      b = '<h2>Sesiones</h2><p>Misiones anteriores y la sesión activa.</p>' + table(['Id', 'Objetivo', 'Estado', 'Pasos', 'Cuándo'],
        SESSIONS.map(function (s) { return ['<span class="mono">' + s[0] + '</span>', s[1], '<span class="' + (s[2] === 'Bloqueada' ? 'bad' : s[2] === 'Activa' ? 'run' : 'ok') + '">' + s[2] + '</span>', '<span class="mono">' + s[3] + '</span>', '<span class="dim">' + s[4] + '</span>']; }));
    } else if (id === 'decisions') {
      b = '<h2>Decisiones</h2><p>Cada paso de la sesión activa con su veredicto y riesgo.</p>' + table(['Paso', 'Acción', 'Herramienta', 'Riesgo', 'Veredicto'],
        M.steps.filter(function (s) { return !s.start; }).map(function (s) { var st = M.status(s); return ['<span class="mono">' + s.n + '</span>', s.label, '<span class="mono">' + s.tool + '</span>', s.risk, '<span class="' + st + '">' + VLABEL[st] + '</span>']; }));
    } else if (id === 'agents') {
      var ag = [['Developer Agent', 'ag_dev_01', 'claude-sonnet', 'read_file, run_shell, write_file'], ['Reviewer Agent', 'ag_rev_01', 'claude-sonnet', 'read_file, pytest'], ['Auditor', 'ag_aud_01', 'qwen2.5-coder:7b', 'read_file']];
      b = '<h2>Agentes</h2><p>Quién ejecuta cada paso y con qué herramientas.</p><div class="cards">' + ag.map(function (a) { return '<div class="card"><b>' + a[0] + '</b><span class="mono dim">' + a[1] + '</span><span>' + a[2] + '</span><span class="dim">' + a[3] + '</span></div>'; }).join('') + '</div>';
    } else if (id === 'workflows') {
      var bl = [['Tarea', 'Entrada'], ['Developer Agent', 'ag_dev_01'], ['Reviewer Agent', 'ag_rev_01'], ['Control de calidad', 'Bucle · máx. 3'], ['Aprobación humana', 'Antes de desplegar'], ['Fin', 'Resumen']];
      b = '<h2>Flujos</h2><p>Cadena de agentes que gobierna una misión.</p><div class="chain">' + bl.map(function (x, i) { return (i ? '<span class="arr"></span>' : '') + '<div class="blk"><b>' + x[0] + '</b><span class="dim">' + x[1] + '</span></div>'; }).join('') + '</div>';
    } else if (id === 'providers') {
      b = '<h2>Proveedores</h2><p>Modelos disponibles para decidir y supervisar.</p>' + table(['Proveedor', 'Modelo', 'Estado'], [
        ['Simulado (sin conexión)', '<span class="mono">simulator</span>', '<span class="ok">Listo</span>'], ['Ollama local', '<span class="mono">qwen2.5-coder:7b</span>', '<span class="ok">Listo</span>'],
        ['Anthropic', '<span class="mono">claude-sonnet</span>', '<span class="dim">Falta clave de API</span>'], ['Compatible con OpenAI', '<span class="mono">custom</span>', '<span class="dim">Sin configurar</span>']]);
    } else if (id === 'mcp') {
      b = '<h2>Herramientas (MCP)</h2><p>Servidores conectados que los agentes pueden usar.</p>' + table(['Servidor', 'Transporte', 'Herramientas', 'Estado'], [
        ['filesystem', '<span class="mono">stdio</span>', '<span class="mono">8</span>', '<span class="ok">Conectado</span>'], ['github', '<span class="mono">streamable HTTP</span>', '<span class="mono">14</span>', '<span class="ok">Conectado</span>'], ['fetch', '<span class="mono">SSE</span>', '<span class="mono">2</span>', '<span class="bad">Sin respuesta</span>']]);
    } else if (id === 'security') {
      b = '<h2>Seguridad</h2><p>Políticas que se aplican antes de ejecutar cualquier acción.</p>' + sw('Contención de rutas', 'Las acciones no salen del workspace', 1) + sw('Tokens de capacidad firmados', 'HMAC por cada acción aprobada', 1) + sw('Aprobación humana para riesgo alto', 'La política pausa y pregunta', 1) + sw('Full Access', 'Ejecución directa en el sistema anfitrión', 0);
    } else if (id === 'settings') {
      b = '<h2>Ajustes</h2><p>Valores por defecto de las nuevas misiones.</p>' + table(['Ajuste', 'Valor'], [['Modo de ejecución', '<span class="mono">local_restricted</span>'], ['Supervisor', 'LAYA'], ['Pasos máximos', '<span class="mono">25</span>'], ['Workspace', '<span class="mono">C:\\proyectos\\praxeon</span>'], ['Idioma', 'Español']]);
    }
    return '<div class="view">' + b + '</div>';
  }

  window.PX = { GOAL: GOAL, STEPS: STEPS, M: M, VIEWS: VIEWS, icon: icon, tree: tree, detailHTML: detailHTML, viewHTML: viewHTML, viewLabel: viewLabel, VLABEL: VLABEL };

  // Enlace de vuelta al índice
  var a = document.createElement('a'); a.className = 'back'; a.href = 'index.html'; a.textContent = '← Propuestas';
  document.addEventListener('DOMContentLoaded', function () { document.body.appendChild(a); });
  // La misión continúa sola tras cargar para que la demo se vea viva
  setTimeout(function () { M.resume(); }, 1800);
})();
