// Avvio dei trasferimenti e pannello della coda.
import { $, api, esc, fmtDur, fmtSize, modal, plural, toast } from './util.js';
import { panes, state } from './state.js';
import { refreshDeviceInfo } from './device.js';

const ACTIVE = new Set(['queued', 'scanning', 'running']);
const openDetails = new Set();
const announced = new Set();
let timer = null;
let firstPoll = true;

export async function transfer(fromSide, mode = 'copy', destOverride = null) {
  const src = panes[fromSide];
  const dst = src.other;
  const items = src.selectedEntries();
  if (!items.length || !src.ready || !dst.ready || !dst.path) return;
  const dest = destOverride || dst.path;
  const sources = items.map((e) => src.join(e.name));
  try {
    if (fromSide === 'phone') {
      if (mode === 'move') {
        const ok = await modal({
          title: `Spostare ${plural(items.length, 'elemento', 'elementi')} sul Mac?`,
          body: `Destinazione: <b>${esc(dest)}</b>`,
          note: 'Ogni file viene copiato sul Mac, verificato (stessa dimensione) e solo dopo eliminato dal telefono. Le cartelle di sistema (DCIM, Camera, Download…) restano, anche se vuote.',
          ok: 'Sposta',
          danger: true,
        });
        if (!ok) return;
      }
      await api('/api/transfer/pull', null, { serial: state.serial, sources, dest, move: mode === 'move', skip: state.skip });
    } else {
      await api('/api/transfer/push', null, { serial: state.serial, sources, dest, skip: state.skip });
    }
    showJobs(true);
    pollJobs();
  } catch (e) {
    toast(e.message, 'error');
  }
}

export async function deletePhone() {
  const p = panes.phone;
  const items = p.selectedEntries();
  if (!items.length || !p.ready) return;
  const names = items.slice(0, 6).map((e) => `• ${esc(e.name)}${e.type === 'dir' ? '/' : ''}`).join('<br>');
  const ok = await modal({
    title: `Eliminare ${plural(items.length, 'elemento', 'elementi')} dal telefono?`,
    body: names + (items.length > 6 ? `<br>… e altri ${items.length - 6}` : ''),
    note: 'Le cartelle vengono eliminate con tutto il contenuto. L’operazione non si può annullare.',
    ok: 'Elimina',
    danger: true,
  });
  if (!ok) return;
  try {
    await api('/api/remote/delete', null, { serial: state.serial, paths: items.map((e) => p.join(e.name)) });
    p.sel.clear();
    showJobs(true);
    pollJobs();
  } catch (e) {
    toast(e.message, 'error');
  }
}

export async function pollJobs() {
  clearTimeout(timer);
  let active = false;
  try {
    const { jobs } = await api('/api/jobs');
    state.jobs = jobs;
    active = jobs.some((j) => ACTIVE.has(j.status));
    for (const j of jobs) {
      if (ACTIVE.has(j.status) || announced.has(j.id)) continue;
      announced.add(j.id);
      if (firstPoll) continue;
      for (const side of j.refresh) panes[side]?.reload();
      if (j.refresh.includes('phone')) refreshDeviceInfo();
      const msg = {
        done: 'completato',
        partial: `completato con ${plural(j.error_count, 'errore', 'errori')}`,
        error: `errore: ${j.message}`,
        cancelled: 'annullato',
      }[j.status];
      toast(`${j.title}: ${msg}`, j.status === 'done' ? '' : 'error');
    }
    firstPoll = false;
    renderJobs();
  } catch { /* server momentaneamente non raggiungibile */ }
  timer = setTimeout(pollJobs, active ? 600 : 4000);
}

export function showJobs(show) {
  $('#jobs').hidden = !show;
  $('#btn-jobs').classList.toggle('on', show);
  renderJobs();
}

function renderJobs() {
  const { jobs } = state;
  const nActive = jobs.filter((j) => ACTIVE.has(j.status)).length;
  const badge = $('#jobs-badge');
  badge.hidden = !nActive;
  badge.textContent = nActive;
  if ($('#jobs').hidden) return;
  const list = $('#jobs-list');
  if (!jobs.length) {
    list.innerHTML = '<div class="none">Nessun trasferimento. Seleziona dei file e usa “Copia”, oppure trascinali sull’altro pannello.</div>';
    return;
  }
  const scroll = list.scrollTop;
  list.innerHTML = jobs.map(jobHtml).join('');
  list.scrollTop = scroll;
}

function jobHtml(j) {
  const active = ACTIVE.has(j.status);
  const pct = j.total_bytes ? (j.done_bytes / j.total_bytes) * 100
    : j.total_files ? (j.done_files / j.total_files) * 100 : (active ? 0 : 100);
  const [stLabel, stCls] = {
    queued: ['In coda', ''],
    scanning: ['Analisi…', ''],
    running: [`${Math.floor(pct)}%`, ''],
    done: ['Completato', 'done'],
    partial: ['Completato con errori', 'warn'],
    error: ['Errore', 'err'],
    cancelled: ['Annullato', 'warn'],
  }[j.status] || [j.status, ''];
  const scanning = j.status === 'queued' || j.status === 'scanning';
  const meta = [];
  if (j.kind === 'delete') meta.push(`${j.done_files}/${j.total_files} elementi`);
  else if (!scanning && !j.total_files && !j.error_count) { if (!j.skipped) meta.push('Nessun file da copiare'); }
  else if (!scanning) {
    meta.push(`${j.done_files}/${plural(j.total_files, 'file', 'file')}`);
    meta.push(`${fmtSize(j.done_bytes)} di ${fmtSize(j.total_bytes)}`);
  }
  if (j.skipped) meta.push(`${plural(j.skipped, 'file già presente', 'file già presenti')}, saltat${j.skipped === 1 ? 'o' : 'i'}`);
  if (active && j.speed > 0) meta.push(`${fmtSize(Math.round(j.speed))}/s`);
  if (active && j.eta != null) meta.push(`ancora ${fmtDur(j.eta)}`);
  if (!active && j.started && j.finished) meta.push(`in ${fmtDur(j.finished - j.started)}`);
  if (j.message) meta.push(esc(j.message));
  const indet = j.status === 'scanning' || (j.status === 'running' && j.kind === 'delete');
  return `<div class="job">
    <div class="t" title="${esc(j.title)}">${esc(j.title)}</div>
    <div>${active ? `<button class="btn ghost small" data-cancel="${j.id}">Annulla</button>` : ''}<span class="st ${stCls}">${stLabel}</span></div>
    ${active || j.status === 'done' ? `<div class="pbar${indet ? ' indet' : ''}"><i style="width:${Math.min(100, pct).toFixed(1)}%"></i></div>` : ''}
    ${j.current && active ? `<div class="cur" title="${esc(j.current)}">&lrm;${esc(j.current)}&lrm;</div>` : ''}
    <div class="meta">${meta.map((m) => `<span>${m}</span>`).join('')}</div>
    ${j.error_count ? `<details data-id="${j.id}"${openDetails.has(j.id) ? ' open' : ''}><summary>${plural(j.error_count, 'errore', 'errori')}</summary><pre>${esc(j.errors.join('\n'))}</pre></details>` : ''}
  </div>`;
}

export function bindJobs() {
  $('#btn-jobs').addEventListener('click', () => showJobs($('#jobs').hidden));
  $('#jobs-close').addEventListener('click', () => showJobs(false));
  $('#jobs-clear').addEventListener('click', async () => {
    await api('/api/jobs/clear', null, {}).catch(() => {});
    pollJobs();
  });
  $('#jobs-list').addEventListener('click', async (e) => {
    const b = e.target.closest('[data-cancel]');
    if (!b) return;
    b.disabled = true;
    await api('/api/jobs/cancel', null, { id: b.dataset.cancel }).catch(() => {});
    pollJobs();
  });
  $('#jobs-list').addEventListener('toggle', (e) => {
    const id = e.target.dataset?.id;
    if (!id) return;
    if (e.target.open) openDetails.add(id); else openDetails.delete(id);
  }, true);
}
