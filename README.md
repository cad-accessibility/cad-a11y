# CAD A11y

A tool for making 3D CAD models accessible to blind and low-vision (BLV) users. It converts STEP/BREP files into accessible SVG representations and streams them to braille displays and tactile hardware in real time.

## Documentation

- [CONTRIBUTING.md](CONTRIBUTING.md) — how to contribute, branch naming, PR guidelines, accessible CLI workflow
- [ACCESSIBILITY.md](ACCESSIBILITY.md) — project accessibility goals, scope, and how to report accessibility issues
- [CHANGELOG.md](CHANGELOG.md) — version history
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — deployment architecture and Docker details
- [docs/MAINTAINER_GUIDE.md](docs/MAINTAINER_GUIDE.md) — release process, branch strategy, triage

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/)

## Running the app

Run the default Docker image configuration:

```bash
docker compose up --build
```

Then open `http://localhost:8635/viewer` in a browser.

Model files placed in `data/models/` are available immediately without rebuilding the image.

For local development with live source-code bind mounts:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

## Directory structure

```
cad-a11y/
├── accessible-3d-viewer.html     # Main viewer UI
├── app/
│   ├── server.py                 # Flask server (entry point inside the container)
│   ├── braille_display.py        # Braille display I/O (Monarch, DotPad)
│   └── cad_comparison_lib.py     # CAD rendering and comparison library
├── src/
│   ├── converter/                # CAD format conversion scripts (STEP → SVG, hatch, slice)
│   └── models/                   # Sample model files
│       ├── brep/
│       ├── stl/
│       └── svg/
├── static/
│   ├── css/viewer.css            # Viewer styles
│   └── js/
│       ├── viewer.js             # Main viewer logic
│       ├── monarch-hid.js        # Monarch braille display (WebHID)
│       ├── trinkey-slider.js     # Adafruit Trinkey slider (WebHID)
│       ├── witmotion-imu.js      # WitMotion IMU for orientation input (WebHID)
│       └── dotpad-integration.js # DotPad haptic display
├── data/models/                  # Model files (bind-mounted into the container)
├── scripts/                      # Utility scripts (SCAD conversion, BREP generation)
├── tests/                        # Test suite
├── docs/                         # Extended documentation
├── environment.yml               # Conda environment used inside the Docker image
├── requirements.txt              # pip dependencies installed inside the Docker image
├── docker-compose.yml            # Default Docker Compose configuration
└── docker-compose.dev.yml        # Optional local-development bind mounts
```

## Hardware setup

The viewer works without any hardware. Connect devices for full tactile and braille output.

### Monarch braille display

1. Charge until the device powers on (hold the power button for 3 seconds).
2. Turn it off, then connect it to your laptop with a USB-C cable.
3. Turn it on.
4. Navigate to **Braille Terminal**: press the up arrow twice, then press the rightmost braille key twice.

#### Monarch controls

The Monarch supports cursor controls and depth changes with the following inputs:

- dot 1: change depth shallower by 10%.
- dot 4: change depth deeper by 10%.
- spacebar: cycles through these cursor modes
    - `none`: hides the cursor and disables cursor movement.
    - `crosshair`: shows a small 5-by-5 pixel crosshair at the cursor position.
    - `guidelines`: shows horizontal and vertical guide lines through the cursor.
    - `horizontal-line`: shows only the horizontal guide line; left and right movement are disabled, since the line spans the full width and is repositioned by moving it up and down.
    - `vertical-line`: shows only the vertical guide line; up and down movement are disabled, since the line spans the full height and is repositioned by moving it left and right.
- right directional pad: controls cursor movement as expected and explained below
    - left navigation button: move cursor or vertical line left
    - right navigation button: move cursor or vertical line right
    - up navigation button: move cursor or horizontal line up
    - down navigation button: move cursor or horizontal line down

NOTE: Cursor or guideline movements only work when the cursor or guidelines are active. Each Monarch button press moves one pixel. Hold and chord gestures are not currently enabled for Monarch controls.

### WitMotion IMU

1. Plug the WitMotion into a USB port.
2. The browser will request WebHID permission on first use.

### Adafruit Slider Trinkey

1. Plug the Trinkey into a USB port (use the USB-A adapter for USB-C ports).

### DotPad display

1. Turn on the DotPad using the switch on right side of device. The device vibrates when it powers on.
2. Wait a few seconds for the device name to appear on the tactile graphic area and Braille text display.
3. In the browser, navigate to the DotPad section and select **Connect BLE**.
4. Press the down arrow until you find your DotPad device name, then press Enter.
5. The DotPad should vibrate again after it connects.

#### DotPad controls

The DotPad supports cursor controls and depth changes with the following inputs:

- dot 1: change depth shallower by 10%.
- dot 4: change depth deeper by 10%.
- letter `v` or dot chord 1 2 3 6: cycles through these cursor modes
    - `none`: hides the cursor and disables cursor movement.
    - `crosshair`: shows a small 5-by-5 pixel crosshair at the cursor position.
    - `guidelines`: shows horizontal and vertical guide lines through the cursor.
    - `horizontal-line`: shows only the horizontal guide line; left and right movement are disabled, since the line spans the full width and is repositioned by moving it up and down.
    - `vertical-line`: shows only the vertical guide line; up and down movement are disabled, since the line spans the full height and is repositioned by moving it left and right.
- dot 3: move cursor or vertical line left
- dot 6: move cursor or vertical line right
- dot 2: move cursor or horizontal line up
- dot 5: move cursor or horizontal line down

NOTE: Cursor or guideline movements will only work when the cursor or guidelines are active. A single button press will move one pixel. A double or triple button press will move the line five or twelve pixels respectively.


## Where models are stored

Models live in two directories, and which one a file is in decides who can see it.

*   `builtin_models/` holds the models that ship with the project. They are tracked in git, copied into the image, and seeded into `data/models` every time the server starts. Seeding on each start is what lets a built-in added later reach a server whose Docker volume already exists, since Docker only seeds a named volume while it is empty.
*   `data/uploads/` holds what people upload. These are private to the person who uploaded them.

To add a built-in model, put the file in `builtin_models/` and rebuild. Do not put it in `data/models` directly: that directory is runtime state and is not tracked.

The two directories must never be the same. Classification is by location, so if uploads landed in the built-in directory every uploaded file would be served to every visitor. That is exactly what happened in #102 once the move to Docker named volumes made `data/models` writable. The server now refuses to start if `UPLOAD_MODEL_DIR` resolves to the built-in directory.

### Uploads on managed servers

`docker-compose.yml` sets `UPLOAD_MODEL_DIR=/project/data/uploads` explicitly. Override it only if that path is not writable:

```bash
UPLOAD_MODEL_DIR=/some/writable/path docker compose up
```

### Cleaning up a server that predates the split

On a deployment where uploads used to share the built-in directory, participant files accumulated there and are treated as public. To see what is affected:

```bash
docker compose exec app python scripts/cleanup_ingest_models.py
```

That reports only. Re-run with `--apply` to delete. It never touches anything shipped in `builtin_models/`, and it warns about models that share a stem, since a stem names one model to the client.
