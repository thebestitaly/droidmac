// Stato condiviso dell'applicazione.
import { store } from './util.js';

export const state = {
  serial: null,          // dispositivo selezionato
  deviceState: null,     // device | unauthorized | offline | ...
  devices: [],
  device: null,          // modello, Android, spazio, scorciatoie
  adb: true,
  home: '',
  skip: store.get('skip', true),
  active: 'phone',       // pannello attivo
  dragFrom: null,
  jobs: [],
};

/** @type {{phone: import('./pane.js').Pane, mac: import('./pane.js').Pane}} */
export const panes = {};

export function setActive(side) {
  if (state.active === side) return;
  state.active = side;
  for (const p of Object.values(panes)) p.el.classList.toggle('active', p.side === side);
}
