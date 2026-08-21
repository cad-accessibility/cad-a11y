#!/usr/bin/env python
"""Probe whether two concurrent /viewer browser sessions stay independent (issue #123).

Three of the mechanisms in #123 live in the browser, not the server, so the pytest
suite in tests/test_concurrent_sessions.py cannot see them: the Flask test client
executes no JavaScript, so the ownership filter, the builtinModelStems latch and the
5 s /get_data poll are all invisible to it. This script covers those.

Each session is a separate Playwright browser context, which is a separate cookie jar
and a separate sessionStorage. That is what makes them two sessions rather than two
tabs of one.

Deliberately not wired into CI: E5b races the client's own polling interval and would
flake. Run it by hand against a container built from the branch under test.

    docker compose up -d --build
    python scripts/concurrent_session_probe.py

Requires playwright in the cad-a11y env:

    pip install playwright && playwright install chromium
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent
INGEST_MODEL = REPO_ROOT / "builtin_models" / "cube.stl"

# The viewer polls /get_data every 5 s, and builtin_model_stems only arrives on that
# poll. Sampling has to outlast one full interval to catch the filter engaging.
SAMPLE_SECONDS = 12.0
SAMPLE_INTERVAL = 0.25

results: list[tuple[str, str, str]] = []


def record(experiment: str, verdict: str, detail: str) -> None:
    results.append((experiment, verdict, detail))
    mark = {"INDEPENDENT": "ok", "INTERFERES": "INTERFERES", "INCONCLUSIVE": "??"}[verdict]
    print(f"  [{mark}] {detail}")


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def read_state(page) -> dict:
    """Everything that identifies which model this tab believes it is showing."""
    return page.evaluate(
        """() => {
            const dd = document.getElementById('model-list-dropdown');
            const img = document.getElementById('tactile-display-img');
            const sb = document.getElementById('sb-model');
            return {
                sbModel: sb ? sb.textContent : null,
                value: dd ? dd.value : null,
                options: dd ? [...dd.options].map(o => o.text) : [],
                disabled: dd ? dd.disabled : null,
                img: img ? (img.src || '') : '',
                bbox: ['x-min','y-min','z-min'].map(
                    id => (document.getElementById('bbox-' + id) || {}).textContent).join('|'),
            };
        }"""
    )


def settle(page, seconds: float = 8.0) -> None:
    """Wait past the first /get_data poll so the ownership filter has engaged."""
    page.wait_for_timeout(int(seconds * 1000))


def experiment_unfiltered_window(browser, base_url: str) -> None:
    """E6: is another session's upload listed before the filter turns on?"""
    print("\nE6  the unfiltered dropdown window")

    owner = browser.new_context()
    owner_page = owner.new_page()
    owner_page.goto(f"{base_url}/viewer")
    owner_page.evaluate(
        """async () => {
            await fetch('/session/identify', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({email: 'owner@example.com', consent: true}),
            });
        }"""
    )
    stem = owner_page.evaluate(
        """async () => {
            const stl = 'solid s\\nfacet normal 0 0 1\\nouter loop\\n'
                + 'vertex 0 0 0\\nvertex 1 0 0\\nvertex 0 1 0\\n'
                + 'endloop\\nendfacet\\nendsolid s\\n';
            const fd = new FormData();
            fd.append('file', new Blob([stl]), 'secret_part.stl');
            const r = await fetch('/upload', {method: 'POST', body: fd});
            return (await r.json()).filename;
        }"""
    )
    print(f"  owner uploaded {stem!r} as an identified session")

    observer = browser.new_context()
    page = observer.new_page()

    samples: list[tuple[float, bool, int]] = []
    start = time.monotonic()
    page.goto(f"{base_url}/viewer")
    while time.monotonic() - start < SAMPLE_SECONDS:
        try:
            options = page.evaluate(
                """() => {
                    const dd = document.getElementById('model-list-dropdown');
                    return dd ? [...dd.options].map(o => o.text) : [];
                }"""
            )
        except Exception:
            options = []
        elapsed = time.monotonic() - start
        visible = any("secret_part" in o for o in options)
        samples.append((elapsed, visible, len(options)))
        page.wait_for_timeout(int(SAMPLE_INTERVAL * 1000))

    exposed = [t for t, vis, _ in samples if vis]
    if not exposed:
        record("E6", "INDEPENDENT", "the observer never listed the owner's upload")
    else:
        first, last = exposed[0], exposed[-1]
        still = last >= samples[-1][0] - (SAMPLE_INTERVAL * 2)
        tail = "still listed when sampling stopped" if still else f"removed at {last:.2f}s"
        record(
            "E6",
            "INTERFERES",
            f"another session's upload was listed from {first:.2f}s to {last:.2f}s ({tail})",
        )

    counts = {n for _, _, n in samples}
    print(f"  dropdown option counts seen: {sorted(counts)}")
    owner.close()
    observer.close()


def experiment_empty_builtin_set(browser, base_url: str) -> None:
    """E7: what does an empty builtin list actually do? Issue #123 item 4 claims it opens up."""
    print("\nE7  behaviour when builtin_model_stems arrives empty")

    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto(f"{base_url}/viewer")
    settle(page, 6.0)

    outcome = page.evaluate(
        """() => {
            builtinModelStems = [];
            lastModelListSignature = null;
            updateModelList(['alpha', 'beta']);
            const dd = document.getElementById('model-list-dropdown');
            return {
                options: [...dd.options].map(o => o.text),
                disabled: dd.disabled,
            };
        }"""
    )
    if outcome["options"] == ["alpha", "beta"]:
        record("E7", "INTERFERES", "an empty builtin set showed every model, as the issue claims")
    elif outcome["disabled"] or outcome["options"] in ([], ["No models found"]):
        record(
            "E7",
            "INDEPENDENT",
            f"an empty builtin set EMPTIES the dropdown: {outcome['options']} "
            f"(disabled={outcome['disabled']}). Issue #123 item 4 has this backwards",
        )
    else:
        record("E7", "INCONCLUSIVE", f"unexpected dropdown state: {outcome}")
    ctx.close()


def experiment_ingest_broadcast(browser, base_url: str) -> None:
    """E5b: does /ingest?open=1 move a tab belonging to someone else?"""
    print("\nE5b  does an ingest hijack an uninvolved viewer")

    ctx_a, ctx_b = browser.new_context(), browser.new_context()
    page_a, page_b = ctx_a.new_page(), ctx_b.new_page()
    for p in (page_a, page_b):
        p.goto(f"{base_url}/viewer")
    settle(page_a, 8.0)

    # Observe the raw channel too, so a tab that drops the frame is distinguishable
    # from a frame that never arrived. This adds a second subscriber; observational only.
    for p in (page_a, page_b):
        p.evaluate(
            """() => {
                window.__sse = [];
                const es = new EventSource('/events');
                es.onmessage = e => window.__sse.push(e.data);
            }"""
        )

    before = {"A": read_state(page_a), "B": read_state(page_b)}
    print(f"  before: A={before['A']['sbModel']!r} idx={before['A']['value']!r} "
          f"img={digest(before['A']['img'])}")

    # Two ingests 7 s apart: the first may be dropped by a tab whose cached model list
    # predates it, the second lands on a tab that has since polled and knows the stem.
    moved = False
    for round_no in (1, 2):
        subprocess.run(
            ["curl", "-sS", "-F", f"file=@{INGEST_MODEL}", "-F", f"first_name=Probe{round_no}",
             f"{base_url}/ingest?open=1"],
            capture_output=True, check=False,
        )
        page_a.wait_for_timeout(3000)
        after = {"A": read_state(page_a), "B": read_state(page_b)}
        for who in ("A", "B"):
            changed = (
                after[who]["sbModel"] != before[who]["sbModel"]
                or after[who]["value"] != before[who]["value"]
                or digest(after[who]["img"]) != digest(before[who]["img"])
            )
            if changed:
                moved = True
                record(
                    "E5b",
                    "INTERFERES",
                    f"round {round_no}: tab {who} moved with no user action: "
                    f"{before[who]['sbModel']!r} -> {after[who]['sbModel']!r}, "
                    f"idx {before[who]['value']!r} -> {after[who]['value']!r}",
                )
        if moved:
            break
        page_a.wait_for_timeout(7000)

    frames = page_a.evaluate("() => window.__sse || []")
    load_frames = [f for f in frames if "load_model" in f]
    if not moved:
        if load_frames:
            record(
                "E5b",
                "INTERFERES",
                f"{len(load_frames)} load_model frame(s) reached an uninvolved session's "
                f"event stream: {load_frames[:2]}. No tab moved this run; see E5c for why "
                f"that is timing, not protection",
            )
        else:
            record("E5b", "INDEPENDENT", "no model switch reached an uninvolved session")
    else:
        print(f"  load_model frames seen on an uninvolved stream: {len(load_frames)}")

    # E5c: the guard at viewer.js:1814 only fires when the pushed stem is already in
    # this tab's cached model list. An ingest always mints a fresh hashed stem, so a
    # tab that has not polled since cannot act on it. That is an accident of timing,
    # not a session check. Feed the same handler a stem the tab already knows and see
    # whether anything at all stands between a broadcast and a hijack.
    print("\nE5c  is the indexOf guard the only thing protecting an uninvolved tab")
    settle(page_b, 6.0)
    hijack = page_b.evaluate(
        """() => {
            const before = {model: currentModel, label: (document.getElementById('sb-model')||{}).textContent};
            const known = lastFullModelList.find(s => String(lastFullModelList.indexOf(s)) !== currentModel);
            if (!known) return {skipped: 'no second model known to this tab'};
            applyServerState({load_model: known});
            return {
                before,
                pushed: known,
                after: {model: currentModel, label: (document.getElementById('sb-model')||{}).textContent},
            };
        }"""
    )
    if hijack.get("skipped"):
        record("E5c", "INCONCLUSIVE", hijack["skipped"])
    elif hijack["before"]["model"] != hijack["after"]["model"]:
        record(
            "E5c",
            "INTERFERES",
            f"pushing a known stem ({hijack['pushed']!r}) moved an uninvolved tab "
            f"from index {hijack['before']['model']!r} to {hijack['after']['model']!r} "
            f"with no session check. The ingest case is masked only because a fresh "
            f"ingest stem is not yet in the tab's cached list",
        )
    else:
        record("E5c", "INDEPENDENT", f"the tab refused the pushed model switch: {hijack}")

    ctx_a.close()
    ctx_b.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8635")
    parser.add_argument("--headed", action="store_true", help="show the browser")
    args = parser.parse_args()

    print(f"probing {args.base_url}")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        try:
            experiment_unfiltered_window(browser, args.base_url)
            experiment_empty_builtin_set(browser, args.base_url)
            experiment_ingest_broadcast(browser, args.base_url)
        finally:
            browser.close()

    print("\n" + "=" * 70)
    for experiment, verdict, detail in results:
        print(f"{experiment:5s} {verdict:13s} {detail}")
    print("=" * 70)

    interferes = [r for r in results if r[1] == "INTERFERES"]
    print(json.dumps({"total": len(results), "interferes": len(interferes)}))
    return 1 if interferes else 0


if __name__ == "__main__":
    sys.exit(main())
