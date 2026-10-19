/* PRAXEON · landing. JavaScript sin dependencias: paralaje 3D, secuencia de la ventana, revelado al hacer scroll y copiar. */
(function () {
  'use strict';
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  // ---- Navegación con borde al hacer scroll
  var nav = $('#nav');
  function onScrollNav() { nav.classList.toggle('scrolled', window.scrollY > 8); }

  // ---- Escalado de la escena al ancho disponible
  var stage = $('#stage'), fit = $('#fit'), scene = $('#scene');
  function resize() {
    var k = Math.min(1, stage.clientWidth / 1040);
    fit.style.setProperty('--k', k.toFixed(4));
    stage.style.height = Math.round(Math.max(300, 640 * k + 20)) + 'px';
  }

  // ---- Paralaje: el puntero inclina la escena y mueve cada capa según su profundidad; el scroll la levanta
  var tx = 0, ty = 0, cx = 0, cy = 0, sy = 0, tsy = 0, running = false;
  function frame() {
    cx += (tx - cx) * 0.07;
    cy += (ty - cy) * 0.07;
    sy += (tsy - sy) * 0.12;
    scene.style.setProperty('--ry', (cx * 9).toFixed(3));
    scene.style.setProperty('--rx', (-cy * 6).toFixed(3));
    scene.style.setProperty('--px', cx.toFixed(3));
    scene.style.setProperty('--py', cy.toFixed(3));
    scene.style.setProperty('--sy', sy.toFixed(3));
    var settling = Math.abs(tx - cx) > 0.001 || Math.abs(ty - cy) > 0.001 || Math.abs(tsy - sy) > 0.001;
    if (settling) requestAnimationFrame(frame); else running = false;
  }
  function kick() { if (!running) { running = true; requestAnimationFrame(frame); } }
  function onScrollParallax() {
    var r = stage.getBoundingClientRect();
    tsy = Math.max(0, Math.min(1, -r.top / Math.max(1, r.height) + 0.15));
    kick();
  }
  if (!reduce) {
    var hero = $('#hero');
    hero.addEventListener('pointermove', function (e) {
      if (e.pointerType === 'touch') return;
      var r = hero.getBoundingClientRect();
      tx = ((e.clientX - r.left) / r.width - 0.5) * 2;
      ty = ((e.clientY - r.top) / Math.min(r.height, window.innerHeight) - 0.5) * 2;
      kick();
    });
    hero.addEventListener('pointerleave', function () { tx = 0; ty = 0; kick(); });
    // Sin puntero (móvil): una oscilación suave para que la escena no quede estática
    if (window.matchMedia('(hover: none)').matches) {
      var t0 = performance.now();
      (function sway(t) {
        tx = Math.sin((t - t0) / 2600) * 0.55;
        ty = Math.cos((t - t0) / 3300) * 0.3;
        kick();
        requestAnimationFrame(sway);
      })(t0);
    }
  }

  // ---- Paralaje de las tarjetas de «Producto»: puntero local + posición al hacer scroll
  var vis = $$('.visual').map(function (el) { return { el: el, tx: 0, ty: 0, x: 0, y: 0, s: 0, ts: 0, on: false }; });
  var vRunning = false;
  function vFrame() {
    var again = false;
    vis.forEach(function (v) {
      v.x += (v.tx - v.x) * 0.1; v.y += (v.ty - v.y) * 0.1; v.s += (v.ts - v.s) * 0.12;
      v.el.style.setProperty('--vx', v.x.toFixed(3));
      v.el.style.setProperty('--vy', v.y.toFixed(3));
      v.el.style.setProperty('--vs', v.s.toFixed(3));
      if (Math.abs(v.tx - v.x) > 0.002 || Math.abs(v.ty - v.y) > 0.002 || Math.abs(v.ts - v.s) > 0.002) again = true;
    });
    if (again) requestAnimationFrame(vFrame); else vRunning = false;
  }
  function vKick() { if (!vRunning) { vRunning = true; requestAnimationFrame(vFrame); } }
  function vScroll() {
    var h = window.innerHeight;
    vis.forEach(function (v) {
      var r = v.el.getBoundingClientRect();
      v.ts = Math.max(-1, Math.min(1, (r.top + r.height / 2 - h / 2) / h));
    });
    vKick();
  }
  if (!reduce) {
    vis.forEach(function (v) {
      v.el.addEventListener('pointermove', function (e) {
        if (e.pointerType === 'touch') return;
        var r = v.el.getBoundingClientRect();
        v.tx = ((e.clientX - r.left) / r.width - 0.5) * 2;
        v.ty = ((e.clientY - r.top) / r.height - 0.5) * 2;
        vKick();
      });
      v.el.addEventListener('pointerleave', function () { v.tx = 0; v.ty = 0; vKick(); });
    });
    window.addEventListener('scroll', vScroll, { passive: true });
    window.addEventListener('resize', vScroll);
    vScroll();
  }

  window.addEventListener('scroll', function () { onScrollNav(); if (!reduce) onScrollParallax(); }, { passive: true });
  window.addEventListener('resize', resize);
  resize(); onScrollNav(); if (!reduce) onScrollParallax();

  // ---- Secuencia de la conversación dentro de la ventana (en bucle)
  var items = $$('#thread .seq'), dot = $('#wdot'), timers = [];
  function play() {
    items.forEach(function (el) { el.classList.remove('on'); });
    dot.className = 'dot run';
    items.forEach(function (el) {
      timers.push(setTimeout(function () { el.classList.add('on'); }, +el.dataset.t));
    });
    timers.push(setTimeout(function () { dot.className = 'dot'; }, 8600));
    timers.push(setTimeout(function () {
      items.forEach(function (el) { el.classList.remove('on'); });
    }, 15000));
    timers.push(setTimeout(play, 15900));
  }
  if (reduce) {
    items.forEach(function (el) { el.classList.add('on'); });
    dot.className = 'dot';
  } else {
    // Solo corre mientras la escena es visible
    var visible = true;
    if ('IntersectionObserver' in window) {
      new IntersectionObserver(function (es) {
        var v = es[0].isIntersecting;
        if (v && !visible) play();
        if (!v) { timers.forEach(clearTimeout); timers = []; }
        visible = v;
      }, { threshold: 0.05 }).observe(stage);
    }
    play();
  }

  // ---- Revelado al hacer scroll
  var rv = $$('.rv');
  if ('IntersectionObserver' in window && !reduce) {
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } });
    }, { threshold: 0.12, rootMargin: '0px 0px -6% 0px' });
    rv.forEach(function (el) { io.observe(el); });
  } else {
    rv.forEach(function (el) { el.classList.add('in'); });
  }

  // ---- Pestañas y copiar del bloque de instalación
  var code = $('#code'), tabs = $$('.term-bar [role=tab]'), copyBtn = $('#copy');
  var PROMPTED = {
    app: '<span class="c"># requiere Python 3.11 o superior</span>\n<span class="p">$ </span>git clone https://github.com/AndreuVM/praxeon.git\n<span class="p">$ </span>cd praxeon\n<span class="p">$ </span>pip install -e ".[desktop]"\n<span class="p">$ </span>praxeon app',
    demo: '<span class="c"># requiere Python 3.11 o superior; no necesita claves de API</span>\n<span class="p">$ </span>git clone https://github.com/AndreuVM/praxeon.git\n<span class="p">$ </span>cd praxeon\n<span class="p">$ </span>pip install -e ".[dev]"\n<span class="p">$ </span>python demo.py'
  };
  var current = 'app';
  tabs.forEach(function (t) {
    t.addEventListener('click', function () {
      current = t.dataset.tab;
      tabs.forEach(function (x) { x.setAttribute('aria-selected', String(x === t)); });
      code.innerHTML = PROMPTED[current];
    });
  });
  copyBtn.addEventListener('click', function () {
    var text = code.getAttribute('data-' + current);
    var done = function () { copyBtn.textContent = 'Copiado'; setTimeout(function () { copyBtn.textContent = 'Copiar'; }, 1800); };
    var fallback = function () {
      var r = document.createRange(); r.selectNodeContents(code);
      var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
      copyBtn.textContent = 'Selecciona y copia';
      setTimeout(function () { copyBtn.textContent = 'Copiar'; }, 2200);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, fallback);
    else fallback();
  });
})();
