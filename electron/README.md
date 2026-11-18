# Desktop viewer for OpenSCAD

An Electron window around the cad-a11y viewer. It watches one OpenSCAD file. When you save the file, or press the shortcut, it compiles the file to an ASCII STL with OpenSCAD and loads it in the viewer. Loading it renders the model again and refreshes the connected braille display.

The viewer and the rendering come from the deployed server, `https://cada11y.cs.washington.edu`, by default. Only OpenSCAD runs on your computer.

## Requirements

- [Node.js](https://nodejs.org/) 20 or later.
- [OpenSCAD](https://openscad.org/) on your `PATH`.

## Run it

```bash
cd electron
npm install
npm start -- path/to/model.scad
```

The window opens on the viewer and loads the model once at start. After that:

- Saving the `.scad` file compiles and loads it.
- `Cmd+Shift+R` on macOS, `Ctrl+Shift+R` elsewhere, compiles and loads it while any app has focus, your editor included.
- When OpenSCAD reports an error, the viewer's upload status shows it and a screen reader announces it.

## Options

| Option | Environment variable | Default |
| --- | --- | --- |
| The `.scad` file, first argument | `CAD_A11Y_SCAD` | none, required |
| `--url` | `CAD_A11Y_URL` | `https://cada11y.cs.washington.edu` |
| `--shortcut`, an [Electron accelerator](https://www.electronjs.org/docs/latest/api/accelerator) | `CAD_A11Y_SHORTCUT` | `CommandOrControl+Shift+R` |
| | `CAD_A11Y_OPENSCAD`, the OpenSCAD program | `openscad` |

Use `--url https://cada11y-test.cs.washington.edu` for staging, or `--url http://localhost:8635` for a server you run with `docker compose up`.

## Limits

- Each compile is an upload, the same as choosing the file with "Upload model…". It adds an entry to the model list and resets the view.
- The browser asks you to pick a braille display, slider or motion sensor; this window cannot show that picker, so it connects to the first device of that kind it finds.

## Tests

```bash
npm test
```
