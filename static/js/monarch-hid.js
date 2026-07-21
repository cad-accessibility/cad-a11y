(function () {
    const MONARCH_VENDOR_ID = 0x1C71;
    const MONARCH_PRODUCT_ID = 0xD110;
    const MONARCH_REPORT_ID = 0x21;

    let monarchHidDevice = null;

    const connectBtn = document.getElementById('monarch-hid-connect-btn');
    const disconnectBtn = document.getElementById('monarch-hid-disconnect-btn');
    const statusEl = document.getElementById('monarch-hid-status');

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
            // Send current model state immediately so the display shows the model on connect.
            if (typeof window.sendStateToServer === 'function') window.sendStateToServer();

            monarchHidDevice.addEventListener('inputreport', (e) => {
                console.log('[Monarch HID] Input report:', e.reportId, e.data);
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

    window._monarchHidOnRender = function (monarchCellsHex) {
        sendCellsToMonarch(monarchCellsHex).catch(err => {
            console.warn('[Monarch HID] Send failed:', err);
        });
    };
})();
