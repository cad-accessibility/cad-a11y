(function () {
    // Adafruit Slide Trinkey — Web Serial API
    // CircuitPython firmware outputs lines: "Slider: <integer>\r\n"
    // Raw ADC range: 0–65535 (analogio.AnalogIn, 16-bit).
    // Averaged over SAMPLE_COUNT readings then mapped to slice depth 0–100.

    const TRINKEY_VID = 0x239A;   // Adafruit vendor ID
    const TRINKEY_PID = 0x8102;   // Slide Trinkey product ID (CircuitPython mode)
    const SAMPLE_COUNT = 4;
    const ADC_MAX = 65535;
    // Minimum change (in percent) required before propagating a new depth to the
    // viewer. ADC noise at a fixed physical position typically causes ±1% jitter;
    // requiring ≥ 2% prevents a flood of ARIA mutations when the slider is held still.
    const MIN_DEPTH_CHANGE = 2;
    // After the slider has been idle for this long, announce the settled depth once.
    const DEPTH_SETTLE_MS = 400;

    let port = null;
    let reader = null;
    let running = false;
    let readLoopDone = Promise.resolve(); // resolves when startReading has released its reader lock
    let lastHardwareDepth = null;   // last depth value sent to updateSliceDepth
    let depthSettleTimer = null;    // fires a single announcement after slider stops

    const connectBtn    = document.getElementById('trinkey-connect-btn');
    const disconnectBtn = document.getElementById('trinkey-disconnect-btn');
    const statusEl      = document.getElementById('trinkey-status');
    const depthValueEl  = document.getElementById('trinkey-depth-value');

    function setStatus(msg) {
        if (statusEl) statusEl.textContent = msg;
    }

    if (!('serial' in navigator)) {
        setStatus('Web Serial API not supported — use Chrome/Edge.');
        if (connectBtn) connectBtn.disabled = true;
        return;
    }

    connectBtn.addEventListener('click', async () => {
        try {
            connectBtn.disabled = true;
            setStatus('Requesting device…');

            // Try exact VID/PID first; fall back to full port list if no match.
            try {
                port = await navigator.serial.requestPort({
                    filters: [{ usbVendorId: TRINKEY_VID, usbProductId: TRINKEY_PID }],
                });
            } catch (notFound) {
                if (notFound.name !== 'NotFoundError') throw notFound;
                // Let user pick from all available ports.
                port = await navigator.serial.requestPort();
            }

            // USB-CDC ignores the baud rate, but the API requires one.
            //await port.open({ baudRate: 115200 });
            await port.open({ baudRate: 9600 });

            setStatus('Connected — reading slider…');
            disconnectBtn.disabled = false;
            if (typeof announce === 'function') announce('Trinkey Slider connected.');
            startReading();
        } catch (err) {
            if (err.name !== 'NotFoundError') {
                setStatus('Error: ' + err.message);
                if (typeof announceAlert === 'function') announceAlert('Trinkey Slider connection error: ' + err.message);
            } else {
                setStatus('No device selected.');
            }
            connectBtn.disabled = false;
        }
    });

    disconnectBtn.addEventListener('click', () => disconnect('Not connected.'));

    async function disconnect(reason) {
        running = false;
        clearTimeout(depthSettleTimer);
        lastHardwareDepth = null;
        if (reader) {
            try { await reader.cancel(); } catch (_) {}
            reader = null;
        }
        // Wait for startReading's finally block to release the reader lock before
        // closing the port — port.close() throws if port.readable is still locked.
        await readLoopDone;
        if (port) {
            try { await port.close(); } catch (_) {}
            port = null;
        }
        setStatus(reason || 'Not connected.');
        disconnectBtn.disabled = true;
        connectBtn.disabled = false;
        if (depthValueEl) depthValueEl.textContent = '--';
        if (typeof announce === 'function') announce('Trinkey Slider disconnected.');
    }

    async function startReading() {
        running = true;

        // Use port.readable directly (no TextDecoderStream/pipeTo) so that
        // reader.releaseLock() in the finally block frees port.readable before
        // disconnect() calls port.close(). The pipeTo pattern locks port.readable
        // for the lifetime of the pipe, causing port.close() to throw when called
        // from disconnect() before the pipe has fully unwound.
        reader = port.readable.getReader();
        const textDecoder = new TextDecoder();
        let buffer = '';
        let samples = [];
        let resolveReadLoopDone;
        readLoopDone = new Promise(r => { resolveReadLoopDone = r; });

        try {
            while (running) {
                const { value, done } = await reader.read();
                if (done) break;
                if (value) buffer += textDecoder.decode(value, { stream: true });

                // Process all complete lines in the buffer.
                let nl;
                while ((nl = buffer.indexOf('\n')) !== -1) {
                    const line = buffer.slice(0, nl).trim();
                    buffer = buffer.slice(nl + 1);

                    if (!line.startsWith('Slider: ')) continue;
                    const raw = parseFloat(line.slice('Slider: '.length));
                    if (isNaN(raw)) continue;

                    samples.push(raw);
                    if (samples.length < SAMPLE_COUNT) continue;

                    const avg = samples.reduce((a, b) => a + b, 0) / samples.length;
                    samples = [];

                    //const depth = Math.round(Math.max(0, Math.min(100, (avg / ADC_MAX) * 100)));
                    const depth = Math.round(Math.max(0, Math.min(100, avg)));
                    if (depthValueEl) depthValueEl.textContent = depth + '%';

                    // Only propagate when the position has moved by ≥ MIN_DEPTH_CHANGE.
                    if (lastHardwareDepth === null || Math.abs(depth - lastHardwareDepth) >= MIN_DEPTH_CHANGE) {
                        lastHardwareDepth = depth;
                        if (typeof updateSliceDepth === 'function') {
                            window.pendingInputSource = 'slider';
                            updateSliceDepth(depth, false);
                        }
                    }

                    // Announce the settled depth once after the slider has been idle.
                    // viewer.js loads first as a classic script, so announceDepthValue
                    // is defined by the time this fires; the guard is just defence
                    // against a failed script load.
                    clearTimeout(depthSettleTimer);
                    depthSettleTimer = setTimeout(() => {
                        if (lastHardwareDepth !== null && typeof announceDepthValue === 'function') {
                            announceDepthValue(lastHardwareDepth);
                        }
                    }, DEPTH_SETTLE_MS);
                }
            }
        } catch (err) {
            if (running) disconnect('Lost connection: ' + err.message);
        } finally {
            try { reader.releaseLock(); } catch (_) {}
            resolveReadLoopDone();
        }
    }
})();
