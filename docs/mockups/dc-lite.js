/*
 * dc-lite.js — jamak_converter 목업 전용 경량 렌더러.
 * 같은 폴더의 목업 HTML이 담고 있는 템플릿(<script type="text/x-dc-template">)과
 * 화면 로직(<script type="text/x-dc-logic">)을 읽어 화면을 그린다.
 * 지원: {{a.b}} 치환, <sc-for list as>, <sc-if value>, on* 이벤트, setState 재렌더,
 *       URL 쿼리로 Tweaks 값 지정 (예: ?palette=acid&sidebar=hidden).
 * 서버 없이 file:// 로 열어도 동작한다. 폰트만 Google Fonts에서 받는다.
 */
(function () {
  'use strict';
  var BOOL_ATTRS = { checked: 1, disabled: 1, readonly: 1, selected: 1, hidden: 1, required: 1 };
  var EVENTS = { onclick: 'click', onchange: 'change', oninput: 'input' };
  var HOLE = /\{\{\s*([\w$.]+)\s*\}\}/g;
  var ONLY_HOLE = /^\s*\{\{\s*([\w$.]+)\s*\}\}\s*$/;

  function lookup(scope, path) {
    if (path === 'true') return true;
    if (path === 'false') return false;
    var parts = path.split('.');
    var v = scope;
    for (var i = 0; i < parts.length; i++) {
      if (v == null) return undefined;
      v = v[parts[i]];
    }
    return v;
  }
  function str(v) { return v == null ? '' : String(v); }
  function fill(text, scope) { return text.replace(HOLE, function (_, p) { return str(lookup(scope, p)); }); }

  function renderChildren(src, scope, out) {
    for (var n = src.firstChild; n; n = n.nextSibling) renderNode(n, scope, out);
  }

  function renderNode(n, scope, out) {
    if (n.nodeType === 3) { out.appendChild(document.createTextNode(fill(n.nodeValue, scope))); return; }
    if (n.nodeType !== 1) return;
    var tag = n.localName;
    if (tag === 'template' && n.hasAttribute('data-sc-for')) {
      var m = ONLY_HOLE.exec(n.getAttribute('list') || '');
      var list = m ? lookup(scope, m[1]) : null;
      var as = n.getAttribute('as') || 'item';
      (list || []).forEach(function (item, i) {
        var s = Object.create(scope); s[as] = item; s.$index = i;
        renderChildren(n.content, s, out);
      });
      return;
    }
    if (tag === 'template' && n.hasAttribute('data-sc-if')) {
      var mv = ONLY_HOLE.exec(n.getAttribute('value') || '');
      if (mv && lookup(scope, mv[1])) renderChildren(n.content, scope, out);
      return;
    }
    var el = n.namespaceURI === 'http://www.w3.org/2000/svg'
      ? document.createElementNS(n.namespaceURI, n.tagName)
      : document.createElement(tag);
    for (var i = 0; i < n.attributes.length; i++) {
      var a = n.attributes[i];
      var name = a.name, lower = name.toLowerCase();
      if (lower.indexOf('hint-') === 0) continue;
      var only = ONLY_HOLE.exec(a.value);
      if (EVENTS[lower]) {
        var fn = only ? lookup(scope, only[1]) : null;
        if (typeof fn === 'function') { el.__on = el.__on || {}; el.__on[EVENTS[lower]] = fn; }
        continue;
      }
      if (only && BOOL_ATTRS[lower]) {
        if (lookup(scope, only[1])) el.setAttribute(name, '');
        continue;
      }
      if (tag === 'textarea' && lower === 'value') {
        el.textContent = only ? str(lookup(scope, only[1])) : fill(a.value, scope);
        continue;
      }
      el.setAttribute(name, fill(a.value, scope));
    }
    renderChildren(n, scope, el);
    if (tag === 'input' && el.hasAttribute('checked')) el.checked = true;
    out.appendChild(el);
  }

  // 새로 그린 트리를 기존 DOM에 덮어써서(바뀐 곳만 고침) 포커스·입력 중인 값·스크롤을 지킨다.
  function morphChildren(cur, next) {
    var incoming = Array.prototype.slice.call(next.childNodes);
    for (var i = 0; i < incoming.length; i++) {
      var n = incoming[i], c = cur.childNodes[i];
      if (!c) { cur.appendChild(n); continue; }
      if (c.nodeType !== n.nodeType || c.nodeName !== n.nodeName) { cur.replaceChild(n, c); continue; }
      if (c.nodeType === 3) { if (c.nodeValue !== n.nodeValue) c.nodeValue = n.nodeValue; continue; }
      morphElement(c, n);
    }
    while (cur.childNodes.length > incoming.length) cur.removeChild(cur.lastChild);
  }
  function morphElement(cur, next) {
    var i, a;
    for (i = cur.attributes.length - 1; i >= 0; i--) {
      a = cur.attributes[i];
      if (!next.hasAttribute(a.name)) cur.removeAttribute(a.name);
    }
    for (i = 0; i < next.attributes.length; i++) {
      a = next.attributes[i];
      if (cur.getAttribute(a.name) !== a.value) cur.setAttribute(a.name, a.value);
    }
    cur.__on = next.__on;
    if (cur.localName === 'textarea') {
      if (document.activeElement !== cur && cur.value !== next.textContent) cur.value = next.textContent;
      return;
    }
    morphChildren(cur, next);
    if (cur.localName === 'input' && (cur.type === 'checkbox' || cur.type === 'radio')) cur.checked = cur.hasAttribute('checked');
  }

  function prepare(text) {
    // sc-for / sc-if 를 <template>로 바꿔 표·select 안에서도 파서가 구조를 보존하게 한다.
    text = text.replace(/<sc-for\b/g, '<template data-sc-for')
      .replace(/<\/sc-for>/g, '</template>')
      .replace(/<sc-if\b/g, '<template data-sc-if')
      .replace(/<\/sc-if>/g, '</template>');
    var doc = new DOMParser().parseFromString('<!doctype html><html><body>' + text + '</body></html>', 'text/html');
    return doc;
  }

  function boot() {
    var tplEl = document.querySelector('script[type="text/x-dc-template"]');
    var logicEl = document.querySelector('script[type="text/x-dc-logic"]');
    var doc = prepare(tplEl.textContent);
    var helmet = doc.querySelector('helmet');
    if (helmet) {
      Array.prototype.slice.call(helmet.children).forEach(function (c) { document.head.appendChild(document.importNode(c, true)); });
      helmet.remove();
    }
    var spec = {};
    try { spec = JSON.parse(logicEl.getAttribute('data-props') || '{}'); } catch (e) {}
    var props = {};
    var q = new URLSearchParams(location.search);
    Object.keys(spec).forEach(function (k) {
      if (k.charAt(0) === '$') return;
      var d = spec[k] && spec[k].default;
      if (q.has(k)) d = spec[k].editor === 'int' ? Number(q.get(k)) : q.get(k);
      props[k] = d;
    });

    var root = document.getElementById('dc-root');
    Object.keys(EVENTS).forEach(function (k) {
      var type = EVENTS[k];
      root.addEventListener(type, function (e) {
        for (var el = e.target; el && el !== root; el = el.parentNode) {
          if (el.__on && el.__on[type]) el.__on[type](e);
        }
      });
    });
    var comp;
    var queued = false;
    function render() {
      queued = false;
      var vals = comp.renderVals();
      var next = document.createElement('div');
      renderChildren(doc.body, vals, next);
      morphChildren(root, next);
    }
    function DCLogic(p) { this.props = p; this.state = {}; }
    DCLogic.prototype.setState = function (s) {
      var next = typeof s === 'function' ? s(this.state, this.props) : s;
      this.state = Object.assign({}, this.state, next);
      if (!queued) { queued = true; requestAnimationFrame(render); }
    };
    var Component = new Function('DCLogic', logicEl.textContent + '\n;return Component;')(DCLogic);
    comp = new Component(props);
    if (!comp.state) comp.state = {};
    render();
    if (!document.documentElement.hasAttribute('data-static') && comp.componentDidMount) comp.componentDidMount();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
