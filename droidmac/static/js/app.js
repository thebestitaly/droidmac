// Punto d'ingresso: avvio, tastiera, ciclo di vita.
import { $, api, isMac, store, TOKEN, toast } from './util.js';
import { panes, setActive, state } from './state.js';
import { Pane } from './pane.js';
import { deletePhone, pollJobs, bindJobs, transfer } from './transfer.js';
import { bindDevice, pollDevices, renderDevice } from './device.js';
import { bindViewer, viewerKey } from './viewer.js';

function onKey(e) {
  if ($('#modal').open) return;
  if (viewerKey(e)) return;
  if (e.target.matches('input:not([type=checkbox]), select, textarea')) return;
  const p = panes[state.active];
  const mod = isMac ? e.metaKey : e.ctrlKey;
  const k = e.key;
  const lk = k.toLowerCase();
  const stop = () => { e.preventDefault(); e.stopPropagation(); };

  if (k === 'Tab' && !mod) { stop(); setActive(p.other.side); p.other.list.focus(); return; }
  if (mod && lk === 'a') { stop(); p.sel = new Set(p.view.map((x) => x.name)); p.renderSel(); return; }
  if (mod && lk === 'r') { stop(); p.action('reload'); return; }
  if (mod && lk === 'l') { stop(); p.editPath(); return; }
  if (mod && lk === 'f') { stop(); p.filterEl.focus(); return; }
  if (mod && e.shiftKey && lk === 'n') { stop(); p.action('mkdir'); return; }
  if (mod && k === 'ArrowRight' && p.side === 'phone') { stop(); transfer('phone', 'copy'); return; }
  if (mod && k === 'ArrowLeft' && p.side === 'mac') { stop(); transfer('mac', 'copy'); return; }
  if (mod && k === 'ArrowUp') { stop(); p.action('up'); return; }
  if (mod && k === 'ArrowDown') { stop(); p.open(p.view[p.cursor]); return; }
  if ((mod && k === '[') || (k === 'Backspace' && !mod)) { stop(); p.action('back'); return; }
  if (p.side === 'phone' && (k === 'Delete' || (mod && k === 'Backspace'))) { stop(); deletePhone(); return; }
  if (p.side === 'phone' && k === 'F2') { stop(); p.action('rename'); return; }
  if (k === 'ArrowDown' || k === 'ArrowUp') { stop(); p.moveCursor(k === 'ArrowDown' ? 1 : -1, e.shiftKey); return; }
  if (k === 'Home' || k === 'End') { stop(); p.moveCursor(k === 'Home' ? -1e9 : 1e9, e.shiftKey); return; }
  if (k === 'PageDown' || k === 'PageUp') { stop(); p.moveCursor(k === 'PageDown' ? 15 : -15, e.shiftKey); return; }
  if ((k === ' ' || k === 'Enter') && p.cursor >= 0) { stop(); p.open(p.view[p.cursor]); return; }
  if (k === 'Escape') { stop(); p.sel.clear(); p.renderSel(); }
}

function banner(html) {
  const b = $('#banner');
  b.hidden = false;
  b.innerHTML = html;
}

async function init() {
  panes.phone = new Pane('phone');
  panes.mac = new Pane('mac');
  state.active = '';
  setActive('phone');

  const skip = $('#opt-skip');
  skip.checked = state.skip;
  skip.addEventListener('change', () => { state.skip = skip.checked; store.set('skip', state.skip); });
  bindJobs();
  bindDevice();
  bindViewer();
  document.addEventListener('keydown', onKey);

  // file trascinati dal Finder: il browser non ne conosce il percorso
  window.addEventListener('dragover', (e) => { if (!state.dragFrom) e.preventDefault(); });
  window.addEventListener('drop', (e) => {
    if (state.dragFrom) return;
    e.preventDefault();
    toast('Dal Finder non posso leggere il percorso: apri la cartella nel pannello Mac e trascina da lì.');
  });
  // in modalità app il server si chiude quando la finestra viene chiusa
  window.addEventListener('pagehide', () => navigator.sendBeacon(`/api/bye?t=${encodeURIComponent(TOKEN)}`));

  try {
    const s = await api('/api/state');
    state.home = s.home;
    state.adb = !!s.adb;
    $('#version').textContent = `v${s.version}`;
    if (!s.adb) banner('adb non trovato. Installa con <code>brew install android-platform-tools</code> e riapri DroidMac.');
  } catch (e) {
    banner(`DroidMac non risponde (${e.message}). Riapri l’app e ricarica la pagina.`);
    return;
  }

  try {
    panes.mac.places = (await api('/api/local/places')).places;
  } catch { /* opzionale */ }
  const dl = panes.mac.places.find((p) => p.label === 'Download')?.path || state.home;
  const savedMac = store.get('path:mac', dl);
  if (!(await panes.mac.load(savedMac, { push: false })) && savedMac !== dl) await panes.mac.load(dl, { push: false });

  panes.phone.renderEmpty();
  renderDevice();
  if (state.adb) pollDevices();
  pollJobs();
}

init();
