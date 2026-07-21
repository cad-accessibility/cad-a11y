import { DotPadSDK, DotPadScanner, DataCodes, DisplayMode } from '/static/js/DotPadSDK-3.0.0.js';

const SERVER_URL = window.location.origin;

const sdk = new DotPadSDK();
const scanner = new DotPadScanner();
let connectedDevice = null;   // DotDevice returned by SDK
let connectionType = null;     // 'ble' | 'usb'
let rawTarget = null;          // BluetoothDevice | SerialPort

const statusEl = document.getElementById('dotpad-status');
const bleScanBtn = document.getElementById('dotpad-scan-ble-btn');
const disconnectBtn = document.getElementById('dotpad-disconnect-btn');
const autoSendCheckbox = document.getElementById('dotpad-auto-send');

function setStatus(msg) {
    if (statusEl) statusEl.textContent = msg;
    // Also update the top status bar DotPad field.
    const sbDotPad = document.getElementById('sb-dotpad');
    if (sbDotPad) sbDotPad.textContent = msg;
}

// --- BLE scan & connect ---
bleScanBtn.addEventListener('click', async () => {
    try {
        setStatus('Scanning for BLE DotPad...');
        const device = await scanner.startBleScan();
        if (!device) { setStatus('No BLE device selected.'); return; }
        rawTarget = device;
        setStatus(`Connecting to ${device.name || 'BLE DotPad'}...`);
        const dotDevice = await sdk.connectBleDevice(device);
        if (dotDevice) {
            connectedDevice = dotDevice;
            connectionType = 'ble';
            sdk.setCallBack(onMessage, onKey);
            setStatus(`Connected: ${device.name || 'BLE DotPad'}`);
            disconnectBtn.disabled = false;
            if (typeof window.announce === 'function') window.announce('DotPad connected via Bluetooth.');
            // Send current model state immediately so the display shows the model on connect.
            if (typeof window.sendStateToServer === 'function') window.sendStateToServer();
        } else {
            setStatus('BLE connection failed.');
            if (typeof window.announceAlert === 'function') window.announceAlert('DotPad Bluetooth connection failed.');
        }
    } catch (err) {
        console.error('BLE scan/connect error:', err);
        setStatus('BLE error: ' + err.message);
        if (typeof window.announceAlert === 'function') window.announceAlert('DotPad Bluetooth error: ' + err.message);
    }
});

// --- Disconnect ---
disconnectBtn.addEventListener('click', () => {
    sdk.disconnect(connectedDevice);
    connectedDevice = null;
    connectionType = null;
    rawTarget = null;
    disconnectBtn.disabled = true;
    setStatus('Disconnected.');
    if (typeof window.announce === 'function') window.announce('DotPad disconnected.');
});

// --- SDK callbacks ---
function onMessage(device, dataCode, msg) {
    if (dataCode === DataCodes.Disconnected) {
        connectedDevice = null;
        connectionType = null;
        rawTarget = null;
        disconnectBtn.disabled = true;
        setStatus('DotPad disconnected unexpectedly.');
        if (typeof window.announceAlert === 'function') window.announceAlert('DotPad disconnected unexpectedly.');
    } else if (dataCode === DataCodes.Connected) {
        // Device fully initialised (board info received, cell dimensions set).
        // Send initial render so the display shows the current model immediately.
        if (typeof window.sendStateToServer === 'function') window.sendStateToServer();
    }
    console.log('[DotPad]', dataCode, msg);
}

function onKey(device, keyCode, keyMsg) {
    console.log('[DotPad key]', keyCode, keyMsg);
}

// --- Send hex data to DotPad ---
let sendInFlight = false;

async function sendHexToDotPad(renderParams) {
    if (!connectedDevice || sendInFlight) return;
    if (autoSendCheckbox && !autoSendCheckbox.checked) return;

    // Read the actual cell grid from the connected device so the server renders
    // at the correct pixel size. Falls back to DotPad 300A defaults (30×10).
    const dotpadCols = connectedDevice.numberCellColumns || 30;
    const dotpadRows = connectedDevice.numberCellRows || 10;

    sendInFlight = true;
    try {
        const resp = await fetch(`${SERVER_URL}/render/dotpad-hex`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ...renderParams, dotpad_cols: dotpadCols, dotpad_rows: dotpadRows }),
            mode: 'cors',
        });
        const data = await resp.json();
        if (data.status === 'success' && data.dotpad_graphic_hex) {
            sdk.displayGraphicData(data.dotpad_graphic_hex, connectedDevice, DisplayMode.GraphicMode);
        }
    } catch (err) {
        console.warn('DotPad hex send failed:', err);
    } finally {
        sendInFlight = false;
    }
}

// Hook into the main render cycle
window._dotpadOnRender = sendHexToDotPad;
