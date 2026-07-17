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
    const debug = responseData && typeof responseData === 'object' ? responseData.debug : null;
    if (!debug || !Array.isArray(debug.camera_center) || debug.camera_center.length !== 2) {
        return;
    }

    const centerX = Number(debug.camera_center[0]);
    const centerY = Number(debug.camera_center[1]);
    if (!Number.isFinite(centerX) || !Number.isFinite(centerY)) {
        return;
    }

    const debugView = typeof debug.view === 'string' && debug.view.trim().length > 0
        ? debug.view
        : requestState.view;
    const debugOrientation = debug.orientation && typeof debug.orientation === 'object'
        ? debug.orientation
        : requestState.orientation;
    const key = getCameraCenterStateKey(debugView, debugOrientation);
    cameraCenterByViewOrientation.set(key, [centerX, centerY]);
    if (sbPanCenter) {
        sbPanCenter.textContent = formatCenter2([centerX, centerY]);
    }

    if (Array.isArray(debug.world_camera_center) && debug.world_camera_center.length === 3) {
        const worldCenter = debug.world_camera_center.map((value) => Number(value));
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
            current_model: currentModel,
            compose_scrollbar: composeScrollbar,
            compose_slicegraph: composeSliceGraph,
            show_view_info_box: showViewInfoBox,
            output_device: currentOutputDevice,
            slicegraph_locked: sliceGraphLocked,
            slicegraph_view: requestedGraphView,
            slicegraph_depth: requestedGraphDepth,
            slicegraph_mode: sliceGraphMode,
            input_source: pendingInputSource,
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
        });
        
    } catch (error) {
        console.warn('Error sending state:', error);
    }
}

// State management
let currentSliceDepth = 50;
let currentView = 'x+';
let currentZoom = 0.0;
let currentRenderMode = 'filled';
let currentRepresentationMode = 'single';
let currentMoveCamera = "none";
let currentPrintView = false;
let currentOutputDevice = 'monarch_hid';
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
let currentModel = "none";
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

// Tracking variables
let serverConnected = null;       // null = unknown, true = up, false = confirmed down
let lastPolledView = null;        // last cube_value received from server
let lastModelListSignature = '';  // prevents redundant dropdown rebuilds
let currentBBoxDimensionsText = '';
let lastAnnouncementMessage = '';
let lastAnnouncedParameterKey = null;
let pendingInputSource = 'keyboard'; // consumed once per sendStateToServer call
let modelLoadAnnouncement = null;
let modelLoadAnnouncementSeq = 0;

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
    announce(`Starting processing for ${label}.`);
}

function isActiveModelLoadTask(task) {
    return Boolean(task && modelLoadAnnouncement && modelLoadAnnouncement.id === task.id);
}

function clearModelLoadTask(task) {
    if (isActiveModelLoadTask(task)) {
        modelLoadAnnouncement = null;
    }
}

function formatZoomPercent(zoomValue) {
    const percent = Math.round(Number(zoomValue) * 100);
    return `${percent}%`;
}

// Remove "Back" from available views
//const views = ['front', 'left', 'top', 'bottom', 'right', 'back'];
const views = ['y-', 'x-', 'z+', 'z-', 'x+', 'y+'];
const MIN_ZOOM = 0.0;
const MAX_ZOOM = Number.POSITIVE_INFINITY;
const ZOOM_STEP = 0.1;
const FINE_ZOOM_STEP = 0.01;
//const views = ['front', 'side', 'top'];

const VIEW_FORWARD_VECTORS = {
    'x+': [1, 0, 0],
    'x-': [-1, 0, 0],
    'y+': [0, 1, 0],
    'y-': [0, -1, 0],
    'z+': [0, 0, 1],
    'z-': [0, 0, -1],
};

const CANONICAL_UP_FOR_VIEW = {
    'x+': [0, 0, 1],
    'x-': [0, 0, 1],
    'y+': [0, 0, 1],
    'y-': [0, 0, 1],
    'z+': [0, 1, 0],
    'z-': [0, 1, 0],
};

let orientationForward = [...VIEW_FORWARD_VECTORS['x+']];
let orientationUp = [...CANONICAL_UP_FOR_VIEW['x+']];

function dotVec3(a, b) {
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

function crossVec3(a, b) {
    return [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ];
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

function rotateVectorByAxis90(vector, axis, quarterTurns) {
    const turns = ((quarterTurns % 4) + 4) % 4;
    if (turns === 0) return [...vector];
    let out = [...vector];
    const k = normalizeAxisVector(axis);
    for (let i = 0; i < turns; i += 1) {
        const projection = dotVec3(k, out);
        const cross = crossVec3(k, out);
        out = [
            k[0] * projection + cross[0],
            k[1] * projection + cross[1],
            k[2] * projection + cross[2],
        ];
    }
    return normalizeAxisVector(out);
}

function orientationViewFromForward(forwardVector) {
    for (const [viewToken, vec] of Object.entries(VIEW_FORWARD_VECTORS)) {
        if (dotVec3(vec, forwardVector) === 1) {
            return viewToken;
        }
    }
    return 'x+';
}

function setOrientationFromView(viewToken) {
    orientationForward = [...(VIEW_FORWARD_VECTORS[viewToken] || VIEW_FORWARD_VECTORS['x+'])];
    orientationUp = [...(CANONICAL_UP_FOR_VIEW[viewToken] || CANONICAL_UP_FOR_VIEW['x+'])];
}

function applyRelativeRotation(kind, direction, announcementText) {
    // direction: +1 or -1 indicates ±90 degree about the local axis.
    let rotationAxis;
    if (kind === 'yaw') {
        rotationAxis = orientationUp;
    } else if (kind === 'pitch') {
        rotationAxis = normalizeAxisVector(crossVec3(orientationUp, orientationForward));
    } else if (kind === 'roll') {
        rotationAxis = orientationForward;
    } else {
        return;
    }

    orientationForward = rotateVectorByAxis90(orientationForward, rotationAxis, direction);
    orientationUp = rotateVectorByAxis90(orientationUp, rotationAxis, direction);

    const newView = orientationViewFromForward(orientationForward);
    updateView(newView, false, { syncOrientation: false });
    announce(announcementText);
}

function getOrientationPayload() {
    const forward = normalizeAxisVector(orientationForward);
    const up = normalizeAxisVector(orientationUp);
    const right = normalizeAxisVector(crossVec3(up, forward));
    return {
        scheme: 'basis-v1',
        forward,
        up,
        right,
    };
}

const axisInfo = {
    'Front': 'X-axis: left-right, Y-axis: up-down, Z-axis: forward-back (viewing from front)',
    'Left': 'Z-axis: left-right, Y-axis: up-down, X-axis: back-forward (viewing from left side)',
    'Right': 'Z-axis: right-left, Y-axis: up-down, X-axis: forward-back (viewing from right side)',
    'Top': 'X-axis: left-right, Z-axis: up-down, Y-axis: forward-back (viewing from above)',
    'Bottom': 'X-axis: left-right, Z-axis: down-up, Y-axis: back-forward (viewing from below)'
};

// DOM elements
const sliceSlider = document.getElementById('slice-depth-slider');
const slicePercentage = document.getElementById('slice-percentage');
const currentViewSpan = document.getElementById('current-view');
const currentSliceDepthInfo = document.getElementById('current-slice-depth-info');
const currentRenderModeInfo = document.getElementById('current-render-mode-info');
const currentZoomInfo = document.getElementById('current-zoom-info');
const currentBBoxDimensionsInfo = document.getElementById('current-bbox-dimensions-info');
const announcementHistory = document.getElementById('announcement-history');
const clearAnnouncementsBtn = document.getElementById('clear-announcements-btn');
const deeperBtn = document.getElementById('deeper-btn');
const shallowerBtn = document.getElementById('shallower-btn');
const zoomInput = document.getElementById('zoom-input');
const zoomLevelValue = document.getElementById('zoom-level-value');
const zoomOutBtn = document.getElementById('zoom-out-btn');
const zoomInBtn = document.getElementById('zoom-in-btn');
const sliceGraphLockBtn = document.getElementById('slice-graph-lock-btn');
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

// New radio group references
const renderModeRadios = () => document.querySelectorAll('input[name="render-mode"]');
const viewModeRadios = () => document.querySelectorAll('input[name="view-mode"]');
const viewRadios = () => document.querySelectorAll('input[name="view-select"]');
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

// Toast / live-region elements
const announcementToast = document.getElementById('announcement-toast');
// Two politeness tiers, each a two-slot swap. Toggling which element in a tier
// receives text guarantees AT sees a fresh DOM mutation for every announcement,
// including consecutive identical messages, without any clear+setTimeout race.
const srLiveTiers = {
    polite: [
        document.getElementById('sr-live-polite-a'),
        document.getElementById('sr-live-polite-b'),
    ],
    assertive: [
        document.getElementById('sr-live-assertive-a'),
        document.getElementById('sr-live-assertive-b'),
    ],
};
const srLiveActiveSlot = { polite: 0, assertive: 0 };
const toastDurationSlider = document.getElementById('toast-duration-slider');
const toastDurationValue = document.getElementById('toast-duration-value');
let toastDurationSec = 3;  // default 3 seconds; 0 = off
let toastTimer = null;

// Toast duration slider handler
if (toastDurationSlider) {
    toastDurationSlider.addEventListener('input', function() {
        toastDurationSec = parseFloat(this.value);
        if (toastDurationValue) toastDurationValue.textContent = toastDurationSec === 0 ? 'off' : toastDurationSec + 's';
        this.setAttribute('aria-valuenow', toastDurationSec);
        this.setAttribute('aria-valuetext', toastDurationSec === 0 ? 'off' : toastDurationSec + ' seconds');
    });
}

/** Update the top status bar to reflect current state. */
function refreshStatusBar() {
    if (sbView) sbView.textContent = currentView;
    if (sbDepth) sbDepth.textContent = currentSliceDepth + '%';
    if (sbRenderMode) sbRenderMode.textContent = renderModeLabel();
    if (sbZoom) sbZoom.textContent = Number(currentZoom).toFixed(1);
    if (sbViewMode) sbViewMode.textContent = representationModeLabel();
}

/** Show a brief on-screen toast and push text to the SR live region for a tier. */
function showToast(message, politeness = 'polite') {
    // Two-slot swap within the chosen tier: write to the next slot and clear the
    // previous one. AT always sees a genuine new-content mutation regardless of
    // whether the message is identical to the last one, and there is no setTimeout
    // race to lose when keys are pressed in rapid succession.
    const tier = srLiveTiers[politeness] ? politeness : 'polite';
    const slots = srLiveTiers[tier];
    srLiveActiveSlot[tier] = 1 - srLiveActiveSlot[tier];
    const activeEl = slots[srLiveActiveSlot[tier]];
    const idleEl   = slots[1 - srLiveActiveSlot[tier]];
    if (activeEl) activeEl.textContent = message;
    if (idleEl)   idleEl.textContent   = '';

    // Visual toast: respect user-chosen duration.
    if (!announcementToast || toastDurationSec <= 0) return;
    announcementToast.textContent = message;
    announcementToast.classList.add('visible');
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
        announcementToast.classList.remove('visible');
    }, toastDurationSec * 1000);
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

// Zoom and depth changes are announced on a trailing debounce that accumulates:
// the first press of a burst anchors `from`, later presses only move `to`, and
// one announcement fires once the burst settles. Holding a key, and the 1%-step
// zoom that would otherwise announce ten times across a range, collapse to a
// single "from <start> to <final>". The render and the braille display are NOT
// debounced — those update on every press so tactile feedback stays immediate;
// only the spoken summary waits.
const PARAMETER_SETTLE_MS = 150;
const pendingParameterAnnouncements = {};

function scheduleAccumulatedAnnouncement(key, from, to, emit) {
    let pending = pendingParameterAnnouncements[key];
    if (!pending) {
        pending = pendingParameterAnnouncements[key] = { timer: null, from: null, to: null };
    }
    if (pending.timer === null) {
        pending.from = from; // anchor once, at the start of the burst
    } else {
        clearTimeout(pending.timer);
    }
    pending.to = to;
    pending.timer = setTimeout(() => {
        pending.timer = null;
        emit(pending.from, pending.to);
    }, PARAMETER_SETTLE_MS);
}

// Build and speak a settled depth change. The first time depth is announced the
// phrase carries its label; an immediately following depth change drops to just
// the new value (see announceParameterValue). A burst that changed nothing still
// speaks, since silence reads as a dropped key.
function emitDepthAnnouncement(from, to) {
    const toToken = depthToken(to);
    if (from === null || from === to) {
        // "surface"/"full depth" are self-identifying; a mid-range no-op keeps the label.
        const text = to === 0 || to === 100 ? toToken : `depth ${toToken}`;
        announceParameterValue('slice-depth', text, text);
        return;
    }
    const arrow = `${from}% to ${toToken}`;
    announceParameterValue('slice-depth', `depth from ${arrow}`, toToken);
}

function announceDepthValue(depthValue, previousDepth = null) {
    const to = clampDepth(depthValue);
    if (to === null) return;
    const from = previousDepth === null || previousDepth === undefined ? null : clampDepth(previousDepth);
    scheduleAccumulatedAnnouncement('slice-depth', from, to, emitDepthAnnouncement);
}

// Zoom, same pattern. Zoom has no upper limit, so the only boundary is minimum.
function emitZoomAnnouncement(from, to) {
    const toPercent = formatZoomPercent(to);
    if (from === null || !Number.isFinite(from) || from === to) {
        const text = to <= MIN_ZOOM ? 'minimum zoom' : `zoom ${toPercent}`;
        announceParameterValue('zoom-level', text, text);
        return;
    }
    const arrow = `${formatZoomPercent(from)} to ${toPercent}`;
    announceParameterValue('zoom-level', `zooming from ${arrow}`, toPercent);
}

function announceZoomValue(zoomValue, previousZoom = null) {
    const to = Number(zoomValue);
    if (!Number.isFinite(to)) return;
    const from = previousZoom === null || previousZoom === undefined ? null : Number(previousZoom);
    scheduleAccumulatedAnnouncement('zoom-level', from, to, emitZoomAnnouncement);
}

// Speak firstText the first time this parameter is announced, then repeatText on
// an immediately following announcement of the SAME parameter. Any other
// announcement in between resets the run, so the fuller phrasing returns once the
// context is no longer obvious. Only zoom and depth use this; everything else
// calls announce() directly.
function announceParameterValue(parameterKey, firstText, repeatText) {
    const normalizedKey = String(parameterKey || '').trim().toLowerCase();
    const useFirst = normalizedKey === '' || normalizedKey !== lastAnnouncedParameterKey;
    const message = useFirst ? String(firstText) : String(repeatText);

    // announce() clears lastAnnouncedParameterKey; re-establish this parameter's
    // key afterwards so a run of the SAME parameter still drops to the arrow.
    announce(message);
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
    deeperBtn.textContent = `Deeper: Currently ${depthText}`;
    deeperBtn.setAttribute('aria-label', `Go deeper. Current depth: ${depthText}. Will increase to ${Math.min(100, currentSliceDepth + 10)}%`);
    shallowerBtn.textContent = `Shallower: Currently ${depthText}`;
    shallowerBtn.setAttribute('aria-label', `Go shallower. Current depth: ${depthText}. Will decrease to ${Math.max(0, currentSliceDepth - 10)}%`);
}

function updateSliceGraphLockUI() {
    const isSliceGraphMode = isSliceGraphRepresentationMode();
    sliceGraphRefreshBtn.disabled = !isSliceGraphMode;
    if (sliceGraphLocked) {
        sliceGraphLockBtn.textContent = 'Slice Graph Lock: On';
        sliceGraphLockBtn.setAttribute('aria-pressed', 'true');
        if (isSliceGraphMode) {
            sliceGraphLockStatus.textContent = `Graph lock is ON. Frozen at view ${sliceGraphAnchorView}, depth ${sliceGraphAnchorDepth}%.`;
        } else {
            sliceGraphLockStatus.textContent = 'Graph lock is ON (default). Switch to Slice Graph mode to refresh.';
        }
    } else {
        sliceGraphLockBtn.textContent = 'Slice Graph Lock: Off';
        sliceGraphLockBtn.setAttribute('aria-pressed', 'false');
        if (isSliceGraphMode) {
            sliceGraphLockStatus.textContent = 'Graph lock is OFF. Graph follows your current location.';
        } else {
            sliceGraphLockStatus.textContent = 'Graph lock is OFF. Switch to Slice Graph mode to use refresh.';
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
    sliceGraphModeBtn.setAttribute('aria-pressed', isColumnCountMode ? 'true' : 'false');
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

function toggleSliceGraphLock() {
    sliceGraphLocked = !sliceGraphLocked;
    if (sliceGraphLocked) {
        // When turning lock back on, freeze at the current exploration point.
        captureSliceGraphAnchor(false);
    }
    updateSliceGraphLockUI();
    sendStateToServer();
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
    debugPipelineToggleBtn.setAttribute('aria-expanded', String(isVisible));
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

    const width = shape && shape.length > 1 ? shape[1] : '--';
    const height = shape && shape.length > 0 ? shape[0] : '--';
    highFidelityPreviewMeta.textContent = `${currentView} · ${currentSliceDepth}% · ${renderModeLabel()} · ${height}×${width}px`;
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
    sliceSlider.value = currentSliceDepth;
    slicePercentage.textContent = currentSliceDepth;
    refreshViewInfoSummary();

    // Only mutate ARIA attributes, button labels, and trigger a render when the
    // value actually changed.
    if (oldDepth !== currentSliceDepth) {
        sliceSlider.setAttribute('aria-valuenow', currentSliceDepth);
        // aria-valuetext is announced by NVDA on every mutation of a range input
        // regardless of focus. When shouldAnnounce=false the caller (a keyboard
        // shortcut handler) will push the announcement through the assertive live
        // region instead, so we must NOT mutate aria-valuetext here — doing so
        // would cause a second, racing announcement on NVDA/JAWS.
        // When shouldAnnounce=true (slider focused, hardware input) the mutation
        // IS the correct announcement channel, so we set it as before.
        if (shouldAnnounce) {
            sliceSlider.setAttribute('aria-valuetext', `${currentSliceDepth} percent depth`);
        }
        updateButtonLabels();
        sendStateToServer();
    }

    return oldDepth !== currentSliceDepth;
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
    syncRadioGroup(viewRadios(), currentView, 'view-select');
    syncRadioGroup(outputDeviceRadios(), currentOutputDevice, 'output-device');
}

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
    }
    if (currentViewSpan) currentViewSpan.textContent = currentView;
    refreshViewInfoSummary();
    updateButtonLabels();
    updateSideBySideAxisLabels();
    syncRadios();
    if (oldView !== currentView && shouldAnnounce) {
        announce(`${currentView.toLowerCase()} view`);
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

// Update the tactile display preview image
function updateTactilePreview(base64, shape) {
    const img = document.getElementById('tactile-display-img');
    const meta = document.getElementById('tactile-preview-meta');
    img.src = 'data:image/png;base64,' + base64;
    img.alt = `Tactile display: ${currentView} view, ${currentSliceDepth}% depth, ${renderModeLabel()}`;
    if (shape) {
        meta.textContent = `${currentView} \u00b7 ${currentSliceDepth}% \u00b7 ${renderModeLabel()} \u00b7 ${shape[1]}\u00d7${shape[0]}px`;
    } else {
        meta.textContent = `${currentView} \u00b7 ${currentSliceDepth}% \u00b7 ${renderModeLabel()}`;
    }
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

    const dropdown = document.getElementById("model-list-dropdown");
    const entries = _visibleModelEntries(model_list);
    // Signature is over the visible stems only so filter changes force a rebuild.
    const signature = entries.map(e => e.stem).join('||');

    if (signature === lastModelListSignature && dropdown.options.length > 0) {
        // Same visible set — restore selection using original server index stored in option.value.
        const currentModelIndex = Number(currentModel);
        const hasCurrentOption = [...dropdown.options].some(o => o.value === String(currentModelIndex));
        if (hasCurrentOption) dropdown.value = String(currentModelIndex);
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
        // option.value carries the ORIGINAL server index so currentModel round-trips correctly.
        option.value = i;
        const ownedFile = [...sessionOwnedModels].find(fn => fn.replace(/\.[^.]+$/, '') === stem);
        if (ownedFile) {
            option.text = stem + ' (your upload)';
            option.dataset.ownedFilename = ownedFile;
        } else {
            option.text = stem;
        }
        dropdown.appendChild(option);
    });

    const currentModelIndex = Number(currentModel);
    const hasCurrentOption = [...dropdown.options].some(o => o.value === String(currentModelIndex));
    if (hasCurrentOption) {
        dropdown.value = String(currentModelIndex);
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
            dropdown.value = String(data.new_model_index);
            currentModel = String(data.new_model_index);
            clearCameraCenterState();
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
let lastSliderRaw = null; // null = never received a server-side slider value yet
function applyServerState(data) {
    if (data.cube_value !== undefined && data.cube_value !== lastPolledView) {
        lastPolledView = data.cube_value;
        pendingInputSource = 'cube';
        updateView(data.cube_value);
    }
    if (data.slider_value !== undefined) {
        const rawValue = data.slider_value;
        if (lastSliderRaw === null) {
            // First reading — record but skip to avoid jumping depth to the
            // server default (0) before the slider hardware has been moved.
            lastSliderRaw = rawValue;
        } else if (rawValue !== lastSliderRaw) {
            lastSliderRaw = rawValue;
            const newDepth = Math.round(Math.max(0, Math.min(100, (rawValue / 65535) * 100)));
            pendingInputSource = 'slider';
            updateSliceDepth(newDepth, false);
        }
    }
    if (data.builtin_model_stems && !builtinModelStems) {
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
    zoomInput.setAttribute('aria-valuetext', `zoom ${formatZoomPercent(currentZoom)}`);

    updateButtonLabels();

    if (shouldAnnounce) {
        announceZoomValue(currentZoom, oldZoom);
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
    if (shouldAnnounce) announce(`${renderModeLabel(previousMode)} to ${renderModeLabel()}`);

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

// Announce a change: adds to visible history, shows toast, and speaks via SR live region.
function emitAnnouncement(message, politeness, isAlert) {
    const normalizedMessage = String(message);

    // Any announcement ends the current zoom/depth run: the next such change
    // re-includes its label, since the context is no longer obvious. A parameter
    // announcement re-establishes its own key immediately after this returns
    // (see announceParameterValue), so its own repeat behaviour is unaffected.
    lastAnnouncedParameterKey = null;

    // Always refresh the status bar so it reflects the latest state.
    refreshStatusBar();

    // Show the toast + push to the screen-reader live region for this tier.
    showToast(normalizedMessage, politeness);

    // Append to visible history log; alerts are weighted so the log distinguishes
    // an interrupting condition from routine state changes.
    if (announcementHistory) {
        if (normalizedMessage !== lastAnnouncementMessage) {
            const item = document.createElement('li');
            if (isAlert) item.style.fontWeight = '600';

            const time = new Date();
            const timestamp = document.createElement('span');
            timestamp.className = 'announcement-time';
            timestamp.textContent = `[${time.toLocaleTimeString()}]`;

            const text = document.createElement('span');
            text.textContent = message;

            item.appendChild(timestamp);
            item.appendChild(text);
            announcementHistory.appendChild(item);
            announcementHistory.scrollTop = announcementHistory.scrollHeight;
        }
    }

    lastAnnouncementMessage = normalizedMessage;
}

/**
 * Announce a state change politely: it waits for the user to pause rather than
 * interrupting. This is the right default for anything the user just did, since
 * they already know it happened and only need the result.
 */
function announce(message) {
    emitAnnouncement(message, 'polite', false);
}

/**
 * Announce assertively, interrupting whatever the AT is currently speaking.
 * Reserve for conditions the user did not initiate and must act on: a lost
 * server or device, a failed render, upload, or export.
 */
function announceAlert(message) {
    emitAnnouncement(message, 'assertive', true);
}

if (clearAnnouncementsBtn && announcementHistory) {
    clearAnnouncementsBtn.addEventListener('click', function() {
        announcementHistory.innerHTML = '';
    });
}

// Event listeners

// Slice depth slider with enhanced feedback
let sliderUpdateTimeout = null;

sliceSlider.addEventListener('input', function() {
    const newValue = parseInt(this.value);
    currentSliceDepth = newValue;
    slicePercentage.textContent = currentSliceDepth;
    
    // Update ARIA attributes immediately
    this.setAttribute('aria-valuenow', currentSliceDepth);
    this.setAttribute('aria-valuetext', `${currentSliceDepth} percent depth`);
    
    // Update button labels immediately
    updateButtonLabels();
});

sliceSlider.addEventListener('change', function() {
    clearTimeout(sliderUpdateTimeout);
    pendingInputSource = 'ui';
    sendStateToServer();
});

// Sync aria-valuetext when the slider receives focus so it reflects any depth
// changes made via keyboard shortcuts while focus was elsewhere.
sliceSlider.addEventListener('focus', function() {
    this.setAttribute('aria-valuenow', currentSliceDepth);
    this.setAttribute('aria-valuetext', `${currentSliceDepth} percent depth`);
});

// Keyboard support for slider
sliceSlider.addEventListener('keydown', function(e) {
    let newValue = currentSliceDepth;
    
    switch(e.key) {
        case 'ArrowUp':
        case 'ArrowRight':
            newValue += 1;
            break;
        case 'ArrowDown':
        case 'ArrowLeft':
            newValue -= 1;
            break;
        case 'PageUp':
            newValue += 10;
            break;
        case 'PageDown':
            newValue -= 10;
            break;
        case 'Home':
            newValue = 0;
            break;
        case 'End':
            newValue = 100;
            break;
        default:
            return; // Don't prevent default for other keys
    }
    
    e.preventDefault();
    updateSliceDepth(newValue, true);
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

// View selection radios
document.addEventListener('change', function(e) {
    if (e.target && e.target.matches('input[name="view-select"]')) {
        if (e.target.checked) {
            pendingInputSource = 'ui';
            updateView(e.target.value);
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
        announce('zoom value unchanged');
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

sliceGraphLockBtn.addEventListener('click', function() {
    toggleSliceGraphLock();
});

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
        code === 'Digit6' || code === 'Numpad6' ? '6' :
        code === 'Digit7' || code === 'Numpad7' ? '7' :
        key
    );
    const supportedShortcuts = new Set([
        'arrowup', 'arrowdown', 'pageup', 'pagedown',
         '2', '3', 'q', 'e',
        'u', 'i', 'o', 'j', 'k', 'l',
        '4', '5', '6', '7', '8', '9', '0', '-', '=',
        'r', 't', 'g', 'v', 'z',
        'w', 'a', 's', 'd', '[', ']', 'h', 'p', '.', 'escape'
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
        '4', '5', 
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
            
        // View shortcuts
        case '7':
            e.preventDefault();
            if (updateView('x-', false)) {
                announce('View changed: x-');
            } else {
                announce('View unchanged: x-');
            }
            break;
        case '8':
            e.preventDefault();
            if (updateView('x+', false)) {
                announce('View changed: x+');
            } else {
                announce('View unchanged: x+');
            }
            break;
        case '9':
            e.preventDefault();
            if (updateView('z+', false)) {
                announce('View changed: z+');
            } else {
                announce('View unchanged: z+');
            }
            break;
        case '0':
            e.preventDefault();
            if (updateView('z-', false)) {
                announce('View changed: z-');
            } else {
                announce('View unchanged: z-');
            }
            break;
        case '-':
            e.preventDefault();
            if (updateView('y-', false)) {
                announce('View changed: y-');
            } else {
                announce('View unchanged: y-');
            }
            break;
        case '=':
            e.preventDefault();
            if (updateView('y+', false)) {
                announce('View changed: y+');
            } else {
                announce('View unchanged: y+');
            }
            break;

        case 'r':
            e.preventDefault();
            {
                const previousMode = currentRenderMode;
                cycleRenderMode(false);
                announce(`Render mode changed: ${renderModeLabel(previousMode)} to ${renderModeLabel()}`);
            }
            break;

        case 't':
            e.preventDefault();
            {
                const previousViewMode = currentRepresentationMode;
                cycleRepresentationMode(false);
                announce(`Display mode changed: ${representationModeLabel(previousViewMode)} to ${representationModeLabel()}`);
            }
            break;

        case 'u':
            e.preventDefault();
            // Roll counterclockwise around current view direction.
            applyRelativeRotation('roll', 1, 'Roll counterclockwise');
            break;

        case 'o':
            e.preventDefault();
            // Roll clockwise around current view direction.
            applyRelativeRotation('roll', -1, 'Roll clockwise');
            break;

        case 'i':
            e.preventDefault();
            applyRelativeRotation('pitch', -1, 'Rotate up');
            break;

        case 'k':
            e.preventDefault();
            applyRelativeRotation('pitch', 1, 'Rotate down');
            break;

        case 'j':
            e.preventDefault();
            applyRelativeRotation('yaw', -1, 'Rotate left');
            break;

        case 'l':
            e.preventDefault();
            applyRelativeRotation('yaw', 1, 'Rotate right');
            break;

        case '.':
            // Read the full top-of-page status bar.
            e.preventDefault();
            announce(getStatusBarAnnouncement());
            break;

        case 'g':
            e.preventDefault();
            if (!isSliceGraphRepresentationMode()) {
                announce('Slice graph refresh: not in slice-graph mode');
                break;
            }
            captureSliceGraphAnchor(true);
            sendStateToServer();
            announce(`Slice graph refreshed: view ${sliceGraphAnchorView}, depth ${sliceGraphAnchorDepth}%`);
            break;

        case 'v':
            e.preventDefault();
            if (!isSliceGraphRepresentationMode()) {
                announce('Slice graph lock: not in slice-graph mode');
                break;
            }
            toggleSliceGraphLock();
            announce(`Slice graph lock ${sliceGraphLocked ? 'on' : 'off'}`);
            break;

        //case '0':
        //    // Jump to 0% depth (surface)
        //    e.preventDefault();
        //    updateSliceDepth(0, true);
        //    break;
        //
        case 'w':
            currentMoveCamera = "up";
            sendStateToServer();
            currentMoveCamera = "none";
            announce('Object panned up');
            break;
        case 'd':
            currentMoveCamera = "right";
            sendStateToServer();
            currentMoveCamera = "none";
            announce('Object panned right');
            break;
        case 's':
            currentMoveCamera = "down";
            sendStateToServer();
            currentMoveCamera = "none";
            announce('Object panned down');
            break;
        case '[':
            composeScrollbar = !composeScrollbar;
            sendStateToServer();
            announce(`Compose scrollbar ${composeScrollbar ? 'on' : 'off'}`);
            break;
        case ']':
            composeSliceGraph = !composeSliceGraph;
            sendStateToServer();
            announce(`Compose slice graph ${composeSliceGraph ? 'on' : 'off'}`);
            break;
        case 'a':
            currentMoveCamera = "left";
            sendStateToServer();
            currentMoveCamera = "none";
            announce('Object panned left');
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
            announce('Focus cleared');
            break;

        case 'h':
            e.preventDefault();
            {
                const shortcutsHeading = document.getElementById('shortcuts-heading');
                if (shortcutsHeading) {
                    shortcutsHeading.focus();
                }
            }
            break;

        case 'p':
            announce('Printing current render');
            print_view();
            break;

        case 'z':
            e.preventDefault();
            currentMoveCamera = "reset";
            sendStateToServer();
            currentMoveCamera = "none";
            announce('Position reset');
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
document.addEventListener('DOMContentLoaded', function() {
    // Move focus to the top element (page title) on load.
    focusTopOfPage();
    
    // Set initial values
    updateSliceDepth(50, false);
    updateView('x+');
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
