// Rilevamento del telefono, informazioni e connessione Wi‑Fi.
import { $, api, esc, fmtSize, modal, store, toast } from './util.js';
import { panes, state } from './state.js';

let timer = null;

export async function pollDevices() {
  clearTimeout(timer);
  try {
    const { devices } = await api('/api/devices');
    state.devices = devices;
    const cur = devices.find((d) => d.serial === state.serial)
      || devices.find((d) => d.state === 'device') || devices[0] || null;
    const serial = cur?.serial || null;
    const dstate = cur?.state || null;
    if (serial !== state.serial || dstate !== state.deviceState) {
      state.serial = serial;
      state.deviceState = dstate;
      await onDeviceChange();
    } else {
      renderDevice();
    }
  } catch (e) {
    if (/adb/.test(e.message)) toast(e.message, 'error');
  }
  timer = setTimeout(pollDevices, 2500);
}

export async function onDeviceChange() {
  const p = panes.phone;
  p.history = [];
  p.path = null;
  state.device = null;
  renderDevice();
  if (p.ready) {
    await refreshDeviceInfo();
    const saved = store.get(p.pathKey, '/sdcard');
    if (!(await p.load(saved, { push: false })) && saved !== '/sdcard') await p.load('/sdcard', { push: false });
  } else {
    p.places = [];
    p.renderEmpty();
  }
  panes.mac.renderFoot();
}

export async function refreshDeviceInfo() {
  if (!panes.phone.ready) return;
  try {
    state.device = await api('/api/device', { serial: state.serial });
    panes.phone.places = state.device.places;
    if (panes.phone.path) panes.phone.renderPlaces();
  } catch (e) {
    toast(e.message, 'error');
  }
  renderDevice();
}

export function renderDevice() {
  const sel = $('#dev-select');
  const ds = state.devices;
  const label = (d) => `${d.model}${d.transport === 'wifi' ? ' (Wi‑Fi)' : ''}${d.state !== 'device' ? ` — ${d.state === 'unauthorized' ? 'da autorizzare' : d.state}` : ''}`;
  const html = ds.length
    ? ds.map((d) => `<option value="${esc(d.serial)}"${d.serial === state.serial ? ' selected' : ''}>${esc(label(d))}</option>`).join('')
    : '<option>Nessun telefono collegato</option>';
  if (sel.innerHTML !== html) sel.innerHTML = html;
  sel.disabled = !ds.length;
  $('#dev-dot').className = `dot${state.deviceState === 'device' ? ' ok' : state.deviceState ? ' warn' : ''}`;
  const info = state.device;
  const ok = info && state.deviceState === 'device';
  $('#dev-meta').textContent = ok
    ? [info.manufacturer && info.manufacturer[0].toUpperCase() + info.manufacturer.slice(1), info.android && `Android ${info.android}`].filter(Boolean).join(' · ')
    : '';
  const st = info?.storage;
  const box = $('#dev-storage');
  box.hidden = !st || !ok;
  if (st) {
    const used = st.total ? st.used / st.total : 0;
    const bar = $('.bar i', box);
    bar.style.width = `${(used * 100).toFixed(1)}%`;
    bar.classList.toggle('full', used > 0.9);
    $('span', box).textContent = `${fmtSize(st.free)} liberi su ${fmtSize(st.total)}`;
  }
}

export async function wifiDialog() {
  const r = await modal({
    title: 'Collega via Wi‑Fi',
    body: 'Sul telefono: <b>Opzioni sviluppatore › Debug wireless</b> (Android 11+). Telefono e Mac devono essere sulla stessa rete.',
    fields: [
      { name: 'host', label: 'Indirizzo IP e porta (schermata “Debug wireless”)', placeholder: '192.168.1.23:37159' },
      { name: 'pair_host', label: 'Solo la prima volta — IP e porta di abbinamento', placeholder: '192.168.1.23:41235' },
      { name: 'code', label: 'Solo la prima volta — codice di abbinamento', placeholder: '123456' },
    ],
    note: 'La prima volta tocca “Abbina dispositivo con codice di abbinamento” sul telefono e compila anche gli ultimi due campi.',
    ok: 'Collega',
  });
  if (!r || !r.host.trim()) return;
  try {
    const res = await api('/api/connect', null, r);
    toast(res.message);
    pollDevices();
  } catch (e) {
    toast(e.message, 'error');
  }
}

export function bindDevice() {
  $('#btn-wifi').addEventListener('click', wifiDialog);
  $('#dev-select').addEventListener('change', async (e) => {
    const d = state.devices.find((x) => x.serial === e.target.value);
    if (!d) return;
    state.serial = d.serial;
    state.deviceState = d.state;
    await onDeviceChange();
  });
}
