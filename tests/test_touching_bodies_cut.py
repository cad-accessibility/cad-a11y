"""A cut through bodies that touch face to face draws the slice (#253).

orientation_test_block is a block with a cube on its +X face, a part on top and
a cone off its +Y side. Cuts through the cube or the top part came out empty,
because meshlib's boolean reads two bodies sharing a face as a self-intersecting
mesh, reports "Bad contour", and returns a partial mesh with nothing on the plane.
"""

from __future__ import annotations

import io

import numpy as np
import pytest
import trimesh

from src.converter.single_view_stl import get_cut_faces


@pytest.fixture(scope="module")
def touching_block() -> trimesh.Trimesh:
    block = trimesh.creation.box(extents=(40, 30, 20))  # centred on the origin
    cube = trimesh.creation.box(extents=(8, 8, 8))
    cube.apply_translation((24, 9, 4))  # on the +X face
    top = trimesh.creation.box(extents=(10, 8, 5))
    top.apply_translation((0, 9, 12.5))  # on the top face
    cone = trimesh.creation.cone(radius=4, height=8)
    cone.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))
    cone.apply_translation((0, 15, 0))  # off the +Y face
    # Through an STL, as an upload arrives, so coincident vertices are merged.
    data = trimesh.util.concatenate([block, cube, top, cone]).export(file_type="stl")
    return trimesh.load(io.BytesIO(data), file_type="stl")


@pytest.mark.parametrize("view", ["front", "top", "left"])
@pytest.mark.parametrize("depth", [d / 10 for d in range(1, 10)])
def test_every_slice_inside_the_model_has_something_on_the_plane(touching_block, view, depth):
    bbox = (*touching_block.bounds[0], *touching_block.bounds[1])
    faces = get_cut_faces(touching_block.copy(), view, depth, bbox)
    assert faces.area > 0, f"{view} at {depth:.0%} drew an empty display"


def test_a_mesh_that_falls_apart_into_open_pieces_is_not_cut_piece_by_piece(monkeypatch):
    """A mesh with every face written twice splits into one piece per face pair, none
    of them closed. Cutting each on its own fixes nothing and ran thousands of
    booleans per cut on the mug from #59, which has 3480 faces written twice."""
    from src.converter import plane_intersection_utils as cut

    box = trimesh.creation.box(extents=(4, 3, 2)).subdivide().subdivide()
    doubled = trimesh.Trimesh(box.vertices, np.vstack([box.faces, box.faces[:, ::-1]]))
    mesh = trimesh.load(io.BytesIO(doubled.export(file_type="stl")), file_type="stl")
    assert len(mesh.split(only_watertight=False)) > 100

    calls = []
    real = cut._difference
    monkeypatch.setattr(cut, "_difference", lambda *args: calls.append(1) or real(*args))
    cut.cut_shape_with_plane(mesh, np.zeros(3), np.array([0.0, 1.0, 0.0]))
    assert len(calls) <= 2
