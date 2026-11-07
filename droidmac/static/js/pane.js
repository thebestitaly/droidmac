// Un pannello file (Telefono o Mac): navigazione, selezione, ordinamento, drag & drop.
import {
  $, api, esc, fmtDate, fmtSize, icon, MAC_SVG, modal, PEN_SVG, PHONE_SVG, plural, store, toast, TOKEN,
} from './util.js';
import { panes, setActive, state } from './state.js';
import { deletePhone, transfer } from './transfer.js';
import { refreshDeviceInfo } from './device.js';
import { openViewer } from './viewer.js';

const collator = new Intl.Collator('it', { numeric: true, sensitivity: 'base' });

export class Pane {
  constructor(side) {
    this.side = side;
    this.el = document.getElementById(`pane-${side}`);
    this.el.append(document.getElementById('pane-tpl').content.cloneNode(true));
    this.list = $('.list', this.el);
    this.crumbsEl = $('.crumbs', this.el);
    this.placesEl = $('.places', this.el);
    this.summaryEl = $('.summary', this.el);
    this.actionsEl = $('.actions', this.el);
    this.allBox = $('.all', this.el);
    this.filterEl = $('.filter', this.el);
    $('.pane-title b', this.el).textContent = side === 'phone' ? 'Telefono' : 'Mac';
    $('.pane-ico', this.el).innerHTML = side === 'phone' ? PHONE_SVG : MAC_SVG;

    this.path = null;
    this.entries = [];
    this.view = [];
    this.sel = new Set();
    this.anchor = -1;
    this.cursor = -1;
    this.sort = store.get(`sort:${side}`, { key: 'name', dir: 1 });
    this.hidden = store.get(`hidden:${side}`, false);
    this.filter = '';
    this.history = [];
    this.places = [];
    this.free = null;
    this.editing = false;
    this.buildActions();
    this.bind();
  }

  get other() { return panes[this.side === 'phone' ? 'mac' : 'phone']; }
  get ready() { return this.side === 'mac' || (!!state.serial && state.deviceState === 'device'); }
  get pathKey() { return this.side === 'phone' ? `path:phone:${state.serial}` : 'path:mac'; }

  join(name, base = this.path) { return base === '/' ? `/${name}` : `${base.replace(/\/$/, '')}/${name}`; }
  parent() {
    if (!this.path || this.path === '/') return null;
    const i = this.path.replace(/\/$/, '').lastIndexOf('/');
    return i <= 0 ? '/' : this.path.slice(0, i);
  }
  selectedEntries() { return this.view.filter((e) => this.sel.has(e.name)); }

  /** URL per l'anteprima di un file (token in query: <img>/<video> non inviano header). */
  fileUrl(name) {
    const params = this.side === 'phone'
      ? { serial: state.serial, path: this.join(name), t: TOKEN }
      : { path: this.join(name), t: TOKEN };
    return `/api/${this.side === 'phone' ? 'remote' : 'local'}/file?${new URLSearchParams(params)}`;
  }

  /* ---------- caricamento ---------- */

  async load(path, { push = true, keep = false, focus = null } = {}) {
    if (!this.ready) { this.renderEmpty(); return false; }
    this.list.classList.add('loading');
    try {
      const data = this.side === 'phone'
        ? await api('/api/remote/ls', { serial: state.serial, path })
        : await api('/api/local/ls', { path });
      const same = data.path === this.path;
      if (push && this.path && !same) this.history.push(this.path);
      const keepSel = keep && same ? this.sel : new Set();
      const curName = focus || (same ? this.view[this.cursor]?.name : null);
      this.path = data.path;
      this.entries = data.entries;
      if (data.free != null) this.free = data.free;
      this.sel = focus ? new Set([focus]) : new Set([...keepSel].filter((n) => data.entries.some((e) => e.name === n)));
      store.set(this.pathKey, this.path);
      this.compute();
      this.cursor = curName ? this.view.findIndex((e) => e.name === curName) : -1;
      this.anchor = this.cursor;
      if (!same) this.list.scrollTop = 0;
      this.render();
      if (this.cursor >= 0) this.scrollToCursor();
      return true;
    } catch (e) {
      toast(e.message, 'error');
      return false;
    } finally {
      this.list.classList.remove('loading');
    }
  }

  reload() { return this.path ? this.load(this.path, { push: false, keep: true }) : Promise.resolve(false); }

  compute() {
    const f = this.filter.toLowerCase();
    const { key, dir } = this.sort;
    this.view = this.entries.filter((e) => (this.hidden || !e.name.startsWith('.')) && (!f || e.name.toLowerCase().includes(f)));
    this.view.sort((a, b) => {
      const da = a.type === 'dir', db = b.type === 'dir';
      if (da !== db) return da ? -1 : 1;
      let r = 0;
      if (key === 'size') r = (a.size ?? -1) - (b.size ?? -1);
      else if (key === 'mtime') r = a.mtime - b.mtime;
      return (r || collator.compare(a.name, b.name)) * dir;
    });
  }

  /* ---------- rendering ---------- */

  render() {
    this.renderCrumbs();
    this.renderPlaces();
    this.renderCols();
    if (!this.view.length) {
      this.list.innerHTML = `<div class="empty">${this.filter ? 'Nessun risultato per il filtro.' : 'Cartella vuota.'}</div>`;
    } else {
      const out = new Array(this.view.length);
      for (let i = 0; i < this.view.length; i++) {
        const e = this.view[i];
        const s = this.sel.has(e.name);
        out[i] = `<div class="row${s ? ' sel' : ''}${i === this.cursor ? ' cur' : ''}${e.name.startsWith('.') ? ' hidden-file' : ''}" data-i="${i}" draggable="true">`
          + `<span class="check"><input type="checkbox" tabindex="-1"${s ? ' checked' : ''}></span>`
          + `<span class="name">${icon(e)}<span>${esc(e.name)}</span></span>`
          + `<span class="size">${e.type === 'file' ? fmtSize(e.size) : ''}</span>`
          + `<span class="date">${fmtDate(e.mtime)}</span></div>`;
      }
      this.list.innerHTML = out.join('');
    }
    this.renderFoot();
  }

  renderSel() {
    for (const r of this.list.children) {
      const i = +r.dataset.i;
      if (Number.isNaN(i)) continue;
      const s = this.sel.has(this.view[i].name);
      r.classList.toggle('sel', s);
      r.classList.toggle('cur', i === this.cursor);
      r.firstChild.firstChild.checked = s;
    }
    this.renderCols();
    this.renderFoot();
  }

  renderEmpty() {
    this.path = null; this.entries = []; this.view = []; this.sel.clear();
    this.crumbsEl.innerHTML = ''; this.placesEl.innerHTML = '';
    let html;
    if (!state.adb) {
      html = `<h3>adb non trovato</h3><p>DroidMac usa ADB, molto più veloce di MTP. Installalo dal Terminale:</p>
        <p><code>brew install android-platform-tools</code></p><p>poi riapri DroidMac.</p>`;
    } else if (state.deviceState === 'unauthorized') {
      html = `<h3>Autorizza questo Mac</h3><p>Sblocca il telefono e tocca <b>Consenti</b> nel popup “Consentire il debug USB?”.<br>Spunta “Consenti sempre da questo computer” per non doverlo rifare.</p>`;
    } else if (state.deviceState) {
      html = `<h3>Dispositivo “${esc(state.deviceState)}”</h3><p>Scollega e ricollega il cavo, oppure disattiva e riattiva il Debug USB.</p>`;
    } else {
      html = `<h3>Collega il telefono</h3><ol>
        <li><b>Impostazioni › Info sul telefono</b>: tocca 7 volte <b>Numero build</b> per attivare le Opzioni sviluppatore.</li>
        <li><b>Impostazioni › Sistema › Opzioni sviluppatore</b>: attiva <b>Debug USB</b>.</li>
        <li>Collega il cavo USB e tocca <b>Consenti</b> sul telefono.</li></ol>
        <p>Senza cavo: usa il pulsante <b>Wi‑Fi</b> in alto (Android 11+).</p>`;
    }
    this.list.innerHTML = `<div class="empty">${html}</div>`;
    this.renderCols();
    this.renderFoot();
  }

  renderCrumbs() {
    if (this.editing) return;
    const roots = this.side === 'phone'
      ? [['/storage/emulated/0', 'Memoria interna'], ['/sdcard', 'Memoria interna']]
      : [[state.home, 'Home'], ['/Volumes', 'Volumi']];
    let base = '', label = null;
    for (const [p, l] of roots) {
      if (p && (this.path === p || this.path.startsWith(`${p}/`))) { base = p; label = l; break; }
    }
    const items = [[label || '/', base || '/']];
    let acc = base;
    for (const seg of this.path.slice(base.length).split('/').filter(Boolean)) {
      acc = acc === '/' || acc === '' ? `/${seg}` : `${acc}/${seg}`;
      items.push([seg, acc]);
    }
    this.crumbItems = items;
    this.crumbsEl.innerHTML = items.map(([l], i) => `${i ? '<span class="sep">›</span>' : ''}<button data-ci="${i}">${esc(l)}</button>`).join('')
      + `<button class="edit" data-edit="1" title="Scrivi un percorso (⌘L)">${PEN_SVG}</button>`;
  }

  editPath() {
    if (!this.path) return;
    this.editing = true;
    this.crumbsEl.innerHTML = `<input type="text" spellcheck="false" autocomplete="off" value="${esc(this.path)}">`;
    const inp = $('input', this.crumbsEl);
    inp.focus();
    inp.select();
    const done = (go) => {
      if (!this.editing) return;
      this.editing = false;
      const v = inp.value.trim();
      if (go && v && v !== this.path) this.load(v);
      else this.renderCrumbs();
      this.list.focus();
    };
    inp.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); done(true); }
      if (e.key === 'Escape') { e.preventDefault(); done(false); }
    });
    inp.addEventListener('blur', () => done(false));
  }

  renderPlaces() {
    const free = this.side === 'phone' ? state.device?.storage?.free : this.free;
    this.placesEl.innerHTML = this.places.map((p, i) => `<button data-pi="${i}" class="${p.path === this.path ? 'cur' : ''}" title="${esc(p.path)}">${esc(p.label)}</button>`).join('')
      + (free != null ? `<span class="free">${fmtSize(free)} liberi</span>` : '');
  }

  renderCols() {
    for (const b of this.el.querySelectorAll('.cols [data-sort]')) {
      const on = b.dataset.sort === this.sort.key;
      b.classList.toggle('sorted', on);
      b.textContent = b.textContent.replace(/ [↑↓]$/, '') + (on ? (this.sort.dir > 0 ? ' ↑' : ' ↓') : '');
    }
    const n = this.selectedEntries().length;
    this.allBox.checked = n > 0 && n === this.view.length;
    this.allBox.indeterminate = n > 0 && n < this.view.length;
    this.allBox.disabled = !this.view.length;
    $('[data-act="hidden"]', this.el).classList.toggle('on', this.hidden);
  }

  buildActions() {
    const a = this.side === 'phone'
      ? [['delete', 'Elimina', 'btn danger', 'Elimina dal telefono (⌘⌫)'],
        ['move', 'Sposta sul Mac', 'btn', 'Copia sul Mac, verifica e poi elimina dal telefono'],
        ['copy', 'Copia sul Mac →', 'btn primary', 'Copia nella cartella aperta sul Mac (⌘→)']]
      : [['copy', '← Copia sul telefono', 'btn primary', 'Copia nella cartella aperta sul telefono (⌘←)']];
    this.actionsEl.innerHTML = a.map(([act, label, cls, title]) => `<button class="${cls}" data-do="${act}" data-title="${esc(title)}">${label}</button>`).join('');
  }

  renderFoot() {
    const sel = this.selectedEntries();
    const files = sel.filter((e) => e.type === 'file');
    const dirs = sel.length - files.length;
    const bytes = files.reduce((s, e) => s + e.size, 0);
    let txt = '';
    if (this.ready && this.path) {
      if (sel.length) {
        txt = `<b>${sel.length} selezionat${sel.length === 1 ? 'o' : 'i'}</b>`;
        if (files.length) txt += ` · ${fmtSize(bytes)}`;
        if (dirs) txt += ` · ${plural(dirs, 'cartella', 'cartelle')}`;
      } else {
        const hiddenN = this.entries.length - this.view.length;
        txt = plural(this.view.length, 'elemento', 'elementi') + (hiddenN && !this.filter ? ` <span class="muted">(${hiddenN} nascosti)</span>` : '');
      }
    }
    this.summaryEl.innerHTML = txt;
    const can = sel.length > 0 && this.ready && this.other?.ready && !!this.other?.path;
    for (const b of this.actionsEl.querySelectorAll('button')) {
      b.disabled = b.dataset.do === 'delete' ? !(sel.length && this.ready) : !can;
      b.title = b.dataset.do !== 'delete' && this.other?.path ? `${b.dataset.title}\nDestinazione: ${this.other.path}` : b.dataset.title;
    }
  }

  scrollToCursor() {
    this.list.children[this.cursor]?.scrollIntoView({ block: 'nearest' });
  }

  /* ---------- selezione ---------- */

  selectRange(a, b, add) {
    if (!add) this.sel.clear();
    const [lo, hi] = a < b ? [a, b] : [b, a];
    for (let i = Math.max(lo, 0); i <= hi; i++) this.sel.add(this.view[i].name);
  }

  setCursor(i) {
    this.cursor = i;
    this.renderSel();
    this.scrollToCursor();
  }

  moveCursor(delta, shift) {
    if (!this.view.length) return;
    let i;
    if (Math.abs(delta) >= 1e6) i = delta > 0 ? this.view.length - 1 : 0;
    else i = this.cursor < 0 ? (delta > 0 ? 0 : this.view.length - 1) : this.cursor + delta;
    i = Math.max(0, Math.min(this.view.length - 1, i));
    if (shift) {
      if (this.anchor < 0) this.anchor = i;
      this.selectRange(this.anchor, i, false);
    } else {
      this.sel = new Set([this.view[i].name]);
      this.anchor = i;
    }
    this.setCursor(i);
  }

  toggle(name) {
    if (this.sel.has(name)) this.sel.delete(name); else this.sel.add(name);
    this.renderSel();
  }

  /** Invio / doppio clic / Spazio: apre la cartella o l'anteprima del file. */
  open(entry) {
    if (!entry) return;
    if (entry.type === 'dir') this.load(this.join(entry.name));
    else openViewer(this, entry.name);
  }

  /* ---------- eventi ---------- */

  bind() {
    this.el.addEventListener('mousedown', () => setActive(this.side));
    this.el.querySelector('.tools').addEventListener('click', (e) => {
      const b = e.target.closest('[data-act]');
      if (b) this.action(b.dataset.act);
    });
    this.actionsEl.addEventListener('click', (e) => {
      const b = e.target.closest('[data-do]');
      if (!b || b.disabled) return;
      if (b.dataset.do === 'delete') deletePhone();
      else transfer(this.side, b.dataset.do);
    });
    this.filterEl.addEventListener('input', () => {
      this.filter = this.filterEl.value;
      this.cursor = -1;
      this.compute();
      this.render();
    });
    this.filterEl.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { this.filterEl.value = ''; this.filterEl.dispatchEvent(new Event('input')); this.list.focus(); }
      if (e.key === 'ArrowDown' || e.key === 'Enter') { e.preventDefault(); this.list.focus(); this.moveCursor(1, false); }
    });
    this.el.querySelector('.cols').addEventListener('click', (e) => {
      const b = e.target.closest('[data-sort]');
      if (!b) return;
      const key = b.dataset.sort;
      this.sort = { key, dir: this.sort.key === key ? -this.sort.dir : (key === 'name' ? 1 : -1) };
      store.set(`sort:${this.side}`, this.sort);
      const curName = this.view[this.cursor]?.name;
      this.compute();
      this.cursor = curName ? this.view.findIndex((x) => x.name === curName) : -1;
      this.render();
    });
    this.allBox.addEventListener('change', () => {
      this.sel = this.allBox.checked ? new Set(this.view.map((e) => e.name)) : new Set();
      this.renderSel();
    });
    this.crumbsEl.addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (!b) return;
      if (b.dataset.edit) { this.editPath(); return; }
      const item = this.crumbItems[+b.dataset.ci];
      if (item && item[1] !== this.path) this.load(item[1]);
    });
    this.placesEl.addEventListener('click', (e) => {
      const b = e.target.closest('[data-pi]');
      if (b) this.load(this.places[+b.dataset.pi].path);
    });

    // mouse: clic, ⌘/Ctrl+clic, ⇧+clic, casella
    this.list.addEventListener('click', (e) => {
      const row = e.target.closest('.row');
      if (!row) {
        if (!e.metaKey && !e.ctrlKey && !e.shiftKey && this.sel.size) { this.sel.clear(); this.renderSel(); }
        return;
      }
      const i = +row.dataset.i;
      const name = this.view[i].name;
      if (e.target.closest('.check') || e.metaKey || e.ctrlKey) {
        if (e.shiftKey && this.anchor >= 0) this.selectRange(this.anchor, i, true);
        else { if (this.sel.has(name)) this.sel.delete(name); else this.sel.add(name); this.anchor = i; }
      } else if (e.shiftKey && this.anchor >= 0) {
        this.selectRange(this.anchor, i, false);
      } else {
        this.sel = new Set([name]);
        this.anchor = i;
      }
      this.cursor = i;
      this.renderSel();
    });
    this.list.addEventListener('dblclick', (e) => {
      const row = e.target.closest('.row');
      if (row && !e.target.closest('.check')) this.open(this.view[+row.dataset.i]);
    });

    // trascinamento tra i pannelli
    this.list.addEventListener('dragstart', (e) => {
      const row = e.target.closest('.row');
      if (!row || !this.other.ready) { e.preventDefault(); return; }
      const i = +row.dataset.i;
      if (!this.sel.has(this.view[i].name)) { this.sel = new Set([this.view[i].name]); this.anchor = i; this.cursor = i; this.renderSel(); }
      state.dragFrom = this.side;
      e.dataTransfer.effectAllowed = 'copy';
      e.dataTransfer.setData('text/plain', 'droidmac');
      const ghost = document.createElement('div');
      ghost.className = 'toast';
      ghost.style.cssText = 'position:fixed;top:-100px;left:0';
      ghost.textContent = this.sel.size === 1 ? this.view[i].name : `${this.sel.size} elementi`;
      document.body.append(ghost);
      e.dataTransfer.setDragImage(ghost, -10, -10);
      setTimeout(() => ghost.remove(), 0);
    });
    this.list.addEventListener('dragend', () => {
      state.dragFrom = null;
      for (const p of Object.values(panes)) p.clearDrop();
    });
    this.el.addEventListener('dragover', (e) => {
      if (!state.dragFrom || state.dragFrom === this.side || !this.ready || !this.path) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = 'copy';
      this.el.classList.add('drop');
      const row = e.target.closest('.row');
      const into = row && this.view[+row.dataset.i]?.type === 'dir' ? row : null;
      for (const r of this.list.querySelectorAll('.drop-into')) if (r !== into) r.classList.remove('drop-into');
      if (into) into.classList.add('drop-into');
    });
    this.el.addEventListener('dragleave', (e) => {
      if (!this.el.contains(e.relatedTarget)) this.clearDrop();
    });
    this.el.addEventListener('drop', (e) => {
      if (!state.dragFrom || state.dragFrom === this.side) return;
      e.preventDefault();
      e.stopPropagation();
      const row = e.target.closest('.row');
      const entry = row ? this.view[+row.dataset.i] : null;
      const dest = entry?.type === 'dir' ? this.join(entry.name) : this.path;
      const from = state.dragFrom;
      state.dragFrom = null;
      this.clearDrop();
      transfer(from, 'copy', dest);
    });
  }

  clearDrop() {
    this.el.classList.remove('drop');
    for (const r of this.list.querySelectorAll('.drop-into')) r.classList.remove('drop-into');
  }

  /* ---------- azioni della barra ---------- */

  async action(act) {
    if (!this.ready && act !== 'hidden') return;
    try {
      switch (act) {
        case 'back': {
          const p = this.history.pop();
          if (p) await this.load(p, { push: false });
          break;
        }
        case 'up': {
          const p = this.parent();
          if (p) await this.load(p, { focus: this.path.replace(/\/$/, '').split('/').pop() });
          break;
        }
        case 'reload':
          if (this.side === 'phone') refreshDeviceInfo();
          else api('/api/local/places').then((r) => { this.places = r.places; this.renderPlaces(); }).catch(() => {});
          await this.reload();
          break;
        case 'hidden':
          this.hidden = !this.hidden;
          store.set(`hidden:${this.side}`, this.hidden);
          if (this.path) { this.compute(); this.render(); } else this.renderCols();
          break;
        case 'mkdir': {
          if (!this.path) return;
          const r = await modal({ title: 'Nuova cartella', fields: [{ name: 'name', label: `In ${this.path}`, value: 'Nuova cartella' }], ok: 'Crea' });
          const name = r?.name.trim();
          if (!name) return;
          await api(this.side === 'phone' ? '/api/remote/mkdir' : '/api/local/mkdir', null, { serial: state.serial, path: this.path, name });
          await this.load(this.path, { push: false, focus: name });
          break;
        }
        case 'rename': {
          const sel = this.selectedEntries();
          if (sel.length !== 1) { toast('Seleziona un solo elemento da rinominare.'); return; }
          const r = await modal({ title: 'Rinomina', fields: [{ name: 'name', label: 'Nuovo nome', value: sel[0].name, selectStem: true }], ok: 'Rinomina' });
          const name = r?.name.trim();
          if (!name || name === sel[0].name) return;
          await api('/api/remote/rename', null, { serial: state.serial, path: this.join(sel[0].name), name });
          await this.load(this.path, { push: false, focus: name });
          break;
        }
        case 'reveal': {
          const sel = this.selectedEntries();
          await api('/api/local/reveal', null, { path: sel.length === 1 ? this.join(sel[0].name) : this.path });
          break;
        }
        default:
      }
    } catch (e) {
      toast(e.message, 'error');
    }
  }
}
