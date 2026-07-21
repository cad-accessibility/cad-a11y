# Workshop STL ingest

This lets a **separate tool** (for example a CAD or design web app built by someone
else) hand an STL file to cad-a11y and drop the participant straight into a
simplified, screen-reader- and braille-friendly viewer.

There are two pieces:

* `POST /ingest` receives the STL (and the participant's first name) and returns a
  ready-to-open **workshop URL**.
* `GET /workshop` is a stripped-down viewer that shows only the controls that matter
  in a workshop: View / face, Depth, Rendering Mode, the output-device selector, and
  the Monarch and DotPad connection panels.

## Participants

Each ingest is tagged with the participant's **first name**, which the sending tool
collects (the experimenter types it in) and sends with the STL. First names are used
because the workshop participants are minors and only first names are available; they
are also what a participant knows and can enter at a braille station.

cad-a11y gives each first name a unique **`user_id`** the first time it is seen and
reuses it for that name's later uploads, so:

* **every model a participant sends is saved**, but only their **most recent** one is
  shown when their first name is looked up;
* the participant's **in-app actions** (renders, key presses) are recorded against the
  same `user_id`, so models and interaction data can be linked for the research.

Because first names are not unique, two people sharing one first name share one
record; the workshop distinguishes them out of band (name tags, photos). Names are
matched case- and spacing-insensitively (`Alex`, `alex`, `  Alex `), and no email or
account is involved.

> Note: workshop participants' actions are recorded without the usual analytics
> consent dialog (the workshop does not show it). This is intentional for the
> research; keep it in mind for any deployment outside the workshop.

## Endpoint contract

```
POST /ingest
  Body:  multipart/form-data with a "file" field   (recommended)
         OR a raw STL request body, with the name in ?filename= or the X-Filename header
  Name:  optional "first_name" (form field, ?first_name= query, or X-First-Name header)
  Query: open=1   (optional) single-station convenience: also live-update an already
                  open viewer on this host and, if INGEST_OPEN_ON_HOST=1, pop a window
  ->  200 application/json
      {
        "status": "success",
        "filename": "ingest_ab12cd34.stl",
        "model_stem": "ingest_ab12cd34",
        "new_model_index": 7,
        "first_name": "Alex",                                 // echo, or null if none sent
        "user_id": "5f0c…",                                   // participant id, or null
        "workshop_url": "http://<host>/workshop?name=Alex",   // opens their latest model
        "workshop_entry_url": "http://<host>/workshop"
      }
  Errors: 400 (no/invalid file or unsupported type; only .stl and .step are allowed)
          413 (larger than MAX_UPLOAD_MB)
          500 (could not save)

  With no first_name the ingest is anonymous and workshop_url is
  http://<host>/workshop?model=<stem> (opens the model directly).
  A plain browser form navigation (Accept: text/html, not an XHR) is answered with a
  302 redirect to workshop_url instead of JSON.

GET /workshop
  ?model=<stem>   Serve the simplified viewer for that model.
  ?name=<first>   Resolve the participant's first name to their latest model, attach
                  their session cookie, and 302-redirect to ?model=...
  (no parameters) The accessible first-name entry page.
```

## Integrating in a web app

`CAD_A11Y` is wherever cad-a11y is reachable from the **participant's browser**, for
example `http://braille-station.local:8635`. CORS is already open, so a cross-origin
`fetch` works.

```js
async function sendToAccessibleViewer(stlBlob, firstName, filename = "result.stl") {
  const fd = new FormData();
  fd.append("file", stlBlob, filename);
  if (firstName) fd.append("first_name", firstName);   // the participant's first name
  const res = await fetch(`${CAD_A11Y}/ingest`, { method: "POST", body: fd });
  const data = await res.json();
  if (data.status !== "success") throw new Error(data.message || "ingest failed");
  return data; // { user_id, workshop_url, workshop_entry_url, ... }
}
```

**Single station** (the design tool and the braille display are the same machine, or
the participant reads the result themselves): open the viewer.

```js
const data = await sendToAccessibleViewer(stlBlob, "Alex");
window.open(data.workshop_url, "_blank", "noopener");
```

**Two stations** (the design tool is on the participant's machine; the braille
display is on a separate station): send the name with the STL, then the participant
enters their first name at the station.

```js
await sendToAccessibleViewer(stlBlob, "Alex");
// At the braille station: open  CAD_A11Y + "/workshop"  and type "Alex".
```

If the tool holds geometry in three.js, export an STL Blob first:

```js
import { STLExporter } from "three/examples/jsm/exporters/STLExporter.js";
const stl = new STLExporter().parse(scene, { binary: false });
const stlBlob = new Blob([stl], { type: "model/stl" });
```

### Command line

```
curl -F file=@result.stl -F first_name=Alex http://braille-station.local:8635/ingest
```

## Trying it without the partner tool

The app serves a test harness that stands in for the sending tool. With the app
running (for example `docker compose up`), open **`http://<host>:8635/ingest-test`**,
type a participant first name, and click **Send**: it POSTs a bundled sample model to
`/ingest`, shows the returned `user_id` and `workshop_url`, and links straight into the
viewer. Because the page is served by the app, the server URL is filled in
automatically, so it works with no configuration.

The same page is the static file `examples/ingest-test.html` (with
`examples/sample-model.stl`), so you can also open it directly from disk and point it
at any server, or send the sample with curl:

```
curl -F file=@examples/sample-model.stl -F first_name=Alex http://<host>:8635/ingest
```

## Configuration

These environment variables (see `.env.example`) tune the behaviour:

| Variable | Default | Effect |
| --- | --- | --- |
| `MAX_UPLOAD_MB` | `100` | Maximum request size; larger uploads get HTTP 413. |
| `INGEST_OPEN_ON_HOST` | `0` | If `1`, an `/ingest` call that also passes `open=1` opens the model in a browser window on the server host. Both are required. |
| `PUBLIC_BASE_URL` | (unset) | Base URL used to build `workshop_url` behind a reverse proxy. |

## Notes

* `.stl` and `.step` are the only accepted file types.
* Sending several models under the same first name keeps them all; entering that name
  opens the most recent one.
