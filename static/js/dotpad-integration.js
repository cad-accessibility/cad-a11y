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

// ── NABCC 8-dot Computer Braille lookup table ────────────────────────────
// Index = ASCII code - 0x20 (covers 0x20 space through 0x7E tilde)
// Value = 8-dot braille byte: bit0=dot1, bit1=dot2, bit2=dot3, bit3=dot4,
//                              bit4=dot5, bit5=dot6, bit6=dot7, bit7=dot8
// Source: BRLTTY en-nabcc.ttb (North American Braille Computer Code)
const NABCC = new Uint8Array([
    0x00, // 0x20  (space)
    0x2E, // 0x21  !  dots 2,3,4,6
    0x10, // 0x22  "  dot 5
    0x3C, // 0x23  #  dots 3,4,5,6
    0x2B, // 0x24  $  dots 1,2,4,6
    0x29, // 0x25  %  dots 1,4,6
    0x2F, // 0x26  &  dots 1,2,3,4,6
    0x04, // 0x27  '  dot 3
    0x37, // 0x28  (  dots 1,2,3,5,6
    0x3E, // 0x29  )  dots 2,3,4,5,6
    0x21, // 0x2A  *  dots 1,6
    0x2C, // 0x2B  +  dots 3,4,6
    0x20, // 0x2C  ,  dot 6
    0x24, // 0x2D  -  dots 3,6
    0x28, // 0x2E  .  dots 4,6
    0x0C, // 0x2F  /  dots 3,4
    0x34, // 0x30  0  dots 3,5,6
    0x02, // 0x31  1  dot 2
    0x06, // 0x32  2  dots 2,3
    0x12, // 0x33  3  dots 2,5
    0x32, // 0x34  4  dots 2,5,6
    0x22, // 0x35  5  dots 2,6
    0x16, // 0x36  6  dots 2,3,5
    0x36, // 0x37  7  dots 2,3,5,6
    0x26, // 0x38  8  dots 2,3,6
    0x14, // 0x39  9  dots 3,5
    0x31, // 0x3A  :  dots 1,5,6
    0x30, // 0x3B  ;  dots 5,6
    0x23, // 0x3C  <  dots 1,2,6
    0x3F, // 0x3D  =  dots 1,2,3,4,5,6
    0x1C, // 0x3E  >  dots 3,4,5
    0x39, // 0x3F  ?  dots 1,4,5,6
    0x48, // 0x40  @  dots 4,7
    0x41, // 0x41  A  dots 1,7
    0x43, // 0x42  B  dots 1,2,7
    0x49, // 0x43  C  dots 1,4,7
    0x59, // 0x44  D  dots 1,4,5,7
    0x51, // 0x45  E  dots 1,5,7
    0x4B, // 0x46  F  dots 1,2,4,7
    0x5B, // 0x47  G  dots 1,2,4,5,7
    0x53, // 0x48  H  dots 1,2,5,7
    0x4A, // 0x49  I  dots 2,4,7
    0x5A, // 0x4A  J  dots 2,4,5,7
    0x45, // 0x4B  K  dots 1,3,7
    0x47, // 0x4C  L  dots 1,2,3,7
    0x4D, // 0x4D  M  dots 1,3,4,7
    0x5D, // 0x4E  N  dots 1,3,4,5,7
    0x55, // 0x4F  O  dots 1,3,5,7
    0x4F, // 0x50  P  dots 1,2,3,4,7
    0x5F, // 0x51  Q  dots 1,2,3,4,5,7
    0x57, // 0x52  R  dots 1,2,3,5,7
    0x4E, // 0x53  S  dots 2,3,4,7
    0x5E, // 0x54  T  dots 2,3,4,5,7
    0x65, // 0x55  U  dots 1,3,6,7
    0x67, // 0x56  V  dots 1,2,3,6,7
    0x7A, // 0x57  W  dots 2,4,5,6,7
    0x6D, // 0x58  X  dots 1,3,4,6,7
    0x7D, // 0x59  Y  dots 1,3,4,5,6,7
    0x75, // 0x5A  Z  dots 1,3,5,6,7
    0x6A, // 0x5B  [  dots 2,4,6,7
    0x73, // 0x5C  \  dots 1,2,5,6,7
    0x7B, // 0x5D  ]  dots 1,2,4,5,6,7
    0x58, // 0x5E  ^  dots 4,5,7
    0x38, // 0x5F  _  dots 4,5,6
    0x08, // 0x60  `  dot 4
    0x01, // 0x61  a  dot 1
    0x03, // 0x62  b  dots 1,2
    0x09, // 0x63  c  dots 1,4
    0x19, // 0x64  d  dots 1,4,5
    0x11, // 0x65  e  dots 1,5
    0x0B, // 0x66  f  dots 1,2,4
    0x1B, // 0x67  g  dots 1,2,4,5
    0x13, // 0x68  h  dots 1,2,5
    0x0A, // 0x69  i  dots 2,4
    0x1A, // 0x6A  j  dots 2,4,5
    0x05, // 0x6B  k  dots 1,3
    0x07, // 0x6C  l  dots 1,2,3
    0x0D, // 0x6D  m  dots 1,3,4
    0x1D, // 0x6E  n  dots 1,3,4,5
    0x15, // 0x6F  o  dots 1,3,5
    0x0F, // 0x70  p  dots 1,2,3,4
    0x1F, // 0x71  q  dots 1,2,3,4,5
    0x17, // 0x72  r  dots 1,2,3,5
    0x0E, // 0x73  s  dots 2,3,4
    0x1E, // 0x74  t  dots 2,3,4,5
    0x25, // 0x75  u  dots 1,3,6
    0x27, // 0x76  v  dots 1,2,3,6
    0x3A, // 0x77  w  dots 2,4,5,6
    0x2D, // 0x78  x  dots 1,3,4,6
    0x3D, // 0x79  y  dots 1,3,4,5,6
    0x35, // 0x7A  z  dots 1,3,5,6
    0x2A, // 0x7B  {  dots 2,4,6
    0x33, // 0x7C  |  dots 1,2,5,6
    0x3B, // 0x7D  }  dots 1,2,4,5,6
    0x18, // 0x7E  ~  dots 4,5
]);

// --- DotPad key mapping to cursor movement ---
const DOTPAD_KEY_ACTIONS = {
    KeyFunction1: [0, -1],
    KeyFunction4: [0, 1],
    PanningLeft: [-1, 0],
    PanningRight: [1, 0],
};
const DOTPAD_ONE_STEP = 1;
const DOTPAD_HOLD_START_MS = 350;
const DOTPAD_HOLD_REPEAT_MS = 100;
const DOTPAD_HOLD_STEPS = [2, 4, 8, 16];
const DOTPAD_MULTI_TAP_WINDOW_MS = 250;
const DOTPAD_TAP_STEPS = [1, 5, 12];

let pendingTap = null;

// ── Braille key chord → letter ───────────────────────────────────────────
// Buttons: LP=dot3, 8=dot2, 4=dot1, 2=dot4, 1=dot5, RP=dot6
// Key label format examples: "LP +0", "RP +4", "AP +12", "+7"
// nabccToChar: braille byte → printable ASCII character (6-dot patterns only).
// Lowercase letters take priority over any punctuation sharing the same pattern.
const nabccToChar = {};
(function buildReverseNabcc() {
    window._nabccReverse = {};
    for (let ascii = 0x20; ascii <= 0x7E; ascii++) {
    const b = NABCC[ascii - 0x20];
    if (b < 0x40 && !nabccToChar[b]) nabccToChar[b] = String.fromCharCode(ascii);
    }
    for (let ascii = 0x61; ascii <= 0x7A; ascii++) {
    const b = NABCC[ascii - 0x20];
    nabccToChar[b] = String.fromCharCode(ascii);       // lowercase wins conflicts
    window._nabccReverse[b] = String.fromCharCode(ascii);
    }
})();

// Parse a key event label into a 6-dot braille byte.
// Buttons: 4=dot1, 8=dot2, LP=dot3, 2=dot4, 1=dot5, RP=dot6
function labelToByte6(label) {
    const hasLP = /\bLP\b/.test(label) || /\bAP\b/.test(label);
    const hasRP = /\bRP\b/.test(label) || /\bAP\b/.test(label);
    const mPlus = label.match(/\+\s*(\d+)/);
    const mBare = !mPlus && label.match(/^\d+$/);
    const num   = mPlus ? parseInt(mPlus[1], 10) : mBare ? parseInt(mBare[0], 10) : 0;
    return ((num & 4)  ? 0x01 : 0) |  // dot 1
            ((num & 8)  ? 0x02 : 0) |  // dot 2
            (hasLP      ? 0x04 : 0) |  // dot 3
            ((num & 2)  ? 0x08 : 0) |  // dot 4
            ((num & 1)  ? 0x10 : 0) |  // dot 5
            (hasRP      ? 0x20 : 0);   // dot 6
}

function byte6ToLetter(byte6) {
    return window._nabccReverse[byte6] || null;
}

function setStatus(msg) {
    if (statusEl) statusEl.textContent = msg;
    // Also update the top status bar DotPad field.
    const sbDotPad = document.getElementById('sb-dotpad');
    if (sbDotPad) sbDotPad.textContent = msg;
}
function setConnectedDotPadDisplay(dotDevice, connectionType){
    if (!dotDevice) return;
    const cellCols = dotDevice.numberCellColumns || 30;
    const cellRows = dotDevice.numberCellRows || 10;
    const pixelWidth = cellCols * 2;  // Each cell is 2 pixels wide
    const pixelHeight = cellRows * 4;  // Each cell is 4 pixels tall
    window.connectedTactileDisplay = {
        type: 'DotPad',
        connection: connectionType,
        cellCols: cellCols,
        cellRows: cellRows,
        pixelWidth: pixelWidth,
        pixelHeight: pixelHeight,
    }
    console.log(`DotPad dimensions: ${cellCols}×${cellRows} cells, ${pixelWidth}×${pixelHeight} pixels`);
}
// --- BLE scan & connect ---
// A DotPad over Web Bluetooth frequently resolves gatt.connect() before its GATT
// services are discoverable (common on Windows/Edge), so the SDK's getPrimaryService
// throws and the attempt fails while leaving the raw GATT half-open. That dangling
// connection is why a first "Connect" can report failure and only a manual
// disconnect+reconnect recovers (#45); a busy page like this viewer, with its SSE
// stream and continuous renders, seems to hit the race more often than a light one.
// Retry a few times, tearing the GATT down between attempts so each starts clean —
// the automatic form of the disconnect-then-reconnect that already works by hand.
const BLE_CONNECT_ATTEMPTS = 3;
const BLE_RETRY_DELAY_MS = 400;

async function connectBleWithRetry(device) {
    for (let attempt = 1; attempt <= BLE_CONNECT_ATTEMPTS; attempt++) {
        const suffix = attempt > 1 ? ` (attempt ${attempt} of ${BLE_CONNECT_ATTEMPTS})` : '';
        setStatus(`Connecting to ${device.name || 'BLE DotPad'}${suffix}...`);
        try {
            const dotDevice = await sdk.connectBleDevice(device);
            if (dotDevice) return dotDevice;
        } catch (err) {
            console.warn(`DotPad BLE connect attempt ${attempt} failed:`, err);
        }
        // Clear the half-open GATT the failed attempt may have left, so the next
        // attempt (and any later manual one) starts from a clean state.
        try {
            if (device.gatt && device.gatt.connected) device.gatt.disconnect();
        } catch (_) { /* already gone */ }
        if (attempt < BLE_CONNECT_ATTEMPTS) {
            await new Promise(resolve => setTimeout(resolve, BLE_RETRY_DELAY_MS));
        }
    }
    return null;
}

bleScanBtn.addEventListener('click', async () => {
    try {
        setStatus('Scanning for BLE DotPad...');
        const device = await scanner.startBleScan();
        if (!device) { setStatus('No BLE device selected.'); return; }
        rawTarget = device;
        const dotDevice = await connectBleWithRetry(device);
        if (dotDevice) {
            connectedDevice = dotDevice;
            connectionType = 'ble';
            sdk.setCallBack(onMessage, onKey);
            setStatus(`Connected: ${device.name || 'BLE DotPad'}`);
            disconnectBtn.disabled = false;
            if (typeof window.announce === 'function') window.announce('DotPad connected via Bluetooth.');
            setConnectedDotPadDisplay(dotDevice, 'ble');
            // Send current model state immediately so the display shows the model on connect.
            if (typeof window.sendStateToServer === 'function') window.sendStateToServer();
        } else {
            setStatus('BLE connection failed. Please try again.');
            if (typeof window.announceAlert === 'function') window.announceAlert('DotPad Bluetooth connection failed.');
        }
    } catch (err) {
        console.error('BLE scan/connect error:', err);
        setStatus('BLE error: ' + err.message);
        if (typeof window.announceAlert === 'function') window.announceAlert('DotPad Bluetooth error: ' + err.message);
    }
});

// --- USB scan & connect ---
usbScanBtn.addEventListener('click', async () => {
    try {
        setStatus('Requesting USB DotPad...');
        const port = await scanner.startUsbScan();
        if (!port) { setStatus('No USB device selected.'); return; }
        rawTarget = port;
        setStatus('Connecting to USB DotPad...');
        const dotDevice = await sdk.connectUsbDevice(port);
        if (dotDevice) {
            connectedDevice = dotDevice;
            connectionType = 'usb';
            sdk.setCallBack(onMessage, onKey);
            setStatus(`Connected: USB DotPad`);
            disconnectBtn.disabled = false;
            if (typeof window.announce === 'function') window.announce('DotPad connected via USB.');
            setConnectedDotPadDisplay(dotDevice, 'usb');
            // Send current model state immediately so the display shows the model on connect.
            if (typeof window.sendStateToServer === 'function') window.sendStateToServer();
        } else {
            setStatus('USB connection failed.');
            if (typeof window.announceAlert === 'function') window.announceAlert('DotPad USB connection failed.');
        }
    } catch (err) {
        console.error('USB scan/connect error:', err);
        setStatus('USB error: ' + err.message);
        if (typeof window.announceAlert === 'function') window.announceAlert('DotPad USB error: ' + err.message);
    }
});

// --- Disconnect ---
disconnectBtn.addEventListener('click', () => {
    if (connectedDevice) sdk.disconnect(connectedDevice);
    // Also tear down the raw GATT directly, in case a prior attempt left one open
    // that the SDK never took ownership of (#45).
    try {
        if (rawTarget && rawTarget.gatt && rawTarget.gatt.connected) rawTarget.gatt.disconnect();
    } catch (_) { /* already gone */ }
    connectedDevice = null;
    connectionType = null;
    rawTarget = null;
    disconnectBtn.disabled = true;
    window.connectedTactileDisplay = null;
    setStatus('Disconnected.');
    if (typeof window.announce === 'function') window.announce('DotPad disconnected.');
    // No global device dimensions exposed in minimal setup
});

// --- SDK callbacks ---
function onMessage(device, dataCode, msg) {
    if (dataCode === DataCodes.Disconnected) {
        connectedDevice = null;
        connectionType = null;
        rawTarget = null;
        disconnectBtn.disabled = true;
        window.connectedTactileDisplay = null;
        setStatus('DotPad disconnected unexpectedly.');
        if (typeof window.announceAlert === 'function') window.announceAlert('DotPad disconnected unexpectedly.');
    } else if (dataCode === DataCodes.Connected) {
        // Device fully initialised (board info received, cell dimensions set).
        // Send initial render so the display shows the current model immediately.
        if (typeof window.sendStateToServer === 'function') window.sendStateToServer();
    }
    console.log('[DotPad]', dataCode, msg);
}

function onKey(device, currKeyCode, keyMsg) {
    const label = keyMsg || currKeyCode;
    const byte6 = labelToByte6(label);
    const letter = byte6ToLetter(byte6);
    const cursorState = window.whichCursor ? window.whichCursor() : 'none';
    const n = 10; // TODO: make this global and dynamic
    if (
        typeof window.getCurrentSliceDepth !== 'function' ||
        typeof window.updateSliceDepth !== 'function' ||
        typeof window.announceDepthShortcut !== 'function'
    ) {
        console.warn('DotPad depth controls are unavailable because viewer depth helpers are not exposed.');
        return;
    }

    if (letter === 'v'){
        if (typeof window.cycleCursorState === 'function') {
            window.cycleCursorState();
            // 20 character announcement for screen reader users to know the cursor state has changed
            console.log('Cursor state now ', window.whichCursor ? window.whichCursor() : 'none');
        }
        return;
    }
    if (byte6 === 0x01){
        // Go shallower (decrease depth by 100/N)
        const previousDepth = window.getCurrentSliceDepth();
        const nextDepth = Math.max(0, previousDepth - 100/n); // TODO: calculate integer value
        window.updateSliceDepth(nextDepth, false);
        window.announceDepthShortcut('Dot 1', previousDepth, nextDepth);
        return;
    }
    if (byte6 === 0x08){
        // Go deeper (increase depth by 100/N)
        const previousDepth = window.getCurrentSliceDepth();
        const nextDepth = Math.min(100, previousDepth + 100/n); // TODO: calculate integer value
        window.updateSliceDepth(nextDepth, false);
        window.announceDepthShortcut('Dot 4', previousDepth, nextDepth);
        return;
    }
    if (typeof window.moveCursor != 'function') return;

    const cursorAction = DOTPAD_KEY_ACTIONS[currKeyCode];
    if (cursorState === 'none') {
        console.log('DotPad key pressed but cursor state is "none":', currKeyCode, keyMsg);
        return;
    }
    if (cursorState === 'horizontal-line' && (currKeyCode === 'KeyFunction1' || currKeyCode === 'KeyFunction4')) {
        console.log('DotPad key pressed but cursor state is "horizontal-line":', currKeyCode, keyMsg);
        return;
    }
    if (cursorState === 'vertical-line' && (currKeyCode === 'PanningLeft' || currKeyCode === 'PanningRight')) {
        console.log('DotPad key pressed but cursor state is "vertical-line":', currKeyCode, keyMsg);
        return;
    }

    if (!cursorAction) {
        console.log('Unmapped DotPad key:', currKeyCode, keyMsg);
    }
    else {
        console.log(`DotPad key: ${currKeyCode} -> action: [${cursorAction[0]}, ${cursorAction[1]}]`);
        if (!pendingTap) {
            pendingTap = {
                keyCode: currKeyCode,
                action: cursorAction,
                repeatCount: 1,
                timer: setTimeout(() => {
                    // Execute the tap action after the multi-tap window expires
                    if (!pendingTap) return;
                    window.moveCursor(cursorAction[0], cursorAction[1]);
                    pendingTap = null;
                }, DOTPAD_MULTI_TAP_WINDOW_MS)
            };
        }
        else if (pendingTap.keyCode === currKeyCode) {
            pendingTap.repeatCount++;
            clearTimeout(pendingTap.timer);
            pendingTap.timer = setTimeout(() => {
                if (!pendingTap) return;
                window.moveCursor(cursorAction[0], cursorAction[1], DOTPAD_TAP_STEPS[Math.min(pendingTap.repeatCount - 1, DOTPAD_TAP_STEPS.length - 1)]);
                pendingTap = null;
            }, DOTPAD_MULTI_TAP_WINDOW_MS);
        }
        else if (pendingTap.keyCode !== currKeyCode) {
            // Different key pressed, execute the previous tap action immediately then start a new tap sequence
            clearTimeout(pendingTap.timer);
            window.moveCursor(pendingTap.action[0], pendingTap.action[1], DOTPAD_TAP_STEPS[Math.min(pendingTap.repeatCount - 1, DOTPAD_TAP_STEPS.length - 1)]);
            pendingTap = {
                keyCode: currKeyCode,
                action: cursorAction,
                repeatCount: 1,
                timer: setTimeout(() => {
                    if (!pendingTap) return;
                    window.moveCursor(cursorAction[0], cursorAction[1]);
                    pendingTap = null;
                }, DOTPAD_MULTI_TAP_WINDOW_MS)
            };
        }
    }
}



// Hold handling unused for now, waiting for SDK to support key release events
let activeHold = null;

function getHoldStep(repeatCount){
    if (repeatCount < 2) return DOTPAD_ONE_STEP;
    if (repeatCount < 5) return DOTPAD_HOLD_STEPS[0];
    if (repeatCount < 10) return DOTPAD_HOLD_STEPS[1];
    if (repeatCount < 20) return DOTPAD_HOLD_STEPS[2];
    return DOTPAD_HOLD_STEPS[3];
}

function stopDotPadHold() {
    if (!activeHold) return;
    clearTimeout(activeHold.startTimer);
    clearInterval(activeHold.repeatTimer);
    activeHold = null;
}

function startDotPadHold(keyCode, action) {
    stopDotPadHold();

    activeHold = {
        keyCode: keyCode,
        action: action,
        repeatCount: 0,
        startTimer: null,
        repeatTimer: null,
    };

    activeHold.startTimer = setTimeout(() => {
        if (!activeHold) return;

        activeHold.repeatTimer = setInterval(() => {
            if (!activeHold || typeof window.moveCursor !== 'function') return;
            activeHold.repeatCount++;
            window.moveCursor(
                activeHold.action[0],
                activeHold.action[1],
                getHoldStep(activeHold.repeatCount)
            );
        }, DOTPAD_HOLD_REPEAT_MS);
    }, DOTPAD_HOLD_START_MS);
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


// --- Send announcements to DotPad ---

function encodeAnnouncementForDotPad(message, cellCount) {
    let hex = '';
    for (let i = 0; i < cellCount; i++) {
        const ch   = i < message.length ? message[i] : ' ';
        const code = ch.charCodeAt(0);
        const b    = (code >= 0x20 && code <= 0x7E) ? NABCC[code - 0x20] : 0x00;
        hex += b.toString(16).padStart(2, '0').toUpperCase();
    }
    return hex;
}

function sendAnnouncementToDotPad({message}) {
    if (!connectedDevice) return;

    const cellCount = connectedDevice.numberBrailleCellColumns || 20;
    const textHex = encodeAnnouncementForDotPad(message, cellCount);
    sdk.displayTextData(textHex, connectedDevice, DisplayMode.TextMode);
}

window.onTactileAnnouncement = sendAnnouncementToDotPad;
