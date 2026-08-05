(function () {
    const MONARCH_VENDOR_ID = 0x1C71;
    const MONARCH_PRODUCT_ID = 0xD110;
    const MONARCH_REPORT_ID = 0x21;

    let monarchHidDevice = null;

    const statusEl = document.getElementById('monarch-hid-status');

    // There is no Monarch-specific connect/disconnect button in the page:
    // the generic "Connect"/"Disconnect" pair in the nav calls connectMonarchHid()
    // / disconnectMonarchHid() directly, exposed at the bottom of this file, when
    // the Output Device setting names Monarch. `connecting` stands in for the
    // disabled-while-connecting state a dedicated button used to carry.
    let connecting = false;

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
    }

    async function connectMonarchHid() {
        if (connecting || monarchHidDevice) return;
        if (!('hid' in navigator)) {
            if (typeof window.announceAlert === 'function') window.announceAlert('Web HID is not supported in this browser.');
            return;
        }
        connecting = true;
        try {
            setStatus('Requesting Monarch USB device…');
            const devices = await navigator.hid.requestDevice({
                filters: [{ vendorId: MONARCH_VENDOR_ID, productId: MONARCH_PRODUCT_ID }],
            });
            if (!devices || devices.length === 0) {
                setStatus('No device selected.');
                return;
            }
            monarchHidDevice = devices[0];
            if (!monarchHidDevice.opened) {
                await monarchHidDevice.open();
            }
            setStatus(`Connected: ${monarchHidDevice.productName || 'Monarch'}`);
            // Only flip the connection flag; leave the output-device radio as the
            // user set it. getEffectiveOutputDevice() already routes to the Monarch
            // when it's connected and the user's choice is 'monarch' or 'auto', so
            // connecting shouldn't override an explicit selection.
            if (typeof setMonarchHidConnected === 'function') setMonarchHidConnected(true);
            if (typeof window.announce === 'function') window.announce('Monarch connected via USB.');
            // A Monarch is 48 cells x 10 lines and a braille cell is 2x4 pixels,
            // so this is exactly the 96x40 default grid. Registering it costs no
            // extra render: naming a size is what routes the request down the
            // single-render path, and it lets the previews say which display they
            // are describing. Registering also re-renders, so the display shows
            // the model on connect.
            window.setTactileDisplay?.('monarch_hid', {
                type: 'Monarch',
                connection: 'usb-hid',
                cellCols: 48,
                cellRows: 10,
                pixelWidth: 96,
                pixelHeight: 40,
                label: 'Monarch 48×10 cells',
            });

            monarchHidDevice.addEventListener('inputreport', (e) => {
                const key = monarchReportKey(e.reportId, e.data);
                const command = MONARCH_COMMANDS[key];

                console.log('[Monarch HID] Input report:', key, command || 'unmapped');
                handleMonarchCommand(command);
            });
        } catch (err) {
            monarchHidDevice = null;
            setStatus('Error: ' + err.message);
            if (typeof window.announceAlert === 'function') window.announceAlert('Monarch connection error: ' + err.message);
        } finally {
            connecting = false;
        }
    }

    async function disconnectMonarchHid() {
        if (monarchHidDevice) {
            try { await monarchHidDevice.close(); } catch (_) {}
            monarchHidDevice = null;
        }
        setStatus('Not connected.');
        if (typeof setMonarchHidConnected === 'function') setMonarchHidConnected(false);
        if (typeof window.announce === 'function') window.announce('Monarch USB disconnected.');
    }

    // Called directly by the generic Connect/Disconnect pair in viewer.js
    // (#145) — see deviceConnectBtn / deviceDisconnectBtn.
    window.connectMonarchHid = connectMonarchHid;
    window.disconnectMonarchHid = disconnectMonarchHid;

    async function sendCellsToMonarch(monarchCellsHex) {
        if (!monarchHidDevice || !monarchHidDevice.opened) return;
        const cells = Uint8Array.from(
            monarchCellsHex.match(/.{2}/g).map(b => parseInt(b, 16))
        );
        await monarchHidDevice.sendReport(MONARCH_REPORT_ID, cells);
    }

    function monarchReportKey(reportId, data) {
        // A DataView can be a window onto a larger buffer, so honour its offset and
        // length. Reading the whole buffer would key off the wrong bytes and every
        // command would silently stop matching.
        return `${reportId}:${Array.from(new Uint8Array(data.buffer, data.byteOffset, data.byteLength)).join(',')}`;
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
