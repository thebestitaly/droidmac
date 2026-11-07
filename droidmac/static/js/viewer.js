// Anteprima stile Quick Look: Spazio per aprire, ← → per scorrere, S per selezionare.
import { $, esc, fmtDate, fmtSize, kindOf } from './util.js';

let cur = null; // { pane, files, index }
const preloaded = new Map();

export const viewerOpen = () => !!cur;

export function openViewer(pane, name) {
  const files = pane.view.filter((e) => e.type === 'file');
  const index = files.findIndex((e) => e.name === name);
  if (index < 0) return;
  // se la selezione è solo il file appena cliccato, si riparte puliti: S seleziona davvero
  if (pane.sel.size === 1 && pane.sel.has(name)) {
    pane.sel.clear();
    pane.renderSel();
  }
  cur = { pane, files, index };
  $('#viewer').hidden = false;
  render();
}

export function closeViewer() {
  if (!cur) return;
  const { pane } = cur;
  cur = null;
  $('#viewer').hidden = true;
  $('#viewer .v-stage').innerHTML = ''; // ferma video e audio
  preloaded.clear();
  pane.list.focus();
}

function go(delta) {
  if (!cur) return;
  const i = cur.index + delta;
  if (i < 0 || i >= cur.files.length) return;
  cur.index = i;
  render();
}

function toggleSelected() {
  if (!cur) return;
  cur.pane.toggle(cur.files[cur.index].name);
  renderTop();
}

function renderTop() {
  const { pane, files, index } = cur;
  const e = files[index];
  const sel = pane.sel.has(e.name);
  $('#viewer .v-name').textContent = e.name;
  $('#viewer .v-meta').textContent = `${index + 1} di ${files.length} · ${fmtSize(e.size)} · ${fmtDate(e.mtime)}`;
  const b = $('#viewer .v-sel');
  b.classList.toggle('on', sel);
  b.textContent = sel ? '✓ Selezionato' : 'Seleziona';
  $('#viewer .v-sel-count').textContent = pane.sel.size ? `${pane.sel.size} selezionati` : '';
  $('#viewer .prev').disabled = index === 0;
  $('#viewer .next').disabled = index === files.length - 1;
}

function render() {
  const { pane, files, index } = cur;
  const e = files[index];
  const url = pane.fileUrl(e.name);
  const kind = kindOf(e.name);
  const stage = $('#viewer .v-stage');
  renderTop();

  // allinea il cursore della lista al file mostrato
  const vi = pane.view.findIndex((x) => x.name === e.name);
  if (vi >= 0) pane.setCursor(vi);

  const fallback = (why) => `<div class="v-none">${esc(why)}<br><span>${esc(e.name)}</span></div>`;
  if (kind === 'image') {
    stage.innerHTML = `<img alt="" src="${esc(url)}">`;
    $('img', stage).addEventListener('error', () => {
      stage.innerHTML = fallback(/\.(heic|heif)$/i.test(e.name)
        ? 'Le foto HEIC non si vedono in questo browser (Safari sì).'
        : 'Anteprima non disponibile.');
    }, { once: true });
  } else if (kind === 'video') {
    stage.innerHTML = `<video controls autoplay playsinline src="${esc(url)}"></video>`;
  } else if (kind === 'audio') {
    stage.innerHTML = `<audio controls autoplay src="${esc(url)}"></audio>`;
  } else if (kind === 'pdf') {
    stage.innerHTML = `<iframe title="PDF" src="${esc(url)}"></iframe>`;
  } else if (kind === 'text') {
    stage.innerHTML = '<pre class="v-text">…</pre>';
    const shown = e;
    fetch(url, { headers: { Range: 'bytes=0-262143' } })
      .then((r) => r.text())
      .then((t) => {
        if (cur && cur.files[cur.index] === shown) {
          $('pre', stage).textContent = t + (e.size > 262144 ? '\n\n… (mostrati i primi 256 KB)' : '');
        }
      })
      .catch(() => { stage.innerHTML = fallback('Anteprima non disponibile.'); });
  } else {
    stage.innerHTML = fallback('Nessuna anteprima per questo tipo di file.');
  }

  // precarica le immagini vicine per scorrere senza attese
  for (const d of [1, -1, 2]) {
    const n = files[index + d];
    if (n && kindOf(n.name) === 'image' && !preloaded.has(n.name)) {
      const img = new Image();
      img.src = pane.fileUrl(n.name);
      preloaded.set(n.name, img);
    }
  }
}

/** Gestione tastiera mentre l'anteprima è aperta. Restituisce true se gestito. */
export function viewerKey(e) {
  if (!cur) return false;
  const k = e.key;
  if (k === 'ArrowRight' || k === 'ArrowDown') go(1);
  else if (k === 'ArrowLeft' || k === 'ArrowUp') go(-1);
  else if (k === 'Escape' || k === ' ') closeViewer();
  else if (k.toLowerCase() === 's' || k === 'Enter') toggleSelected();
  else if (k === 'Home') go(-cur.index);
  else if (k === 'End') go(cur.files.length - 1 - cur.index);
  else return false;
  e.preventDefault();
  return true;
}

export function bindViewer() {
  const v = $('#viewer');
  v.querySelector('.prev').addEventListener('click', () => go(-1));
  v.querySelector('.next').addEventListener('click', () => go(1));
  v.querySelector('.v-close').addEventListener('click', closeViewer);
  v.querySelector('.v-sel').addEventListener('click', toggleSelected);
  v.querySelector('.v-stage').addEventListener('click', (e) => { if (e.target.classList.contains('v-stage')) closeViewer(); });
}
