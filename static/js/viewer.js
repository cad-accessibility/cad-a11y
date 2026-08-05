// Configuration
const SERVER_URL = window.location.origin;
const UPLOAD_SESSION_STORAGE_KEY = 'cadA11yUploadSessionId';

function getUploadSessionId() {
    try {
        let sessionId = window.sessionStorage.getItem(UPLOAD_SESSION_STORAGE_KEY);
        if (!sessionId) {
            sessionId = `tab-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
            window.sessionStorage.setItem(UPLOAD_SESSION_STORAGE_KEY, sessionId);
        }
        return sessionId;
    } catch (_) {
        // If sessionStorage is unavailable, still provide a best-effort volatile id.
        return `tab-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    }
}

// Drag-to-resize columns
(function() {
    const divider = document.getElementById('col-divider');
    const leftCol = document.getElementById('left-col');
    const layout  = divider && divider.parentElement;
    if (!divider || !leftCol || !layout) return;

    let dragging = false;
    let startX, startWidth;

    divider.addEventListener('mousedown', function(e) {
        dragging = true;
        startX = e.clientX;
        startWidth = leftCol.getBoundingClientRect().width;
        divider.classList.add('dragging');
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
        e.preventDefault();
    });

    document.addEventListener('mousemove', function(e) {
        if (!dragging) return;
        const delta = e.clientX - startX;
        const layoutWidth = layout.getBoundingClientRect().width;
        const newWidth = Math.min(Math.max(startWidth + delta, 200), layoutWidth - 200);
        leftCol.style.width = newWidth + 'px';
        leftCol.style.flex = '0 0 auto';
    });

    document.addEventListener('mouseup', function() {
        if (!dragging) return;
        dragging = false;
        divider.classList.remove('dragging');
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
    });

    // Keyboard resize support (arrow keys on the divider)
    divider.addEventListener('keydown', function(e) {
        console.debug("keydown seen:", e.key, e.code, e.target.tagName);
        const step = e.shiftKey ? 50 : 10;
        const layoutWidth = layout.getBoundingClientRect().width;
        const currentWidth = leftCol.getBoundingClientRect().width;
        if (e.key === 'ArrowLeft') {
            e.preventDefault();
            leftCol.style.width = Math.max(currentWidth - step, 200) + 'px';
            leftCol.style.flex = '0 0 auto';
        } else if (e.key === 'ArrowRight') {
            e.preventDefault();
            leftCol.style.width = Math.min(currentWidth + step, layoutWidth - 200) + 'px';
            leftCol.style.flex = '0 0 auto';
        }
    });
})();

// AbortController for in-flight render requests — cancels stale renders on rapid state changes
let renderAbortController = null;
let previewAbortController = null;
let previewRequestSequence = 0;
let previewRequestTimer = null;

// A render the client walks away from still costs the server a full render:
// aborting the fetch does not cancel work already queued behind the server's
// render lock. Holding a key therefore queued one whole render per keypress and
// the display lagged by the entire backlog. Keep at most one request in flight
// plus one follow-up, so a burst collapses to the first frame and the settled
// one. The follow-up reads the live state when it fires, so it sends the newest
// values rather than a stale snapshot.
let renderRequestInFlight = false;
let renderResendPending = false;

function scheduleHighFidelityPreview(state) {
    if (previewRequestTimer) {
        clearTimeout(previewRequestTimer);
    }
    const stateSnapshot = { ...state };
    previewRequestTimer = setTimeout(() => {
        previewRequestTimer = null;
        requestHighFidelityPreview(stateSnapshot);
    }, 250);
}

function requestHighFidelityPreview(state) {
    if (previewAbortController) {
        previewAbortController.abort();
    }
    previewAbortController = new AbortController();
    previewRequestSequence += 1;
    const requestId = previewRequestSequence;

    fetch(`${SERVER_URL}/render/preview`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(state),
        mode: 'cors',
        signal: previewAbortController.signal,
    })
    .then(res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
    })
    .then(data => {
        if (requestId !== previewRequestSequence) {
            return;
        }
        updateHighFidelityPreview(data);
    })
    .catch(error => {
        if (error.name === 'AbortError') return;
        console.warn('Preview request failed:', error.message);
    });
}

const cameraCenterByViewOrientation = new Map();
let currentWorldCameraCenter = null;

function getCameraCenterStateKey(viewToken, orientationPayload) {
    const normalizedView = String(viewToken || '').toLowerCase();
    const forward = Array.isArray(orientationPayload?.forward)
        ? orientationPayload.forward.map((value) => Number(value)).join(',')
        : '';
    const up = Array.isArray(orientationPayload?.up)
        ? orientationPayload.up.map((value) => Number(value)).join(',')
        : '';
    return `${normalizedView}|f:${forward}|u:${up}`;
}

function getCurrentCameraCenter(viewToken, orientationPayload) {
    const key = getCameraCenterStateKey(viewToken, orientationPayload);
    const value = cameraCenterByViewOrientation.get(key);
    return Array.isArray(value) && value.length === 2 ? [...value] : null;
}

function clearCameraCenterState() {
    cameraCenterByViewOrientation.clear();
    currentWorldCameraCenter = null;
}

function syncCameraCenterFromResponse(responseData, requestState) {
    // This window's own view position, remembered here and sent back on the next
    // render. The server does not keep it: one renderer is shared by every window
    // looking at a model, so a centre held there meant one person's pan moved
    // everybody else's view.
    //
    // The server used to be expected to return this inside `debug`, and never
    // did, so this function returned early every single time and the centre
    // stayed null forever. It is a real field now; `debug` is read as a fallback
    // for one release in case an older server is answering.
    const body = responseData && typeof responseData === 'object' ? responseData : null;
    const debug = body && typeof body.debug === 'object' ? body.debug : null;
    const reported = (body && Array.isArray(body.camera_center)) ? body.camera_center
        : (debug && Array.isArray(debug.camera_center)) ? debug.camera_center
        : null;
    if (!reported || reported.length !== 2) {
        return;
    }

    const centerX = Number(reported[0]);
    const centerY = Number(reported[1]);
    if (!Number.isFinite(centerX) || !Number.isFinite(centerY)) {
        return;
    }

    const answeredView = (debug && typeof debug.view === 'string' && debug.view.trim())
        ? debug.view
        : requestState.view;
    const answeredOrientation = (debug && debug.orientation && typeof debug.orientation === 'object')
        ? debug.orientation
        : requestState.orientation;
    const key = getCameraCenterStateKey(answeredView, answeredOrientation);
    cameraCenterByViewOrientation.set(key, [centerX, centerY]);
    if (sbPanCenter) {
        sbPanCenter.textContent = formatCenter2([centerX, centerY]);
    }

    const reportedWorld = (body && Array.isArray(body.world_camera_center)) ? body.world_camera_center
        : (debug && Array.isArray(debug.world_camera_center)) ? debug.world_camera_center
        : null;
    if (reportedWorld && reportedWorld.length === 3) {
        const worldCenter = reportedWorld.map((value) => Number(value));
        if (worldCenter.every((value) => Number.isFinite(value))) {
            currentWorldCameraCenter = [...worldCenter];
        }
    }
}

// Function to send current state to the server
async function sendStateToServer() {
    try {
        if (previewRequestTimer) {
            clearTimeout(previewRequestTimer);
            previewRequestTimer = null;
        }

        // When the polling loop has already confirmed the server is down,
        // skip active render requests until we detect a reconnection.
        if (serverConnected === false) {
            return;
        }

        // Coalesce rather than stack up work the server cannot skip.
        if (renderRequestInFlight) {
            renderResendPending = true;
            return;
        }

        // Cancel any in-flight render request so stale responses don't overwrite newer state
        if (renderAbortController) {
            renderAbortController.abort();
        }
        renderAbortController = new AbortController();

        const requestedGraphView = sliceGraphLocked ? sliceGraphAnchorView : currentView;
        const requestedGraphDepth = sliceGraphLocked ? sliceGraphAnchorDepth : currentSliceDepth;
        const renderPipelineParams = getRenderPipelineParams(currentRenderMode);
        const orientationPayload = getOrientationPayload();
        const moveCamera = currentMoveCamera;
        const cameraCenter = getCurrentCameraCenter(currentView, orientationPayload);
        const worldCameraCenter = currentWorldCameraCenter;

        const state = {
            view: currentView,
            orientation: orientationPayload,
            camera_center: cameraCenter,
            world_camera_center: worldCameraCenter,
            zoom: currentZoom,
            depth: currentSliceDepth,
            renderMode: renderPipelineParams.renderMode,
            projectionMode: renderPipelineParams.projectionMode,
            mode: getServerRepresentationMode(),
            move_camera_center: moveCamera,
            print_view: currentPrintView,
            model: currentModel,
            current_model: currentModel,
            compose_cursor: true, // for now always true, maybe later make it configurable
            cursor_col: currentCursorCol,
            cursor_row: currentCursorRow,
            cursor_state: whichCursor(),
            compose_scrollbar: composeScrollbar,
            compose_slicegraph: composeSliceGraph,
            show_view_info_box: showViewInfoBox,
            output_device: getEffectiveOutputDevice(),
            slicegraph_locked: sliceGraphLocked,
            slicegraph_view: requestedGraphView,
            slicegraph_depth: requestedGraphDepth,
            slicegraph_mode: sliceGraphMode,
            input_source: pendingInputSource,
            // The grid of the display actually receiving output, so the render,
            // the payload sent to it and both previews all describe one thing.
            target_pixel_width: activeTactileGrid().pixelWidth,
            target_pixel_height: activeTactileGrid().pixelHeight,
        };
        if (sbPanCmd) {
            sbPanCmd.textContent = String(moveCamera || 'none');
        }
        if (sbPanCenter && Array.isArray(cameraCenter) && cameraCenter.length === 2) {
            sbPanCenter.textContent = formatCenter2(cameraCenter);
        }
        const activeModelLoadTask = modelLoadAnnouncement
            ? { ...modelLoadAnnouncement }
            : null;
        if (activeModelLoadTask) {
            announce(`${activeModelLoadTask.label}: generating render.`);
        }
        pendingInputSource = 'keyboard'; // reset to default after consuming

        // Send to server and process response
        renderRequestInFlight = true;
        fetch(`${SERVER_URL}/render`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(state),
            mode: 'cors',
            signal: renderAbortController.signal,
        })
        .then(res => {
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            return res.json();
        })
        .then(data => {
            // Render success is sufficient proof the server is reachable — clear
            // any down state immediately rather than waiting for the next poll.
            if (serverConnected === false) {
                serverConnected = true;
                announce('Server reconnected.');
            }
            serverConnected = true;

            // Update bounding box if available in response
            if (data.bbox) {
                updateBoundingBox(data.bbox);
            }
            if (data.model_list) {
                updateModelList(data.model_list);
            }
            syncCameraCenterFromResponse(data, state);
            // Update tactile display preview
            if (data.image_base64) {
                lastRenderedGrid = gridForSize(state.target_pixel_width, state.target_pixel_height);
                updateTactilePreview(data.image_base64, data.image_shape);
                if (isActiveModelLoadTask(activeModelLoadTask)) {
                    // One announcement per event: two calls in the same tick would
                    // land in the two swap slots and the first would be blanked
                    // before AT reads it.
                    announce(`${activeModelLoadTask.label} loaded. Tactile preview ready.`);
                    clearModelLoadTask(activeModelLoadTask);
                }
            }
            renderPipelineDebug(data.debug_pipeline, data.debug);
            const shouldRequestPreview =
                state.move_camera_center === 'none' &&
                !(state.mode === 'slice-graph' && state.slicegraph_mode === 'column-count');
            if (shouldRequestPreview) {
                scheduleHighFidelityPreview(state);
            }

            // Trigger DotPad web send if connected
            if (typeof window._dotpadOnRender === 'function') {
                window._dotpadOnRender(state);
            }
            // Trigger Monarch browser Web HID send if connected
            if (typeof window._monarchHidOnRender === 'function' && data.monarch_cells_hex) {
                window._monarchHidOnRender(data.monarch_cells_hex);
            }
        })
        .catch(error => {
            if (error.name === 'AbortError') return; // Superseded by a newer request — ignore
            // A single render failure (busy server, transient network hiccup, scroll-time
            // deprioritisation) does not mean the server is down. Announcing a disconnect
            // here causes the spurious unavailable→reconnected cycle observed during normal
            // use. Connection state is managed exclusively by the health poll below so that
            // only sustained, confirmed outages interrupt the user.
            console.warn('Render request failed:', error.message);
            if (isActiveModelLoadTask(activeModelLoadTask)) {
                announceAlert(`Processing failed for ${activeModelLoadTask.label}.`);
                clearModelLoadTask(activeModelLoadTask);
            }
        })
        .finally(() => {
            renderRequestInFlight = false;
            if (renderResendPending) {
                renderResendPending = false;
                sendStateToServer();
            }
        });

    } catch (error) {
        renderRequestInFlight = false;
        console.warn('Error sending state:', error);
    }
}

// State management
let currentSliceDepth = 50;
// The slice plane's position along each model axis, as a 0-1 fraction from
// that axis's minimum end. Kept independent of viewing direction so turning
// the model doesn't relocate the physical plane -- rotating only changes
// which of the three is currently the active (visible) one, and from which
// side currentSliceDepth reads it. See activeSliceAxis/displayDepthFromPlanes.
let slicePlanes = { x: 0.5, y: 0.5, z: 0.5 };
let currentView = 'x+';
let currentZoom = 0.0;
let currentRenderMode = 'filled';
let currentRepresentationMode = 'single';
let currentMoveCamera = "none";
let currentPrintView = false;
// The output-device radio the user picked: 'monarch', 'dotpad', or 'auto'.
// Kept separate from whether a Monarch is actually connected over Web HID
// (monarchHidConnected) so that selecting a radio can never turn off a live
// Monarch feed — see getEffectiveOutputDevice and issue #75.
let currentOutputDevice = 'monarch';
let monarchHidConnected = false;
// Single source of truth for render modes.
//   key        held in currentRenderMode and used as the radio `value`. Lowercase
//              throughout, so a case mismatch cannot silently unselect the group.
//   label      the only spelling the user ever sees or hears.
//   wire       sent to the server and stored in render_stats.render_mode.
//   projection paired with `wire` in the render request.
const renderModes = [
    { key: 'filled', label: 'Filled', wire: 'Filled', projection: 'orthographic' },
    { key: 'outline', label: 'Outline', wire: 'Outline', projection: 'silhouette' },
    { key: 'cut', label: 'Cut', wire: 'Cut', projection: 'orthographic' },
    { key: 'xray', label: 'X-Ray', wire: 'x-ray', projection: 'x-ray' },
];
// Single source of truth for view modes. Same shape as renderModes, plus:
//   sliceGraphMode  which slice-graph variant this mode selects, when it is one.
// `wire` collapses both slice-graph variants to the one mode name the server
// knows; the variant is a client-side concern.
const representationModes = [
    { key: 'single', label: 'Single (with scrollbar)', wire: 'single' },
    { key: 'side-by-side', label: 'Side-by-Side', wire: 'side-by-side' },
    { key: 'slice-graph-difference', label: 'Slice Graph (Difference)', wire: 'slice-graph', sliceGraphMode: 'difference' },
    { key: 'slice-graph-column-count', label: 'Slice Graph (Slice Area)', wire: 'slice-graph', sliceGraphMode: 'column-count' },
];
let currentModel = null;   // the model this window is showing, by name
let sessionOwnedModels = new Set(); // filenames (with extension) owned by the current cookie session
let builtinModelStems = null;       // stems from MODEL_DIR; null = not yet received, show all
let lastFullModelList = [];         // unfiltered server model_list for re-filtering on state change
let composeScrollbar = true;
let composeSliceGraph = false;
let showViewInfoBox = false;
let sliceGraphLocked = true;
let sliceGraphAnchorView = 'y-';
let sliceGraphAnchorDepth = 50;
let sliceGraphMode = 'difference';

// Cursor variables
let currentCursorCol = 2;
let currentCursorRow = 2;
const cursorStep = 1;
let cursorStates = ['none', 'crosshair', 'guidelines', 'horizontal-line', 'vertical-line'];
let currentCursorStateIndex = 0;


// Tracking variables
let serverConnected = null;       // null = unknown, true = up, false = confirmed down
let lastModelListSignature = '';  // prevents redundant dropdown rebuilds
let currentBBoxDimensionsText = '';
let lastAnnouncementMessage = '';
let lastAnnouncedParameterKey = null;
let pendingInputSource = 'keyboard'; // consumed once per sendStateToServer call
let modelLoadAnnouncement = null;
let modelLoadAnnouncementSeq = 0;

// Cursor position is in 2D display coordinates, not CAD/world coordinates.
// Mapping to CAD X/Y/Z depends on currentView and currentSliceDepth.
function moveCursor(dCol, dRow, stepSize = cursorStep) {
    // Simple movement: advance by the configured cursorStep (pixels).
    if (!Number.isFinite(dCol) || !Number.isFinite(dRow) || !Number.isFinite(stepSize)) {
        console.error('Invalid cursor movement values.');
        return;
    }
    else if (!Number.isInteger(dCol) || !Number.isInteger(dRow) || !Number.isInteger(stepSize)) {
        console.error('Cursor movement values must be integers.');
        return;
    }
    const activeGrid = activeTactileGrid();
    const displayWidth = activeGrid.pixelWidth;
    const displayHeight = activeGrid.pixelHeight;

    const usableWidth = composeScrollbar? Math.max(1, displayWidth - 2) : displayWidth;
    const usableHeight = composeScrollbar? Math.max(1, displayHeight - 2) : displayHeight;
    // dont let cursor go negative or beyond the display bounds (for 40x60 tactile display)
    const maxCol = usableWidth - 1;
    const maxRow = usableHeight - 1;

    const nextCol = currentCursorCol + dCol * stepSize;
    currentCursorCol = Math.min(Math.max(nextCol, 0), maxCol);
    const nextRow = currentCursorRow + dRow * stepSize;
    currentCursorRow = Math.min(Math.max(nextRow, 0), maxRow);

    pendingInputSource = 'dotpad';
    console.debug(`Display cursor: col ${currentCursorCol}, row ${currentCursorRow}`);
    announceAlert(`Column ${currentCursorCol}, row ${currentCursorRow}`);
    sendStateToServer();
}

function whichCursor() {
    return cursorStates[currentCursorStateIndex] || 'none';
}

function cycleCursorState() {
    currentCursorStateIndex = (currentCursorStateIndex + 1) % cursorStates.length;
    const newState = whichCursor();
    announceAlert(`${newState} cursor`);
    pendingInputSource = 'dotpad';
    sendStateToServer();
}
function renderModeByKey(modeKey) {
    return renderModes.find(mode => mode.key === modeKey) || null;
}

/** User-facing name for a render mode key. Never leak the key itself to a person. */
function renderModeLabel(modeKey = currentRenderMode) {
    const mode = renderModeByKey(modeKey);
    return mode ? mode.label : String(modeKey);
}

function representationModeByKey(modeKey) {
    return representationModes.find(mode => mode.key === modeKey) || null;
}

/** User-facing name for a view mode key. Never leak the key itself to a person. */
function representationModeLabel(modeKey = currentRepresentationMode) {
    const mode = representationModeByKey(modeKey);
    return mode ? mode.label : String(modeKey);
}

function isSliceGraphRepresentationMode(modeValue = currentRepresentationMode) {
    const mode = representationModeByKey(modeValue);
    return Boolean(mode) && mode.wire === 'slice-graph';
}

function getServerRepresentationMode(modeValue = currentRepresentationMode) {
    const mode = representationModeByKey(modeValue);
    return mode ? mode.wire : modeValue;
}

function beginModelLoadAnnouncement(modelLabel, source = 'selection') {
    const label = String(modelLabel || 'model').trim();
    modelLoadAnnouncementSeq += 1;
    modelLoadAnnouncement = {
        id: modelLoadAnnouncementSeq,
        label,
        source,
    };
    announce(`${label} processing started.`);
}

function isActiveModelLoadTask(task) {
    return Boolean(task && modelLoadAnnouncement && modelLoadAnnouncement.id === task.id);
}

function clearModelLoadTask(task) {
    if (isActiveModelLoadTask(task)) {
        modelLoadAnnouncement = null;
    }
}

function ratioToPercent(zoomValue) {
    const percent = Math.round(Number(zoomValue) * 100);
    return `${percent}%`;
}

const MIN_ZOOM = 0.0;
const MAX_ZOOM = Number.POSITIVE_INFINITY;
const ZOOM_STEP = 0.1;
const FINE_ZOOM_STEP = 0.01;

// The camera basis for each named view, in model coordinates. These mirror
// _get_view_basis in src/converter/single_view_stl.py exactly, so sending this
// basis for a named view renders the same picture as naming the view does.
//
// All three axes are tracked rather than two plus a cross product, because the
// six views are not consistently handed: y-, y+ and z- have right x up = -depth
// where z+, x- and x+ have +depth. Deriving `right` mirrors half of them.
const VIEW_BASIS = {
    'z+': { right: [1, 0, 0],  up: [0, 1, 0],  depth: [0, 0, 1] },   // top
    'y-': { right: [1, 0, 0],  up: [0, 0, 1],  depth: [0, 1, 0] },   // front
    'x-': { right: [0, 1, 0],  up: [0, 0, 1],  depth: [1, 0, 0] },   // left
    'x+': { right: [0, -1, 0], up: [0, 0, 1],  depth: [-1, 0, 0] },  // right
    'y+': { right: [-1, 0, 0], up: [0, 0, 1],  depth: [0, -1, 0] },  // back
    'z-': { right: [-1, 0, 0], up: [0, -1, 0], depth: [0, 0, -1] },  // bottom
};

// [1,0,0] -> "pos X", [-1,0,0] -> "neg X", [0,0,-1] -> "neg Z"
// Words, not a +/- glyph: a sign glued to a letter ("-X") can get misread by a
// screen reader (e.g. as "dash X") depending on context.
// Kept short (pos/neg, not positive/negative) since this gets spoken often.
function axisLabel(vec) {
    const names = ['X', 'Y', 'Z'];
    const i = vec.findIndex(v => v !== 0);
    return `${vec[i] > 0 ? 'pos' : 'neg'} ${names[i]}`;
}

// Plain-English description of a { right, up, depth } basis.
function describeBasis(basis) {
    return `${axisLabel(basis.depth)} toward you, Right: ${axisLabel(basis.right)}, Up: ${axisLabel(basis.up)}`;
}

let orientationRight = [...VIEW_BASIS['x+'].right];
let orientationUp = [...VIEW_BASIS['x+'].up];
let orientationDepth = [...VIEW_BASIS['x+'].depth];

function dotVec3(a, b) {
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

function normalizeAxisVector(v) {
    const rounded = [Math.round(v[0]), Math.round(v[1]), Math.round(v[2])];
    const mag = Math.abs(rounded[0]) + Math.abs(rounded[1]) + Math.abs(rounded[2]);
    if (mag === 0) return [1, 0, 0];
    if (mag === 1) return rounded;
    // Safety net for numerical drift; choose the dominant axis.
    const absVals = rounded.map(Math.abs);
    const maxIndex = absVals.indexOf(Math.max(...absVals));
    return [
        maxIndex === 0 ? Math.sign(rounded[0]) || 1 : 0,
        maxIndex === 1 ? Math.sign(rounded[1]) || 1 : 0,
        maxIndex === 2 ? Math.sign(rounded[2]) || 1 : 0,
    ];
}

function negateVec3(v) {
    return [-v[0], -v[1], -v[2]];
}

function orientationViewFromDepth(depthVector) {
    for (const [viewToken, basis] of Object.entries(VIEW_BASIS)) {
        if (dotVec3(basis.depth, depthVector) === 1) {
            return viewToken;
        }
    }
    return 'x+';
}

function setOrientationFromView(viewToken) {
    const basis = VIEW_BASIS[viewToken] || VIEW_BASIS['x+'];
    orientationRight = [...basis.right];
    orientationUp = [...basis.up];
    orientationDepth = [...basis.depth];
}

const RELATIVE_ROTATIONS = {
    rollCounterclockwise: { speech: 'roll counterclockwise' },
    rollClockwise:        { speech: 'roll clockwise' },
    pitchUp:              { speech: 'pitch up' },
    pitchDown:            { speech: 'pitch down' },
    yawLeft:              { speech: 'yaw left' },
    yawRight:             { speech: 'yaw right' },
};


function applyRelativeRotation(rotationName, emit = announceAlert) {
    const rotation = RELATIVE_ROTATIONS[rotationName];
    const right = orientationRight, up = orientationUp, depth = orientationDepth;
    switch (rotationName) {
        case 'rollClockwise':
            orientationRight = up;
            orientationUp = negateVec3(right);
            break;
        case 'rollCounterclockwise':
            orientationRight = negateVec3(up);
            orientationUp = right;
            break;
        case 'pitchUp':
            orientationUp = negateVec3(depth);
            orientationDepth = up;
            break;
        case 'pitchDown':
            orientationUp = depth;
            orientationDepth = negateVec3(up);
            break;
        case 'yawLeft':
            orientationRight = depth;
            orientationDepth = negateVec3(right);
            break;
        case 'yawRight':
            orientationRight = negateVec3(depth);
            orientationDepth = right;
            break;
        default:
            return;
    }

    // Pitch/yaw can switch which of the three persisted planes is now facing
    // the viewer; roll never does (it doesn't touch orientationDepth), so this
    // is a no-op there. Must run before updateView/sendStateToServer below so
    // the request they send already carries the re-derived depth.
    const depthChanged = syncSliceDepthFromPlanes();
    const depthMessage = depthChanged ? `depth ${currentSliceDepth}%` : "";
    const viewChanged = updateView(orientationViewFromDepth(orientationDepth), false,
                                   { syncOrientation: false });
    const currentBasis = { right: orientationRight, up: orientationUp, depth: orientationDepth };
    const orientationMessage = describeBasis(currentBasis);

    if (!viewChanged) {
        // A roll leaves the same face toward the reader, so updateView sees no
        // change and would not redraw.
        if (isSliceGraphRepresentationMode()) {
            autoRefreshSliceGraph({ updateAnchor: true });
        } else {
            sendStateToServer();
        }

    }

    const message = `${rotation.speech}, ${orientationMessage}. ${depthMessage}`;
    const messageShort = `${orientationMessage}. ${depthMessage}`;
    announceParameterValue(rotationName, message, messageShort, emit);
}

function getOrientationPayload() {
    return {
        scheme: 'basis-v1',
        forward: normalizeAxisVector(orientationDepth),
        up: normalizeAxisVector(orientationUp),
        right: normalizeAxisVector(orientationRight),
    };
}

// Which model axis the current view slices along, and from which side.
// sign > 0 means depth points toward that axis's positive end (0% is the
// negative end); sign < 0 means the reverse. This sign is what makes the same
// physical plane read as (100 - x)% after a 180-degree turn around that axis.
function activeSliceAxis() {
    const d = orientationDepth;
    if (d[0] !== 0) return { axis: 'x', sign: Math.sign(d[0]) };
    if (d[1] !== 0) return { axis: 'y', sign: Math.sign(d[1]) };
    return { axis: 'z', sign: Math.sign(d[2]) };
}

// Convert the persisted absolute plane position into the view-relative
// percentage the slider/announcements show ("X% from where you're looking").
function displayDepthFromPlanes() {
    const { axis, sign } = activeSliceAxis();
    const fraction = slicePlanes[axis];
    return Math.round((sign > 0 ? fraction : 1 - fraction) * 100);
}

// The inverse: fold a view-relative percentage back into the persisted
// absolute position of whichever axis is currently active.
function writeDisplayDepthToPlanes(depthPercent) {
    const { axis, sign } = activeSliceAxis();
    const fraction = depthPercent / 100;
    slicePlanes[axis] = sign > 0 ? fraction : 1 - fraction;
}

function resetSlicePlanes() {
    slicePlanes = { x: 0.5, y: 0.5, z: 0.5 };
}

// Re-derive currentSliceDepth from the persisted planes after the active axis
// may have changed (any pitch/yaw, or picking a different named view). Roll
// never changes the active axis, so this is a harmless no-op there. Does not
// write back to slicePlanes -- only updateSliceDepth (a user-initiated change)
// does that.
function syncSliceDepthFromPlanes() {
    const oldDepth = currentSliceDepth;
    currentSliceDepth = displayDepthFromPlanes();
    if (sliceSlider) {
        sliceSlider.value = currentSliceDepth;
    }
    if (slicePercentage) slicePercentage.textContent = currentSliceDepth;
    return oldDepth !== currentSliceDepth;
}

// DOM elements
const sliceSlider = document.getElementById('slice-depth-slider');
const slicePercentage = document.getElementById('slice-percentage');
const currentViewSpan = document.getElementById('current-view');
const currentSliceDepthInfo = document.getElementById('current-slice-depth-info');
const currentRenderModeInfo = document.getElementById('current-render-mode-info');
const currentZoomInfo = document.getElementById('current-zoom-info');
const currentBBoxDimensionsInfo = document.getElementById('current-bbox-dimensions-info');
const deeperBtn = document.getElementById('deeper-btn');
const shallowerBtn = document.getElementById('shallower-btn');
const zoomInput = document.getElementById('zoom-input');
const zoomLevelValue = document.getElementById('zoom-level-value');
const zoomOutBtn = document.getElementById('zoom-out-btn');
const zoomInBtn = document.getElementById('zoom-in-btn');
const sliceGraphLockCheckbox = document.getElementById('slice-graph-lock-checkbox');
const sliceGraphRefreshBtn = document.getElementById('slice-graph-refresh-btn');
const sliceGraphModeBtn = document.getElementById('slice-graph-mode-btn');
const resetPositionBtn = document.getElementById('reset-position-btn');
const sliceGraphLockStatus = document.getElementById('slice-graph-lock-status');
const showViewInfoBoxCheckbox = document.getElementById('show-view-info-box');
const exportSliceSvgBtn = document.getElementById('export-slice-svg-btn');
const highFidelityPreviewImg = document.getElementById('high-fidelity-preview-img');
const highFidelityPreviewMeta = document.getElementById('high-fidelity-preview-meta');
const debugPipelineToggleBtn = document.getElementById('debug-pipeline-toggle-btn');
const debugPipelineContent = document.getElementById('debug-pipeline-content');
const debugPipelineSummary = document.getElementById('debug-pipeline-summary');
const debugStageList = document.getElementById('debug-stage-list');
const DEBUG_PIPELINE_VISIBILITY_KEY = 'debugPipelineVisible';
const shortcutsDialog = document.getElementById('shortcuts-dialog');
const shortcutsCloseBtn = document.getElementById('shortcuts-close-btn');
const shortcutsHeading = document.getElementById('shortcuts-heading');
const mainContent = document.getElementById('main-content');

// New radio group references
const renderModeRadios = () => document.querySelectorAll('input[name="render-mode"]');
const viewModeRadios = () => document.querySelectorAll('input[name="view-mode"]');
const outputDeviceRadios = () => document.querySelectorAll('input[name="output-device"]');

function getRenderPipelineParams(uiRenderMode) {
    // Single-mode UI mapping: projection is no longer a separate user control.
    const mode = renderModeByKey(uiRenderMode) || renderModes[0];
    return { renderMode: mode.wire, projectionMode: mode.projection };
}

// Status bar elements
const sbView = document.getElementById('sb-view');
const sbDepth = document.getElementById('sb-depth');
const sbRenderMode = document.getElementById('sb-render-mode');
const sbZoom = document.getElementById('sb-zoom');
const sbViewMode = document.getElementById('sb-view-mode');
const sbModel = document.getElementById('sb-model');
const sbDotPad = document.getElementById('sb-dotpad');
const sbPanCmd = document.getElementById('sb-pan-cmd');
const sbPanCenter = document.getElementById('sb-pan-center');

// Ensure a high-fidelity preview overlay exists for drawing a scaled DotPad cursor


function formatCenter2(value) {
    if (!Array.isArray(value) || value.length !== 2) {
        return '--';
    }
    const x = Number(value[0]);
    const y = Number(value[1]);
    if (!Number.isFinite(x) || !Number.isFinite(y)) {
        return '--';
    }
    return `${x.toFixed(3)},${y.toFixed(3)}`;
}

// Message window elements: two persistent, always-visible native ARIA
// live regions (see the markup for role="alert" vs aria-live="polite"). Assertive
// for anything the user just did or an error to act on; polite for background/
// system events.
const announcementWindow = document.getElementById('announcement-window');
const announcementWindowPolite = document.getElementById('announcement-window-polite');

/** Update the top status bar to reflect current state. */
function refreshStatusBar() {
    if (sbView) sbView.textContent = currentView;
    if (sbDepth) sbDepth.textContent = currentSliceDepth + '%';
    if (sbRenderMode) sbRenderMode.textContent = renderModeLabel();
    if (sbZoom) sbZoom.textContent = Number(currentZoom).toFixed(1);
    if (sbViewMode) sbViewMode.textContent = representationModeLabel();
}

/** Write the message into the message window for the given politeness tier. */
function updateMessageWindow(message, politeness = 'polite') {
    const field = politeness === 'assertive' ? announcementWindow : announcementWindowPolite;
    if (field) field.textContent = message;
}

// A depth value as a single spoken token: the ends get words, the middle a percent.
function depthToken(pct) {
    if (pct === 0) return 'surface';
    if (pct === 100) return 'full depth';
    return `${pct}%`;
}

function clampDepth(value) {
    const n = Math.max(0, Math.min(100, Number(value)));
    return Number.isFinite(n) ? Math.round(n) : null;
}

// Every depth/zoom change is announced (or logged) immediately, with no debounce.
// For the assertive keyboard/hardware channel (announceAlert) each new announcement
// already interrupts and replaces whatever's currently being spoken

function announceDepthValue(depthValue, previousDepth = null, emit = announceAlert) {
    const to = clampDepth(depthValue);
    if (to === null) return;
    announceParameterValue("depth", `Depth ${to}%`, `${to}%`, emit);
}

function announceZoomValue(zoomValue, previousZoom = null, emit = announceAlert) {
    const to = Number(zoomValue);
    if (!Number.isFinite(to)) return;
    // ratioToPercent already appends "%" — don't add a second one.
    announceParameterValue("zoom", `Zoom ${ratioToPercent(to)}`, `${ratioToPercent(to)}`, emit);
}

// Speak firstText the first time this parameter is announced, then repeatText
// on an immediately following announcement of the SAME parameter. Any other
// announcement in between resets the run, so the fuller phrasing returns once the
// context is no longer obvious. Only zoom and depth use this; everything else
// calls announceAlert()/announce() directly.
function announceParameterValue(parameterKey, firstText, repeatText, emit = announceAlert) {
    const normalizedKey = String(parameterKey || '').trim().toLowerCase();
    const useFirst = normalizedKey === '' || normalizedKey !== lastAnnouncedParameterKey;
    const message = useFirst ? String(firstText) : String(repeatText);

    // emit() clears lastAnnouncedParameterKey; re-establish this parameter's key
    // afterwards so a run of the SAME parameter still drops to the arrow.
    emit(message);
    if (normalizedKey) {
        lastAnnouncedParameterKey = normalizedKey;
    }
}

function refreshViewInfoSummary() {
    if (currentSliceDepthInfo) {
        currentSliceDepthInfo.textContent = `${currentSliceDepth}%`;
    }
    if (currentRenderModeInfo) {
        currentRenderModeInfo.textContent = renderModeLabel();
    }
    if (currentZoomInfo) {
        currentZoomInfo.textContent = Number(currentZoom).toFixed(1);
    }
    if (currentBBoxDimensionsInfo) {
        currentBBoxDimensionsInfo.textContent = currentBBoxDimensionsText;
    }
    refreshStatusBar();
}

function getStatusBarAnnouncement() {
    const readText = (element, fallback = '--') => {
        const text = element && typeof element.textContent === 'string'
            ? element.textContent.trim()
            : '';
        return text || fallback;
    };

    return [
        `View: ${readText(sbView, currentView)}`,
        `Depth: ${readText(sbDepth, `${currentSliceDepth}%`)}`,
        `Render: ${readText(sbRenderMode, renderModeLabel())}`,
        `Zoom: ${readText(sbZoom, Number(currentZoom).toFixed(1))}`,
        `Layout: ${readText(sbViewMode, representationModeLabel())}`,
        `Model: ${readText(sbModel)}`,
        `DotPad: ${readText(sbDotPad)}`,
    ].join('. ');
}

// Update button labels with current state information
function updateButtonLabels() {
    const depthText = `${currentSliceDepth}%`;
    deeperBtn.textContent = `Deeper 10%`;
    shallowerBtn.textContent = `Shallower 10%`;
}

function updateSliceGraphLockUI() {
    const isSliceGraphMode = isSliceGraphRepresentationMode();
    sliceGraphRefreshBtn.disabled = !isSliceGraphMode;
    if (sliceGraphLockCheckbox) {
        sliceGraphLockCheckbox.checked = sliceGraphLocked;
    }
    if (sliceGraphLocked) {
        if (isSliceGraphMode) {
            sliceGraphLockStatus.textContent = `Freeze graph`;
        } else {
            sliceGraphLockStatus.textContent = 'Switch to Slice Graph mode to refresh.';
        }
    } else {
        if (isSliceGraphMode) {
            sliceGraphLockStatus.textContent = `view ${sliceGraphAnchorView}, depth ${sliceGraphAnchorDepth}%`;
        } else {
            sliceGraphLockStatus.textContent = 'Switch to Slice Graph mode to use refresh.';
        }
    }
}

function updateSliceGraphModeUI() {
    if (!sliceGraphModeBtn) {
        return;
    }
    const isColumnCountMode = sliceGraphMode === 'column-count';
    sliceGraphModeBtn.textContent = isColumnCountMode
        ? 'Graph Mode: Slice Area'
        : 'Graph Mode: Difference';
}

function toggleSliceGraphMode() {
    sliceGraphMode = sliceGraphMode === 'difference' ? 'column-count' : 'difference';
    updateSliceGraphModeUI();
    pendingInputSource = 'ui';
    sendStateToServer();
}

function captureSliceGraphAnchor(shouldAnnounce = true) {
    sliceGraphAnchorView = currentView;
    sliceGraphAnchorDepth = currentSliceDepth;
    updateSliceGraphLockUI();
}

function autoRefreshSliceGraph(options = {}) {
    const { updateAnchor = false } = options;
    if (!isSliceGraphRepresentationMode()) {
        return;
    }

    // In locked mode, keep the graph centered on the current exploration point.
    if (updateAnchor && sliceGraphLocked) {
        captureSliceGraphAnchor(false);
    }

    sendStateToServer();
}

function setSliceGraphLocked(locked) {
    sliceGraphLocked = locked;
    if (sliceGraphLocked) {
        // When turning lock back on, freeze at the current exploration point.
        captureSliceGraphAnchor(false);
    }
    updateSliceGraphLockUI();
    sendStateToServer();
}

function toggleSliceGraphLock() {
    setSliceGraphLocked(!sliceGraphLocked);
}

function print_view(){
    currentPrintView = true;
    sendStateToServer();
    currentPrintView = !currentPrintView;
}

function formatDebugValue(value) {
    if (value === undefined || value === null) {
        return 'null';
    }
    if (typeof value === 'number') {
        if (Number.isInteger(value)) {
            return String(value);
        }
        return value.toFixed(6).replace(/0+$/, '').replace(/\.$/, '');
    }
    if (typeof value === 'string' || typeof value === 'boolean') {
        return String(value);
    }
    try {
        return JSON.stringify(value, null, 2);
    } catch (_) {
        return String(value);
    }
}

function setDebugPipelineVisible(isVisible) {
    if (!debugPipelineContent || !debugPipelineToggleBtn) {
        return;
    }
    debugPipelineContent.hidden = !isVisible;
    debugPipelineToggleBtn.textContent = isVisible ? 'Hide Debug Pipeline' : 'Show Debug Pipeline';
    try {
        window.localStorage.setItem(DEBUG_PIPELINE_VISIBILITY_KEY, isVisible ? '1' : '0');
    } catch (_) {
        // Ignore localStorage failures (e.g., privacy mode).
    }
}

function toggleDebugPipelineVisibility() {
    if (!debugPipelineContent) {
        return;
    }
    setDebugPipelineVisible(debugPipelineContent.hidden);
}

function initializeDebugPipelineVisibility() {
    // Hidden by default: the panel is a developer diagnostic, and while visible it
    // rebuilt a JSON dump on every render. Only a saved, explicit choice to show it
    // ('1') brings it back; a first visit, a prior hide, or no storage stays hidden.
    let isVisible = false;
    try {
        isVisible = window.localStorage.getItem(DEBUG_PIPELINE_VISIBILITY_KEY) === '1';
    } catch (_) {
        // Keep default hidden if persistence is unavailable.
    }
    setDebugPipelineVisible(isVisible);
}

// Keyboard shortcuts dialog (#146). Native <dialog> + showModal() supplies focus
// containment, background inertness, and Escape-to-close for free; the one thing
// that isn't automatic is returning focus to whatever triggered the dialog once
// it closes, which is what shortcutsDialogTrigger is for. See the ARIA APG
// pattern: https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/examples/dialog/
let shortcutsDialogTrigger = null;

function openShortcutsDialog() {
    if (!shortcutsDialog || !shortcutsDialog.showModal || shortcutsDialog.open) {
        return;
    }
    shortcutsDialogTrigger = document.activeElement;
    // Backs up the native modal inertness for older assistive tech, same as the
    // session consent dialog.
    if (mainContent) mainContent.setAttribute('aria-hidden', 'true');
    shortcutsDialog.showModal();
    // showModal() defaults to focusing the first focusable element, which here is
    // the Close button at the very end of the content — reading forward from there
    // reads nothing. Per the ARIA APG dialog pattern, a read-only dialog like this
    // one should instead land on a static element at the top (the heading, made
    // focusable via tabindex="-1"), so reading forward covers the whole dialog.
    if (shortcutsHeading) shortcutsHeading.focus({ preventScroll: true });
}

function closeShortcutsDialog() {
    if (!shortcutsDialog || !shortcutsDialog.open) {
        return;
    }
    shortcutsDialog.close();
}

function restoreAfterShortcutsDialogClose() {
    if (mainContent) mainContent.removeAttribute('aria-hidden');
    // Return focus to whatever opened the dialog rather than stranding it at the
    // top of the document (or on an element that's since been removed).
    if (shortcutsDialogTrigger && document.contains(shortcutsDialogTrigger)) {
        shortcutsDialogTrigger.focus();
    }
    shortcutsDialogTrigger = null;
}

if (shortcutsDialog) {
    // Cleanup after the dialog has actually closed. (Escape triggers `cancel`, whose
    // default action closes the dialog and then fires `close`.)
    shortcutsDialog.addEventListener('close', restoreAfterShortcutsDialogClose);
}
if (shortcutsCloseBtn) {
    shortcutsCloseBtn.addEventListener('click', closeShortcutsDialog);
}

function renderPipelineDebug(debugPipeline, debugInfo = null) {
    if (!debugPipelineSummary || !debugStageList) {
        return;
    }

    const stages = Array.isArray(debugPipeline && debugPipeline.stages) ? debugPipeline.stages : [];
    if (stages.length === 0) {
        debugStageList.innerHTML = '';

        const totalMs = debugInfo && typeof debugInfo.phase1_total_ms === 'number'
            ? `${debugInfo.phase1_total_ms.toFixed(1)}ms`
            : '--';
        const exactHit = Boolean(debugInfo && debugInfo.phase1_exact_cache_hit);
        const quantizedHit = Boolean(debugInfo && debugInfo.phase1_quantized_cache_hit);
        debugPipelineSummary.textContent = `Live debug: total ${totalMs} | exact cache: ${exactHit ? 'yes' : 'no'} | quantized cache: ${quantizedHit ? 'yes' : 'no'}`;

        const telemetryCard = document.createElement('article');
        telemetryCard.className = 'debug-stage ok';
        telemetryCard.innerHTML = [
            '<div class="debug-stage-title">1. Realtime Render Telemetry</div>',
            '<div class="debug-stage-status">ok</div>',
            '<div class="debug-stage-explanation"><div><strong>What:</strong> Live backend timing and cache status from /render.</div><div><strong>Inputs:</strong> Current viewpoint, depth, mode.</div><div><strong>Outputs:</strong> End-to-end latency and cache hit type.</div></div>',
        ].join('');
        const telemetryPre = document.createElement('pre');
        telemetryPre.textContent = JSON.stringify({
            phase1_total_ms: debugInfo ? debugInfo.phase1_total_ms : null,
            phase1_exact_cache_hit: debugInfo ? debugInfo.phase1_exact_cache_hit : null,
            phase1_quantized_cache_hit: debugInfo ? debugInfo.phase1_quantized_cache_hit : null,
            side_by_side_orientation_fallback: debugInfo ? debugInfo.side_by_side_orientation_fallback : null,
        }, null, 2);
        telemetryCard.appendChild(telemetryPre);
        debugStageList.appendChild(telemetryCard);
        return;
    }

    const statusCounts = stages.reduce((acc, stage) => {
        const status = String(stage && stage.status ? stage.status : 'unknown');
        acc[status] = (acc[status] || 0) + 1;
        return acc;
    }, {});
    debugPipelineSummary.textContent = `Stages: ${stages.length} | ok: ${statusCounts.ok || 0} | skipped: ${statusCounts.skipped || 0} | error: ${statusCounts.error || 0}`;

    debugStageList.innerHTML = '';

    const stageDocs = {
        request: {
            summary: 'Captures the render request exactly as sent from the UI.',
            inputs: 'Current UI state: view, depth, render mode, projection mode, selected model.',
            outputs: 'Normalized parameters that downstream stages use.',
        },
        mesh_input: {
            summary: 'Loads/selects the source STL mesh before any clipping or slicing.',
            inputs: 'Selected model mesh and requested view.',
            outputs: 'Mesh statistics and a baseline render of the full geometry.',
        },
        full_stl_color: {
            summary: 'Renders the complete STL with depth-based colors for visual debugging.',
            inputs: 'Full input mesh and view projection.',
            outputs: 'Color image showing depth variation across faces.',
        },
        slice_plane: {
            summary: 'Computes where the slicing plane sits in 3D for the current depth.',
            inputs: 'Bounding box, view normal, and requested depth.',
            outputs: 'Plane origin/normal and projection-distance values.',
        },
        depth_peel_progression: {
            summary: 'Shows multiple peel depths to visualize how geometry is removed over depth.',
            inputs: 'Input mesh, slice normal, and sampled depth values.',
            outputs: 'A sequence of color renders at increasing peel depth.',
        },
        depth_peel: {
            summary: 'Applies boolean clipping to keep the mesh on one side of the slice plane.',
            inputs: 'Input mesh and computed slice plane.',
            outputs: 'Clipped mesh plus its updated geometry statistics.',
        },
        slice_faces: {
            summary: 'Extracts triangles that lie on the slice plane after clipping.',
            inputs: 'Clipped mesh and slice plane definition.',
            outputs: 'On-plane face mesh used for slice-style views.',
        },
        interpolation_diagnostic: {
            summary: 'Compares anti-aliasing and thresholding choices that can thicken lines.',
            inputs: 'Rendered grayscale channel before braille binarization.',
            outputs: 'AA/threshold comparison panel and raised-pixel counts.',
        },
        renderer_output_raw: {
            summary: 'Shows the direct renderer output before any additional conversion/debug handling.',
            inputs: 'Final raster image returned by CADComparisonRenderer.render(...).',
            outputs: 'Raw RGBA image preview and basic image metadata.',
        },
        render_image: {
            summary: 'Final low-fidelity rendered image before braille conversion.',
            inputs: 'Active render mode output from the renderer pipeline.',
            outputs: 'RGBA bitmap plus channel statistics.',
        },
        hf_binary_raw: {
            summary: 'Shows the raw high-fidelity binary before optimization.',
            inputs: 'Inverted renderer channel thresholded with any nonzero treated as raised.',
            outputs: 'Unoptimized binary mask used as payload source for braille downsampling.',
        },
        slice_pipeline: {
            summary: 'Reports when slice-specific processing is skipped or fails.',
            inputs: 'Slice prerequisites (bbox, view mapping, mesh).',
            outputs: 'Skip/error reason so pipeline gaps are visible.',
        },
        slice_graph_data: {
            summary: 'Load-time precomputed pairwise slice-area difference matrix used to draw the line graph overlay.',
            inputs: 'CADComparisonRenderer.__init__() -> _load_models() -> _compute_slice_graphs() result (view_diff_mats); current view and depth.',
            outputs: 'Row vector at current depth sent to compose_slicegraph branch of CADComparisonRenderer.render().',
        },
    };

    stages.forEach((stage, index) => {
        const status = String(stage && stage.status ? stage.status : 'unknown');
        const title = stage && stage.title ? stage.title : `Stage ${index + 1}`;
        const dataText = formatDebugValue(stage ? stage.data : null);
        const stageId = stage && stage.id ? String(stage.id) : '';
        const doc = stageDocs[stageId] || {
            summary: 'Pipeline stage diagnostic information.',
            inputs: 'Previous stage outputs and current render context.',
            outputs: 'Stage-specific data and optional preview image.',
        };

        const card = document.createElement('article');
        card.className = `debug-stage ${status}`;

        const titleEl = document.createElement('div');
        titleEl.className = 'debug-stage-title';
        titleEl.textContent = `${index + 1}. ${title}`;

        const statusEl = document.createElement('div');
        statusEl.className = 'debug-stage-status';
        statusEl.textContent = status;

        const pre = document.createElement('pre');
        pre.textContent = dataText;

        card.appendChild(titleEl);
        card.appendChild(statusEl);

        const expl = document.createElement('div');
        expl.className = 'debug-stage-explanation';
        const pipelineFunc = stage && stage.data && stage.data.pipeline_function
            ? `<div><strong>Function:</strong> <code>${stage.data.pipeline_function}</code></div>` : '';
        expl.innerHTML =
            pipelineFunc +
            `<div><strong>What:</strong> ${doc.summary}</div>` +
            `<div><strong>Inputs:</strong> ${doc.inputs}</div>` +
            `<div><strong>Outputs:</strong> ${doc.outputs}</div>`;
        card.appendChild(expl);

        const previewImageBase64 = stage && stage.preview_image_base64 ? String(stage.preview_image_base64) : '';
        if (previewImageBase64.length > 0) {
            const imageLabel = document.createElement('div');
            imageLabel.className = 'debug-stage-image-label';
            imageLabel.textContent = 'Stage preview';

            const image = document.createElement('img');
            image.className = 'debug-stage-image';
            image.src = 'data:image/png;base64,' + previewImageBase64;
            image.alt = `${title} preview image`;

            card.appendChild(imageLabel);
            card.appendChild(image);
        }

        card.appendChild(pre);
        debugStageList.appendChild(card);
    });
}

function fetchExportSourceState() {
    const requestedGraphView = sliceGraphLocked ? sliceGraphAnchorView : currentView;
    const requestedGraphDepth = sliceGraphLocked ? sliceGraphAnchorDepth : currentSliceDepth;
    const renderPipelineParams = getRenderPipelineParams(currentRenderMode);
    return {
        view: currentView,
        orientation: getOrientationPayload(),
        zoom: currentZoom,
        depth: currentSliceDepth,
        renderMode: renderPipelineParams.renderMode,
        projectionMode: renderPipelineParams.projectionMode,
        mode: getServerRepresentationMode(),
        move_camera_center: 'none',
        print_view: false,
        model: currentModel,
        current_model: currentModel,
        compose_scrollbar: composeScrollbar,
        compose_slicegraph: composeSliceGraph,
        show_view_info_box: showViewInfoBox,
        output_device: currentOutputDevice,
        slicegraph_locked: sliceGraphLocked,
        slicegraph_view: requestedGraphView,
        slicegraph_depth: requestedGraphDepth,
        slicegraph_mode: sliceGraphMode,
        export_width: 1000,
    };
}

function updateHighFidelityPreview(data) {
    if (!highFidelityPreviewImg || !highFidelityPreviewMeta) return;

    // Prefer the direct render_preview_base64 field; fall back to the
    // legacy debug_pipeline hf_binary_raw stage for older server versions.
    let previewBase64 = data && data.render_preview_base64;
    let shape = data && Array.isArray(data.render_preview_shape) ? data.render_preview_shape : null;

    if (!previewBase64) {
        const stages = Array.isArray(data && data.debug_pipeline && data.debug_pipeline.stages)
            ? data.debug_pipeline.stages : [];
        const hfStage = stages.find(s => s && s.id === 'hf_binary_raw');
        previewBase64 = hfStage && hfStage.preview_image_base64;
        shape = hfStage && hfStage.data && Array.isArray(hfStage.data.shape) ? hfStage.data.shape : null;
    }

    if (!previewBase64) {
        highFidelityPreviewMeta.textContent = 'Render preview unavailable';
        return;
    }

    highFidelityPreviewImg.src = 'data:image/png;base64,' + previewBase64;
    highFidelityPreviewImg.alt = `Render preview: ${currentView} view, ${currentSliceDepth}% depth, ${renderModeLabel()}`;

    highFidelityPreviewMeta.textContent = previewCaption(shape);
}

async function exportCurrentSliceAsPng() {
    try {
        exportSliceSvgBtn.disabled = true;
        announce('rendering high-fidelity export');

        const response = await fetch(`${SERVER_URL}/render/export-source`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(fetchExportSourceState()),
            mode: 'cors'
        });

        if (!response.ok) {
            throw new Error(`Export render request failed (${response.status})`);
        }

        const data = await response.json();
        if (!data.image_base64) {
            throw new Error('Export render response missing image data');
        }

        const downloadUrl = 'data:image/png;base64,' + data.image_base64;
        const sanitizedView = String(currentView).replace(/[^a-zA-Z0-9+-]/g, '_');
        const filename = `slice_${sanitizedView}_${currentSliceDepth}_${currentRenderMode}.png`;

        const link = document.createElement('a');
        link.href = downloadUrl;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);

        announce('slice exported as png');
    } catch (error) {
        console.warn('Failed to export slice as PNG:', error);
        announceAlert('High-fidelity export failed');
    } finally {
        exportSliceSvgBtn.disabled = false;
    }
}

// Update slice depth display and announce changes
function updateSliceDepth(newDepth, shouldAnnounce = true) {
    const oldDepth = currentSliceDepth;
    currentSliceDepth = Math.max(0, Math.min(100, newDepth));
    writeDisplayDepthToPlanes(currentSliceDepth);
    sliceSlider.value = currentSliceDepth;
    slicePercentage.textContent = currentSliceDepth;
    refreshViewInfoSummary();

    // Only mutate button labels and trigger a render when the value actually
    // changed.
    if (oldDepth !== currentSliceDepth) {
        // shouldAnnounce=false means the caller (a keyboard shortcut handler, or
        // hardware acting through window.updateSliceDepth) announces its own
        // settled value separately. shouldAnnounce=true (a mouse click on the
        // slider or the +/- buttons) has no other feedback mechanism, so announce
        // it here.
        if (shouldAnnounce) {
            announceDepthValue(currentSliceDepth, oldDepth, announceAlert);
        }
        updateButtonLabels();
        sendStateToServer();
    }

    return oldDepth !== currentSliceDepth;
}

function getCurrentSliceDepth(){
    return currentSliceDepth;
}

/**
 * Check exactly the radio matching `currentValue`, and report when none does.
 *
 * A group whose values have drifted from the state they mirror ends up with
 * every radio unchecked, because assigning `.checked` overrides the `checked`
 * attribute in the markup. That is silent, survives review, and leaves the
 * group announcing no selection. Say so instead.
 */
function syncRadioGroup(radios, currentValue, groupLabel) {
    let matched = false;
    radios.forEach(r => {
        const isMatch = (r.value === currentValue);
        r.checked = isMatch;
        matched = matched || isMatch;
    });
    if (!matched && radios.length > 0) {
        console.error(
            `syncRadios: no ${groupLabel} radio has value "${currentValue}"; ` +
            `the group is now showing no selection. Values: ` +
            `${[...radios].map(r => r.value).join(', ')}`
        );
    }
}

// Helper to sync radios with current state
function syncRadios() {
    syncRadioGroup(renderModeRadios(), currentRenderMode, 'render-mode');
    syncRadioGroup(viewModeRadios(), currentRepresentationMode, 'view-mode');
    syncRadioGroup(outputDeviceRadios(), currentOutputDevice, 'output-device');
}

// The server only attaches monarch_cells_hex to a render when output_device is
// 'monarch_hid'. Send that whenever a Monarch is connected over Web HID and the
// user has not explicitly chosen a different device, independent of which radio
// is selected — so picking the Monarch radio cannot turn its own feed off (#75).
function getEffectiveOutputDevice() {
    if (monarchHidConnected && (currentOutputDevice === 'monarch' || currentOutputDevice === 'auto')) {
        return 'monarch_hid';
    }
    return currentOutputDevice;
}

// Called by the Monarch Web HID integration on connect/disconnect. Only toggles
// the connection flag; the radio preference is the user's and is left alone.
function setMonarchHidConnected(connected) {
    monarchHidConnected = Boolean(connected);
}

// --- Connected tactile displays -------------------------------------------
//
// One entry per device, keyed by the names getEffectiveOutputDevice() returns.
// This used to be a single global slot, which meant a Monarch disconnecting
// cleared the entry belonging to a DotPad connected at the same time. That is
// why the Monarch deliberately never reported its dimensions at all, and why the
// two devices took different routes through the render path.
//
// A Monarch is 48 cells x 10 lines and a braille cell is 2x4 pixels, so it is
// exactly the 96x40 default. Only the DotPad differs, at 60x40. "Nothing
// connected" and "Monarch connected" therefore describe the same grid.
window.tactileDisplays = window.tactileDisplays || {};

// With nothing connected there is no right answer, so sit between the two
// displays we support rather than favouring either: a Monarch is 96x40 and a
// DotPad 60x40. 78 is the midpoint and still a whole number of braille cells,
// which are two pixels wide. This drives the render as well as the caption, so
// what the preview reports is what was actually drawn.
const DEFAULT_TACTILE_GRID = Object.freeze({
    pixelWidth: 78,
    pixelHeight: 40,
    label: 'default grid',
});

// The grid the render currently on screen was made at. Captions describe that
// payload rather than whatever is connected at this instant, so connecting a
// display cannot pair its new label with the previous render's dimensions.
let lastRenderedGrid = DEFAULT_TACTILE_GRID;

/** The display using this grid, or the default if none does. */
/** The registry key for a device.
 *
 * The two integrations in this repo pass lowercase literals that already match
 * what getEffectiveOutputDevice() returns, so this changes nothing today. It is
 * here so that a third integration registering "DotPad" cannot end up filed
 * under a key nothing ever looks up. Applied on both writing and reading, since
 * normalising only one side would create exactly the mismatch it guards against.
 */
function displayKey(deviceKey) {
    return String(deviceKey).toLowerCase();
}

function gridForSize(width, height) {
    // Compared as numbers: a device reporting "60" rather than 60 would other-
    // wise match nothing and read as no device connected at all, which looks
    // like a hardware fault rather than a type confusion.
    width = Number(width);
    height = Number(height);
    for (const entry of Object.values(window.tactileDisplays)) {
        if (Number(entry.pixelWidth) === width && Number(entry.pixelHeight) === height) {
            return entry;
        }
    }
    return DEFAULT_TACTILE_GRID;
}

/** Register (or with `null`, clear) one device without touching the others. */
function setTactileDisplay(deviceKey, info) {
    if (!deviceKey) return;
    const key = displayKey(deviceKey);
    if (info) {
        window.tactileDisplays[key] = info;
    } else {
        delete window.tactileDisplays[key];
    }
    // The size only reached the server on the next render, so connecting a
    // display left the previews describing the previous one until the user
    // happened to do something else.
    if (typeof sendStateToServer === 'function') sendStateToServer();
}

/** The grid to render at: the display that will actually receive this frame.
 *
 * Not simply the selected output device. That setting is a preference and
 * defaults to the Monarch whether or not one is attached, while a connected
 * DotPad is sent every frame regardless of it. Keying only on the preference
 * meant plugging in a DotPad while the setting said Monarch left the render at
 * the default size, so the display received a frame shaped for something else.
 *
 * So: the selected device if it is actually connected; failing that, the only
 * display that is, since with one attached there is no ambiguity; failing that,
 * the default.
 */
function activeTactileGrid() {
    const selected = window.tactileDisplays[displayKey(getEffectiveOutputDevice())];
    if (selected) return selected;

    const connected = Object.values(window.tactileDisplays);
    if (connected.length === 1) return connected[0];

    return DEFAULT_TACTILE_GRID;
}

window.setTactileDisplay = setTactileDisplay;
window.activeTactileGrid = activeTactileGrid;

function switchOutputDevice(targetDevice) {
    if (currentOutputDevice === targetDevice) {
        announce(`already using ${targetDevice}`);
        return;
    }

    currentOutputDevice = targetDevice;
    syncRadios();
    announce(`output device ${targetDevice}`);
    sendStateToServer();
    return true;
}

// Helper to update composeScrollbar and composeSliceGraph based on view mode
function updateDisplayOptions() {
    switch (currentRepresentationMode) {
        case 'single':
            composeScrollbar = true;
            composeSliceGraph = false;
            break;
        case 'side-by-side':
            composeScrollbar = false;
            composeSliceGraph = false;
            break;
        case 'slice-graph-difference':
        case 'slice-graph-column-count':
            composeScrollbar = false;
            composeSliceGraph = true;
            break;
    }
    updateSideBySideAxisLabels();
}

function getLegendAxisForSliceAxis(cutAxis) {
    const legendFromSlice = {
        'x+': 'z+',
        'y+': 'x+',
        'z+': 'y+',
        'x-': 'z-',
        'y-': 'x-',
        'z-': 'y-',
    };
    return legendFromSlice [cutAxis] || 'x+';
}

function updateSideBySideAxisLabels() {
    const labelsContainer = document.getElementById('side-by-side-axis-labels');
    const leftLabel = document.getElementById('left-view-axis-label');
    const rightLabel = document.getElementById('right-view-axis-label');
    if (!labelsContainer || !leftLabel || !rightLabel) {
        return;
    }

    if (currentRepresentationMode === 'side-by-side') {
        const rightAxis = currentView;
        const leftAxis = getLegendAxisForSliceAxis(rightAxis);
        leftLabel.textContent = `Left view: ${leftAxis}`;
        rightLabel.textContent = `Right view: ${rightAxis}`;
        labelsContainer.hidden = false;
    } else {
        labelsContainer.hidden = true;
    }
}

// Update view information
function updateView(newView, shouldAnnounce = true, options = {}) {
    const syncOrientation = options.syncOrientation !== false;
    const oldView = currentView;
    currentView = newView;
    if (syncOrientation && oldView !== currentView) {
        setOrientationFromView(currentView);
        syncSliceDepthFromPlanes();
    }
    if (currentViewSpan) currentViewSpan.textContent = currentView;
    refreshViewInfoSummary();
    updateButtonLabels();
    updateSideBySideAxisLabels();
    syncRadios();
    if (oldView !== currentView && shouldAnnounce) {
        // Only real caller: the WitMotion orientation-cube hardware reporting a
        // new face (applyRelativeRotation and page-load both call with false/no-op).
        announceAlert(`${currentView.toLowerCase()} view`);
    }

    // Send state to server if changed
    if (oldView !== currentView) {
        if (isSliceGraphRepresentationMode()) {
            autoRefreshSliceGraph({ updateAnchor: true });
        } else {
            sendStateToServer();
        }
    }

    return oldView !== currentView;
}

/** Caption for either preview. Both go through this so they cannot disagree
 * about the order of the dimensions, or about which display they describe.
 * `shape` is [height, width], as numpy reports it. */
function previewCaption(shape) {
    const parts = [currentView, `${currentSliceDepth}%`, renderModeLabel()];
    if (shape && shape.length > 1) {
        parts.push(`${shape[1]}\u00d7${shape[0]}px`);
    }
    // Labelled by the grid that produced this render rather than by whatever is
    // connected now, so connecting a display cannot pair its name with the
    // previous render's size. lastRenderedGrid always holds a grid, but a device
    // may register without a label, and "undefined" in the caption would be
    // worse than saying the size plainly.
    parts.push(lastRenderedGrid.label
        || `${lastRenderedGrid.pixelWidth}\u00d7${lastRenderedGrid.pixelHeight} grid`);
    return parts.join(' \u00b7 ');
}

// Update the tactile display preview image
function updateTactilePreview(base64, shape) {
    const img = document.getElementById('tactile-display-img');
    const meta = document.getElementById('tactile-preview-meta');
    img.src = 'data:image/png;base64,' + base64;
    img.alt = `Tactile display: ${currentView} view, ${currentSliceDepth}% depth, ${renderModeLabel()}`;
    meta.textContent = previewCaption(shape);
}

// Update bounding box display
function updateBoundingBox(bbox) {
    if (!bbox || bbox.length !== 6) {
        return;
    }

    const [xmin, ymin, zmin, xmax, ymax, zmax] = bbox;
    const format = (num) => typeof num === 'number' ? num.toFixed(2) : '--';

    const setEl = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
    setEl('bbox-x-min', format(xmin));
    setEl('bbox-x-max', format(xmax));
    setEl('bbox-x-width', format(xmax - xmin));
    setEl('bbox-y-min', format(ymin));
    setEl('bbox-y-max', format(ymax));
    setEl('bbox-y-height', format(ymax - ymin));
    setEl('bbox-z-min', format(zmin));
    setEl('bbox-z-max', format(zmax));
    setEl('bbox-z-depth', format(zmax - zmin));

    currentBBoxDimensionsText = `${format(xmax - xmin)} × ${format(ymax - ymin)} × ${format(zmax - zmin)}`;
    refreshViewInfoSummary();
}

function _visibleModelEntries(model_list) {
    // Before builtinModelStems arrives, show the full list unfiltered.
    if (!builtinModelStems) {
        return model_list.map((stem, i) => ({ stem, i }));
    }
    const builtinSet = new Set(builtinModelStems);
    const ownedStems = new Set([...sessionOwnedModels].map(fn => fn.replace(/\.[^.]+$/, '')));
    return model_list
        .map((stem, i) => ({ stem, i }))
        .filter(({ stem }) => builtinSet.has(stem) || ownedStems.has(stem));
}

function updateModelList(model_list) {
    if (!Array.isArray(model_list)) return;
    lastFullModelList = model_list;

    // In the simplified workshop viewer the model dropdown is hidden and the model
    // is chosen from the URL, so never rebuild it or reset the current selection
    // (the ownership filter would otherwise drop an ingested model and reset to 0).
    // Just keep the status-bar label in sync with the URL-selected model.
    if (document.body.classList.contains('simple-ui')) {
        if (sbModel && currentModel) sbModel.textContent = currentModel;
        return;
    }

    const dropdown = document.getElementById("model-list-dropdown");
    const entries = _visibleModelEntries(model_list);
    // Signature is over the visible stems only so filter changes force a rebuild.
    const signature = entries.map(e => e.stem).join('||');

    if (signature === lastModelListSignature && dropdown.options.length > 0) {
        // Same visible set — restore the selection by name.
        const hasCurrentOption = [...dropdown.options].some(o => o.value === currentModel);
        if (hasCurrentOption) dropdown.value = currentModel;
        if (sbModel && dropdown.selectedIndex >= 0) {
            sbModel.textContent = dropdown.options[dropdown.selectedIndex].text;
        }
        return;
    }

    lastModelListSignature = signature;
    dropdown.innerHTML = '';

    if (entries.length === 0) {
        dropdown.innerHTML = '<option value="" selected>No models found</option>';
        dropdown.disabled = true;
        return;
    }

    dropdown.disabled = false;

    entries.forEach(({ stem, i }) => {
        const option = document.createElement("option");
        // The model's name, not its position in the server's list. That list is
        // rebuilt whenever anyone uploads, so a position meant a different model
        // afterwards and this window would silently start showing it.
        option.value = stem;
        const ownedFile = [...sessionOwnedModels].find(fn => fn.replace(/\.[^.]+$/, '') === stem);
        if (ownedFile) {
            option.text = stem + ' (your upload)';
            option.dataset.ownedFilename = ownedFile;
        } else {
            option.text = stem;
        }
        dropdown.appendChild(option);
    });

    const hasCurrentOption = [...dropdown.options].some(o => o.value === currentModel);
    if (hasCurrentOption) {
        dropdown.value = currentModel;
        if (sbModel) sbModel.textContent = dropdown.options[dropdown.selectedIndex].text;
    } else {
        dropdown.selectedIndex = 0;
        currentModel = dropdown.value;
        if (sbModel && dropdown.options.length > 0) sbModel.textContent = dropdown.options[0].text;
    }
    refreshDeleteButton();
}

document.getElementById("model-list-dropdown").addEventListener("input", function() {
    // Keep local state in sync while keyboard arrows navigate options.
    currentModel = this.value;
    if (sbModel && this.selectedIndex >= 0) {
        sbModel.textContent = this.options[this.selectedIndex].text;
    }
    refreshDeleteButton();
});

document.getElementById("model-list-dropdown").addEventListener("change", function() {
    const selectedItem = this.value;
    currentModel = selectedItem;
    clearCameraCenterState();
    resetSlicePlanes();
    const selectedLabel = this.selectedIndex >= 0 ? this.options[this.selectedIndex].text : `model ${selectedItem}`;
    if (sbModel && this.selectedIndex >= 0) {
        sbModel.textContent = this.options[this.selectedIndex].text;
    }
    beginModelLoadAnnouncement(selectedLabel, 'selection');
    pendingInputSource = 'ui';
    refreshDeleteButton();
    if (isSliceGraphRepresentationMode()) {
        autoRefreshSliceGraph({ updateAnchor: false });
    } else {
        sendStateToServer();
    }
});

// ---------------------------------------------------------------------------
// Session-owned model restoration and explicit delete
// ---------------------------------------------------------------------------

function refreshDeleteButton() {
    const dropdown = document.getElementById('model-list-dropdown');
    const btn = document.getElementById('delete-model-btn');
    if (!btn || !dropdown || dropdown.selectedIndex < 0) return;
    const selectedOption = dropdown.options[dropdown.selectedIndex];
    const ownedFile = selectedOption?.dataset.ownedFilename || '';
    btn.hidden = !ownedFile;
    if (ownedFile) {
        const stem = selectedOption.text.replace(/ \(your upload\)$/, '');
        btn.textContent = `Remove "${stem}"`;
    }
}

async function initSessionModels() {
    try {
        const resp = await fetch(`${SERVER_URL}/session/models`);
        if (!resp.ok) return;
        const data = await resp.json();
        const available = (data.models || []).filter(m => m.available);
        sessionOwnedModels = new Set(available.map(m => m.filename));
        // Force a full rebuild so the filter and annotations are applied correctly.
        lastModelListSignature = null;
        if (lastFullModelList.length > 0) updateModelList(lastFullModelList);
    } catch (_) {}
}

document.getElementById('delete-model-btn').addEventListener('click', async function() {
    const dropdown = document.getElementById('model-list-dropdown');
    const statusEl = document.getElementById('upload-model-status');
    if (!dropdown || dropdown.selectedIndex < 0) return;

    const selectedOption = dropdown.options[dropdown.selectedIndex];
    const filename = selectedOption?.dataset.ownedFilename;
    if (!filename) return;
    const stem = selectedOption.text.replace(/ \(your upload\)$/, '');

    this.disabled = true;
    try {
        const resp = await fetch(`${SERVER_URL}/models/${encodeURIComponent(filename)}`, {
            method: 'DELETE',
        });
        if (resp.ok) {
            sessionOwnedModels.delete(filename);
            dropdown.remove(dropdown.selectedIndex);
            if (dropdown.options.length > 0) {
                dropdown.selectedIndex = 0;
                currentModel = dropdown.value;
                sendStateToServer();
            }
            refreshDeleteButton();
            statusEl.textContent = `✓ ${stem} removed`;
            announce(`Model ${stem} removed.`);
        } else {
            const errData = await resp.json().catch(() => ({}));
            statusEl.textContent = `Delete failed: ${errData.message || resp.status}`;
        }
    } catch (err) {
        statusEl.textContent = `Delete error: ${err.message}`;
    } finally {
        this.disabled = false;
    }
});

// Handle STL/STEP file upload
document.getElementById('upload-model-input').addEventListener('change', async function() {
    const file = this.files[0];
    if (!file) return;

    const statusEl = document.getElementById('upload-model-status');
    const label = document.getElementById('upload-model-label');

    statusEl.textContent = `Uploading ${file.name}…`;
    label.setAttribute('aria-disabled', 'true');
    this.disabled = true;

    const formData = new FormData();
    formData.append('file', file);
    formData.append('upload_session_id', getUploadSessionId());

    try {
        const resp = await fetch(`${SERVER_URL}/upload`, {
            method: 'POST',
            body: formData,
            mode: 'cors',
        });
        const data = await resp.json();

        if (data.status === 'success') {
            if (data.filename) sessionOwnedModels.add(data.filename);
            updateModelList(data.model_list);
            // Select the newly uploaded model
            const dropdown = document.getElementById('model-list-dropdown');
            const uploadedStem = data.model_stem
                || (data.filename || '').replace(/\.[^.]+$/, '');
            dropdown.value = uploadedStem;
            currentModel = uploadedStem;
            clearCameraCenterState();
            resetSlicePlanes();
            const selectedLabel = dropdown.selectedIndex >= 0 ? dropdown.options[dropdown.selectedIndex].text : data.filename;
            if (sbModel && dropdown.selectedIndex >= 0) {
                sbModel.textContent = dropdown.options[dropdown.selectedIndex].text;
            }
            statusEl.textContent = `✓ ${data.filename} uploaded`;
            announce(`Model ${data.filename} uploaded.`);
            beginModelLoadAnnouncement(selectedLabel, 'upload');
            pendingInputSource = 'upload';
            refreshDeleteButton();
            sendStateToServer();
        } else {
            statusEl.textContent = `Upload failed: ${data.message}`;
            announceAlert(`Upload failed: ${data.message}`);
        }
    } catch (err) {
        statusEl.textContent = `Upload error: ${err.message}`;
        announceAlert(`Upload error: ${err.message}`);
    } finally {
        label.removeAttribute('aria-disabled');
        this.disabled = false;
        // Clear file input so re-uploading the same file fires change again
        this.value = '';
    }
});

// Apply a server state snapshot to local UI — shared by SSE and fallback poll.
//
// The cube and the slider are deliberately absent. They are connected to a
// browser, by this window, through witmotion-imu.js and trinkey-slider.js, and
// they drive this window only. The server used to poll them over its own serial
// ports and broadcast every reading to every connected browser, so one cube
// turned at the server moved everybody's view. There was no way to address a
// message to one window: the client registry is a list of queues with nothing
// attached to say who is who.
function applyServerState(data) {
    if (Array.isArray(data.builtin_model_stems) && data.builtin_model_stems.length && !builtinModelStems) {
        builtinModelStems = data.builtin_model_stems;
        // Force a rebuild now that the filter is known.
        lastModelListSignature = null;
        if (lastFullModelList.length > 0) updateModelList(lastFullModelList);
    }
    const modelDropdown = document.getElementById("model-list-dropdown");
    const dropdownFocused = document.activeElement === modelDropdown;
    if (data.model_list && !dropdownFocused) {
        updateModelList(data.model_list);
    }
    if (data.bbox) {
        updateBoundingBox(data.bbox);
    }
    // Live model switch pushed by /ingest?open=1 over SSE: jump an already-open
    // viewer to a freshly-ingested model. Transient — /get_data never carries this,
    // and the index guard keeps it idempotent.
    if (data.load_model) {
        if (data.load_model !== currentModel) {
            currentModel = data.load_model;
            clearCameraCenterState();
            resetSlicePlanes();
            pendingInputSource = 'ingest';
            sendStateToServer();
        }
    }
}

// SSE: server pushes hardware state changes (WitMotion IMU, Slider) immediately
// instead of the client polling every second — reduces latency from ~1000 ms to ~10 ms.
(function connectSSE() {
    const evtSource = new EventSource(`${SERVER_URL}/events`);
    evtSource.onmessage = function(event) {
        try {
            const data = JSON.parse(event.data);
            if (serverConnected === false) {
                serverConnected = true;
                announce('Server reconnected.');
            } else {
                serverConnected = true;
            }
            applyServerState(data);
        } catch(e) {
            console.warn('SSE parse error:', e);
        }
    };
    evtSource.onerror = function() {
        // Do NOT set serverConnected = false here. EventSource fires onerror on
        // every reconnect attempt (normal behavior), which would block all renders.
        // Connection state is managed exclusively by the health poll below.
        console.warn('SSE connection error — will reconnect automatically.');
    };
})();

// Number of consecutive /get_data failures required before declaring the server
// unreachable. A single failure can be a transient hiccup (busy server, brief
// network interruption, browser scroll-time fetch deprioritisation) — requiring
// two in a row prevents spurious "Server unavailable" announcements on the
// Braille display during normal use.
let pollFailCount = 0;
const POLL_FAIL_THRESHOLD = 2;

// Slow fallback poll: the sole authority for serverConnected state changes.
// Keeps model list and bbox in sync for state that isn't pushed over SSE
// (e.g. model uploads). Runs every 5 s.
setInterval(() => {
    fetch(`${SERVER_URL}/get_data`)
        .then(res => res.ok ? res.json() : Promise.reject(new Error(`HTTP ${res.status}`)))
        .then(data => {
            pollFailCount = 0;
            if (serverConnected === false) {
                serverConnected = true;
                announce('Server reconnected.');
            } else {
                serverConnected = true;
            }
            applyServerState(data);
        })
        .catch(error => {
            pollFailCount++;
            console.warn(`Poll failed (${pollFailCount}/${POLL_FAIL_THRESHOLD}):`, error.message);
            if (pollFailCount >= POLL_FAIL_THRESHOLD && serverConnected !== false) {
                serverConnected = false;
                announceAlert('Server unavailable — rendering paused.');
            }
        });
}, 5000);

// Update zoom information
function updateZoom(newZoom, shouldAnnounce = true, sendToServer = true) {
    const oldZoom = currentZoom;
    const parsedZoom = Number(newZoom);
    if (!Number.isFinite(parsedZoom)) {
        return false;
    }
    currentZoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, parsedZoom));

    const zoomText = currentZoom.toFixed(1);
    zoomInput.value = zoomText;
    zoomLevelValue.textContent = zoomText;
    refreshViewInfoSummary();
    updateButtonLabels();

    // shouldAnnounce=true here only ever comes from a mouse click (zoom +/- buttons,
    // the zoom number field on change), which has no other feedback mechanism, so
    // announce it. Keyboard callers pass shouldAnnounce=false and announce their
    // own settled value.
    if (shouldAnnounce) {
        announceZoomValue(currentZoom, oldZoom, announceAlert);
    }

    console.log(oldZoom, currentZoom);
    if (sendToServer && oldZoom !== currentZoom) {
        if (isSliceGraphRepresentationMode()) {
            autoRefreshSliceGraph({ updateAnchor: false });
        } else {
            console.log("sendStateToServer");
            sendStateToServer();
        }
    }

    return oldZoom !== currentZoom;
}

async function fitCurrentViewToDevice() {
    const renderPipelineParams = getRenderPipelineParams(currentRenderMode);
    const orientationPayload = getOrientationPayload();

    const payload = {
        view: currentView,
        orientation: orientationPayload,
        zoom: currentZoom,
        depth: currentSliceDepth,
        renderMode: renderPipelineParams.renderMode,
        projectionMode: renderPipelineParams.projectionMode,
        mode: getServerRepresentationMode(),
        model: currentModel,
        current_model: currentModel,
        output_device: getEffectiveOutputDevice(),
        compose_scrollbar: composeScrollbar,
        compose_slicegraph: composeSliceGraph,
        show_view_info_box: showViewInfoBox,
    };

    const response = await fetch(`${SERVER_URL}/render/fit-view`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });

    const data = await response.json();
    console.log('fit view response', data);

    if (data.status !== 'success') {
        announceAlert('Fit view failed');
        return;
    }

    if (Array.isArray(data.camera_center) && data.camera_center.length === 2) {
        const key = getCameraCenterStateKey(currentView, orientationPayload);
        cameraCenterByViewOrientation.set(key, data.camera_center);
    }

    updateZoom(data.zoom, false, false);
    sendStateToServer();
    // Only reachable via the 'f' keyboard shortcut — no on-screen button.
    announceAlert(`View fitted to ${payload.output_device}`);

}

// Switch to a specific render mode
function switchToRenderMode(targetMode, shouldAnnounce = true) {
    if (!renderModeByKey(targetMode)) {
        console.error(`switchToRenderMode: unknown render mode ${targetMode}`);
        return;
    }
    if (currentRenderMode === targetMode) {
        if (shouldAnnounce) announce(`already ${renderModeLabel(targetMode)}`);
        return;
    }
    const previousMode = currentRenderMode;
    currentRenderMode = targetMode;
    refreshViewInfoSummary();
    updateButtonLabels();
    syncRadios();
    if (shouldAnnounce) announce(`${renderModeLabel()}`);

    // Send state to server
    sendStateToServer();
    return true;
}

function cycleRenderMode(shouldAnnounce = true) {
    const currentIndex = renderModes.findIndex(mode => mode.key === currentRenderMode);
    const nextIndex = (currentIndex + 1) % renderModes.length;
    switchToRenderMode(renderModes[nextIndex].key, shouldAnnounce);
}

function switchToRepresentationMode(targetMode, shouldAnnounce = true) {
    const mode = representationModeByKey(targetMode);
    if (!mode) {
        console.error(`switchToRepresentationMode: unknown view mode ${targetMode}`);
        return;
    }
    if (currentRepresentationMode === targetMode) {
        if (shouldAnnounce) announce(`already ${representationModeLabel(targetMode)}`);
        return;
    }
    const previousMode = currentRepresentationMode;
    const enteringSliceGraph = !isSliceGraphRepresentationMode(previousMode) && isSliceGraphRepresentationMode(targetMode);

    if (mode.sliceGraphMode) {
        sliceGraphMode = mode.sliceGraphMode;
    }

    currentRepresentationMode = targetMode;
    updateDisplayOptions();
    if (enteringSliceGraph) {
        sliceGraphLocked = true;
        captureSliceGraphAnchor(false);
    }
    updateButtonLabels();
    updateSliceGraphLockUI();
    updateSliceGraphModeUI();
    updateSideBySideAxisLabels();
    syncRadios();
    if (shouldAnnounce) announce(`${representationModeLabel(previousMode)} to ${representationModeLabel()}`);

    // Send state to server
    sendStateToServer();
    return true;
}

function cycleRepresentationMode(shouldAnnounce = true) {
    const currentIndex = representationModes.findIndex(mode => mode.key === currentRepresentationMode);
    const nextIndex = (currentIndex + 1) % representationModes.length;
    switchToRepresentationMode(representationModes[nextIndex].key, shouldAnnounce);
}

// Announce a change: updates the message window, speaks via the SR live region,
// echoes to the tactile display.
function emitAnnouncement(message, politeness) {
    const normalizedMessage = String(message);
    // Any announcement ends the current zoom/depth run: the next such change
    // re-includes its label, since the context is no longer obvious. A parameter
    // announcement re-establishes its own key immediately after this returns
    // (see announceParameterValue), so its own repeat behaviour is unaffected.
    lastAnnouncedParameterKey = null;

    // Always refresh the status bar so it reflects the latest state.
    refreshStatusBar();

    updateMessageWindow(normalizedMessage, politeness);

    // Send announcement to tactile display
    if (typeof window.onTactileAnnouncement === 'function') {
        try {
            window.onTactileAnnouncement({
                message: normalizedMessage,
                politeness
            });
        } catch (err) {
            console.warn('Tactile announcement failed:', err);
        }
    }
}

/**
 * Announce a background/system event politely: it waits for the user to pause
 * rather than interrupting. Reserve for things not tied to an immediate action —
 * a server reconnecting, a model finishing a load that was kicked off moments
 * ago — since the user may be doing something else entirely when it lands.
 */
function announce(message) {
    emitAnnouncement(message, 'polite');
}

/**
 * Announce assertively, interrupting whatever the AT is currently speaking.
 */
function announceAlert(message) {
    emitAnnouncement(message, 'assertive');
}

// External API used by hardware integration modules.
window.moveCursor = moveCursor;
window.cycleCursorState = cycleCursorState;
window.whichCursor = whichCursor;
window.getCurrentSliceDepth = getCurrentSliceDepth;
window.updateSliceDepth = updateSliceDepth;
window.announceDepthValue = announceDepthValue;

// Event listeners

// Slice depth slider with enhanced feedback
let sliderUpdateTimeout = null;

sliceSlider.addEventListener('input', function() {
    const newValue = parseInt(this.value);
    currentSliceDepth = newValue;
    slicePercentage.textContent = currentSliceDepth;

    // Update button labels immediately
    updateButtonLabels();
});

sliceSlider.addEventListener('change', function() {
    clearTimeout(sliderUpdateTimeout);
    pendingInputSource = 'ui';
    sendStateToServer();
});

// Render mode radios
document.addEventListener('change', function(e) {
    if (e.target && e.target.matches('input[name="render-mode"]')) {
        if (e.target.checked) {
            pendingInputSource = 'ui';
            switchToRenderMode(e.target.value);
        }
    }
});

// View mode radios (Single, Side-by-side, Slice graph)
document.addEventListener('change', function(e) {
    if (e.target && e.target.matches('input[name="view-mode"]')) {
        if (e.target.checked) {
            pendingInputSource = 'ui';
            switchToRepresentationMode(e.target.value);
        }
    }
});

// Output device radios (Monarch, DotPad, Auto)
document.addEventListener('change', function(e) {
    if (e.target && e.target.matches('input[name="output-device"]')) {
        if (e.target.checked) {
            pendingInputSource = 'ui';
            switchOutputDevice(e.target.value);
        }
    }
});

// Zoom number input — debounce while typing; commit on change/stepper controls
let zoomDebounceTimer = null;

zoomInput.addEventListener('input', function() {
    clearTimeout(zoomDebounceTimer);
    if (!Number.isFinite(this.valueAsNumber)) {
        return;
    }
    zoomDebounceTimer = setTimeout(() => {
        pendingInputSource = 'ui';
        updateZoom(this.valueAsNumber, false, true);
    }, 150);
});

zoomInput.addEventListener('change', function() {
    clearTimeout(zoomDebounceTimer);
    if (!Number.isFinite(this.valueAsNumber)) {
        this.value = currentZoom.toFixed(1);
        return;
    }
    pendingInputSource = 'ui';
    updateZoom(this.valueAsNumber, true, true);
});

zoomOutBtn.addEventListener('click', function() {
    pendingInputSource = 'ui';
    updateZoom(currentZoom - ZOOM_STEP, true, true);
});

zoomInBtn.addEventListener('click', function() {
    pendingInputSource = 'ui';
    updateZoom(currentZoom + ZOOM_STEP, true, true);
});

showViewInfoBoxCheckbox.addEventListener('change', function() {
    showViewInfoBox = this.checked;
    pendingInputSource = 'ui';
    sendStateToServer();
});

// Deeper depth button
deeperBtn.addEventListener('click', function() {
    pendingInputSource = 'ui';
    updateSliceDepth(currentSliceDepth + 10, true);
});

// Shallower depth button
shallowerBtn.addEventListener('click', function() {
    pendingInputSource = 'ui';
    updateSliceDepth(currentSliceDepth - 10, true);
});

if (sliceGraphLockCheckbox) {
    sliceGraphLockCheckbox.addEventListener('change', function() {
        setSliceGraphLocked(this.checked);
        // The checkbox's own checked state already gives its own accessible
        // feedback; only log the change 
        announce(`Slice graph lock ${sliceGraphLocked ? 'on' : 'off'}`);
    });
}

sliceGraphRefreshBtn.addEventListener('click', function() {
    if (!isSliceGraphRepresentationMode()) {
        announce('refresh only available in slice-graph mode');
        return;
    }
    captureSliceGraphAnchor(true);
    pendingInputSource = 'ui';
    sendStateToServer();
});

if (sliceGraphModeBtn) {
    sliceGraphModeBtn.addEventListener('click', function() {
        toggleSliceGraphMode();
        announce(`Slice graph mode ${sliceGraphMode === 'column-count' ? 'column count' : 'difference'}`);
    });
}

if (resetPositionBtn) {
    resetPositionBtn.addEventListener('click', function() {
        pendingInputSource = 'ui';
        currentMoveCamera = "reset";
        sendStateToServer();
        currentMoveCamera = "none";
        announce('Position reset');
    });
}

// The same six turns the keys perform, for anyone not driving from the keyboard.
// Buttons rather than a radio group per view: there is no fixed set of
// orientations to choose from once roll is available, only turns to make from
// wherever the model currently is.
const ORIENTATION_BUTTONS = {
    'pitch-up-btn': 'pitchUp',
    'pitch-down-btn': 'pitchDown',
    'yaw-left-btn': 'yawLeft',
    'yaw-right-btn': 'yawRight',
    'roll-ccw-btn': 'rollCounterclockwise',
    'roll-cw-btn': 'rollClockwise',
};

for (const [buttonId, rotationName] of Object.entries(ORIENTATION_BUTTONS)) {
    const button = document.getElementById(buttonId);
    if (!button) continue;
    button.addEventListener('click', function() {
        pendingInputSource = 'ui';
        applyRelativeRotation(rotationName, announce);
    });
}

exportSliceSvgBtn.addEventListener('click', function() {
    exportCurrentSliceAsPng();
});

if (debugPipelineToggleBtn) {
    debugPipelineToggleBtn.addEventListener('click', function() {
        toggleDebugPipelineVisibility();
    });
}

// Global keyboard navigation support for accessibility
document.addEventListener('keydown', function(e) {
    const target = e.target;
    const tagName = target && target.tagName ? target.tagName.toLowerCase() : '';
    const inputType = target && tagName === 'input' ? String(target.type || '').toLowerCase() : '';
    const isTextEntryTarget = Boolean(
        target && (
            target.isContentEditable ||
            tagName === 'textarea' ||
            (tagName === 'input' && (
                inputType === 'text' ||
                inputType === 'search' ||
                inputType === 'email' ||
                inputType === 'url' ||
                inputType === 'password' ||
                inputType === 'number' ||
                inputType === 'tel'
            ))
        )
    );

    // Do not override native keyboard behavior for text entry fields.
    if (isTextEntryTarget) {
        return;
    }

    // A modal dialog (shortcuts help, session consent) makes the rest of the page
    // inert — Escape and Tab must stay scoped to it, not also fire a background
    // shortcut underneath.
    if (document.querySelector('dialog[open]')) {
        return;
    }

    // Leave browser/app shortcuts untouched (Cmd/Ctrl/Alt combos).
    if (e.metaKey || e.ctrlKey || e.altKey) {
        return;
    }

    const rawKey = String(e.key || '');
    const key = rawKey.toLowerCase();
    const code = String(e.code || '');
    const normalizedKey = (
        code === 'Digit2' || code === 'Numpad2' ? '2' :
        code === 'Digit3' || code === 'Numpad3' ? '3' :
        code === 'Digit4' || code === 'Numpad4' ? '4' :
        code === 'Digit5' || code === 'Numpad5' ? '5' :
        key
    );
    const supportedShortcuts = new Set([
        'arrowup', 'arrowdown', 'pageup', 'pagedown',
         '2', '3', 'q', 'e',
        'u', 'i', 'o', 'j', 'k', 'l',
        '4', '5',
        'r', 't', 'g', 'v', 'z',
        'w', 'a', 's', 'd', '[', ']', 'h', '?', 'p', '.', 'escape', 'f'
    ]);

    if (!supportedShortcuts.has(normalizedKey)) {
        return;
    }

    // Allow key-hold repeat only for continuous controls (depth/zoom).
    // For other shortcuts, swallow repeats so native radio-group arrow
    // behavior cannot switch mode selections while a key is held.
    const repeatableShortcuts = new Set([
        'pageup', 'pagedown',
        'arrowup', 'arrowdown', '2', '3',
        '4', '5', 'n', 'm'
    ]);
    if (e.repeat && !repeatableShortcuts.has(normalizedKey)) {
        e.preventDefault();
        return;
    }

    switch(normalizedKey) {
        case 'arrowup':
            // Go deeper (increase depth by 1%)
            e.preventDefault();
            {
                const previousDepth = currentSliceDepth;
                const nextDepth = Math.min(100, currentSliceDepth + 1);
                updateSliceDepth(nextDepth, false);
                announceDepthValue(nextDepth, previousDepth);
            }
            break;
        case 'arrowdown':
            // Go shallower (decrease depth by 1%)
            e.preventDefault();
            {
                const previousDepth = currentSliceDepth;
                const nextDepth = Math.max(0, currentSliceDepth - 1);
                updateSliceDepth(nextDepth, false);
                announceDepthValue(nextDepth, previousDepth);
            }
            break;
        case 'pageup':
            // Go deeper (increase depth by 10%)
            e.preventDefault();
            {
                const previousDeeperDepth = currentSliceDepth;
                const newDeeperDepth = Math.min(100, currentSliceDepth + 10);
                updateSliceDepth(newDeeperDepth, false);
                announceDepthValue(newDeeperDepth, previousDeeperDepth);
            }
            break;
        case 'pagedown':
            // Go shallower (decrease depth by 10%)
            e.preventDefault();
            {
                const previousShallowerDepth = currentSliceDepth;
                const newShallowerDepth = Math.max(0, currentSliceDepth - 10);
                updateSliceDepth(newShallowerDepth, false);
                announceDepthValue(newShallowerDepth, previousShallowerDepth);
            }
            break;

        case '2':
            e.preventDefault();
            {
                const previousZoom = currentZoom;
                const zoomChanged = updateZoom(currentZoom - ZOOM_STEP, false, true);
                if (zoomChanged) {
                    announceZoomValue(currentZoom, previousZoom);
                } else {
                    announceZoomValue(previousZoom, previousZoom);
                }
            }
            break;
        case '3':
            e.preventDefault();
            {
                const previousZoom = currentZoom;
                const zoomChanged = updateZoom(currentZoom + ZOOM_STEP, false, true);
                if (zoomChanged) {
                    announceZoomValue(currentZoom, previousZoom);
                } else {
                    announceZoomValue(previousZoom, previousZoom);
                }
            }
            break;

        case 'f':
            e.preventDefault();
            fitCurrentViewToDevice();
            break;
            
        // View shortcuts
        case 'r':
            e.preventDefault();
            {
                const previousMode = currentRenderMode;
                cycleRenderMode(false);
                announceAlert(`${renderModeLabel()}`);
            }
            break;

        case 't':
            e.preventDefault();
            {
                const previousViewMode = currentRepresentationMode;
                cycleRepresentationMode(false);
                announceAlert(`${representationModeLabel()}`);
            }
            break;

        case 'u':
            e.preventDefault();
            applyRelativeRotation('rollCounterclockwise');
            break;

        case 'o':
            e.preventDefault();
            applyRelativeRotation('rollClockwise');
            break;

        case 'i':
            e.preventDefault();
            applyRelativeRotation('pitchUp');
            break;

        case 'k':
            e.preventDefault();
            applyRelativeRotation('pitchDown');
            break;

        case 'j':
            e.preventDefault();
            applyRelativeRotation('yawLeft');
            break;

        case 'l':
            e.preventDefault();
            applyRelativeRotation('yawRight');
            break;

        case '.':
            // Read the full top-of-page status bar.
            e.preventDefault();
            announceAlert(getStatusBarAnnouncement());
            break;

        case 'g':
            e.preventDefault();
            if (!isSliceGraphRepresentationMode()) {
                announceAlert('not in slice-graph mode');
                break;
            }
            captureSliceGraphAnchor(true);
            sendStateToServer();
            announceAlert(`view ${sliceGraphAnchorView}, depth ${sliceGraphAnchorDepth}%`);
            break;

        case 'v':
            e.preventDefault();
            if (!isSliceGraphRepresentationMode()) {
                announceAlert('not in slice-graph mode');
                break;
            }
            toggleSliceGraphLock();
            announceAlert(`${sliceGraphLocked ? 'on' : 'off'}`);
            break;

        case 'w':
            currentMoveCamera = "up";
            sendStateToServer();
            currentMoveCamera = "none";
            announceAlert('up');
            break;
        case 'd':
            currentMoveCamera = "right";
            sendStateToServer();
            currentMoveCamera = "none";
            announceAlert('right');
            break;
        case 's':
            currentMoveCamera = "down";
            sendStateToServer();
            currentMoveCamera = "none";
            announceAlert('down');
            break;
        case '[':
            composeScrollbar = !composeScrollbar;
            sendStateToServer();
            announceAlert(`${composeScrollbar ? 'on' : 'off'}`);
            break;
        case ']':
            composeSliceGraph = !composeSliceGraph;
            sendStateToServer();
            announceAlert(`${composeSliceGraph ? 'on' : 'off'}`);
            break;

        case 'a':
            currentMoveCamera = "left";
            sendStateToServer();
            currentMoveCamera = "none";
            announceAlert('left');
            break;

        case '4':
            e.preventDefault();
            {
                const previousZoom = currentZoom;
                const zoomChanged = updateZoom(currentZoom - FINE_ZOOM_STEP, false);
                if (zoomChanged) {
                    announceZoomValue(currentZoom, previousZoom);
                } else {
                    announceZoomValue(previousZoom, previousZoom);
                }
            }
            break;
        case '5':
            e.preventDefault();
            {
                const previousZoom = currentZoom;
                const zoomChanged = updateZoom(currentZoom + FINE_ZOOM_STEP, false);
                if (zoomChanged) {
                    announceZoomValue(currentZoom, previousZoom);
                } else {
                    announceZoomValue(previousZoom, previousZoom);
                }
            }
            break;

        case 'escape':
            e.preventDefault();
            document.activeElement.blur();
            announceAlert('Focus cleared');
            break;

        case 'h':
        case '?':
            e.preventDefault();
            openShortcutsDialog();
            break;

        case 'p':
            announceAlert('Printing current render');
            print_view();
            break;

        case 'z':
            e.preventDefault();
            currentMoveCamera = "reset";
            sendStateToServer();
            currentMoveCamera = "none";
            announceAlert('Position reset');
            break;

        default:
            return;
    }
});

function focusTopOfPage() {
    const pageTitle = document.getElementById('page-title');
    if (!pageTitle) {
        return;
    }
    // Delay one frame so layout is ready before moving focus.
    requestAnimationFrame(() => {
        pageTitle.focus({ preventScroll: true });
        pageTitle.scrollIntoView({ block: 'start' });
    });
}

// Initialize the interface
document.addEventListener('DOMContentLoaded', async function() {
    // Move focus to the top element (page title) on load.
    focusTopOfPage();

    // Simplified workshop viewer: the /workshop route (or ?ui=simple) shows only
    // the core controls (see viewer.css) and constrains depth to four steps.
    const workshopParams = new URLSearchParams(location.search);
    if (location.pathname.replace(/\/+$/, '') === '/workshop' || workshopParams.get('ui') === 'simple') {
        document.body.classList.add('simple-ui');
        // The workshop viewer opens on the y+ face in X-Ray, the orientation and
        // rendering a session starts from. The full viewer keeps x+ and Filled.
        // Set directly rather than through updateView/switchToRenderMode so no
        // extra render is sent before the requested model is resolved below.
        currentView = 'y+';
        setOrientationFromView(currentView);
        currentRenderMode = 'xray';
    }

    // Set initial values
    updateSliceDepth(50, false);
    updateView(currentView);
    updateDisplayOptions();
    updateZoom(0, false);
    syncRadios();
    updateButtonLabels();
    updateSliceGraphLockUI();
    updateSliceGraphModeUI();
    refreshViewInfoSummary();
    showViewInfoBoxCheckbox.checked = showViewInfoBox;
    refreshStatusBar();

    // Expose globally so display-connect handlers can trigger a send.
    window.sendStateToServer = sendStateToServer;
    initializeDebugPipelineVisibility();

    // Pre-select a model when opened via /workshop?model=<stem> or ?model=<stem>.
    // The URL already carries the name a render wants, so there is nothing to
    // look up: this used to fetch the whole model list on every start purely to
    // turn that name back into a position.
    const wantedModel = workshopParams.get('model');
    if (wantedModel) {
        currentModel = wantedModel.replace(/\.[^.]+$/, '');
        if (sbModel) sbModel.textContent = currentModel;
    }

    // Send initial state to server
    pendingInputSource = 'init';
    sendStateToServer();

});

// Ensure top focus is restored when returning via browser history cache.
window.addEventListener('pageshow', function() {
    focusTopOfPage();
});

// Handle browser zoom and text scaling
function handleZoomChanges() {
    // Ensure the interface remains usable at different zoom levels
    const container = document.querySelector('.container');

    function checkZoom() {
        const devicePixelRatio = window.devicePixelRatio || 1;
        if (devicePixelRatio !== 1) {
            container.style.maxWidth = '95vw';
        } else {
            container.style.maxWidth = '900px';
        }
    }

    window.addEventListener('resize', checkZoom);
    checkZoom();
}

// Initialize zoom handling
document.addEventListener('DOMContentLoaded', handleZoomChanges);
