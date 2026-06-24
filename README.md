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

```bash
docker compose up --build
```

Then open `http://localhost:8635/viewer` in a browser.

Model files placed in `data/models/` are available immediately without rebuilding the image.

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
└── docker-compose.yml            # Docker Compose configuration
```

## Hardware setup

The viewer works without any hardware. Connect devices for full tactile and braille output.

### Monarch braille display

1. Charge until the device powers on (hold the power button for 3 seconds).
2. Turn it off, then connect it to your laptop with a USB-C cable.
3. Turn it on.
4. Navigate to **Braille Terminal**: press the up arrow twice, then press the rightmost braille key twice.

### WitMotion IMU

1. Plug the WitMotion into a USB port.
2. The browser will request WebHID permission on first use.

### Adafruit Slider Trinkey

1. Plug the Trinkey into a USB port (use the USB-A adapter for USB-C ports).

## Uploads on managed servers

If the container cannot write to `/project/data/models`, uploads fall back to `/tmp/cad-a11y/models`. Override the path with:

```bash
UPLOAD_MODEL_DIR=/some/writable/path docker compose up
```
