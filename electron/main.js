const { app, BrowserWindow, globalShortcut, session } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { parseArgs } = require('node:util');

const { compileScad, serialize } = require('./compile');

const WATCH_DEBOUNCE_MS = 300;
const BLUETOOTH_SCAN_MS = 15_000;
const RETRY_MS = 5_000;
const DEVICE_PERMISSIONS = new Set(['hid', 'serial', 'bluetooth']);

function readConfig() {
  const { values, positionals } = parseArgs({
    args: process.argv.slice(app.isPackaged ? 1 : 2),
    options: { url: { type: 'string' }, shortcut: { type: 'string' } },
    allowPositionals: true,
    strict: false, // Chromium switches such as --remote-debugging-port pass through
  });
  const scad = positionals[0] || process.env.CAD_A11Y_SCAD;
  if (!scad) fail('Give the .scad file to watch: npm start -- path/to/model.scad');
  // npm runs the start script from electron/, so a relative path means the caller's directory.
  const scadPath = path.resolve(process.env.INIT_CWD || process.cwd(), scad);
  if (!fs.existsSync(scadPath)) fail(`No such .scad file: ${scadPath}`);
  return {
    scadPath,
    url: values.url || process.env.CAD_A11Y_URL || 'https://cada11y.cs.washington.edu',
    shortcut: values.shortcut || process.env.CAD_A11Y_SHORTCUT || 'CommandOrControl+Shift+R',
    openscad: process.env.CAD_A11Y_OPENSCAD || 'openscad',
  };
}

function fail(message) {
  console.error(message);
  process.exit(1);
}

// Electron has no device chooser, so the viewer's requestDevice/requestPort
// calls would fail. Pick the first device offered, for the viewer's origin only.
function allowDevices(win, origin) {
  const ses = session.defaultSession;
  const sameOrigin = (url) => {
    try {
      return new URL(url).origin === origin;
    } catch {
      return false;
    }
  };
  ses.setPermissionCheckHandler((_wc, permission, requestingOrigin) =>
    DEVICE_PERMISSIONS.has(permission) ? sameOrigin(requestingOrigin) : true);
  ses.setDevicePermissionHandler((details) => sameOrigin(details.origin));

  ses.on('select-hid-device', (event, details, callback) => {
    event.preventDefault();
    callback(details.deviceList[0]?.deviceId);
  });
  ses.on('select-serial-port', (event, portList, _wc, callback) => {
    event.preventDefault();
    callback(portList[0]?.portId ?? '');
  });

  // Fires again each time the scan finds more devices, with the same callback.
  let bluetooth = null;
  const finishBluetooth = (deviceId) => {
    if (!bluetooth) return;
    clearTimeout(bluetooth.timer);
    bluetooth.callback(deviceId);
    bluetooth = null;
  };
  win.webContents.on('select-bluetooth-device', (event, devices, callback) => {
    event.preventDefault();
    bluetooth ??= { callback, timer: setTimeout(() => finishBluetooth(''), BLUETOOTH_SCAN_MS) };
    if (devices.length) finishBluetooth(devices[0].deviceId);
  });
}

function main() {
  const config = readConfig();
  const win = new BrowserWindow({
    width: 1280,
    height: 900,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  allowDevices(win, new URL(config.url).origin);
  const pageLoaded = new Promise((resolve) => win.webContents.once('did-finish-load', resolve));
  win.webContents.on('did-fail-load', (_event, code, description, url, isMainFrame) => {
    // -3 is ERR_ABORTED: a newer navigation replaced this one, nothing failed.
    if (!isMainFrame || code === -3) return;
    console.error(`Could not load ${url} (${description}), retrying in ${RETRY_MS / 1000} seconds`);
    setTimeout(() => win.loadURL(config.url), RETRY_MS);
  });

  const recompile = serialize(async () => {
    try {
      const model = await compileScad(config.scadPath, { openscad: config.openscad });
      await pageLoaded;
      console.log(`Compiled ${model.name}, loading it in the viewer`);
      win.webContents.send('scad:model', model);
    } catch (err) {
      console.error(`OpenSCAD failed: ${err.message}`);
      await pageLoaded;
      win.webContents.send('scad:error', err.message);
    }
  });

  // Watch the directory, not the file: editors save by renaming a new file over it.
  let debounce;
  const scadName = path.basename(config.scadPath);
  fs.watch(path.dirname(config.scadPath), (_event, filename) => {
    if (filename && filename !== scadName) return;
    clearTimeout(debounce);
    debounce = setTimeout(recompile, WATCH_DEBOUNCE_MS);
  });

  if (!globalShortcut.register(config.shortcut, recompile)) {
    console.warn(`Could not register ${config.shortcut}; saving the file still recompiles it.`);
  }

  console.log(`Watching ${config.scadPath}; ${config.shortcut} recompiles it.`);
  win.loadURL(config.url);
  recompile();
}

app.whenReady().then(main);
app.on('will-quit', () => globalShortcut.unregisterAll());
app.on('window-all-closed', () => app.quit());
