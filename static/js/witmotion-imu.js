(function () {
    // WitMotion IMU (BLE) — Web Bluetooth API
    // Compatible with WT901BLE, BWT901BLE, BWT901BLECL5.0 and similar.
    //
    // BLE protocol — two frame types:
    //
    // 0x53 (11 bytes, older firmware — angle-only):
    //   [0x55][0x53][Roll LE2][Pitch LE2][Yaw LE2][Temp LE2][checksum]
    //   Roll/Pitch/Yaw: int16 LE → / 32768.0 * 180.0 (degrees)
    //   Checksum: (sum bytes 0-9) & 0xFF
    //
    // 0x61 (20 bytes, BWT901BLECL5.0 and similar BLE5 devices — combo):
    //   [0x55][0x61][Ax LE2][Ay LE2][Az LE2][Wx LE2][Wy LE2][Wz LE2][Roll LE2][Pitch LE2][Yaw LE2]
    //   Roll/Pitch/Yaw at byte offsets 14/16/18; no trailing checksum byte.
    //
    // Orientation math ported from server.py:
    //   R = Rz(yaw) @ Ry(pitch) @ Rx(roll)
    //   view = face whose (R @ normal) best aligns with world-up [0,0,1]

    // Primary service/characteristic UUIDs (FFE5 family).
    // Fallback: FFE0 service with FFE1 characteristic (some older firmwares).
    const CANDIDATE_SERVICES = [
        { service: '0000ffe5-0000-1000-8000-00805f9a34fb',
          notify:  '0000ffe4-0000-1000-8000-00805f9a34fb' },
        { service: '0000ffe0-0000-1000-8000-00805f9a34fb',
          notify:  '0000ffe1-0000-1000-8000-00805f9a34fb' },
    ];

    // Face normals and names (matches Python _FACE_NORMALS / _FACE_NAMES).
    const FACE_NORMALS = [
        [ 1,  0,  0],   // x+
        [-1,  0,  0],   // x-
        [ 0,  1,  0],   // y+
        [ 0, -1,  0],   // y-
        [ 0,  0,  1],   // z+
        [ 0,  0, -1],   // z-
    ];
    const FACE_NAMES = ['x+', 'x-', 'y+', 'y-', 'z+', 'z-'];
    const WORLD_UP   = [0, 0, 1];
    const d = new Date();
    var first_angle = [];

    // ---- Math helpers -------------------------------------------------------

    function matMul3(A, B) {
        const C = [[0,0,0],[0,0,0],[0,0,0]];
        for (let i = 0; i < 3; i++)
            for (let j = 0; j < 3; j++)
                for (let k = 0; k < 3; k++)
                    C[i][j] += A[i][k] * B[k][j];
        return C;
    }

    function matVec3(M, v) {
        return [
            M[0][0]*v[0] + M[0][1]*v[1] + M[0][2]*v[2],
            M[1][0]*v[0] + M[1][1]*v[1] + M[1][2]*v[2],
            M[2][0]*v[0] + M[2][1]*v[1] + M[2][2]*v[2],
        ];
    }

    function dot3(a, b) {
        return a[0]*b[0] + a[1]*b[1] + a[2]*b[2];
    }

    function eulerToRotationMatrix(rollDeg, pitchDeg, yawDeg) {
        const r = rollDeg  * Math.PI / 180;
        const p = pitchDeg * Math.PI / 180;
        const y = yawDeg   * Math.PI / 180;

        const cr = Math.cos(r), sr = Math.sin(r);
        const cp = Math.cos(p), sp = Math.sin(p);
        const cy = Math.cos(y), sy = Math.sin(y);

        const Rx = [[1, 0,   0 ], [0,  cr, -sr], [0, sr, cr]];
        const Ry = [[cp, 0, sp ], [0,   1,   0 ], [-sp, 0, cp]];
        const Rz = [[cy, -sy, 0], [sy,  cy,  0 ], [0,  0,  1]];

        // R = Rz @ Ry @ Rx  (same as Python: Rz @ Ry @ Rx)
        return matMul3(matMul3(Rz, Ry), Rx);
    }

    // Returns the face name (e.g. "x+") whose normal, rotated into the world
    // frame, most aligns with the world-up direction. Direct port of Python
    // _orientation_to_view().
    function orientationToView(rollDeg, pitchDeg, yawDeg) {
        const R = eulerToRotationMatrix(rollDeg, pitchDeg, yawDeg);
        let bestIdx = 0;
        let bestDot = -Infinity;
        for (let i = 0; i < FACE_NORMALS.length; i++) {
            const worldNormal = matVec3(R, FACE_NORMALS[i]);
            const d = dot3(worldNormal, WORLD_UP);
            if (d > bestDot) { bestDot = d; bestIdx = i; }
        }
        return FACE_NAMES[bestIdx];
    }

    // ---- BLE packet parsing -------------------------------------------------

    // Scan a DataView for a WitMotion angle frame and return { roll, pitch, yaw }.
    //
    // Two frame types are supported:
    //   0x53 — 11-byte angle-only frame (older firmware):
    //     [0x55][0x53][roll LE2][pitch LE2][yaw LE2][temp LE2][checksum]
    //   0x61 — 20-byte combo frame (BWT901BLECL5.0 and similar newer BLE5 devices):
    //     [0x55][0x61][ax LE2][ay LE2][az LE2][wx LE2][wy LE2][wz LE2][roll LE2][pitch LE2][yaw LE2]
    //     (no checksum byte)
    //
    // Returns null if no valid frame is found.
    function parseAngleFrame(dataView) {
        const len = dataView.byteLength;
        for (let i = 0; i + 1 < len; i++) {
            if (dataView.getUint8(i) !== 0x55) continue;
            const frameType = dataView.getUint8(i + 1);

            if (frameType === 0x53 && i + 10 < len) {
                // Verify checksum: sum of bytes 0-9, low byte.
                let sum = 0;
                for (let k = 0; k < 10; k++) sum += dataView.getUint8(i + k);
                if ((sum & 0xFF) !== dataView.getUint8(i + 10)) continue;

                    let roll =  dataView.getInt16(i + 2, true) / 32768.0 * 180.0;
                    let pitch = dataView.getInt16(i + 4, true) / 32768.0 * 180.0;
                    let yaw =   dataView.getInt16(i + 6, true) / 32768.0 * 180.0;
            }

            if (frameType === 0x61 && i + 19 < len) {
                    let roll =  dataView.getInt16(i + 14, true) / 32768.0 * 180.0;
                    let pitch = dataView.getInt16(i + 16, true) / 32768.0 * 180.0;
                    let yaw =   dataView.getInt16(i + 18, true) / 32768.0 * 180.0;
            }
            if (first_angle.length() > 0){
                roll -= first_angle[0]
                pitch -= first_angle[1]
                yaw -= first_angle[2]
            }
            if (first_angle.length() == 0 && d.getTime() - start > 5000){
                first_angle = [roll, pitch, yaw];
                console.log("first_angle", first_angle);
            }
            return {roll, pitch, yaw};
        }
        return null;
    }

    // ---- BLE connection state -----------------------------------------------

    let bleDevice     = null;
    let gattServer    = null;
    let notifyChar    = null;
    let lastView      = null;
    let start_time = d.getTime();

    const connectBtn    = document.getElementById('witmotion-connect-btn');
    const disconnectBtn = document.getElementById('witmotion-disconnect-btn');
    const statusEl      = document.getElementById('witmotion-status');
    const viewValueEl   = document.getElementById('witmotion-view-value');
    const anglesEl      = document.getElementById('witmotion-angles');

    function setStatus(msg) {
        if (statusEl) statusEl.textContent = msg;
    }

    if (!('bluetooth' in navigator)) {
        setStatus('Web Bluetooth API not supported — use Chrome with the Experimental Web Platform flag, or Chrome on Android/macOS.');
        if (connectBtn) connectBtn.disabled = true;
        return;
    }

    connectBtn.addEventListener('click', async () => {
        try {
            connectBtn.disabled = true;
            setStatus('Opening BLE device picker…');

            // Request device — accept any WitMotion family name.
            bleDevice = await navigator.bluetooth.requestDevice({
                filters: [
                    { namePrefix: 'WT9' },
                    { namePrefix: 'BWT' },
                    { namePrefix: 'WITMOTION' },
                ],
                optionalServices: CANDIDATE_SERVICES.map(c => c.service),
            });

            bleDevice.addEventListener('gattserverdisconnected', onGattDisconnected);
            await connectGatt();

        } catch (err) {
            if (err.name !== 'NotFoundError') setStatus('Error: ' + err.message);
            else setStatus('No device selected.');
            connectBtn.disabled = false;
        }
    });

    disconnectBtn.addEventListener('click', () => {
        if (gattServer && gattServer.connected) gattServer.disconnect();
        cleanupState('Not connected.');
    });

    function onGattDisconnected() {
        cleanupState('Disconnected — tap Connect to reconnect.');
    }

    function cleanupState(reason) {
        notifyChar   = null;
        gattServer   = null;
        lastView     = null;
        disconnectBtn.disabled = true;
        connectBtn.disabled   = false;
        if (viewValueEl) viewValueEl.textContent = '--';
        if (anglesEl)    anglesEl.textContent    = '--';
        setStatus(reason || 'Not connected.');
        if (typeof announce === 'function') announce('WitMotion IMU disconnected.');
    }

    async function connectGatt() {
        setStatus('Connecting to GATT server…');
        gattServer = await bleDevice.gatt.connect();
        setStatus('Discovering services…');

        let service  = null;
        let charUuid = null;

        for (const candidate of CANDIDATE_SERVICES) {
            try {
                service  = await gattServer.getPrimaryService(candidate.service);
                charUuid = candidate.notify;
                break;
            } catch (_) {
                // Try next candidate.
            }
        }

        if (!service) {
            cleanupState('No supported service found on device.');
            return;
        }

        notifyChar = await service.getCharacteristic(charUuid);
        await notifyChar.startNotifications();
        notifyChar.addEventListener('characteristicvaluechanged', onCharacteristicChanged);

        setStatus('Connected — receiving orientation…');
        disconnectBtn.disabled = false;
        if (typeof announce === 'function') announce('WitMotion IMU connected.');
    }

    function onCharacteristicChanged(event) {
        const result = parseAngleFrame(event.target.value);
        console.log(result);
        if (!result) return;

        const { roll, pitch, yaw } = result;
        const view = orientationToView(roll, pitch, yaw);

        if (anglesEl) {
            anglesEl.textContent =
                `R ${roll.toFixed(1)}° P ${pitch.toFixed(1)}° Y ${yaw.toFixed(1)}°`;
        }

        if (view !== lastView) {
            lastView = view;
            if (viewValueEl) viewValueEl.textContent = view;
            if (typeof updateView === 'function') {
                window.pendingInputSource = 'witmotion';
                updateView(view);
            }
        }
    }
})();
