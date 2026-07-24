(function () {
    const MONARCH_VENDOR_ID = 0x1C71;
    const MONARCH_PRODUCT_ID = 0xD110;
    const MONARCH_REPORT_ID = 0x21;

    let monarchHidDevice = null;

    const connectBtn = document.getElementById('monarch-hid-connect-btn');
    const disconnectBtn = document.getElementById('monarch-hid-disconnect-btn');
    const statusEl = document.getElementById('monarch-hid-status');

    const MONARCH_COMMANDS = {
        '32:0,32,0': { type: 'move', dCol: -1, dRow: 0 },
        '32:0,64,0': { type: 'move', dCol: 1, dRow: 0 },
        '32:0,8,0': { type: 'move', dCol: 0, dRow: -1 },
        '32:0,16,0': { type: 'move', dCol: 0, dRow: 1 },
        '32:1,0,0': { type: 'depth', delta: -10 },
        '32:8,0,0': { type: 'depth', delta: 10 },
        '32:0,1,0': { type: 'cycle-cursor' },
    };

    function setStatus(msg) {
        if (statusEl) statusEl.textContent = msg;
    }

    if (!('hid' in navigator)) {
        setStatus('Web HID API not supported in this browser.');
        if (connectBtn) connectBtn.disabled = true;
    }

    connectBtn.addEventListener('click', async () => {
        try {
            connectBtn.disabled = true;
            setStatus('Requesting Monarch USB device…');
            const devices = await navigator.hid.requestDevice({
                filters: [{ vendorId: MONARCH_VENDOR_ID, productId: MONARCH_PRODUCT_ID }],
            });
            if (!devices || devices.length === 0) {
                setStatus('No device selected.');
                connectBtn.disabled = false;
                return;
            }
            monarchHidDevice = devices[0];
            if (!monarchHidDevice.opened) {
                await monarchHidDevice.open();
            }
            setStatus(`Connected: ${monarchHidDevice.productName || 'Monarch'}`);
            disconnectBtn.disabled = false;
            // Only flip the connection flag; leave the output-device radio as the
            // user set it. getEffectiveOutputDevice() already routes to the Monarch
            // when it's connected and the user's choice is 'monarch' or 'auto', so
            // connecting shouldn't override an explicit selection.
            if (typeof setMonarchHidConnected === 'function') setMonarchHidConnected(true);
            if (typeof window.announce === 'function') window.announce('Monarch connected via USB.');
            window.connectedTactileDisplay = {
                type: 'Monarch',
                connection: 'hid',
                pixelWidth: 96,
                pixelHeight: 40
            }
            // Send current model state immediately so the display shows the model on connect.
            if (typeof window.sendStateToServer === 'function') window.sendStateToServer();

            monarchHidDevice.addEventListener('inputreport', (e) => {
                const key = monarchReportKey(e.reportId, e.data);
                const command = MONARCH_COMMANDS[key];

                console.log('[Monarch HID] Input report:', key, command || 'unmapped');
                handleMonarchCommand(command);
            });
        } catch (err) {
            setStatus('Error: ' + err.message);
            if (typeof window.announceAlert === 'function') window.announceAlert('Monarch connection error: ' + err.message);
            connectBtn.disabled = false;
        }
    });

    disconnectBtn.addEventListener('click', async () => {
        if (monarchHidDevice) {
            try { await monarchHidDevice.close(); } catch (_) {}
            monarchHidDevice = null;
        }
        setStatus('Not connected.');
        disconnectBtn.disabled = true;
        connectBtn.disabled = false;
        window.connectedTactileDisplay = null;
        if (typeof setMonarchHidConnected === 'function') setMonarchHidConnected(false);
        if (typeof window.announce === 'function') window.announce('Monarch USB disconnected.');
    });

    async function sendCellsToMonarch(monarchCellsHex) {
        if (!monarchHidDevice || !monarchHidDevice.opened) return;
        const cells = Uint8Array.from(
            monarchCellsHex.match(/.{2}/g).map(b => parseInt(b, 16))
        );
        await monarchHidDevice.sendReport(MONARCH_REPORT_ID, cells);
    }

    function monarchReportKey(reportId, data) {
        return `${reportId}:${Array.from(new Uint8Array(data.buffer)).join(',')}`;
    }

    function handleMonarchCommand(command) {
        if (!command) return;

        if (command.type === 'cycle-cursor') {
            window.cycleCursorState?.();
            return;
        }

        if (command.type === 'depth') {
            const previousDepth = window.getCurrentSliceDepth?.();
            if (previousDepth == null) return;

            const nextDepth = Math.max(0, Math.min(100, previousDepth + command.delta));
            window.updateSliceDepth?.(nextDepth, false);
            window.announceDepthValue?.(nextDepth, previousDepth);
            return;
        }

        if (command.type === 'move') {
            const cursorState = window.whichCursor?.() || 'none';
            if (cursorState === 'none') return;

            if (cursorState === 'horizontal-line' && command.dCol !== 0) return;
            if (cursorState === 'vertical-line' && command.dRow !== 0) return;

            window.moveCursor?.(command.dCol, command.dRow);
        }
    }

    window._monarchHidOnRender = function (monarchCellsHex) {
        sendCellsToMonarch(monarchCellsHex).catch(err => {
            console.warn('[Monarch HID] Send failed:', err);
        });
    };
})();
