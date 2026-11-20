"""A mesh that isn't closed still cuts (#59).

Two ways CAD exports break meshlib's boolean, which then returns nothing: every
face written twice (the mug in #59 has 3480 of its 7424 faces twice), and cracks,
where one face is split more finely than the face next to it, so the edge between
them doesn't match up (a T-junction). Cut mode used to draw an empty display for
those slices, while the modes that draw the solid drew the whole uncut model.
"""

import numpy as np
import pytest
import trimesh
from trimesh import remesh

from src.converter.plane_intersection_utils import depth_peeling_single_depth_with_bbox
from src.converter.single_view_stl import get_cut_faces


@pytest.fixture(scope="module")
def cracked_box():
    """A 4 x 3 x 2 box whose +X face is subdivided and its neighbours aren't,
    loaded through an STL as an upload would be."""
    box = trimesh.creation.box(extents=(4.0, 3.0, 2.0))
    plus_x = np.nonzero(np.isclose(box.face_normals[:, 0], 1.0))[0]
    vertices, faces = remesh.subdivide(box.vertices, box.faces, face_index=plus_x)
    data = trimesh.Trimesh(vertices, faces).export(file_type="stl")
    mesh = trimesh.load(trimesh.util.wrap_as_stream(data), file_type="stl")
    assert not mesh.is_watertight
    return mesh


@pytest.fixture(scope="module")
def doubled_box():
    """A 4 x 3 x 2 box with every face written twice, the second time facing in."""
    box = trimesh.creation.box(extents=(4.0, 3.0, 2.0))
    doubled = trimesh.Trimesh(box.vertices, np.vstack([box.faces, box.faces[:, ::-1]]))
    data = doubled.export(file_type="stl")
    mesh = trimesh.load(trimesh.util.wrap_as_stream(data), file_type="stl")
    assert not mesh.is_watertight
    return mesh


def _bbox(mesh):
    return (*mesh.bounds[0], *mesh.bounds[1])


# The cross-section of the box from each view, cut across y, z and x.
SECTION_AREA = {"front": 8.0, "top": 12.0, "left": 6.0}


@pytest.mark.parametrize("depth", [0.25, 0.5, 0.75])
@pytest.mark.parametrize("view", sorted(SECTION_AREA))
@pytest.mark.parametrize("model", ["cracked_box", "doubled_box"])
def test_the_cut_draws_the_whole_cross_section(request, model, view, depth):
    mesh = request.getfixturevalue(model)
    faces = get_cut_faces(mesh.copy(), view, depth, _bbox(mesh))
    assert faces.area == pytest.approx(SECTION_AREA[view], rel=1e-6)


@pytest.mark.parametrize("model", ["cracked_box", "doubled_box"])
def test_the_solid_behind_the_plane_is_cut_too(request, model):
    """The modes that draw the solid used to get the whole model back."""
    mesh = request.getfixturevalue(model)
    normal = np.array([0.0, 1.0, 0.0])  # a plane that crosses the cracked face
    kept, origin = depth_peeling_single_depth_with_bbox(mesh.copy(), normal, 0.5, _bbox(mesh))
    assert kept.bounds[1][1] == pytest.approx(origin[1], abs=1e-6)
    assert kept.bounds[0][1] == pytest.approx(-1.5)


def test_a_hole_in_a_cracked_section_stays_a_hole():
    """A ring, cracked on its outer wall, cut across its axis, leaves the middle empty."""
    ring = trimesh.creation.annulus(r_min=1.0, r_max=2.0, height=2.0, sections=32)
    outer = np.nonzero(np.linalg.norm(ring.triangles_center[:, :2], axis=1) > 1.5)[0]
    outer = outer[np.isclose(ring.face_normals[outer, 2], 0.0)][::2]  # every other one
    vertices, faces = remesh.subdivide(ring.vertices, ring.faces, face_index=outer)
    cracked = trimesh.load(
        trimesh.util.wrap_as_stream(trimesh.Trimesh(vertices, faces).export(file_type="stl")), file_type="stl"
    )
    assert not cracked.is_watertight
    intact = get_cut_faces(ring.copy(), "top", 0.3, _bbox(ring))
    faces = get_cut_faces(cracked.copy(), "top", 0.3, _bbox(cracked))
    assert intact.area == pytest.approx(np.pi * (2.0**2 - 1.0**2), rel=0.03)  # 32-sided
    assert faces.area == pytest.approx(intact.area, rel=1e-6)
