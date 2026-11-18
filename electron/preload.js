const { ipcRenderer } = require('electron');

// The viewer's own upload handler does the work: it uploads under this tab's
// session, selects the model, renders it and refreshes the braille display.
let waiting = null;

function upload(model) {
  const input = document.getElementById('upload-model-input');
  if (input.disabled) {
    // An upload is in flight; the observer below sends the newest model after it.
    waiting = model;
    return;
  }
  const files = new DataTransfer();
  files.items.add(new File([model.text], model.name, { type: 'model/stl' }));
  input.files = files.files;
  input.dispatchEvent(new Event('change'));
}

function showError(message) {
  const text = `OpenSCAD could not compile the model. ${message}`;
  document.getElementById('upload-model-status').textContent = text;
  // The viewer's assertive live region, the one its announceAlert writes to.
  document.getElementById('announcement-window').textContent = text;
}

window.addEventListener('DOMContentLoaded', () => {
  const input = document.getElementById('upload-model-input');
  new MutationObserver(() => {
    if (waiting && !input.disabled) {
      const model = waiting;
      waiting = null;
      upload(model);
    }
  }).observe(input, { attributeFilter: ['disabled'] });
});

ipcRenderer.on('scad:model', (_event, model) => upload(model));
ipcRenderer.on('scad:error', (_event, message) => showError(message));
