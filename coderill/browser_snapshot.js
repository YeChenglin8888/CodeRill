// Adapted from browser-use/jev-ultrafast/snapshot.js; see browser_NOTICE.txt.
() => {
  const ids = new WeakMap(), nodes = new Map();
  let next = 1, generation = 0, saved = null;
  const identity = e => {
    if (!ids.has(e)) ids.set(e, next++);
    const id = ids.get(e); nodes.set(id, e); return id;
  };
  const safe = e => !['password', 'file', 'hidden'].includes(e.type);
  const visible = e => !e.closest('[aria-hidden="true"],[inert]') &&
    e.checkVisibility({checkOpacity: true, checkVisibilityCSS: true});
  const name = (e, seen = new Set()) => {
    if (!e || seen.has(e)) return '';
    seen.add(e);
    const referenced = (e.getAttribute('aria-labelledby') || '').split(/\s+/)
      .map(id => name(document.getElementById(id), seen)).filter(Boolean).join(' ');
    return (referenced || e.getAttribute('aria-label') ||
      [...(e.labels || [])].map(l => name(l, seen)).filter(Boolean).join(' ') ||
      (['button', 'submit', 'reset'].includes(e.type) ? e.value : '') || e.getAttribute('alt') ||
      (e.tagName === 'INPUT' ? '' : [...e.childNodes].map(n => n.nodeType === 3 ? n.textContent :
        n.nodeType === 1 && n.getAttribute('aria-hidden') !== 'true' ? name(n, seen) : '').join(' ').trim()) ||
      e.getAttribute('title') || e.getAttribute('placeholder') || '').slice(0, 500);
  };
  const roles = ['button', 'link', 'checkbox', 'radio', 'switch', 'tab', 'menuitem', 'menuitemradio',
    'option', 'gridcell', 'combobox', 'textbox', 'searchbox', 'spinbutton'];
  const selector = 'a[href],button,input,textarea,select,summary,[contenteditable="true"],' +
    roles.map(r => '[role="' + r + '"]').join(',');
  const role = e => {
    const explicit = e.getAttribute('role');
    if (roles.includes(explicit)) return explicit;
    if (e.tagName === 'BUTTON' || e.tagName === 'SUMMARY') return 'button';
    if (e.tagName === 'A') return 'link';
    if (e.tagName === 'SELECT') return 'combobox';
    if (e.tagName === 'TEXTAREA' || e.isContentEditable) return 'textbox';
    if (e.tagName === 'INPUT') {
      if (['checkbox', 'radio'].includes(e.type)) return e.type;
      if (['button', 'submit', 'reset', 'image'].includes(e.type)) return 'button';
      if (e.type === 'search') return 'searchbox';
      if (e.type === 'number') return 'spinbutton';
      if (['text', 'email', 'url', 'tel'].includes(e.type)) return 'textbox';
    }
    return null;
  };
  const optionKey = e => [...(e.options || [])].map(o =>
    [identity(o), o.label, o.value, o.selected, o.disabled, !!o.closest('optgroup[disabled]')]);
  const pageKey = () => [performance.timeOrigin, location.href, scrollX, scrollY, innerWidth, innerHeight,
    [...document.querySelectorAll('input,textarea,select')].filter(safe)
      .map(e => [identity(e), e.value, e.checked, e.selectedIndex, e.disabled, e.readOnly, optionKey(e)])];
  const guard = e => {
    if (!e?.isConnected || !safe(e) || !visible(e)) return null;
    const scope = e.closest('form,dialog,[role="dialog"],article,li,tr,[role="row"]') || e.parentElement;
    return [identity(e), role(e), name(e), e.value ?? null, e.checked ?? null, e.selectedIndex ?? null,
      e.readOnly ?? null, e.matches(':disabled'), e.getAttribute('aria-disabled'),
      e.getAttribute('aria-readonly'), e.getAttribute('aria-expanded'), e.getAttribute('aria-checked'),
      e.getAttribute('aria-selected'), e.getAttribute('href'), e.getAttribute('target'),
      e.getAttribute('download'), optionKey(e), scope?.innerText?.slice(0, 6000) || ''];
  };
  const readText = () => {
    const words = [], walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const range = document.createRange(); let node, length = 0, truncated = false;
    while ((node = walker.nextNode())) {
      const value = node.textContent.trim(), parent = node.parentElement;
      if (!value || !parent || parent.closest('script,style,noscript,template') || !visible(parent)) continue;
      range.selectNodeContents(node); const r = range.getBoundingClientRect();
      if (r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth) {
        if (length >= 6000) { truncated = true; break; }
        words.push(value); length += value.length + 1;
        if (length > 6000) truncated = true;
      }
    }
    return {text: words.join('\n').slice(0, 6000), text_truncated: truncated};
  };
  const observe = () => {
    if (!document.body) throw new Error('Page has no body');
    for (const [id, e] of nodes) if (!e.isConnected) nodes.delete(id);
    const actions = [], entries = new Map(), guards = new Map();
    let omitted = 0;
    const add = (action, e, option = null) => {
      if (actions.length >= 250) { omitted++; return; }
      const id = 'e' + (actions.length + 1);
      actions.push({...action, id});
      if (!guards.has(e)) guards.set(e, JSON.stringify(guard(e)));
      entries.set(id, {element: e, option, guard: guards.get(e)});
    };
    const limitations = new Set();
    if (document.querySelector('iframe,frame')) limitations.add('Frame contents are unavailable');
    for (const e of document.querySelectorAll(selector)) {
      if (!safe(e) || !visible(e) || e.matches(':disabled') || e.closest('[aria-disabled="true"]')) continue;
      const r = e.getBoundingClientRect(), x = r.x + r.width / 2, y = r.y + r.height / 2, rname = role(e);
      if (!rname || r.width <= 0 || r.height <= 0 || x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) continue;
      if (rname === 'gridcell' && e.querySelector('button,[role="button"]')) continue;
      const value = 'value' in e ? String(e.value) : e.isContentEditable ? e.innerText : '';
      const base = {node: identity(e), role: rname, label: name(e) || rname,
        value: value.slice(0, 2000), value_truncated: value.length > 2000,
        href: e.tagName === 'A' ? e.href : null,
        checked: e.checked ?? e.getAttribute('aria-checked'), expanded: e.getAttribute('aria-expanded'),
        selected: e.getAttribute('aria-selected')};
      let unsupported = '';
      if (e.tagName === 'A' && !['http:', 'https:', 'javascript:'].includes(e.protocol))
        unsupported = 'External application and non-HTTP links are unsupported';
      if (e.closest('a[target="_blank"]')) unsupported = 'New tabs are unsupported';
      if (e.closest('a[download]')) unsupported = 'Downloads are unsupported';
      if (e.tagName === 'SELECT' && e.multiple) unsupported = 'Multiple selection is unsupported';
      for (let p = e.parentElement; p && p !== document.body && p !== document.documentElement; p = p.parentElement) {
        const css = getComputedStyle(p);
        if (/(auto|scroll)/.test(css.overflowY) && p.scrollHeight > p.clientHeight + 2)
          unsupported = 'Nested scrolling is unsupported';
      }
      if (unsupported) { add({...base, kind: 'click', unsupported}, e); continue; }
      if (e.tagName === 'SELECT') {
        base.selected_label = [...e.selectedOptions].map(o => o.label).join(', ').slice(0, 500);
        for (const o of e.options) if (!o.selected && !o.disabled && !o.closest('optgroup[disabled]'))
          add({...base, kind: 'select', option_label: o.label.slice(0, 500), option_value: o.value.slice(0, 2000)}, e, o);
      } else {
        const editable = !e.readOnly && e.getAttribute('aria-readonly') !== 'true' &&
          (e.isContentEditable || e.tagName === 'TEXTAREA' ||
            (e.tagName === 'INPUT' && ['text', 'search', 'email', 'url', 'tel', 'number'].includes(e.type)));
        add({...base, kind: editable ? 'fill' : 'click'}, e);
        if (editable) add({...base, kind: 'click', label: 'Open ' + base.label}, e);
      }
    }
    const text = readText();
    const snapshot = {snapshot_id: ++generation, url: location.href, title: document.title.slice(0, 1000),
      ...text, actions, omitted_actions: omitted,
      scroll: {x: scrollX, y: scrollY, height: document.documentElement.scrollHeight, viewport: innerHeight},
      limitations: [...limitations]};
    saved = {id: generation, pageKey: JSON.stringify(pageKey()), entries, title: document.title, text: text.text};
    return snapshot;
  };
  const resolve = ({snapshot_id, id}) => {
    if (!saved || saved.id !== snapshot_id || saved.pageKey !== JSON.stringify(pageKey()))
      return {status: 'stale'};
    const entry = saved.entries.get(id);
    if (!entry || entry.guard !== JSON.stringify(guard(entry.element))) return {status: 'stale'};
    const e = entry.element, r = e.getBoundingClientRect(), x = r.x + r.width / 2, y = r.y + r.height / 2;
    const hit = document.elementFromPoint(x, y);
    if (x < 0 || y < 0 || x >= innerWidth || y >= innerHeight || !hit || (hit !== e && !e.contains(hit)))
      return {status: 'covered'};
    if (entry.option && (!entry.option.isConnected || entry.option.parentElement.closest('select') !== e))
      return {status: 'stale'};
    return {status: 'ready', element: e, option: entry.option};
  };
  const fresh = snapshot_id => !!saved && saved.id === snapshot_id && saved.pageKey === JSON.stringify(pageKey()) &&
    saved.title === document.title && saved.text === readText().text;
  return {observe, resolve, fresh};
}
