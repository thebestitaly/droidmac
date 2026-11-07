// Utilità condivise: API, formattazione, icone, dialog e notifiche.

export const TOKEN = document.querySelector('meta[name="dm-token"]').content;
export const $ = (s, el = document) => el.querySelector(s);
export const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const isMac = /Mac/.test(navigator.platform);

export async function api(path, params, body) {
  const url = params ? `${path}?${new URLSearchParams(params)}` : path;
  const res = await fetch(url, {
    method: body ? 'POST' : 'GET',
    headers: { 'X-Token': TOKEN, 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Errore ${res.status}`);
  return data;
}

export const store = {
  get(k, d) { try { const v = localStorage.getItem(`droidmac:${k}`); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(`droidmac:${k}`, JSON.stringify(v)); } catch { /* storage non disponibile */ } },
};

/* ---------- formattazione ---------- */

const nf = new Intl.NumberFormat('it-IT', { maximumFractionDigits: 1 });
export function fmtSize(n) {
  if (n == null) return '';
  if (n < 1000) return `${n} B`;
  const u = ['KB', 'MB', 'GB', 'TB'];
  let i = -1;
  do { n /= 1000; i++; } while (n >= 1000 && i < u.length - 1);
  return `${nf.format(n < 10 ? n : Math.round(n))} ${u[i]}`;
}

const dToday = new Intl.DateTimeFormat('it-IT', { hour: '2-digit', minute: '2-digit' });
const dYear = new Intl.DateTimeFormat('it-IT', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
const dOld = new Intl.DateTimeFormat('it-IT', { day: 'numeric', month: 'short', year: 'numeric' });
export function fmtDate(sec) {
  if (!sec) return '';
  const d = new Date(sec * 1000), now = new Date();
  if (d.toDateString() === now.toDateString()) return `Oggi ${dToday.format(d)}`;
  return d.getFullYear() === now.getFullYear() ? dYear.format(d) : dOld.format(d);
}

export function fmtDur(s) {
  if (s == null || !isFinite(s)) return '';
  s = Math.round(s);
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min${s % 60 ? ` ${s % 60} s` : ''}`;
  return `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min`;
}

export const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

/* ---------- tipi di file e icone ---------- */

const KINDS = {
  image: ['jpg jpeg png gif webp heic heif bmp tif tiff dng svg avif', '#e0663a'],
  video: ['mp4 mov m4v mkv avi 3gp webm', '#8e5bd8'],
  audio: ['mp3 m4a aac wav flac ogg opus amr', '#d6409f'],
  pdf: ['pdf', '#d64545'],
  text: ['txt md csv json log xml vcf ini yml yaml srt', '#3b7ddd'],
  doc: ['doc docx xls xlsx ppt pptx rtf odt ods pages numbers key', '#3b7ddd'],
  archive: ['zip rar 7z tar gz tgz bz2 xz', '#9a7b4f'],
  apk: ['apk apks xapk', '#2f9e55'],
};
const EXT = {};
for (const [kind, [exts, color]] of Object.entries(KINDS)) for (const e of exts.split(' ')) EXT[e] = { kind, color };

export function kindOf(name) {
  const ext = name.includes('.') ? name.split('.').pop().toLowerCase() : '';
  return EXT[ext]?.kind || 'other';
}

const FOLDER_SVG = '<svg class="fi dir" viewBox="0 0 20 20"><path d="M2 5.5A1.5 1.5 0 0 1 3.5 4H8l2 2h6.5A1.5 1.5 0 0 1 18 7.5v7a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 2 14.5z"/></svg>';
export function icon(e) {
  if (e.type === 'dir') return FOLDER_SVG;
  const ext = e.name.includes('.') ? e.name.split('.').pop().toLowerCase() : '';
  const c = EXT[ext]?.color || 'var(--muted)';
  return `<svg class="fi" viewBox="0 0 20 20" style="color:${c}"><path d="M5 2.5h6.5L15.5 6.5V17a.5.5 0 0 1-.5.5H5a.5.5 0 0 1-.5-.5V3a.5.5 0 0 1 .5-.5z" fill="currentColor" fill-opacity=".12"/><path d="M11.5 2.5v4h4"/></svg>`;
}
export const PHONE_SVG = '<svg viewBox="0 0 20 20"><rect x="5" y="1.5" width="10" height="17" rx="2"/><path d="M9 15.5h2"/></svg>';
export const MAC_SVG = '<svg viewBox="0 0 20 20"><rect x="2" y="3" width="16" height="11" rx="1.5"/><path d="M7 17.5h6M10 14v3.5"/></svg>';
export const PEN_SVG = '<svg viewBox="0 0 20 20"><path d="M3 17h4L17 7l-4-4L3 13z"/></svg>';

/* ---------- dialog e notifiche ---------- */

export function modal({ title, body = '', fields = null, note = '', ok = 'OK', cancel = 'Annulla', danger = false }) {
  const d = $('#modal');
  const f = $('#modal-form');
  f.innerHTML = `<h2>${esc(title)}</h2>${body ? `<p>${body}</p>` : ''}`
    + (fields || []).map((x, i) => `<div class="modal-field"><label for="mf${i}">${esc(x.label)}</label><input id="mf${i}" name="${x.name}" value="${esc(x.value || '')}" placeholder="${esc(x.placeholder || '')}" autocomplete="off" spellcheck="false"></div>`).join('')
    + (note ? `<div class="note">${note}</div>` : '')
    // il pulsante di conferma è il primo nel DOM (Invio conferma), l'ordine visivo lo inverte
    + `<div class="modal-btns"><button class="btn primary${danger ? ' danger' : ''}" value="ok">${esc(ok)}</button><button class="btn" value="cancel" formnovalidate>${esc(cancel)}</button></div>`;
  return new Promise((resolve) => {
    d.onclose = () => {
      if (d.returnValue !== 'ok') return resolve(null);
      if (!fields) return resolve(true);
      const out = {};
      for (const x of fields) out[x.name] = f.elements[x.name].value;
      return resolve(out);
    };
    d.returnValue = '';
    d.showModal();
    const first = f.querySelector('input');
    if (first) {
      first.focus();
      const dot = fields[0].selectStem ? first.value.lastIndexOf('.') : -1;
      if (dot > 0) first.setSelectionRange(0, dot); else first.select();
    } else {
      f.querySelector('.btn.primary').focus();
    }
  });
}

export function toast(msg, kind = '') {
  const t = document.createElement('div');
  t.className = `toast ${kind}`;
  t.textContent = msg;
  $('#toasts').append(t);
  setTimeout(() => t.remove(), kind === 'error' ? 7000 : 3500);
}
