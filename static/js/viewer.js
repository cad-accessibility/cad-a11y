// Configuration
const SERVER_URL = window.location.origin;

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

// Function to send current state to the server
async function sendStateToServer() {
    try {
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
        const state = {
            view: currentView,
            zoom: currentZoom,
            depth: currentSliceDepth,
            renderMode: currentRenderMode,
            projectionMode: currentProjectionMode,
            mode: currentRepresentationMode,
            move_camera_center: currentMoveCamera,
            print_view: currentPrintView,
            current_model: currentModel,
            compose_scrollbar: composeScrollbar,
            compose_slicegraph: composeSliceGraph,
            show_view_info_box: showViewInfoBox,
            output_device: currentOutputDevice,
            slicegraph_locked: sliceGraphLocked,
            slicegraph_view: requestedGraphView,
            slicegraph_depth: requestedGraphDepth,
            input_source: pendingInputSource,
        };
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
            // Update tactile display preview
            if (data.image_base64) {
                updateTactilePreview(data.image_base64, data.image_shape);
            }
            renderPipelineDebug(data.debug_pipeline);
            updateHighFidelityPreview(data);

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
        });
        
    } catch (error) {
        console.warn('Error sending state:', error);
    }
}

// State management
let currentSliceDepth = 50;
let currentView = 'x+';
let currentZoom = 0.0;
let currentRenderMode = 'Shaded';
let currentProjectionMode = 'orthographic';
let currentRepresentationMode = 'single';
let currentMoveCamera = "none";
let currentPrintView = false;
let currentOutputDevice = 'monarch_hid';
const renderModes = ['Shaded', 'Outline', 'Cut'];
const representationModes = ['single', 'side-by-side', 'slice-graph'];
let currentModel = "none";
let composeScrollbar = true;
let composeSliceGraph = false;
let showViewInfoBox = false;
let sliceGraphLocked = true;
let sliceGraphAnchorView = 'y-';
let sliceGraphAnchorDepth = 50;

// Tracking variables
let serverConnected = null;       // null = unknown, true = up, false = confirmed down
let lastPolledView = null;        // last cube_value received from server
let lastModelListSignature = '';  // prevents redundant dropdown rebuilds
let currentBBoxDimensionsText = '';
let lastAnnouncementMessage = '';
let pendingInputSource = 'keyboard'; // consumed once per sendStateToServer call

// Remove "Back" from available views
//const views = ['front', 'left', 'top', 'bottom', 'right', 'back'];
const views = ['y-', 'x-', 'z+', 'z-', 'x+', 'y+'];
const MIN_ZOOM = 0.0;
const MAX_ZOOM = 3.0;
const ZOOM_STEP = 0.1;
//const views = ['front', 'side', 'top'];

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
const zoomSlider = document.getElementById('zoom-slider');
const zoomLevelValue = document.getElementById('zoom-level-value');
const sliceGraphLockBtn = document.getElementById('slice-graph-lock-btn');
const sliceGraphRefreshBtn = document.getElementById('slice-graph-refresh-btn');
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
const projectionModeRadios = () => document.querySelectorAll('input[name="projection-mode"]');
const viewRadios = () => document.querySelectorAll('input[name="view-select"]');
const outputDeviceRadios = () => document.querySelectorAll('input[name="output-device"]');

// Status bar elements
const sbView = document.getElementById('sb-view');
const sbDepth = document.getElementById('sb-depth');
const sbRenderMode = document.getElementById('sb-render-mode');
const sbZoom = document.getElementById('sb-zoom');
const sbViewMode = document.getElementById('sb-view-mode');
const sbModel = document.getElementById('sb-model');
const sbDotPad = document.getElementById('sb-dotpad');

// Toast / live-region elements
const announcementToast = document.getElementById('announcement-toast');
// Two-slot live-region swap: toggling which element receives text each time
// guarantees AT sees a fresh DOM mutation for every announcement, including
// consecutive identical messages, without any clear+setTimeout race.
const srLiveSlots = [
    document.getElementById('sr-live-a'),
    document.getElementById('sr-live-b')
];
let srLiveActiveSlot = 0;
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
    if (sbRenderMode) sbRenderMode.textContent = currentRenderMode;
    if (sbZoom) sbZoom.textContent = Number(currentZoom).toFixed(1);
    if (sbViewMode) sbViewMode.textContent = currentRepresentationMode;
}

/** Show a brief on-screen toast and push text to the SR live region. */
function showToast(message) {
    // Two-slot swap: write to the next slot and clear the previous one.
    // AT always sees a genuine new-content mutation regardless of whether the
    // message is identical to the last one, and there is no setTimeout race to
    // lose when keys are pressed in rapid succession.
    srLiveActiveSlot = 1 - srLiveActiveSlot;
    const activeEl = srLiveSlots[srLiveActiveSlot];
    const idleEl   = srLiveSlots[1 - srLiveActiveSlot];
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

// Consistent depth announcement formatter
function depthAnnouncement(pct) {
    if (pct === 0) return 'surface';
    if (pct === 100) return 'full depth';
    return `depth ${pct}%`;
}

function refreshViewInfoSummary() {
    if (currentSliceDepthInfo) {
        currentSliceDepthInfo.textContent = `${currentSliceDepth}%`;
    }
    if (currentRenderModeInfo) {
        currentRenderModeInfo.textContent = currentRenderMode;
    }
    if (currentZoomInfo) {
        currentZoomInfo.textContent = Number(currentZoom).toFixed(1);
    }
    if (currentBBoxDimensionsInfo) {
        currentBBoxDimensionsInfo.textContent = currentBBoxDimensionsText;
    }
    refreshStatusBar();
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
    const isSliceGraphMode = currentRepresentationMode === 'slice-graph';
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

function captureSliceGraphAnchor(shouldAnnounce = true) {
    sliceGraphAnchorView = currentView;
    sliceGraphAnchorDepth = currentSliceDepth;
    updateSliceGraphLockUI();
}

function autoRefreshSliceGraph(options = {}) {
    const { updateAnchor = false } = options;
    if (currentRepresentationMode !== 'slice-graph') {
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
    let isVisible = true;
    try {
        const saved = window.localStorage.getItem(DEBUG_PIPELINE_VISIBILITY_KEY);
        if (saved === '0') {
            isVisible = false;
        }
    } catch (_) {
        // Keep default visible if persistence is unavailable.
    }
    setDebugPipelineVisible(isVisible);
}

function renderPipelineDebug(debugPipeline) {
    if (!debugPipelineSummary || !debugStageList) {
        return;
    }

    const stages = Array.isArray(debugPipeline && debugPipeline.stages) ? debugPipeline.stages : [];
    if (stages.length === 0) {
        debugPipelineSummary.textContent = 'No stage data returned by server.';
        debugStageList.innerHTML = '';
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
    return {
        view: currentView,
        zoom: currentZoom,
        depth: currentSliceDepth,
        renderMode: currentRenderMode,
        mode: currentRepresentationMode,
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
    highFidelityPreviewImg.alt = `Render preview: ${currentView} view, ${currentSliceDepth}% depth, ${currentRenderMode}`;

    const width = shape && shape.length > 1 ? shape[1] : '--';
    const height = shape && shape.length > 0 ? shape[0] : '--';
    highFidelityPreviewMeta.textContent = `${currentView} · ${currentSliceDepth}% · ${currentRenderMode} · ${height}×${width}px`;
}

async function exportCurrentSliceAsPng() {
    try {
        exportSliceSvgBtn.disabled = true;
        announceStatus('rendering high-fidelity export');

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
        const filename = `slice_${sanitizedView}_${currentSliceDepth}_${currentRenderMode.toLowerCase()}.png`;

        const link = document.createElement('a');
        link.href = downloadUrl;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);

        announceStatus('slice exported as png');
    } catch (error) {
        console.warn('Failed to export slice as PNG:', error);
        announceStatus('High-fidelity export failed');
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

// Helper to sync radios with current state
function syncRadios() {
    renderModeRadios().forEach(r => { r.checked = (r.value === currentRenderMode); });
    viewModeRadios().forEach(r => { r.checked = (r.value === currentRepresentationMode); });
    projectionModeRadios().forEach(r => { r.checked = (r.value === currentProjectionMode); });
    viewRadios().forEach(r => { r.checked = (r.value === currentView); });
    outputDeviceRadios().forEach(r => { r.checked = (r.value === currentOutputDevice); });
}

function switchOutputDevice(targetDevice) {
    if (currentOutputDevice === targetDevice) {
        announceStatus(`already using ${targetDevice}`);
        return;
    }

    currentOutputDevice = targetDevice;
    syncRadios();
    announceStatus(`output device ${targetDevice}`);
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
        case 'slice-graph':
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
function updateView(newView, shouldAnnounce = true) {
    const oldView = currentView;
    currentView = newView;
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
        if (currentRepresentationMode === 'slice-graph') {
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
    img.alt = `Tactile display: ${currentView} view, ${currentSliceDepth}% depth, ${currentRenderMode}`;
    if (shape) {
        meta.textContent = `${currentView} \u00b7 ${currentSliceDepth}% \u00b7 ${currentRenderMode} \u00b7 ${shape[1]}\u00d7${shape[0]}px`;
    } else {
        meta.textContent = `${currentView} \u00b7 ${currentSliceDepth}% \u00b7 ${currentRenderMode}`;
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

function updateModelList(model_list) {
    const dropdown = document.getElementById("model-list-dropdown");

    if (!Array.isArray(model_list)) {
        return;
    }

    const signature = model_list.join('||');
    if (signature === lastModelListSignature && dropdown.options.length > 0) {
        return;
    }

    lastModelListSignature = signature;
    dropdown.innerHTML = '';

    if (model_list.length === 0) {
        dropdown.innerHTML = '<option value="" selected>No models found</option>';
        dropdown.disabled = true;
        return;
    }

    dropdown.disabled = false;

    model_list.forEach((item, i) => {
        let option = document.createElement("option");
        option.value = i;
        option.text = item;
        dropdown.appendChild(option);
    });

    const currentModelIndex = Number(currentModel);
    if (Number.isInteger(currentModelIndex) && currentModelIndex >= 0 && currentModelIndex < model_list.length) {
        dropdown.value = String(currentModelIndex);
        if (sbModel) sbModel.textContent = model_list[currentModelIndex];
    } else {
        dropdown.selectedIndex = 0;
        currentModel = dropdown.value;
        if (sbModel && model_list.length > 0) sbModel.textContent = model_list[0];
    }
}

document.getElementById("model-list-dropdown").addEventListener("input", function() {
    // Keep local state in sync while keyboard arrows navigate options.
    currentModel = this.value;
});

document.getElementById("model-list-dropdown").addEventListener("change", function() {
    const selectedItem = this.value;
    currentModel = selectedItem;
    pendingInputSource = 'ui';
    if (currentRepresentationMode === 'slice-graph') {
        autoRefreshSliceGraph({ updateAnchor: false });
    } else {
        sendStateToServer();
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

    try {
        const resp = await fetch(`${SERVER_URL}/upload`, {
            method: 'POST',
            body: formData,
            mode: 'cors',
        });
        const data = await resp.json();

        if (data.status === 'success') {
            updateModelList(data.model_list);
            // Select the newly uploaded model
            const dropdown = document.getElementById('model-list-dropdown');
            dropdown.value = String(data.new_model_index);
            currentModel = String(data.new_model_index);
            statusEl.textContent = `✓ ${data.filename} uploaded`;
            announce(`Model ${data.filename} uploaded and selected.`);
            pendingInputSource = 'upload';
            sendStateToServer();
        } else {
            statusEl.textContent = `Upload failed: ${data.message}`;
            announce(`Upload failed: ${data.message}`);
        }
    } catch (err) {
        statusEl.textContent = `Upload error: ${err.message}`;
        announce(`Upload error: ${err.message}`);
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
    const modelDropdown = document.getElementById("model-list-dropdown");
    const dropdownFocused = document.activeElement === modelDropdown;
    if (data.model_list && !dropdownFocused) {
        updateModelList(data.model_list);
    }
    if (data.bbox) {
        updateBoundingBox(data.bbox);
    }
}

// SSE: server pushes hardware state changes (GoDice, Slider) immediately
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
                announce('Server unavailable — rendering paused.');
            }
        });
}, 5000);

// Update zoom information
function updateZoom(newZoom, shouldAnnounce = true, sendToServer = true) {
    const oldZoom = currentZoom;
    currentZoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, Number(newZoom)));
    const zoomText = currentZoom.toFixed(1);
    zoomSlider.value = zoomText;
    zoomLevelValue.textContent = zoomText;
    refreshViewInfoSummary();
    zoomSlider.setAttribute('aria-valuenow', zoomText);
    zoomSlider.setAttribute('aria-valuetext', `zoom level ${zoomText}`);

    updateButtonLabels();

    if (shouldAnnounce) {
        announceStatus(`zoom ${zoomText}`);
    }

    console.log(oldZoom, currentZoom);
    if (sendToServer && oldZoom !== currentZoom) {
        if (currentRepresentationMode === 'slice-graph') {
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
    if (currentRenderMode === targetMode) {
        if (shouldAnnounce) announceStatus(`already ${targetMode.toLowerCase()}`);
        return;
    }
    const previousMode = currentRenderMode;
    currentRenderMode = targetMode;
    refreshViewInfoSummary();
    updateButtonLabels();
    syncRadios();
    if (shouldAnnounce) announce(`${previousMode.toLowerCase()} to ${currentRenderMode.toLowerCase()}`);

    // Send state to server
    sendStateToServer();
    return true;
}

function cycleRenderMode(shouldAnnounce = true) {
    const currentIndex = renderModes.indexOf(currentRenderMode);
    const nextIndex = (currentIndex + 1) % renderModes.length;
    switchToRenderMode(renderModes[nextIndex], shouldAnnounce);
}

function switchToProjectionMode(targetMode) {
    if (currentProjectionMode === targetMode) {
        announceStatus(`already ${targetMode}`);
        return;
    }
    const previousMode = currentProjectionMode;
    currentProjectionMode = targetMode;
    syncRadios();
    announce(`${previousMode} to ${currentProjectionMode} projection`);
    sendStateToServer();
}

function switchToRepresentationMode(targetMode, shouldAnnounce = true) {
    if (currentRepresentationMode === targetMode) {
        if (shouldAnnounce) announceStatus(`already ${targetMode.toLowerCase()}`);
        return;
    }
    const previousMode = currentRepresentationMode;
    currentRepresentationMode = targetMode;
    updateDisplayOptions();
    if (targetMode === 'slice-graph') {
        sliceGraphLocked = true;
        captureSliceGraphAnchor(false);
    }
    updateButtonLabels();
    updateSliceGraphLockUI();
    updateSideBySideAxisLabels();
    syncRadios();
    if (shouldAnnounce) announce(`${previousMode.toLowerCase()} to ${currentRepresentationMode.toLowerCase()}`);

    // Send state to server
    sendStateToServer();
    return true;
}

function cycleRepresentationMode(shouldAnnounce = true) {
    const currentIndex = representationModes.indexOf(currentRepresentationMode);
    const nextIndex = (currentIndex + 1) % representationModes.length;
    switchToRepresentationMode(representationModes[nextIndex], shouldAnnounce);
}

// Announce a change: adds to visible history, shows toast, and speaks via SR live region.
function announce(message, isUrgent = true) {
    const normalizedMessage = String(message);

    // Always refresh the status bar so it reflects the latest state.
    refreshStatusBar();

    // Show the toast + push to screen-reader live region.
    showToast(normalizedMessage);

    // Append to visible history log.
    if (announcementHistory) {
        if (normalizedMessage !== lastAnnouncementMessage) {
            const item = document.createElement('li');
            if (isUrgent) item.style.fontWeight = '600';

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

// Announce status information (same mechanism, just non-urgent weight).
function announceStatus(message) {
    announce(message, false);
}

function announceDepthShortcut(shortcutLabel, previousDepth, depthValue) {
    if (previousDepth === depthValue) {
        announce(`Cut depth unchanged: ${depthValue}%`);
        return;
    }
    const direction = depthValue > previousDepth ? 'deeper' : 'shallower';
    if (depthValue === 0) {
        announce('Cut depth: surface');
    } else if (depthValue === 100) {
        announce('Cut depth: full depth');
    } else {
        announce(`Cut depth ${direction}: ${depthValue}%`);
    }
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

// Projection mode radios
document.addEventListener('change', function(e) {
    if (e.target && e.target.matches('input[name="projection-mode"]')) {
        if (e.target.checked) {
            pendingInputSource = 'ui';
            switchToProjectionMode(e.target.value);
        }
    }
});

// View mode radios (Single, Side-by-side, Slice graph)
document.addEventListener('change', function(e) {
    if (e.target && e.target.matches('input[name="view-mode"]')) {
        if (e.target.checked) {
            pendingInputSource = 'ui';
            switchToRepresentationMode(e.target.value);
            updateDisplayOptions();
            sendStateToServer();
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

// Zoom slider — debounce server sends on drag; commit immediately on release
let zoomDebounceTimer = null;

zoomSlider.addEventListener('input', function() {
    //updateZoom(this.value, false, false); // Update UI immediately, skip server send
    clearTimeout(zoomDebounceTimer);
    zoomDebounceTimer = setTimeout(() => {
        pendingInputSource = 'ui';
        updateZoom(zoomSlider.value, false, true); // Send to server after 150ms idle
    }, 150);
});

zoomSlider.addEventListener('change', function() {
    clearTimeout(zoomDebounceTimer);
    pendingInputSource = 'ui';
    updateZoom(this.value, true, true); // Dragging stopped — send immediately
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
    if (currentRepresentationMode !== 'slice-graph') {
        announceStatus('refresh only available in slice-graph mode');
        return;
    }
    captureSliceGraphAnchor(true);
    pendingInputSource = 'ui';
    sendStateToServer();
});

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

    if (e.repeat) {
        return;
    }
    const rawKey = String(e.key || '');
    const key = rawKey.toLowerCase();
    const code = String(e.code || '');
    const normalizedKey = (
        code === 'Digit1' || code === 'Numpad1' ? '1' :
        code === 'Digit2' || code === 'Numpad2' ? '2' :
        key
    );
    const supportedShortcuts = new Set([
        '1', '2', '4', '5', '7', '8', '9', '0', '-', '=',
        'q', 'e', 'f', 'r', 't', 'l', 'g', 'o', 'c', 'z',
        'w', 'a', 's', 'd', '[', ']', 'i', 'h', 'p', 'escape'
    ]);

    if (!supportedShortcuts.has(normalizedKey)) {
        return;
    }

    switch(normalizedKey) {
        case 'e':
            // Go deeper (increase depth by 1%)
            e.preventDefault();
            {
                const previousDepth = currentSliceDepth;
                const nextDepth = Math.min(100, currentSliceDepth + 1);
                updateSliceDepth(nextDepth, false);
                announceDepthShortcut('E', previousDepth, nextDepth);
            }
            break;
        case 'q':
            // Go shallower (decrease depth by 1%)
            e.preventDefault();
            {
                const previousDepth = currentSliceDepth;
                const nextDepth = Math.max(0, currentSliceDepth - 1);
                updateSliceDepth(nextDepth, false);
                announceDepthShortcut('Q', previousDepth, nextDepth);
            }
            break;
        case '2':
            // Go deeper (increase depth by 10%)
            e.preventDefault();
            {
                const previousDeeperDepth = currentSliceDepth;
                const newDeeperDepth = Math.min(100, currentSliceDepth + 10);
                updateSliceDepth(newDeeperDepth, false);
                announceDepthShortcut('2', previousDeeperDepth, newDeeperDepth);
            }
            break;
            
        case '1':
            // Go shallower (decrease depth by 10%)
            e.preventDefault();
            {
                const previousShallowerDepth = currentSliceDepth;
                const newShallowerDepth = Math.max(0, currentSliceDepth - 10);
                updateSliceDepth(newShallowerDepth, false);
                announceDepthShortcut('1', previousShallowerDepth, newShallowerDepth);
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

        case 'f':
            e.preventDefault();
            if (switchToRenderMode('Shaded', false)) {
                announce('Render mode: shaded');
            } else {
                announce('Render mode unchanged: shaded');
            }
            break;

        case 'r':
            e.preventDefault();
            {
                const previousMode = currentRenderMode;
                cycleRenderMode(false);
                announce(`Render mode changed: ${previousMode.toLowerCase()} to ${currentRenderMode.toLowerCase()}`);
            }
            break;

        case 't':
            e.preventDefault();
            {
                const previousViewMode = currentRepresentationMode;
                cycleRepresentationMode(false);
                announce(`Display mode changed: ${previousViewMode} to ${currentRepresentationMode}`);
            }
            break;

        case 'l':
            e.preventDefault();
            toggleSliceGraphLock();
            announce(`Slice graph lock ${sliceGraphLocked ? 'on' : 'off'}`);
            break;

        case 'g':
            e.preventDefault();
            if (currentRepresentationMode !== 'slice-graph') {
                announce('Slice graph refresh: not in slice-graph mode');
                break;
            }
            captureSliceGraphAnchor(true);
            sendStateToServer();
            announce(`Slice graph refreshed: view ${sliceGraphAnchorView}, depth ${sliceGraphAnchorDepth}%`);
            break;

        case 'o':
            e.preventDefault();
            if (switchToRenderMode('Outline', false)) {
                announce('Render mode: outline');
            } else {
                announce('Render mode unchanged: outline');
            }
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
            announce('Scene moved up');
            break;
        case 'd':
            currentMoveCamera = "right";
            sendStateToServer();
            currentMoveCamera = "none";
            announce('Scene moved right');
            break;
        case 's':
            currentMoveCamera = "down";
            sendStateToServer();
            currentMoveCamera = "none";
            announce('Scene moved down');
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
            announce('Scene moved left');
            break;

        case '4':
            e.preventDefault();
            {
                const previousZoom = currentZoom;
                const zoomChanged = updateZoom(currentZoom - ZOOM_STEP, false);
                if (zoomChanged) {
                    announce(`Zoom out: ${currentZoom.toFixed(1)}`);
                } else {
                    announce(`Zoom unchanged: ${previousZoom.toFixed(1)}`);
                }
            }
            break;
        case '5':
            e.preventDefault();
            {
                const previousZoom = currentZoom;
                const zoomChanged = updateZoom(currentZoom + ZOOM_STEP, false);
                if (zoomChanged) {
                    announce(`Zoom in: ${currentZoom.toFixed(1)}`);
                } else {
                    announce(`Zoom unchanged: ${previousZoom.toFixed(1)}`);
                }
            }
            break;

        case 'escape':
            e.preventDefault();
            document.activeElement.blur();
            announce('Focus cleared');
            break;

        case 'i':
            // Concise current status
            e.preventDefault();
            {
                const depthLabel = currentSliceDepth === 0 ? 'surface' :
                                   currentSliceDepth === 100 ? 'full depth' :
                                   `${currentSliceDepth}%`;
                announce(`View: ${currentView}, Cut depth: ${depthLabel}, Render: ${currentRenderMode.toLowerCase()}`);
            }
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

        case 'c':
            e.preventDefault();
            if (switchToRenderMode('Cut', false)) {
                announce('Render mode: cut');
            } else {
                announce('Render mode unchanged: cut');
            }
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
