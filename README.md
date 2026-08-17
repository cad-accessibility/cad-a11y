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
│       ├── trinkey-slider.js     # Adafruit Trinkey slider (Web Serial)
│       ├── witmotion-imu.js      # WitMotion IMU for orientation input (Web Bluetooth)
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

1. Power on the WitMotion.
2. In the browser, go to the WitMotion IMU section and select **Connect BLE**.
3. Pick the device in the browser's pairing prompt.

Turning the cube changes the view in that browser window only. Needs a browser
with Web Bluetooth, which today means a Chromium-based one.

### Adafruit Slider Trinkey

1. Plug the Trinkey into a USB port (use the USB-A adapter for USB-C ports).
2. In the browser, go to the Trinkey Slider section and select **Connect USB**.
3. Pick the port in the browser's prompt.

The slider sets the depth in that browser window only. Needs Web Serial, which
today means a Chromium-based browser.

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


## Running a study session

`/study` runs the study protocol end to end. It is the ordinary viewer, with the
model chooser removed and a study region added at the top carrying the current
step and an "I am ready to move on" button. Models load themselves at each step,
so the experimenter never has to find one in a list mid-session.

There are two pages, and they stay in sync over Server-Sent Events, so the
experimenter can drive the session from their own machine while the participant
works on theirs.

| Page | Who uses it | What they need |
| --- | --- | --- |
| `/study/control` | The experimenter | Nothing — opening it starts a session |
| `/study` | The participant | The four-character code from the panel |

### Running one

1. Open **`/study/control`**. That starts a session — there is nothing to fill in
   first, because the participant id, the model pairs and the session number are
   all decided by the protocol.
2. The panel shows two things to read out: the address `/study`, and a
   four-character code.
3. The participant opens `/study` in Chrome — Chrome specifically, because the
   braille display connects over Bluetooth — and enters the code.
4. Work through the steps. Each shows what to do, what to say and what to ask,
   which printed model to hand over, and, for the exploration steps, the answer
   key for that pair. "Next step" advances both views and loads the next model.
5. End the session when you reach the last step. That closes the record.

The participant sees a practice round with the Lego brick, then two of the three
model pairs, assigned by a Latin square so they stay balanced across
participants. The panel shows the assignment.

### If you only have one computer

On the panel, **Run the study on this device**. That window becomes the
participant's view — exactly `/study`, nothing added — and the session moves to
the next step when they press "I am ready to move on" rather than waiting for a
Next button you can no longer reach.

Have the protocol to hand: the script is not shown there. Putting it on that page
would put it where the participant's screen reader can read it, along with the
answer key.

Everything else is the same. The same events are recorded in the same order; the
only difference in the log is that the step advances are attributed to the
participant, because they are the one who pressed the button.

### One panel, one session

A tab of `/study/control` owns exactly one session. Reloading stays on the same
session; opening the panel in a **new tab or window starts another**. That is how
two people run participants at the same time — two panels, two codes — and it is
also why you should not open the panel "just to look" while a session is running.

The code is what ties a participant's browser to a session, and it is asked for
every time, whether one session is running or five. One instruction to give, and
it never changes.

### Access

There is none by default: whoever has the address can open the panel. That is
deliberate — it keeps the thing you actually do down to opening the app and
starting.

On a public deployment it also means a stranger with the URL can advance a live
session, see the answer key and download a participant's interaction log. Setting
`STUDY_CONTROL_TOKEN` in `.env` turns a gate back on, and the panel then needs
`/study/control?token=…`. It is off unless that variable is set.

### What each step shows you

Steps are written as labelled blocks rather than one run of prose, because most
mix things to do with things to say:

| Label | Means |
| --- | --- |
| **Say** | Read this to the participant. Shown in quotes. |
| **Do** | An action you perform. Not spoken. |
| **Ask** | Questions to ask verbally, sometimes with the response options. |
| **Note** | Context or a reminder. Never spoken. |

### Questionnaires

The background questions, the rating scale after each object, and the closing
discussion questions are all in the panel, so there is no second document to keep
open. They are there **to read from**: ask them out loud and write the answers on
your own sheet. The panel gives you nowhere to type them and stores none of them.

Consent is not part of the session. It is given before the participant is sent
the link, so step 1 is settling them in and step 2 is setting up the machine and
the display.

### Where the data goes

Two records, written independently, both keyed to the participant ID:

- `data/db/study.db` — a SQLite database, separate from the usage database.
  Interactions and their timings: keypresses, renders, step advances, model
  loads, readiness signals, announcements.
- `data/logs/study/<participant>_S<n>_<date>.jsonl` — append-only, one event per
  line, each line carrying the full viewer state at that moment. This is the
  record a session is reconstructed from, and it is written even when the
  database write is the thing that failed.

Nothing else is stored. What the participant said and what the experimenter
observed stay on the experimenter's own sheet.

Ending a session checkpoints the database, so `study.db` is complete on its own.
Copy it mid-session and you get only what SQLite has folded in so far — take the
`-wal` file too, or use the per-session JSON download in the panel.

The control panel reports whether logging is working while the session is
running, so a storage problem is visible at the time rather than discovered
during analysis.

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
