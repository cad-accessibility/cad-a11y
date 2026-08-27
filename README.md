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
│   ├── recording.py              # The one place interaction data leaves the process
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
│       ├── demo-bootstrap.js     # Switches off recording and storage on /demo
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


## Running a demo session (nothing is recorded)

`/demo` is the viewer with recording switched off. It exists for events where
capturing what people do is not permitted. The hands-on session at the Andrew
Heiskell Braille and Talking Book Library is the one it was built for. It is not
a study session, it does not ask for consent, and it has no facilitator panel.

There is no login, no session code and no setup. One address, and it works.

### Launching three stations

Each station is its own server process. That is not a limitation to work around;
it is what makes the stations independent, and it means a station keeps working
if the venue wifi drops, because nothing leaves the machine after the page loads.

**One laptop per station (what to do if you can).** On each laptop:

```bash
CAD_A11Y_DEMO=1 python -m app.server
```

It opens `http://localhost:6969/demo` by itself. Nothing else to type.

**Three stations on one laptop.** Give each one its own port, in three terminals:

```bash
CAD_A11Y_DEMO=1 PORT=6969 python -m app.server
```

```bash
CAD_A11Y_DEMO=1 PORT=6970 python -m app.server
```

```bash
CAD_A11Y_DEMO=1 PORT=6971 python -m app.server
```

Then open `http://localhost:6969/demo`, `http://localhost:6970/demo` and
`http://localhost:6971/demo`, one per browser window, and pair each window with
its own display from the **Connect** button. Each window talks only to the
display it paired with.

`CAD_A11Y_DEMO=1` is the important part. It puts the whole process into demo
mode: nothing can be recorded by any request, and the study endpoints are not
served at all.

### Checking that recording is off, at the venue, in ten seconds

You do not need to read any code, and you do not need a terminal.

1. **Look at the top of the page.** A boxed message reads *"Demo mode. Nothing
   you do here is recorded."* If that box is missing, or if it is red rather than
   cream, **stop and do not use the station**.
2. **Press the "Check recording status again" button** inside that box. It asks
   the server again, right then, and shows what the server said. You can do this
   with somebody watching. The line underneath should read:

   ```
   Server says: recording=false · sink=null · whole process in demo mode=true
   · study endpoints served=false · writes refused so far=<a number>
   ```

   Read it out loud if a host asks. What each part means:

   | Part | What it tells you |
   | --- | --- |
   | `recording=false` | The server is not writing anything for this page |
   | `sink=null` | There is no database or log file attached at all |
   | `whole process in demo mode=true` | Nothing on this machine can record, not just this page |
   | `study endpoints served=false` | The study pages do not exist on this machine |
   | `writes refused so far` | How many writes have been turned away since it started. It goes **up** as people explore. That is the proof it is working. |

3. **Check the browser tab title.** It reads *"Demo (not recording)"*. If it
   reads *"Demo (CHECK RECORDING)"*, stop.

A screen reader user gets the same information: the message is announced on load,
the "Check recording status again" button is reachable by keyboard, and the main
area of the page is named with it, so it is heard on entering the content.

### If something looks wrong

| What you see | What it means | What to do |
| --- | --- | --- |
| No box at the top | The page did not start in demo mode | Check the address is `/demo`. Reload. |
| Red box, "could not confirm" | The page could not reach the server | Reload. If it repeats, restart the station. |
| Red box, "recording is ON" | The server is recording | Stop. Close the browser and relaunch with `CAD_A11Y_DEMO=1`. |
| Page is blank | The component that switches recording off did not load, so the viewer refused to start | Reload. Nothing was recorded: the viewer never ran. |

The blank-page case is deliberate. If the part that switches recording off is
missing, the viewer does not start rather than starting without it.

### What people can do in a demo session

* **Switch between models freely.** The ordinary model chooser, with the models
  the server has. Nothing is preselected and nothing is hidden: the demo is the
  viewer with recording off, not a different set of objects. The mug is the
  quickest thing to reach for when showing somebody what slicing does to a shape,
  and the LEGO brick, pencil holder and cane tip are all there.
* **Bring their own model.** The **Upload model...** control takes an STL or STEP
  file. It is reachable by keyboard and labelled for a screen reader. No record
  of the upload is written: on the demo path the row that would normally be
  created is not. The file itself is removed when the tab closes.
* **Every exploration control, unchanged.** Depth and slicing, pitch/roll/yaw on
  `U`/`O`, `I`/`K`, `J`/`L`, render mode on `R`, and the buttons on the display
  itself. Press `H` for the full list.

There is no consent flow, no onboarding script, no task sequence, no rating
scales and no control panel. Those belong to `/study`, which a demo station does
not serve.

### Afterwards

Shut each station down with Ctrl-C. Its scratch directory goes with it. There is
nothing to delete, because there is nothing to find: no rows were written, no log
lines appended, no participant numbers issued, and no cookies set.

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
