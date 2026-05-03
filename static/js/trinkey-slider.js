(function () {
    // Adafruit Slide Trinkey — Web Serial API
    // CircuitPython firmware outputs lines: "Slider: <integer>\r\n"
    // Raw ADC range: 0–65535 (analogio.AnalogIn, 16-bit).
    // Averaged over SAMPLE_COUNT readings then mapped to slice depth 0–100.

    const TRINKEY_VID = 0x239A;   // Adafruit vendor ID
    const TRINKEY_PID = 0x8102;   // Slide Trinkey product ID (CircuitPython mode)
    const SAMPLE_COUNT = 10;
    const ADC_MAX = 65535;

    let port = null;
    let reader = null;
    let running = false;

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
            await port.open({ baudRate: 115200 });

            setStatus('Connected — reading slider…');
            disconnectBtn.disabled = false;
            if (typeof announce === 'function') announce('Trinkey Slider connected.');
            startReading();
        } catch (err) {
            if (err.name !== 'NotFoundError') setStatus('Error: ' + err.message);
            else setStatus('No device selected.');
            connectBtn.disabled = false;
        }
    });

    disconnectBtn.addEventListener('click', () => disconnect('Not connected.'));

    async function disconnect(reason) {
        running = false;
        if (reader) {
            try { await reader.cancel(); } catch (_) {}
            reader = null;
        }
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

        // Stream readable bytes through a text decoder.
        const decoder = new TextDecoderStream();
        const pipePromise = port.readable.pipeTo(decoder.writable);
        reader = decoder.readable.getReader();

        let buffer = '';
        let samples = [];

        try {
            while (running) {
                const { value, done } = await reader.read();
                if (done) break;
                if (value) buffer += value;

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

                    const depth = Math.round(Math.max(0, Math.min(100, (avg / ADC_MAX) * 100)));
                    if (depthValueEl) depthValueEl.textContent = depth + '%';

                    if (typeof updateSliceDepth === 'function') {
                        updateSliceDepth(depth, false);
                    }
                }
            }
        } catch (err) {
            if (running) {
                await disconnect('Lost connection: ' + err.message);
            }
        }

        try { await pipePromise; } catch (_) {}
    }
})();
