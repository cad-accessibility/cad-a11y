const { ipcRenderer, webFrame } = require('electron');

// The viewer's own upload handler does the work: it uploads under this tab's
// session, selects the model, renders it and refreshes the braille display.
// A model that arrives while that cannot happen yet (an upload in flight, the
// page still loading) waits here, and only the newest one is kept.
let waiting = null;

function upload(model) {
  const input = document.getElementById('upload-model-input');
  if (!input || input.disabled) {
    waiting = model;
    return;
  }
  waiting = null;
  const files = new DataTransfer();
  files.items.add(new File([model.text], model.name, { type: 'model/stl' }));
  input.files = files.files;
  input.dispatchEvent(new Event('change'));
}

function say(text) {
  const status = document.getElementById('upload-model-status');
  if (status) status.textContent = text;
  // announceAlert lives in the page's world, not this one. Unlike writing the
  // live region directly, it also puts the message on the braille display.
  webFrame.executeJavaScript(`typeof announceAlert === 'function' && announceAlert(${JSON.stringify(text)})`);
}

window.addEventListener('DOMContentLoaded', () => {
  const input = document.getElementById('upload-model-input');
  if (!input) {
    console.error('cad-a11y: this page is not the viewer, so compiled models cannot be loaded');
    return;
  }
  new MutationObserver(() => {
    if (waiting) upload(waiting);
  }).observe(input, { attributeFilter: ['disabled'] });
  if (waiting) upload(waiting);
});

ipcRenderer.on('scad:model', (_event, model) => upload(model));
ipcRenderer.on('scad:error', (_event, text) => {
  // A model still waiting is older than the source that just failed.
  waiting = null;
  say(text);
});
ipcRenderer.on('scad:notice', (_event, text) => say(text));
