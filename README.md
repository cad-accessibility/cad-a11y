# CAD A11y

A tool for making 3D CAD models accessible to blind and low-vision (BLV) users. It converts STEP/BREP files into accessible SVG representations and streams them to braille displays and tactile hardware in real time.

## Documentation

- [CONTRIBUTING.md](CONTRIBUTING.md) — how to contribute, branch naming, PR guidelines, accessible CLI workflow
- [ACCESSIBILITY.md](ACCESSIBILITY.md) — project accessibility goals, scope, and how to report accessibility issues
- [CHANGELOG.md](CHANGELOG.md) — version history
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — deployment architecture and Docker details
- [docs/STUDIES.md](docs/STUDIES.md): defining, running, exporting and retiring a study
- [docs/STUDY_DATA_EXPORT.md](docs/STUDY_DATA_EXPORT.md): what a study's exported data means
- [docs/MAINTAINER_GUIDE.md](docs/MAINTAINER_GUIDE.md) — release process, branch strategy, triage

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/)

## Running the app

Run the default Docker image configuration:

```bash
docker compose up --build
```

Then open `http://localhost:8635/` in a browser. The viewer is the site's address; `/viewer` still works.

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
│   ├── studies/                  # Studies at /studies/<slug>: engine, storage, export
│   │   └── definitions/          # One file per study, with its status
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
├── environment.yml               # Conda packages, at exact versions, used inside the Docker image
├── requirements.txt              # Direct dependencies, as minimum versions
├── requirements-lock.txt         # Exact versions pip installs on top of environment.yml
├── docker-compose.yml            # Default Docker Compose configuration
└── docker-compose.dev.yml        # Optional local-development bind mounts
```

## Axis modes: Turn and XYZ

Settings → Axis Mode chooses how the model is oriented. The two never share a
key: a key from the other mode only says which mode it belongs to.

- **XYZ** (the default): X, Y and Z slice along that axis, repeating toggles which direction
  you are looking from. Every view is one of OpenSCAD's standard
  views, named by the side you look from: Z is Top, from Z plus, and Z again
  Bottom, from Z minus; Y is Front, from Y minus, and Y again Back; X is Right,
  from X plus, and X again Left.
  On the display, you can choose to have additional context -- a view
  label showing all three axes, or axis markers at the positive end of each axis.
- **Turn**: U/O roll, I/K pitch and J/L yaw the model a quarter turn at a time.
  Left, right, up and down are as they are on the display, whichever way the
  model faces. The comparison study ran in Turn mode; a study's own settings
  choose the mode its models load in.

In either mode, "." says where you are ("View from X plus, Y right, Z up.
Depth: 50%. Origin: H: 42% V: 42%. Render: Outline. Zoom: 0.0. Model: mug."),
"," says where the model's origin is on the display, across from the left edge
and up from the bottom ("Horizontal: 42%, Vertical: 42%", or past 0 and 100 when
it is off the display), and 0 resets the view (it was Z, which XYZ mode needs).
Reset puts the slice plane at 50% on every axis in both modes, which is also
where a model starts.

M toggles the origin mark on the display. The toggle also switches
to Outline, where a small mark is easiest to find, or back to the
render mode that was showing (unless R has chosen another since). Turning the mark on
also invokes ",". The origin is represented as a plus (+) of five
pins. When it is off the display past one side, a T is shown on that 
side: its bar runs along the edge, and its stem indicates the
direction to the origin. If the origin is outside two sides
at once, a square of four pins is drawn in that corner.

Axis labels on the pins, the letters at the display edges and the view info box,
are x, y and z in computer braille.

## The tutorial

The first time someone opens `/viewer` in a browser, a tutorial starts in a region at the top of
the page, after the consent dialog. It teaches every feature on a practice mug in 16 lessons
(setup, core, optional and wrap-up), using the real viewer controls. Exit tutorial is on every
step and pauses it: the viewer is then yours, and the Tutorial button in the main menu (or the
top of the keyboard shortcuts list) resumes at the same lesson and step, starts over, or opens
any lesson from a list. Someone who used the viewer before the tutorial existed hears one line
saying it is there, and it does not start by itself for them.

Where it starts by itself is one table, `TUTORIAL_ROUTES` in `static/js/tutorial.js`:

| Path | Starts by itself | Offered from the menu | Can resume |
|---|---|---|---|
| `/viewer` | yes | yes | yes |
| `/demo` | no | yes | no (demo storage resets on every load) |
| `/workshop`, `?ui=simple` | no | no | no |
| `/study` | no | no | no (it has its own onboarding) |

`?tutorial=start` starts it on any path that offers it (a facilitator at a demo station, for
one), and `?tutorial=off` keeps it closed for one load. Progress is kept in this browser's
`localStorage` (`cadA11yTutorial`), never on the server. With analytics allowed, only coarse
progress is recorded (loaded, lesson finished or skipped, exited, completed); never keys.

The pieces:

- `static/js/tutorial.js` runs it: the lesson state machine, the checks, speech, locks and Key
  help mode.
- `app/tutorial_lessons.py` holds the lessons as data, and the key names they use, in one
  table checked against the viewer by `tests/test_tutorial_lessons.py`.
- `app/tutorial.py` serves the lessons, a display test pattern, where the handle is on the
  display (`/tutorial/locate`), and the printable mug, slice plaques and larger mug pieces
  (`/tutorial/prints.zip`).
- `app/tutorial_prints/` holds the larger mug pieces. They go in the zip as they are, and
  `FIXED_PRINTS` in `app/tutorial.py` says what each one is.
- `builtin_models/tutorial_mug.stl` and `app/tutorial_mug.landmarks.json` are generated from
  `builtin_models/mug.stl` by `python scripts/build_tutorial_mug.py`. Rerun it after changing
  the band descriptions in that script; `tests/test_tutorial_mug.py` fails until you do.

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
- dots 1-3-4-6, 1-3-4-5-6 and 1-3-5-6 (the letters x, y and z): slice along that axis in XYZ mode; repeating toggles direction, as on the keyboard.
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
with Web Bluetooth, which today means a Chromium-based one. A face has to be
clearly up, and stay up for a moment, before the view changes, so the cube held
near a corner no longer flickers between two views. In XYZ mode the face
pointing up is the axis coming out of the display, and turning to it keeps that
axis's slice where it was.

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
- letters `x`, `y` and `z` (dot chords 1 3 4 6, 1 3 4 5 6 and 1 3 5 6): slice along that axis in XYZ mode; repeating toggles direction. The chord is read as a whole letter, the way `v` is.
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

* **Switch between the study's models.** The model chooser offers the study's
  six objects: the two cane tips, the two pencil holders and the two LEGO
  bricks. The study's onboarding mug is left out on purpose (#198). The
  tutorial, opened from the Tutorial button, brings its own practice mug.
* **Bring their own model.** The **Upload model...** control takes an STL or STEP
  file. It is reachable by keyboard and labelled for a screen reader. No record
  of the upload is written: on the demo path the row that would normally be
  created is not. The file itself is removed when the tab closes.
* **Every exploration control, unchanged.** Depth and slicing, pitch/roll/yaw on
  `U`/`O`, `I`/`K`, `J`/`L`, render mode on `R`, and the buttons on the display
  itself. Press `H` for the full list.

There is no consent flow, no onboarding script, no task sequence, no rating
scales and no control panel. Those belong to `/studies`, which a demo station
does not serve.

### Afterwards

Shut each station down with Ctrl-C. Its scratch directory goes with it. There is
nothing to delete, because there is nothing to find: no rows were written, no log
lines appended, no participant numbers issued, and no cookies set.

## Running a study session

A study runs on the ordinary viewer at `/studies/<slug>`, with the model chooser
removed and a study region at the top carrying the current step and an "I am
ready to move on" button. Models load themselves at each step. The experimenter
drives the session from `/studies/<slug>/control`, usually on a second machine,
and the two stay in step over Server-Sent Events.

| Page | Who uses it | What they need |
| --- | --- | --- |
| `/studies/<slug>/control` | The experimenter | The study's panel token, once a day |
| `/studies/<slug>` | The participant | The four-character code from the panel |

1. Open the panel and sign in with the study's token.
2. Choose the task set. The panel shows the participant's code and a join code.
3. The participant opens the study's address in Chrome, which is what connects
   their braille display, and enters the join code.
4. Work through the steps. Each shows what to do, what to say and what to ask,
   what to hand over, and the answer key where the task has one. N moves on and
   loads the next model; N on the last step ends the session.

Each study is a definition in `app/studies/definitions/` with a status that
decides what the servers serve. Nothing answers at `/study` any more: the
comparison study that ran there is closed, which leaves only its data downloads,
behind its own token. [docs/STUDIES.md](docs/STUDIES.md) covers
writing a new study from the example, tokens, running one on a single computer,
where the data is kept, getting it out without shell access, and retiring a
study.

## Where models are stored

Models live in two directories, and which one a file is in decides who can see it.

*   `builtin_models/` holds the models that ship with the project. They are tracked in git, copied into the image, and seeded into `data/models` every time the server starts. Seeding on each start is what lets a built-in added later reach a server whose Docker volume already exists, since Docker only seeds a named volume while it is empty.
*   `data/uploads/` holds what people upload. These are private to the person who uploaded them.

To add a built-in model, put the file in `builtin_models/` and rebuild. Do not put it in `data/models` directly: that directory is runtime state and is not tracked.

The two directories must never be the same. Classification is by location, so if uploads landed in the built-in directory every uploaded file would be served to every visitor. That is exactly what happened in #102 once the move to Docker named volumes made `data/models` writable. The server now refuses to start if `UPLOAD_MODEL_DIR` resolves to the built-in directory.

`CAD_A11Y_MODEL_DIR` moves the built-in directory itself, the way `UPLOAD_MODEL_DIR` moves uploads, and the same check applies wherever it points. The test suite sets both to a temporary directory (`tests/conftest.py`), so running it leaves nothing in the repo's `data/`.

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

## License

BSD 3-Clause, in the University of Washington's wording: see [LICENSE](LICENSE). It covers this project's own code. The libraries it depends on keep their own licenses.
