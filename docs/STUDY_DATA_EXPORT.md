# Study Data Export

How to get a study's interaction data out as a spreadsheet, and what its columns
mean. Written for whoever is doing the analysis. [STUDIES.md](STUDIES.md) covers
the rest of running a study.

## Getting the file

From the study's control panel, `/studies/<slug>/control`: sign in, and use the
**Data** section. **Everything, as one zip** holds the database, every session
log, this spreadsheet with its codebook, a table of sessions and the data checks.
**Long CSV** is the spreadsheet on its own.

Or run the script, which is the same request with the checks that stop a bad
download being saved as if it were data:

```bash
STUDY_TOKEN=... scripts/download_study_data.sh <slug>
```

| | |
|---|---|
| `HOST=prod` | production instead of staging |
| `CSV=1` | the long CSV instead of the zip |
| `SESSION=7` | with `CSV=1`, one session instead of all of them |
| second argument | where to write the file |

Both need the study's panel token; the script asks for it when `STUDY_TOKEN` is
not set. A server serves a study's data while the study is open or closed.
Staging updates on every merge to `master`, production on a `v*` release tag.

The comparison study is closed, so both work for it with its own token:
`scripts/download_study_data.sh comparison-2026`. [STUDIES.md](STUDIES.md) has
the rest of getting its data out.

`long.json`, beside `long.csv` in the zip, describes every column below in a
form a script can read: its description, its levels where it has a fixed set,
and its units, with the study's own phases and steps filled in.

## Cells that start with a quote

A spreadsheet runs any cell that starts with `=`, `+`, `-` or `@` as a formula,
and some of these columns hold text a participant's browser sent. So any text
cell starting with one of those, or with a tab or a carriage return, has a `'`
added in front. Plain numbers are left alone: `-12.5` is a number, not a formula.
Remove the leading `'` to get the value that was recorded, which matters most for
`key`, where `'-` is the minus key.

## What is in it

One row per interaction, not one per session. Every question the analysis asks
is a count over interactions grouped some way, so the grouping is yours to
choose rather than baked into the file.

| Columns | What they are for |
|---|---|
| `participant_code`, `session_number`, `study_session_id`, `seq` | which session, and the order within it |
| `timestamp`, `elapsed_ms`, `step_elapsed_ms` | when, since the session started, and since the current step started |
| `phase`, `step_id`, `step_index` | where in the protocol. `phase` is `onboarding`, `task1`, `task2`, `discussion` and so on |
| `event_type`, `source` | what happened. `render` means the display updated; anything else is an event such as `keyboard` or `braille_send` |
| `key`, `key_repeat`, `key_shift` | which key was pressed, whether it was a hold-repeat rather than a fresh press, and whether Shift was held |
| `input_source` | on render rows, what triggered the render |
| `model`, `view`, `render_mode`, `layout_mode`, `depth`, `zoom`, `cache_hit` | the state of the viewer at that moment |
| `orientation_x`, `orientation_y`, `orientation_z`, `orientation_basis` | how the object was turned. See below |
| `axis_mode`, `cut_axis`, `cut_side`, `cut_percent` | the axis mode and where the cut was along its axis. See below |

Only `render` rows record viewer state directly. On every other row the state
columns show the most recent render in the same session, meaning what was on the
display when the event happened. They are blank before the first render of a
session, and nothing carries across sessions.

## Counting commands

Count keypresses using `key`, not by diffing viewer state between renders.

**In Turn mode there is no view-switching command.** The view label is derived
from whichever axis the slices are currently cut along, so a rotation that moves
that axis changes the view as a side effect. Rotations and "axis switches" are
therefore not two things that happen to correlate; they are one thing counted
twice. Pitch and yaw move the depth axis and take the view label with them, and
roll does not move it, so a participant who never rolled will show a view change
on every single rotation.

XYZ mode (`axis_mode` `xyz`, added for #185) is the exception: `x`, `y` and `z`
pick the axis and the side directly, the letter alone for the view from the
right, the front or above, and the same letter again for the other side. `key`
reads the same for both presses, and `key_shift` does not tell them apart, so
read the side off the row itself: `cut_side` says which side the cut was seen
from. The comparison study ran in Turn mode, so its sessions only contain XYZ
rows if someone changed the setting mid-session. A study's `viewer_defaults` set
the mode each of its models loads in.

| Key | Command |
|---|---|
| `i`, `k` | pitch up, pitch down |
| `j`, `l` | yaw left, yaw right |
| `u`, `o` | roll counterclockwise, roll clockwise |
| `arrowup`, `arrowdown` | depth by 1% (XYZ mode: the cut by 1% of the object along its axis, always toward the axis's highest value for `arrowup`) |
| `pageup`, `pagedown` | depth by a larger step (XYZ mode: 10%) |
| `home`, `end` | depth to the surface or the far side (XYZ mode: the object's lowest or highest coordinate on the axis) |
| `x`, `y`, `z` | XYZ mode: cut along that axis, from the right, the front or above; the same key again, from the other side (see `cut_side`). In Turn mode: nothing but a message saying so |
| `,` | where the origin is, in both modes (XYZ names its coordinate on the axis) |
| `2`, `3` | zoom out, zoom in |
| `4`, `5` | zoom out, zoom in, fine |
| `w`, `a`, `s`, `d` | pan up, left, down, right |
| `r` | cycle render mode |
| `t` | cycle view mode (single, side-by-side, slice graph) |
| `.` | where am I: which way the model faces, where the cut is, then the rest of the status bar |
| `g`, `v` | slice-graph anchor, slice-graph lock |
| `[`, `]` | toggle scrollbar, toggle slice graph |
| `f` | fit to device |
| `0` | reset position |
| `z` | reset position before #185; XYZ mode's Z axis after it (in Turn mode, a message saying reset is now `0`) |
| `h`, `?`, `p`, `escape` | help, help, print, clear focus |
| `q`, `e` | nothing. Accepted and logged, but bound to no action |

Two things to apply before counting. `key_repeat` is `True` for keypresses
generated by holding a key down, which affects the continuous controls most, so
decide whether a hold is one command or many and say which. And `q` and `e`
reach the log without doing anything, so they are keypresses but not commands.

## Render modes

`render_mode` is one of `filled`, `outline`, `cut`, `x-ray`, always lowercase.
The viewer sends three of them capitalised and `x-ray` not, which is a detail of
the wire format; the export normalises it so grouping does not split on casing.
Older sessions also contain the earlier wire names `slice` and `shaded`, which
are reported as `cut` and `filled`.

**X-Ray is a fourth mode, and the export does not fold it into anything.** `R`
cycles through all four, so a row recording x-ray is a participant who was in it.
The protocol teaches three modes and never enumerates them, so participants
cycling with `R` pass through x-ray whether or not anyone intended it. A figure
therefore has to either report four modes or say plainly that x-ray rows were set
aside and how many there were. Mapping it onto one of the other three would put
time in a mode the participant was not in.

## Completed sessions only, and the rest kept apart

`long.csv` holds completed sessions only, and that is not something the caller can
turn off. A session is completed when it reached the last step and was ended
there. Ended earlier, by the experimenter or after a long time with no activity,
it is abandoned.

The zip also holds `long_incomplete.csv`, in the same columns, for the sessions
`long.csv` leaves out that nothing is still writing to: every abandoned session,
and, once a study is closed, the sessions it left active. A closed study runs no
idle sweep, so nothing will end those; they are usually finished sessions whose
panel was closed without pressing End. An open study's active sessions are still
being written to, so they are in neither file. `sessions.csv` gives each
session's status and `step_index`, the last step it reached, so using any of them
is a decision made per participant, and worth saying in the write-up.

This is a filter on status, which is not the same as a filter on usable. A
session that stopped at step 19 of 21 is in `long_incomplete.csv`; a session
completed after 21 seconds is in `long.csv`. Sessions recorded before 2026-10
could be ended as completed from any step, so for those, check `step_index`.
Asking for one session with `?session=<id>` or `SESSION=<id>` returns 409 with its
status when it is not completed, rather than an empty file, so a participant
cannot go quietly missing from the analysis; its rows are in
`long_incomplete.csv`, or in its JSON export at any time.

## Axis mode and the cut

`axis_mode` is `turn` or `xyz`. The other three describe the cut, in either
mode:

* `cut_axis` is the model axis the slices are cut along, `x`, `y` or `z`.
* `cut_side` is the side it is seen from: `above` or `below` for Z, `front` or
  `back` for Y, `right` or `left` for X.
* `cut_percent` is how far along the object the cut is, from its lowest
  coordinate on that axis (0) to its highest (100). Unlike `depth`, it does not
  change when the same plane is seen from the other side.

All four are blank on rows recorded before they existed.

## Orientation

Rotation happens in whole quarter turns, so `orientation_x`, `orientation_y` and
`orientation_z` are always `0`, `90`, `180` or `270` degrees. Equal triples mean
the same orientation and different triples mean different ones, so counting
distinct orientations or orientation changes works on these three columns alone.

Do not read them as a record of which rotation keys were pressed. When
`orientation_y` is `90` or `270` the object points along the X axis, where a turn
about X and a turn about Z are the same motion and the two cannot be told apart.
`orientation_z` is reported as `0` in that case by convention. Use `key` for what
was actually pressed, and `orientation_basis`, which holds the untouched vectors,
for anything that cannot live with the convention.

Blank in all four columns means no orientation was recorded, which is not the
same as zero.

### What the `view` tokens mean, and the one that reads backwards

`view` holds the token the viewer has used since long before #185. For Y and Z it
names the side the reader is on; for X it names the opposite one:

| `view` | Reader is on | OpenSCAD view | `cut_side` |
|---|---|---|---|
| `z+` | +Z | Top | `above` |
| `z-` | −Z | Bottom | `below` |
| `y-` | −Y | Front | `front` |
| `y+` | +Y | Back | `back` |
| `x-` | **+X** | Right | `right` |
| `x+` | **−X** | Left | `left` |

So an `x-` row is the view from the +X side, not the −X side. Nobody hears these
tokens — the viewer says "seen from the right" and never "x minus" — but they are
in this data, so anyone filtering on `view` has to know about the flip. Renaming
them would silently change what every stored row means, including the sessions
already collected, which is why they stand. Use `cut_side` where you want a name
that reads the way it sounds.

### Sessions from before the orientation fix (#185) read differently

The fix for #185 changed what three of the six views store, so do not pool
sessions from either side of it without accounting for this.

* **Telling them apart.** It shows in the data. On a row whose `view` is `y-`,
  `y+` or `z-`, the `forward` vector in `orientation_basis` points the other way
  after the fix: a `y-` row recorded before it has `forward` `[0, 1, 0]`, and one
  recorded after has `[0, -1, 0]`. The export reads this for you: the
  `view_convention` column of `sessions.csv` says `before #185` or `after #185`
  for every session that used those views, `checks.json` lists the ones from
  before, and when there are any, the codebook's depth and angle entries and the
  zip's README say so too.
* **Depth.** Front (`y-`), back (`y+`) and bottom (`z-`) used to measure depth in
  from the far side. They now measure it from the surface nearest the reader, as
  the other three always did, so the same plane reads as `100 - depth`: 30%
  before the fix is 70% after. Rows in `z+`, `x-` and `x+` are unaffected.
* **Angles.** Those three views were stored as mirror images rather than
  rotations, so the angles derived from them before the fix do not describe a
  turn that could have happened. After the fix every stored basis is a rotation
  and every angle means what it says. Counting distinct orientations within one
  side of the fix still works; comparing angles across it does not.
* **What participants felt did not change for `y-` and `y+`**: the same face,
  the same way up. Bottom did change: it used to show the top of the object
  turned 180 degrees, and now shows the view from below.

## If something looks wrong

* Nothing but a header row: no session has been completed yet.
* A participant missing: their session is probably active or abandoned. Look in
  `long_incomplete.csv` and `sessions.csv`.
* `key` blank on a `keyboard` row: the event was recorded without a payload.
  Rare, and the row is otherwise intact.
* HTTP 401: the token is wrong, or the panel's sign-in has lapsed.
* HTTP 404: that server does not serve the study, because it is a draft or
  retired, or because the release that opened it has not gone out; or the study
  has no data on that server.
* The per-session JSON at `/studies/<slug>/control/export/sessions/<id>.json`
  works for a session in any state, mid-session included. It is the right thing
  to use when the question is about one session rather than the analysis set.
