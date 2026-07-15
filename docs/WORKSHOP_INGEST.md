# Workshop STL ingest

This lets a **separate tool** (for example a CAD or design web app built by someone
else) hand an STL file to cad-a11y and drop the participant straight into a
simplified, screen-reader- and braille-friendly viewer.

There are two pieces:

* `POST /ingest` receives the STL (and, optionally, a participant code that **your
  tool generates**) and returns a ready-to-open **workshop URL**.
* `GET /workshop` is a stripped-down viewer that shows only the controls that matter
  in a workshop: View / face, Depth (four fixed steps), Rendering Mode, the output
  device selector, and the Monarch and DotPad connection panels.

## Participant codes

In a two-station setup (the design tool on one machine, the braille display on
another), each model is tagged with a short **participant code** so the participant
can pull up their model at the braille station. **Your tool generates the code and
shows it to the participant** (for example on a printed card); cad-a11y only stores
it and matches it later.

Use a code that is easy to read aloud, hear, and braille, for example two common
words like `CEDAR MANGO`. This is friendlier for participants who are blind, low
vision, or have cognitive disabilities than an email or password (see WCAG 2.2
Success Criterion 3.3.8, Accessible Authentication): a screen reader reads it word
by word, it shows cleanly on a braille display, and there is nothing to memorise.

cad-a11y matches codes case- and separator-insensitively, so `Cedar Mango`,
`cedar-mango`, and `CEDAR_MANGO` all resolve to the same model. No email, name, or
other personal data is collected; the code is stored locally in the existing SQLite
database. If you omit the code, the ingest is anonymous and the returned
`workshop_url` opens the model directly (handy for a single-station setup).

## Endpoint contract

```
POST /ingest
  Body:  multipart/form-data with a "file" field   (recommended)
         OR a raw STL request body, with the name in ?filename= or the X-Filename header
  Code:  optional, in a "code" form field, a ?code= query parameter, or an X-Code header
  Query: open=1   (optional) single-station convenience: also live-update an already
                  open viewer on this host and, if INGEST_OPEN_ON_HOST=1, pop a window
  ->  200 application/json
      {
        "status": "success",
        "filename": "part_ab12cd34.stl",
        "model_stem": "part_ab12cd34",
        "new_model_index": 7,
        "code": "cedar-mango",           // normalised echo of the code you sent, or null
        "code_display": "CEDAR MANGO",    // or null when no code was sent
        "workshop_url": "http://<host>/workshop?model=part_ab12cd34",
        "workshop_entry_url": "http://<host>/workshop"
      }
  Errors: 400 (no/invalid file or unsupported type; only .stl and .step are allowed)
          413 (larger than MAX_UPLOAD_MB)
          500 (could not save)

  A plain browser form navigation (Accept: text/html, not an XHR) is answered with a
  302 redirect straight to workshop_url instead of JSON.

GET /workshop
  ?model=<stem>   Serve the simplified viewer for that model.
  ?code=<code>    Resolve the participant code and 302-redirect to ?model=...
  (no parameters) The accessible code-entry page.
```

## Integrating in a web app

`CAD_A11Y` is wherever cad-a11y is reachable from the **participant's browser**, for
example `http://braille-station.local:8635`. CORS is already open, so a cross-origin
`fetch` works.

```js
async function sendToAccessibleViewer(stlBlob, filename = "result.stl", code) {
  const fd = new FormData();
  fd.append("file", stlBlob, filename);
  if (code) fd.append("code", code);            // the code your tool generated + showed
  const res = await fetch(`${CAD_A11Y}/ingest`, { method: "POST", body: fd });
  const data = await res.json();
  if (data.status !== "success") throw new Error(data.message || "ingest failed");
  return data; // { workshop_url, workshop_entry_url, ... }
}
```

**Single station** (the design tool and the braille display are the same machine, or
the participant reads the result themselves): skip the code and open the viewer.

```js
const data = await sendToAccessibleViewer(stlBlob);
window.open(data.workshop_url, "_blank", "noopener");
```

**Two stations** (the design tool is on the participant's machine; the braille
display is on a separate station): generate a code, show it to the participant, and
send it with the STL. The participant then enters it at the station.

```js
const code = myCodeGenerator();               // e.g. "CEDAR MANGO"
showCard(code);                               // display / print for the participant
await sendToAccessibleViewer(stlBlob, "result.stl", code);
// At the braille station: open  CAD_A11Y + "/workshop"  and type the code.
```

If the tool holds geometry in three.js, export an STL Blob first:

```js
import { STLExporter } from "three/examples/jsm/exporters/STLExporter.js";
const stl = new STLExporter().parse(scene, { binary: false });
const stlBlob = new Blob([stl], { type: "model/stl" });
```

### Zero-JavaScript option

A plain HTML form works too; the new tab is redirected straight into the viewer:

```html
<form action="http://braille-station.local:8635/ingest" method="post"
      enctype="multipart/form-data" target="_blank">
  <input type="file" name="file" accept=".stl,.step">
  <input type="hidden" name="code" value="CEDAR MANGO">
  <button>Open in accessible viewer</button>
</form>
```

### Command line

```
curl -F file=@result.stl -F code="cedar mango" http://braille-station.local:8635/ingest
```

## Configuration

These environment variables (see `.env.example`) tune the behaviour:

| Variable | Default | Effect |
| --- | --- | --- |
| `MAX_UPLOAD_MB` | `100` | Maximum request size; larger uploads get HTTP 413. |
| `INGEST_OPEN_ON_HOST` | `0` | If `1`, `/ingest` also opens the model in a browser window on the server host. |
| `PUBLIC_BASE_URL` | (unset) | Base URL used to build `workshop_url` behind a reverse proxy. |

## Notes

* `.stl` and `.step` are the only accepted file types.
* If a participant sends several models under the same code, entering that code opens
  the most recent one.
* Codes are matched case- and separator-insensitively (`CEDAR MANGO` == `cedar-mango`).
