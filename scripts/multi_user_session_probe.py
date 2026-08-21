#!/usr/bin/env python
"""Simulate many concurrent /viewer users and measure whether they stay independent.

Issue #123 asks whether the normal viewer keeps concurrent sessions apart. The
single-pair probe in concurrent_session_probe.py answers that for two sessions; this
one answers it at the scale the tool is actually meant for, with enough users that
ordering, filtering and cross-talk effects become visible rather than anecdotal.

Every user is a separate Playwright browser context, which is a separate cookie jar
and a separate sessionStorage. Users act through asyncio.gather, so uploads and
renders genuinely interleave rather than running one after another.

Cohorts, so that both halves of the ownership rule get exercised:

  owner-N     unique email, uploads one model. Should see built-ins plus its own.
  shared-N    two users sharing one email. By design these SHOULD see each other's
              models: db.session_owns_model widens to every session with the same
              identifier so a person can open their models on a second device.
  anon-N      never answers the consent dialog, so never gets a cookie. Two of them
              upload anyway, which is the ownerless-upload case.

Usage:

    docker compose up -d --build
    python scripts/multi_user_session_probe.py --users 24

Requires playwright in the cad-a11y env:

    pip install playwright && playwright install chromium
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import struct
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from playwright.async_api import async_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent


def make_stl(scale: float, shape: int = 0) -> bytes:
    """A binary STL of a box with distinctive size AND proportions.

    Every user gets different geometry. If they all uploaded the same file, two users
    could swap models without any image or bounding box changing, and an experiment
    comparing renders would report independence it had not actually established.

    Proportions have to vary, not just scale: the viewer fits a model to the display,
    so two boxes with the same aspect ratio at different sizes produce byte-identical
    images once fitted, and only the raw bounding box tells them apart.
    """
    x = scale
    y = scale * (0.25 + 0.09 * (shape % 7))
    z = scale * (0.15 + 0.07 * ((shape // 7) % 5 + shape % 3))
    corners = [
        (0, 0, 0), (x, 0, 0), (x, y, 0), (0, y, 0),
        (0, 0, z), (x, 0, z), (x, y, z), (0, y, z),
    ]
    quads = [
        (0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1),
        (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0),
    ]
    triangles = []
    for a, b, c, d in quads:
        triangles.append((corners[a], corners[b], corners[c]))
        triangles.append((corners[a], corners[c], corners[d]))

    out = b"\0" * 80 + struct.pack("<I", len(triangles))
    for v0, v1, v2 in triangles:
        out += struct.pack("<12fH", 0.0, 0.0, 1.0, *v0, *v1, *v2, 0)
    return out

SHARED_EMAIL = "shared-device@example.com"

# Distinct render settings so each user's expected image is unique. Cycled per user.
VIEWS = ["x+", "x-", "y+", "y-", "z+", "z-"]
RENDER_MODES = ["Filled", "Outline", "XRay"]


@dataclass
class User:
    name: str
    cohort: str            # owner | shared | anon
    email: str | None
    context: object = None
    page: object = None
    uploaded_stem: str | None = None
    session_cookie: str | None = None
    scale: float = 1.0
    own_bbox: list | None = None
    # measurements
    server_list: list[str] = field(default_factory=list)
    owned: list[str] = field(default_factory=list)
    dropdown: list[str] = field(default_factory=list)

    @property
    def identified(self) -> bool:
        return self.email is not None


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def build_roster(total: int) -> list[User]:
    """Two shared-email users and four anonymous, the rest unique-email owners."""
    n_shared, n_anon = 2, 4
    n_owner = max(1, total - n_shared - n_anon)
    roster = [User(f"owner-{i:02d}", "owner", f"owner{i:02d}@example.com") for i in range(n_owner)]
    roster += [User(f"shared-{i}", "shared", SHARED_EMAIL) for i in range(n_shared)]
    roster += [User(f"anon-{i}", "anon", None) for i in range(n_anon)]
    return roster


async def start_user(browser, base_url: str, user: User) -> None:
    user.context = await browser.new_context()
    user.page = await user.context.new_page()
    await user.page.goto(f"{base_url}/viewer", wait_until="domcontentloaded")
    if user.identified:
        await user.context.request.post(
            f"{base_url}/session/identify",
            data={"email": user.email, "consent": True},
        )
        for cookie in await user.context.cookies():
            if cookie["name"] == "cad_session":
                user.session_cookie = cookie["value"]


async def upload(base_url: str, user: User, label: str, scale: float = 1.0, shape: int = 0) -> None:
    """Upload one uniquely named model, with geometry unique to this user."""
    resp = await user.context.request.post(
        f"{base_url}/upload",
        multipart={
            "file": {
                "name": f"{label}.stl",
                "mimeType": "application/octet-stream",
                "buffer": make_stl(scale, shape),
            }
        },
    )
    if resp.ok:
        body = await resp.json()
        user.uploaded_stem = Path(body.get("filename", "")).stem or None
        user.scale = scale


async def collect_visibility(base_url: str, user: User) -> None:
    """What the server offers this user, what it says they own, what the UI shows."""
    resp = await user.context.request.get(f"{base_url}/models")
    if resp.ok:
        user.server_list = (await resp.json()).get("model_list", [])

    resp = await user.context.request.get(f"{base_url}/session/models")
    if resp.ok:
        user.owned = [m["filename"] for m in (await resp.json()).get("models", [])]

    try:
        user.dropdown = await user.page.evaluate(
            """() => {
                const dd = document.getElementById('model-list-dropdown');
                return dd ? [...dd.options].map(o => o.text.replace(/ \\(your upload\\)$/, '')) : [];
            }"""
        )
    except Exception:
        user.dropdown = []


async def render(base_url: str, user: User, model, **params) -> dict:
    body = {
        "current_model": model,
        "view": "y-",
        "zoom": "0",
        "depth": 0,
        "renderMode": "Filled",
        "mode": "single",
    }
    body.update(params)
    resp = await user.context.request.post(
        f"{base_url}/render",
        headers={"Content-Type": "application/json"},
        data=json.dumps(body),
    )
    if not resp.ok:
        return {}
    return await resp.json()


async def harness_selfcheck(base_url: str, user: User) -> bool:
    """Confirm render parameters actually reach the server before trusting any result.

    If the body were posted in a form encoding, request.get_json would return None,
    every render would fall back to the same defaults, and an experiment comparing
    images would quietly compare identical pictures and report independence.
    """
    a = await render(base_url, user, 0, view="y-", renderMode="Filled", depth=0, zoom="0")
    b = await render(base_url, user, 0, view="z+", renderMode="Outline", depth=60, zoom="2")
    ok = bool(a.get("image_base64")) and bool(b.get("image_base64")) \
        and a["image_base64"] != b["image_base64"]
    print(f"  harness self-check: render params take effect = {ok}")
    if not ok:
        print("  !! render parameters are NOT reaching the server; results below are void")
    return ok


# ---------------------------------------------------------------------------
# Experiments
# ---------------------------------------------------------------------------

async def exp_visibility_matrix(users: list[User], findings: list) -> None:
    """Can user i see user j's upload? Server side and UI side, separately."""
    print("\n" + "=" * 78)
    print("M1  upload visibility matrix")
    print("=" * 78)

    uploaders = [u for u in users if u.uploaded_stem]
    print(f"  {len(uploaders)} users uploaded a model")

    server_leaks: list[tuple[str, str]] = []
    ui_leaks: list[tuple[str, str]] = []
    own_missing: list[str] = []
    shared_ok = 0

    for viewer in users:
        for uploader in uploaders:
            stem = uploader.uploaded_stem
            if viewer is uploader:
                if stem not in viewer.dropdown:
                    own_missing.append(f"{viewer.name} cannot see its own {stem}")
                continue
            same_identity = (
                viewer.identified
                and uploader.identified
                and viewer.email == uploader.email
            )
            if stem in viewer.server_list:
                if same_identity:
                    shared_ok += 1
                else:
                    server_leaks.append((viewer.name, uploader.name))
            if stem in viewer.dropdown and not same_identity:
                ui_leaks.append((viewer.name, uploader.name))

    total_pairs = sum(1 for v in users for u in uploaders if v is not u)
    print(f"  server side : {len(server_leaks)}/{total_pairs} cross-user pairs exposed")
    print(f"  UI side     : {len(ui_leaks)}/{total_pairs} cross-user pairs exposed")
    print(f"  same-email pairs that correctly shared: {shared_ok}")
    if own_missing:
        print(f"  users who cannot see their OWN upload: {len(own_missing)}")
        for line in own_missing[:5]:
            print(f"     {line}")

    findings.append((
        "M1-server",
        "INTERFERES" if server_leaks else "INDEPENDENT",
        f"GET /models exposed {len(server_leaks)}/{total_pairs} cross-user pairs",
    ))
    findings.append((
        "M1-ui",
        "INTERFERES" if ui_leaks else "INDEPENDENT",
        f"the dropdown exposed {len(ui_leaks)}/{total_pairs} cross-user pairs"
        + (f" e.g. {ui_leaks[0][0]} saw {ui_leaks[0][1]}'s model" if ui_leaks else ""),
    ))
    findings.append((
        "M1-own",
        "INTERFERES" if own_missing else "INDEPENDENT",
        f"{len(own_missing)} user(s) could not see their own upload",
    ))


async def exp_email_mapping(users: list[User], findings: list) -> None:
    """Two sessions sharing an email SHOULD see each other. That is the feature."""
    print("\n" + "=" * 78)
    print("M2  email mapping (expected to share, by design)")
    print("=" * 78)

    shared = [u for u in users if u.cohort == "shared"]
    if len(shared) < 2:
        findings.append(("M2", "INCONCLUSIVE", "not enough shared-email users"))
        return

    a, b = shared[0], shared[1]
    print(f"  {a.name} and {b.name} both identified as {SHARED_EMAIL}")
    print(f"  distinct cookies: {a.session_cookie != b.session_cookie}")
    a_sees_b = b.uploaded_stem and any(b.uploaded_stem in f for f in a.owned)
    b_sees_a = a.uploaded_stem and any(a.uploaded_stem in f for f in b.owned)
    print(f"  {a.name} owns: {a.owned}")
    print(f"  {b.name} owns: {b.owned}")

    if a_sees_b and b_sees_a:
        findings.append(("M2", "INDEPENDENT", "same-email sessions share models, as designed"))
    else:
        findings.append((
            "M2", "INCONCLUSIVE",
            f"same-email sharing did not round-trip ({a.name}->{b.name}={bool(a_sees_b)}, "
            f"{b.name}->{a.name}={bool(b_sees_a)})",
        ))


async def exp_cross_reachability(base_url: str, users: list[User], findings: list) -> None:
    """Even unlisted, can a user render someone else's model by naming its index?"""
    print("\n" + "=" * 78)
    print("M3  can a user render a model that is not theirs")
    print("=" * 78)

    uploaders = [u for u in users if u.uploaded_stem]
    if len(uploaders) < 2:
        findings.append(("M3", "INCONCLUSIVE", "not enough uploads"))
        return

    # Each user first renders their own model, so we know what their geometry looks
    # like. Every user's box has a different size, so a bounding box identifies whose
    # model came back. Without that, "a render succeeded" would not distinguish
    # getting someone else's model from getting a fallback to one's own.
    async def own(u: User) -> None:
        try:
            index = u.server_list.index(u.uploaded_stem)
        except ValueError:
            return
        result = await render(base_url, u, index)
        u.own_bbox = result.get("bbox")

    await asyncio.gather(*[own(u) for u in uploaders])
    known = [u for u in uploaders if u.own_bbox]
    distinct_bboxes = len({json.dumps(u.own_bbox) for u in known})
    print(f"  {len(known)} users rendered their own model, {distinct_bboxes} distinct geometries")

    async def attempt(viewer: User, target: User) -> tuple[str, str, bool]:
        try:
            index = viewer.server_list.index(target.uploaded_stem)
        except ValueError:
            return viewer.name, target.name, False
        result = await render(base_url, viewer, index)
        got = result.get("bbox")
        # Only counts if the geometry that came back is demonstrably the target's.
        return viewer.name, target.name, bool(got) and got == target.own_bbox

    pairs = [(known[i], known[(i + 1) % len(known)]) for i in range(len(known))]
    outcomes = await asyncio.gather(*[attempt(v, t) for v, t in pairs])
    reached = [(v, t) for v, t, ok in outcomes if ok]

    print(f"  {len(reached)}/{len(pairs)} users received another user's exact geometry")
    for v, t in reached[:4]:
        print(f"     {v} rendered {t}'s upload and got {t}'s bounding box")

    findings.append((
        "M3",
        "INTERFERES" if reached else "INDEPENDENT",
        f"{len(reached)}/{len(pairs)} users rendered another user's model and received "
        f"that user's exact geometry ({distinct_bboxes} distinct geometries in play)",
    ))


async def exp_concurrent_commands(base_url: str, users: list[User], findings: list) -> None:
    """Every user drives their own view/depth/zoom at once. Does anyone's drift?"""
    print("\n" + "=" * 78)
    print("M4  command independence under concurrent load")
    print("=" * 78)

    # Each user gets a distinct, repeatable render request against a public built-in
    # so the only variable is whether other users disturb it.
    settings = []
    for i, u in enumerate(users):
        settings.append({
            "view": VIEWS[i % len(VIEWS)],
            "renderMode": RENDER_MODES[i % len(RENDER_MODES)],
            "depth": (i * 7) % 90,
            "zoom": str(i % 4),
        })

    # Each user drives a different built-in. The braille frame is only 40x96, so
    # synthetic boxes collapse to a handful of identical images once fitted to the
    # display, and two users sharing an image would hide a swap between them. The
    # shipped models (mug, lego, cane tip, rocking chair) are visually distinct enough
    # to tell apart at that resolution.
    builtins = [i for i, stem in enumerate(users[0].server_list[:16])]

    def assigned_index(i: int) -> int:
        return builtins[i % len(builtins)] if builtins else 0

    async def baseline(u: User, s: dict, i: int) -> str:
        r = await render(base_url, u, assigned_index(i), **s)
        return r.get("image_base64", "")

    print(f"  {len(users)} users rendering {len(builtins)} distinct built-ins "
          f"with distinct settings concurrently...")
    first = await asyncio.gather(
        *[baseline(u, s, i) for i, (u, s) in enumerate(zip(users, settings))]
    )

    # Now everyone renders again, still concurrently and still interleaved with each
    # other. An image that changes means someone else's request altered this user's.
    second = await asyncio.gather(
        *[baseline(u, s, i) for i, (u, s) in enumerate(zip(users, settings))]
    )

    drifted = [
        users[i].name
        for i in range(len(users))
        if first[i] and second[i] and first[i] != second[i]
    ]
    empty = [users[i].name for i in range(len(users)) if not first[i] or not second[i]]

    print(f"  users whose repeated identical request changed: {len(drifted)}")
    if drifted:
        print(f"     {drifted[:6]}")
    if empty:
        print(f"  users with a failed render: {len(empty)} {empty[:4]}")

    findings.append((
        "M4",
        "INTERFERES" if drifted else "INDEPENDENT",
        f"{len(drifted)}/{len(users)} users saw their own repeated request return a "
        f"different image under concurrent load",
    ))

    # Distinctness: with different settings, images should differ between users.
    unique = len({d for d in first if d})
    print(f"  distinct images across {len(users)} users: {unique}")
    findings.append((
        "M4-distinct",
        "INDEPENDENT" if unique > 1 else "INTERFERES",
        f"{unique} distinct images across {len(users)} differently-configured users",
    ))


async def exp_pan_crosstalk(base_url: str, users: list[User], findings: list) -> None:
    """One user pans. Does anyone else's camera move?"""
    print("\n" + "=" * 78)
    print("M5  does one user's pan move everyone else's camera")
    print("=" * 78)

    watchers = users[:8]
    panner = users[-1]
    params = {"view": "y-", "renderMode": "Filled", "depth": 0, "zoom": "0"}

    async def sweep():
        """One measurement cycle, including the cache-defeating render."""
        base = await asyncio.gather(*[render(base_url, u, 0, **params) for u in watchers])
        for u in watchers:
            await render(base_url, u, 0, **{**params, "zoom": "1"})
        end = await asyncio.gather(*[render(base_url, u, 0, **params) for u in watchers])
        return [
            watchers[i].name
            for i in range(len(watchers))
            if base[i].get("image_base64") and end[i].get("image_base64")
            and base[i]["image_base64"] != end[i]["image_base64"]
        ]

    # Control first: the identical cycle with nobody panning. Anything that moves here
    # is an artefact of the cache-defeating render, not of the pan.
    control_moved = await sweep()
    print(f"  control (no pan): {len(control_moved)}/{len(watchers)} watchers moved")

    async def sweep_with_pan():
        base = await asyncio.gather(*[render(base_url, u, 0, **params) for u in watchers])
        for direction in ("left", "left", "up"):
            await render(base_url, panner, 0, move_camera_center=direction, **params)
        for u in watchers:
            await render(base_url, u, 0, **{**params, "zoom": "1"})
        end = await asyncio.gather(*[render(base_url, u, 0, **params) for u in watchers])
        return [
            watchers[i].name
            for i in range(len(watchers))
            if base[i].get("image_base64") and end[i].get("image_base64")
            and base[i]["image_base64"] != end[i]["image_base64"]
        ]

    moved = await sweep_with_pan()
    print(f"  {panner.name} panned: {len(moved)}/{len(watchers)} watchers moved")
    if moved:
        print(f"     {moved}")

    if control_moved:
        findings.append((
            "M5", "INCONCLUSIVE",
            f"the control cycle already moved {len(control_moved)}/{len(watchers)} watchers, "
            f"so the pan cannot be isolated",
        ))
    else:
        findings.append((
            "M5",
            "INTERFERES" if moved else "INDEPENDENT",
            f"one user's pan moved {len(moved)}/{len(watchers)} other users' cameras "
            f"(control with no pan moved 0)",
        ))


async def exp_selection_stability(base_url: str, users: list[User], findings: list) -> None:
    """A user picks a model. Others then upload. Does the first user's pick still mean the same file?"""
    print("\n" + "=" * 78)
    print("M6  does someone else's upload renumber my selection")
    print("=" * 78)

    picker = next((u for u in users if u.identified), users[0])
    listing = picker.server_list
    if len(listing) < 2:
        findings.append(("M6", "INCONCLUSIVE", "model list too short"))
        return

    target_index = len(listing) - 1
    target_stem = listing[target_index]
    print(f"  {picker.name} selected index {target_index} = {target_stem!r}")

    # Several other users upload names chosen to sort early inside the upload dir.
    latecomers = [u for u in users if u is not picker and u.identified][:4]
    await asyncio.gather(*[
        upload(base_url, u, f"000_late_{i}_{random.randint(1000, 9999)}")
        for i, u in enumerate(latecomers)
    ])
    print(f"  {len(latecomers)} other users then uploaded early-sorting names")

    resp = await picker.context.request.get(f"{base_url}/models")
    after = (await resp.json()).get("model_list", [])
    now_at_index = after[target_index] if target_index < len(after) else None
    print(f"  index {target_index} now names {now_at_index!r}")

    if now_at_index == target_stem:
        findings.append(("M6", "INDEPENDENT", f"index {target_index} still names {target_stem!r}"))
    else:
        findings.append((
            "M6", "INTERFERES",
            f"index {target_index} moved from {target_stem!r} to {now_at_index!r} because "
            f"other users uploaded",
        ))


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8635")
    parser.add_argument("--users", type=int, default=24)
    parser.add_argument("--settle", type=float, default=8.0,
                        help="seconds to wait for the client's first /get_data poll")
    args = parser.parse_args()

    random.seed(20260731)
    roster = build_roster(args.users)
    findings: list[tuple[str, str, str]] = []

    print(f"simulating {len(roster)} concurrent users against {args.base_url}")
    cohorts = defaultdict(int)
    for u in roster:
        cohorts[u.cohort] += 1
    print(f"cohorts: {dict(cohorts)}")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            print("\nstarting sessions...")
            await asyncio.gather(*[start_user(browser, args.base_url, u) for u in roster])
            identified = sum(1 for u in roster if u.session_cookie)
            print(f"  {identified}/{len(roster)} hold a cad_session cookie")

            print("uploading, concurrently...")
            uploaders = [u for u in roster if u.cohort in ("owner", "shared")]
            uploaders += [u for u in roster if u.cohort == "anon"][:2]
            await asyncio.gather(*[
                # A different size and shape each, so both the bounding box and the
                # fitted image say whose model it is.
                upload(args.base_url, u, f"model_of_{u.name.replace('-', '_')}",
                       scale=1.0 + 0.37 * i, shape=i)
                for i, u in enumerate(uploaders)
            ])
            got = sum(1 for u in roster if u.uploaded_stem)
            print(f"  {got}/{len(uploaders)} uploads registered a filename")

            # Identify and upload went through the context's HTTP client, not through
            # the page, so each page still holds the sessionOwnedModels it bootstrapped
            # with before the cookie existed. A real user's upload runs inside the page
            # and refreshes that. Reload so the client state matches the session state;
            # without this every user appears unable to see their own upload, which is
            # the harness talking rather than the app.
            print("reloading pages so each client bootstraps with its own cookie...")
            await asyncio.gather(*[u.page.reload(wait_until="domcontentloaded") for u in roster])

            print(f"settling {args.settle}s so every client completes a /get_data poll...")
            await asyncio.sleep(args.settle)

            if not await harness_selfcheck(args.base_url, roster[0]):
                return 2

            await asyncio.gather(*[collect_visibility(args.base_url, u) for u in roster])

            await exp_visibility_matrix(roster, findings)
            await exp_email_mapping(roster, findings)
            await exp_cross_reachability(args.base_url, roster, findings)
            await exp_concurrent_commands(args.base_url, roster, findings)
            await exp_pan_crosstalk(args.base_url, roster, findings)
            await exp_selection_stability(args.base_url, roster, findings)
        finally:
            await browser.close()

    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)
    for name, verdict, detail in findings:
        print(f"{name:14s} {verdict:13s} {detail}")
    bad = [f for f in findings if f[1] == "INTERFERES"]
    print("=" * 78)
    print(json.dumps({"experiments": len(findings), "interferes": len(bad)}))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
