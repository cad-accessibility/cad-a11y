import numpy as np
import networkx as nx
from shapely import polygonize, polygonize_full, unary_union, line_merge
import matplotlib.pyplot as plt
from shapely.geometry import Polygon, MultiPolygon, Point, LineString
from .plane_intersection_utils import depth_peeling_single_depth_with_bbox
from matplotlib.path import Path
import trimesh
import cv2
import os
from matplotlib.patches import PathPatch
from copy import deepcopy
import svgwrite
from .render_low_res import low_res_render

from OCC.Core.GeomAbs import GeomAbs_Plane
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Extend.DataExchange import write_stl_file
from OCC.Core.TopoDS import topods
from OCC.Core.TopAbs import TopAbs_SOLID, TopAbs_FACE
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.gp import gp_Pnt, gp_Dir, gp_Trsf, gp_Vec, gp_Ax1
from OCC.Core.Quantity import Quantity_NOC_BLACK, Quantity_NOC_RED, Quantity_NOC_WHITE, Quantity_Color, Quantity_TOC_RGB
from OCC.Core.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_EDGE
from OCC.Core.STEPControl import STEPControl_Reader
from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
from OCC.Display.backend import load_pyqt5
from OCC.Display.SimpleGui import init_display

def scale_point(p, bounds, resolution):
    xmin, xmax, ymin, ymax = bounds
    x = int((p[0] - xmin) / (xmax - xmin) * (resolution - 1))
    y = int((p[1] - ymin) / (ymax - ymin) * (resolution - 1))
    return x, resolution - 1 - y  # flip y-axis

def unscale_point(px, py, bounds, resolution):
    xmin, xmax, ymin, ymax = bounds
    x = px / (resolution - 1) * (xmax - xmin) + xmin
    y = (resolution - 1 - py) / (resolution - 1) * (ymax - ymin) + ymin
    return x, y
def get_bbox(lines):
    pts = []
    for line in lines:
        pts += line
    pts = np.array(pts)
    return [np.min(pts[:, 0]), np.max(pts[:, 0]), np.min(pts[:, 1]), np.max(pts[:, 1])]

def polygon_to_path(polygon: Polygon):
    """Convert a Shapely polygon (with holes) into a matplotlib Path."""
    vertices = []
    codes = []

    # Exterior
    x, y = polygon.exterior.coords.xy
    points = list(zip(x, y))
    vertices.extend(points)
    codes.extend([Path.MOVETO] + [Path.LINETO] * (len(points) - 2) + [Path.CLOSEPOLY])

    # Interiors (holes)
    for interior in polygon.interiors:
        x, y = interior.coords.xy
        points = list(zip(x, y))
        vertices.extend(points)
        codes.extend([Path.MOVETO] + [Path.LINETO] * (len(points) - 2) + [Path.CLOSEPOLY])

    return Path(vertices, codes)

def polygon_to_path_multi(polygon_or_multipolygon):
    """Convert Polygon or MultiPolygon to a Matplotlib Path."""
    if isinstance(polygon_or_multipolygon, Polygon):
        polygons = [polygon_or_multipolygon]
    elif isinstance(polygon_or_multipolygon, MultiPolygon):
        polygons = list(polygon_or_multipolygon.geoms)
    else:
        raise ValueError("Input must be a Polygon or MultiPolygon")

    all_vertices = []
    all_codes = []

    for poly in polygons:
        exterior = np.array(poly.exterior.coords)
        vertices = np.concatenate([
            exterior,
            [[0, 0]]  # Dummy for CLOSEPOLY
        ])
        codes = [Path.MOVETO] + [Path.LINETO] * (len(exterior) - 1) + [Path.CLOSEPOLY]

        all_vertices.append(vertices)
        all_codes.append(codes)

        # Add interior holes (optional)
        for interior in poly.interiors:
            ring = np.array(interior.coords)
            ring_vertices = np.concatenate([
                ring,
                [[0, 0]]  # Dummy for CLOSEPOLY
            ])
            ring_codes = [Path.MOVETO] + [Path.LINETO] * (len(ring) - 1) + [Path.CLOSEPOLY]

            all_vertices.append(ring_vertices)
            all_codes.append(ring_codes)

    vertices = np.concatenate(all_vertices)
    codes = np.concatenate(all_codes)
    return Path(vertices, codes)


def write_svg_lines(filename, lines, width=500, height=500, stroke_width=1.0):
    if len(lines) == 0:
        x_min, x_max, y_min, y_max = 0, 100, 0, 100
    else:
        x_min, x_max, y_min, y_max = get_bbox(lines)

    scale_factor = np.min([width/(x_max-x_min), height/(y_max-y_min)])

    dwg = svgwrite.Drawing(filename,
        size=(f"{width}px", f"{height}px"),
    )
    dwg = svgwrite.Drawing(filename, size=(f"{width}px", f"{height}px"))
    
    for line in lines:
        copied_line = deepcopy(line)
        for i in range(len(line)):
            copied_line[i] = [scale_factor*(line[i][0]-x_min), scale_factor*(line[i][1]-y_min)]
        dwg.add(dwg.polyline(points=copied_line, stroke='black', stroke_width=stroke_width, fill="none"))
    
    dwg.save()

def sample_edge(edge, view, num_samples=20):
    adaptor = BRepAdaptor_Curve(edge)
    first = adaptor.FirstParameter()
    last = adaptor.LastParameter()
    
    # Sample parameters
    params = np.linspace(first, last, num_samples)
    points_2d = [[adaptor.Value(u).X(), adaptor.Value(u).Y()] for u in params]

    return points_2d

# Loop through edges in a shape
def sample_all_edges_projected(shape, view, num_samples=20):
    edge_explorer = TopExp_Explorer(shape, TopAbs_EDGE)
    all_edge_polylines = []

    while edge_explorer.More():
        edge = edge_explorer.Current()
        polyline = sample_edge(edge, view, num_samples)
        if polyline:
            all_edge_polylines.append(polyline)
        edge_explorer.Next()

    return all_edge_polylines

def apply_location_to_shape(shape):
    loc = shape.Location()
    if loc.IsIdentity():
        return shape  # no transform needed
    trsf = loc.Transformation()
    return BRepBuilderAPI_Transform(shape, trsf, True).Shape()

def round_line(l, tol=1e-7):
    return [[round(l[0][0]/tol)*tol, round(l[0][1]/tol)*tol],
            [round(l[1][0]/tol)*tol, round(l[1][1]/tol)*tol]]

def group_lines_by_buffer(lines, buffer_radius=1e-6):
    """
    lines: list of shapely LineString objects
    buffer_radius: distance to consider lines "connected"
    Returns: list of lists of LineString objects (groups)
    """
    n = len(lines)
    G = nx.Graph()
    G.add_nodes_from(range(n))

    # Precompute buffered polygons
    buffers = [l.buffer(buffer_radius) for l in lines]

    # Connect lines whose buffers intersect
    for i in range(n):
        for j in range(i+1, n):
            if buffers[i].intersects(buffers[j]):
                G.add_edge(i, j)

    # Connected components are line groups
    groups = []
    for comp in nx.connected_components(G):
        #group = [lines[i] for i in comp]
        group = [i for i in comp]
        groups.append(group)
    return groups

def closed_linestrings_from_groups(groups):
    """
    groups: list of lists of LineString objects
    Returns: list of closed LineStrings (or Polygons)
    """
    closed_loops = []
    for group in groups:
        merged = unary_union(group)
        # polygonize returns polygons; convert exterior to LineString if needed
        polygons = list(polygonize(merged))
        for poly in polygons:
            closed_loops.append(LineString(poly.exterior.coords))
    return closed_loops

def get_cut_out_polygon_from_face_edges(face_edges):
    biggest_poly_i = 0
    biggest_area = -1
    #for line in face_edges:
    #     line = np.array(line)
    #     plt.plot(line[:,0], line[:,1])
    ##ax.set_aspect('equal')
    #plt.show()
    # get intersecting lines
    groups = group_lines_by_buffer([LineString(line) for line in face_edges])
    print(groups)
    polygons = []
    for group in groups:
        polygons.append(unary_union(get_regions([face_edges[i] for i in group])))
    #polygons = polygonize([line_merge(group) for group in groups])
    #fig, ax = plt.subplots()
    #for poly in polygons:
    #    print("poly")
    #    path = polygon_to_path_multi(poly)
    #    patch = PathPatch(path, edgecolor='black', alpha=0.5)
    #    ax.add_patch(patch)
    #    x, y = poly.exterior.xy
    #    ax.plot(x, y)
    #    #for line in projected_edges:
    #    #    ax.plot(np.array(line)[:, 0], np.array(line)[:, 1])
    #    #ax.fill(x, y, alpha=0.5)  # alpha for transparency, optional

    #    ax.set_aspect('equal')
    #    plt.show()

    #polygons, dangles, cuts, invalids = polygonize_full([LineString(round_line(line)).buffer(0.001) for line in face_edges])
    #polygons = list(polygons.geoms)
    print("polygons")
    print(polygons)
    for i, poly in enumerate(polygons):
        if poly.area > biggest_area:
            biggest_poly_i = i
            print(biggest_poly_i)
            biggest_area = poly.area
    # everything else are holes
    face_poly = polygons[biggest_poly_i]
    #print(biggest_area)
    for i, line in enumerate(polygons):
        if i == biggest_poly_i:
            continue
        print("diff")
        print(face_poly.contains(line))
        print(line.contains(face_poly))
        face_poly = face_poly.difference(line)
    #polygons[biggest_poly_i] = face_poly
    return face_poly

def project_shape_section_faces(myshape, plane_origin, normal_dir):

    all_shape_edges = []
    regions = []
    exp = TopExp_Explorer(myshape, TopAbs_FACE)
    while exp.More():
        #face = apply_location_to_shape(topods.Face(exp.Current()))
        face = topods.Face(exp.Current())
        adaptor = BRepAdaptor_Surface(face)
        if adaptor.GetType() == GeomAbs_Plane:
            #print(face)
            plane = adaptor.Plane()
            # Compare normals
            n = plane.Axis().Direction()
            origin = plane.Location()
            print("normals")
            print(n.X(), n.Y(), n.Z())
            print(normal_dir.X(), normal_dir.Y(), normal_dir.Z())
            print("origins")
            print(origin.X(), origin.Y(), origin.Z())
            print(plane_origin.X(), plane_origin.Y(), plane_origin.Z())
            face_edges = []
            if np.isclose(normal_dir.X(), 1.0):
                if np.isclose(n.Z(), 1.0) and np.isclose(origin.Z(), plane_origin.X()):
                    face_edges, projector = project_shape(face)
                    print(face_edges)
            elif np.isclose(normal_dir.Y(), 1.0):
                if np.isclose(n.Z(), 1.0) and np.isclose(origin.Z(), plane_origin.Y()):
                    face_edges, projector = project_shape(face)
                    print(face_edges)
            elif np.isclose(normal_dir.Z(), 1.0):
                if np.isclose(n.Z(), 1.0) and np.isclose(origin.Z(), plane_origin.Z()):
                    face_edges, projector = project_shape(face)
                    #print("here")
                    #print(face_edges)
                    #for line in face_edges:
                    #    line = np.array(line)
                    #    plt.plot(line[:,0], line[:,1])
                    #plt.show()
            all_shape_edges += face_edges
            if len(face_edges) > 0:
                # identify outermost line
#                biggest_poly_i = 0
#                biggest_area = -1
#                #for line in face_edges:
#                #     line = np.array(line)
#                #     plt.plot(line[:,0], line[:,1])
#                ##ax.set_aspect('equal')
#                #plt.show()
#                # get intersecting lines
#                groups = group_lines_by_buffer([LineString(line) for line in face_edges])
#                print(groups)
#                polygons = []
#                for group in groups:
#                    polygons.append(unary_union(get_regions([face_edges[i] for i in group])))
#                #polygons = polygonize([line_merge(group) for group in groups])
#
#                #polygons, dangles, cuts, invalids = polygonize_full([LineString(round_line(line)).buffer(0.001) for line in face_edges])
#                #polygons = list(polygons.geoms)
#                print("polygons")
#                print(polygons)
#                for i, poly in enumerate(polygons):
#                    if poly.area > biggest_area:
#                        biggest_poly_i = i
#                        print(biggest_poly_i)
#                        biggest_area = poly.area
#                # everything else are holes
#                face_poly = polygons[biggest_poly_i]
#                #print(biggest_area)
#                for i, line in enumerate(polygons):
#                    if i == biggest_poly_i:
#                        continue
#                    print("diff")
#                    print(face_poly.contains(line))
#                    print(line.contains(face_poly))
#                    face_poly = face_poly.difference(line)
                #polygons[biggest_poly_i] = face_poly
                face_poly = get_cut_out_polygon_from_face_edges(face_edges)
                regions.append(face_poly)

                #fig, ax = plt.subplots()
                #path = polygon_to_path(face_poly)
                #for interior in face_poly.interiors:
                #    x, y = interior.coords.xy
                #    ax.plot(x, y, c="black")
                #patch = PathPatch(path, edgecolor='black', facecolor="red", alpha=0.5)
                #ax.add_patch(patch)
                #x, y = face_poly.exterior.xy
                #ax.plot(x, y, c="black")
                #plt.show()

        exp.Next()
    #fig, ax = plt.subplots()
    #print(regions)
    #cmap = ["g", "r"]
    #for i, poly in enumerate(regions):
    #    path = polygon_to_path(poly)
    #    print(path)
    #    patch = PathPatch(path, edgecolor='black', facecolor=cmap[i], alpha=0.5)
    #    #else:
    #    #    #continue
    #    #    patch = PathPatch(path, edgecolor='black', facecolor="blue", alpha=0.5)
    #    ax.add_patch(patch)
    #    x, y = poly.exterior.xy
    #    ax.plot(x, y, c="black")
    #    #for interior in poly.interiors:
    #    #    x, y = interior.coords.xy
    #    #    ax.plot(x, y, c="black")
    #    #ax.fill(x, y, alpha=0.5)  # alpha for transparency, optional
    #    #ax.scatter(vertices_2d[:, 0], vertices_2d[:,1])
    #for line in face_edges:
    #    line = np.array(line)
    #    ax.plot(line[:,0], line[:,1])

    #ax.set_aspect('equal')
    #plt.show()

    return all_shape_edges, regions 

def project_shape(shape):

    algo = HLRBRep_Algo()
    algo.Add(shape)
    algo.Update()
    projector = algo.Projector()
    algo.Hide()
    hlr = HLRBRep_HLRToShape(algo)

    edges_projected = {
        "visible": hlr.VCompound(),
        "visible_smooth": hlr.Rg1LineVCompound(),
        "visible_seam": hlr.RgNLineVCompound(),
        "visible_outlines": hlr.OutLineVCompound(),
        "visible_iso": hlr.IsoLineVCompound(),
        #"hidden": hlr.HCompound(),
        #"hidden_smooth": hlr.Rg1LineHCompound(),
        #"hidden_seam": hlr.RgNLineHCompound(),
        #"hidden_outlines": hlr.OutLineHCompound(),
        #"hidden_iso": hlr.IsoLineHCompound(),
    }
    all_edges = []
    for edge_type_key in edges_projected.keys():
        if not edges_projected[edge_type_key] is None:
            edges_2d = sample_all_edges_projected(edges_projected[edge_type_key], "wow")
        else:
            edges_2d = []
        all_edges += edges_2d
    #print(all_edges)
    algo.OutLinedShapeNullify()  # clears internal face data (safe to call both)
    return all_edges, projector

views = {
    "top": {
        "eye": gp_Pnt(0, 0, -1000),
        "dir": gp_Dir(0, 0, 1)
    },
    "front": {
        "eye": gp_Pnt(0, -1000, 0),
        "dir": gp_Dir(0, 1, 0)
    },
    "side": {
        "eye": gp_Pnt(-1000, 0, 0),
        "dir": gp_Dir(1, 0, 0)
    }
}

def get_regions(all_edges):
    # Define raster size and compute bounds
    W, H = 1000, 1000

    all_pts = [pt for edge in all_edges for pt in edge]
    xmin = min(p[0] for p in all_pts)
    xmax = max(p[0] for p in all_pts)
    ymin = min(p[1] for p in all_pts)
    ymax = max(p[1] for p in all_pts)
    bounds = (xmin, xmax, ymin, ymax)
    if np.isclose(xmax-xmin, 0.0) or np.isclose(ymax-ymin, 0.0):
        return []

    # Step 1: Rasterize all edges to binary canvas
    canvas = np.zeros((H, W), dtype=np.uint8)

    for edge in all_edges:
        int_pts = [scale_point(p, bounds, W) for p in edge]
        #plt.plot(np.array(int_pts)[:, 0], np.array(int_pts)[:,1])
        cv2.polylines(canvas, [np.array(int_pts, dtype=np.int32)], isClosed=False, color=255, thickness=3)

    # Step 2: Find contours
    contours, _ = cv2.findContours(canvas, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    cv2.drawContours(canvas, contours, -1, (0,255,0), 3)
    #plt.show()

    polygons = []
    for cnt in contours:
        pts = cnt.reshape(-1, 2)
        if len(pts) < 4:
            continue
        pts = [unscale_point(p[0], p[1], bounds, W) for p in pts]
        poly = Polygon(pts)
        # Optional: Check if polygon is valid (simple polygon)
        if poly.is_valid and poly.area > 0.0001:
            polygons.append(poly)
        else:
            print("polygon not valid")
            print(poly.is_valid)
            print(poly.area)
    return polygons

def plot_regions(polygons):
    fig, ax = plt.subplots()
    for poly in polygons:
        x, y = poly.exterior.xy
        ax.plot(x, y)
        ax.fill(x, y, alpha=0.5)  # alpha for transparency, optional

    ax.set_aspect('equal')
    plt.show()

def cut_hole_from_face(face, face_edges, normal_dir):
    # TODO: make this more robust by sampling multiple times and getting a consensus mask
    #face = apply_location_to_shape(topods.Face(exp.Current()))
    write_stl_file(face, "face.stl", linear_deflection=0.1)
    face_mesh = trimesh.load_mesh("face.stl")
    potential_face_polys = []
    for i in range(10):
        vertices, face_ids = trimesh.sample.sample_surface_even(face_mesh, count=10)
        #if np.isclose(normal_dir.X(), 1.0):
        #    vertices_2d = [[v[1], v[2]] for v in vertices]
        #elif np.isclose(normal_dir.Y(), 1.0):
        #    vertices_2d = [[v[0], v[1]] for v in vertices]
        #elif np.isclose(normal_dir.Z(), 1.0):
        #    print(vertices)
            #vertices_2d = [[v[0], v[2]] for v in vertices]
        vertices_2d = [[v[0], v[1]] for v in vertices]

        vertices_2d = np.array(vertices_2d)

        W, H = 1000, 1000

        all_pts = [pt for edge in face_edges for pt in edge]
        xmin = min(p[0] for p in all_pts)
        xmax = max(p[0] for p in all_pts)
        ymin = min(p[1] for p in all_pts)
        ymax = max(p[1] for p in all_pts)
        bounds = (xmin, xmax, ymin, ymax)
        if np.isclose(xmax-xmin, 0.0) or np.isclose(ymax-ymin, 0.0):
            return []

        # Step 1: Rasterize all edges to binary canvas
        canvas = np.zeros((H, W), dtype=np.uint8)

        for edge in face_edges:
            int_pts = [scale_point(p, bounds, W) for p in edge]
            #plt.plot(np.array(int_pts)[:, 0], np.array(int_pts)[:,1])
            cv2.polylines(canvas, [np.array(int_pts, dtype=np.int32)], isClosed=False, color=255, thickness=3)
        for v in vertices_2d:
            p = np.array(scale_point(v, bounds, W), dtype=np.int32)
            p[0] = max(0, p[0])
            p[0] = min(W-1, p[0])
            p[1] = max(0, p[1])
            p[1] = min(H-1, p[1])
            #print(p)
            #cv2.circle(canvas, center=(p[0], p[1]), radius=1, color=255, thickness=1)
            cv2.floodFill(canvas, None, p, 255)
        #cv2.imshow("canvas", canvas)
        #cv2.waitKey(0)

        # Step 2: Find contours
        contours, hierarchy = cv2.findContours(canvas, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
        new_img = np.zeros((H, W, 3), dtype=np.uint8)
        #cv2.drawContours(new_img, contours, -1, (0,255,0), 3)
        #cv2.imshow("new_img", new_img)
        #cv2.waitKey(0)
        exteriors = []
        holes_dict = {}

        for i, cnt in enumerate(contours):
            # Flatten contour to (x, y) pairs
            pts = cnt[:, 0, :]
            pts = [unscale_point(p[0], p[1], bounds, W) for p in pts]

            if hierarchy[0][i][3] == -1:  
                # No parent → it's an exterior
                exteriors.append((i, pts))
            else:
                # Has a parent → it's a hole
                parent = hierarchy[0][i][3]
                holes_dict.setdefault(parent, []).append(pts)

        # Build shapely polygons
        polygons = []
        for idx, ext in exteriors:
            holes = holes_dict.get(idx, [])
            poly = Polygon(ext, holes)
            polygons.append(poly)

        # If you expect just one main shape:
        poly = polygons[0]
        potential_face_polys.append(poly)


    areas = [poly.area for poly in potential_face_polys]
    votes = {}
    max_key = 0
    max_votes = 0
    for i in range(len(areas)):
        key = np.round(areas[i],5)
        if not key in votes.keys():
            votes[key] = 0
        votes[key] += 1
        if votes[key] > max_votes:
            max_votes = votes[key]
            max_key = i
    #print(votes)
    #print(max_key)
    #all_edges += projected_edges
    face_poly = potential_face_polys[max_key]
    #fig, ax = plt.subplots()
    ##for poly in faces:
    #path = polygon_to_path_multi(poly)
    #patch = PathPatch(path, edgecolor='black', alpha=0.5)
    #ax.add_patch(patch)
    #x, y = poly.exterior.xy
    #ax.plot(x, y)
    ##ax.fill(x, y, alpha=0.5)  # alpha for transparency, optional
    ##ax.scatter(vertices_2d[:, 0], vertices_2d[:,1])

    #ax.set_aspect('equal')
    #plt.show()
    return face_poly
    #exit()

def has_neighbor(i, j, array):
    return 
        
def shrink_filled_faces(filled_faces, brep):
    filled_faces_np = np.zeros([filled_faces.shape[0], filled_faces.shape[1]], dtype=int)
    brep_np = np.zeros([filled_faces.shape[0], filled_faces.shape[1]], dtype=int)
    for i in range(filled_faces.shape[0]):
        for j in range(filled_faces.shape[1]):
            if np.all(filled_faces[i][j] == [0,0,0,255]):
                filled_faces_np[i][j] = 1
            if brep[i][j][0] == 0:
                brep_np[i][j] = 1

    change = True
    #plt.imshow(filled_faces_np)
    #plt.show()
    #exit()
    while change:
        change = False
        for i in range(filled_faces.shape[0]):
            for j in range(filled_faces.shape[1]):
                if filled_faces_np[i][j] == 0 or brep_np[i][j] == 1:
                    continue
                #print(i, j, filled_faces_np[i][j], brep_np[i][j])
                before_value = filled_faces_np[i][j]
                filled_faces_np[i][j] = np.min([
                    filled_faces_np[max(i-1,0)][max(0, j-1)],
                    filled_faces_np[max(i-1,0)][j],
                    filled_faces_np[max(i-1,0)][min(brep_np.shape[1]-1, j+1)],
                    filled_faces_np[i][max(0, j-1)],
                    filled_faces_np[i][j],
                    filled_faces_np[i][min(brep_np.shape[1]-1, j+1)],
                    filled_faces_np[min(brep_np.shape[0]-1, i+1)][max(0, j-1)],
                    filled_faces_np[min(brep_np.shape[0]-1, i+1)][j],
                    filled_faces_np[min(brep_np.shape[0]-1, i+1)][min(brep_np.shape[1]-1, j+1)]])
                #print(i, j, filled_faces_np[i][j])
                change = change or (filled_faces_np[i][j] != before_value)
        #print(change)
        #plt.imshow(filled_faces_np)
        #plt.show()

    filled_rgba = np.zeros((filled_faces_np.shape[0], filled_faces_np.shape[1], 4), dtype=np.uint8)
    mask = filled_faces_np == 1
    filled_rgba[mask] = [0, 0, 0, 255]

    outline = filled_rgba.copy()
    for i in range(outline.shape[0]):
        for j in range(outline.shape[1]):
            if np.all(filled_rgba[i][j] == [0,0,0,255]):
                if not np.min([
                    filled_faces_np[max(i-1,0)][max(0, j-1)],
                    filled_faces_np[max(i-1,0)][j],
                    filled_faces_np[max(i-1,0)][min(brep_np.shape[1]-1, j+1)],
                    filled_faces_np[i][max(0, j-1)],
                    filled_faces_np[i][j],
                    filled_faces_np[i][min(brep_np.shape[1]-1, j+1)],
                    filled_faces_np[min(brep_np.shape[0]-1, i+1)][max(0, j-1)],
                    filled_faces_np[min(brep_np.shape[0]-1, i+1)][j],
                    filled_faces_np[min(brep_np.shape[0]-1, i+1)][min(brep_np.shape[1]-1, j+1)]]) == 0:
                    outline[i][j] = [255,255,255,255]
    #plt.imshow(filled_rgba)
    #plt.show()
    return filled_rgba, outline

def sort_face_hole_polygons(myshape, shape_regions, plane_origin, normal_dir):
    faces = []
    holes = []
    vertices_2d = []
    exp = TopExp_Explorer(myshape, TopAbs_FACE)
    while exp.More():
        #face = apply_location_to_shape(topods.Face(exp.Current()))
        face = topods.Face(exp.Current())
        adaptor = BRepAdaptor_Surface(face)
        if adaptor.GetType() == GeomAbs_Plane:
            plane = adaptor.Plane()
            # Compare normals
            n = plane.Axis().Direction()
            origin = plane.Location()
            if np.isclose(normal_dir.X(), 1.0):
                if np.isclose(n.X(), 1.0) and np.isclose(origin.X(), plane_origin.X()):
                    write_stl_file(face, "face.stl", linear_deflection=0.1)
                    face_mesh = trimesh.load_mesh("face.stl")
                    vertices, face_ids = trimesh.sample.sample_surface_even(face_mesh, count=10)
                    vertices_2d += [[v[1], v[2]] for v in vertices]
            elif np.isclose(normal_dir.Y(), 1.0):
                if np.isclose(n.Z(), 1.0) and np.isclose(origin.Z(), plane_origin.Y()):
                    write_stl_file(face, "face.stl", linear_deflection=0.1)
                    face_mesh = trimesh.load_mesh("face.stl")
                    vertices, face_ids = trimesh.sample.sample_surface_even(face_mesh, count=10)
                    vertices_2d += [[v[0], v[1]] for v in vertices]
            elif np.isclose(normal_dir.Z(), 1.0):
                if np.isclose(n.Y(), 1.0) and np.isclose(origin.Y(), plane_origin.Z()):
                    write_stl_file(face, "face.stl", linear_deflection=0.1)
                    face_mesh = trimesh.load_mesh("face.stl")
                    vertices, face_ids = trimesh.sample.sample_surface_even(face_mesh, count=10)
                    vertices_2d += [[v[0], v[2]] for v in vertices]
        exp.Next()

    vertices_2d = np.array(vertices_2d)
    if len(vertices_2d) == 0:
        return faces
    for poly in shape_regions:
        if np.sum([poly.contains(Point(v)) for v in vertices_2d])/len(vertices_2d) < 0.5:
            holes.append(poly)
        else:
            faces.append(poly)
    # subtract holes from faces
    for i, f in enumerate(faces):
        for h in holes:
            faces[i] = faces[i].difference(h)

    fig, ax = plt.subplots()
    for poly in faces:
        path = polygon_to_path_multi(poly)
        patch = PathPatch(path, edgecolor='black', alpha=0.5)
        ax.add_patch(patch)
        #x, y = poly.exterior.xy
        #ax.plot(x, y)
        #ax.fill(x, y, alpha=0.5)  # alpha for transparency, optional
        ax.scatter(vertices_2d[:, 0], vertices_2d[:,1])

    ax.set_aspect('equal')
    plt.show()
    # mark holes


        #ray_origins = np.array([v + [0,0,+1e-5] for v in vertices])
        #ray_directions = np.zeros([len(vertices), 3])
        #ray_directions[:, 2] = +1

    #    #if is_face_visible(face, myshape):
    #    #    visible_faces.append(face)
    #    #else:
    #    #    exp.Next()
    #    #    continue
    #    projected_edges, _ = project_shape(face)
    #    polys = get_regions(projected_edges)
    #    all_edges += projected_edges

    #    all_face_polys += polys
    #    face_regions[face_counter] = [face, polys]
    #    face_counter += 1
    #    exp.Next()

    return faces, holes 

def get_single_view(shape, bbox, cut_depth=0.9, view_key="top", rendering_mode="brep", imposed_ax_limits=[]):

    normal_dir = views[view_key]["dir"]
    get_section_only = rendering_mode=="slice"
    myshape, plane_origin = depth_peeling_single_depth_with_bbox(shape, gp_Dir(normal_dir.X(), normal_dir.Y(), normal_dir.Z()), 
                                                                  depth=cut_depth, bbox=bbox)

    for i,k in enumerate(views.keys()):
        trsf = gp_Trsf()
        axis = gp_Ax1(gp_Pnt(0, 0, 0), views[k]["dir"])
        trsf.SetRotation(axis, -0.5 * 3.141592653589793)  # -90 degrees in radians
        myshape = BRepBuilderAPI_Transform(myshape, trsf, True).Shape()
        if k == view_key:
            break

#    bbox = Bnd_Box()
#    brepbndlib.Add(myshape, bbox)
#    xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
#    # center bbox for projection
#    trsf = gp_Trsf()
#    trsf.SetTranslation(gp_Vec(-(xmin+xmax)/2.0, -(ymin+ymax)/2.0, -(zmin+zmax)/2.0,))
#    myshape = BRepBuilderAPI_Transform(myshape, trsf, True).Shape()

    algo = HLRBRep_Algo()
    algo.Add(myshape)
    algo.Projector()
    algo.Update()
    algo.Hide()

    hlr = HLRBRep_HLRToShape(algo)

    edges_projected = {
        "visible": hlr.VCompound(),
        "visible_smooth": hlr.Rg1LineVCompound(),
        "visible_seam": hlr.RgNLineVCompound(),
        "visible_outlines": hlr.OutLineVCompound(),
        "visible_iso": hlr.IsoLineVCompound(),

        #"hidden": hlr.HCompound(),
        #"hidden_smooth": hlr.Rg1LineHCompound(),
        #"hidden_seam": hlr.RgNLineHCompound(),
        #"hidden_outlines": hlr.OutLineHCompound(),
        #"hidden_iso": hlr.IsoLineHCompound(),
    }

    face_counter = 0
    all_face_polys = []
    #face_regions = project_faces(myshape)


    if get_section_only:
        all_face_edges, regions = project_shape_section_faces(myshape, plane_origin, normal_dir)
        #if len(all_shape_edges) > 0:
        #    shape_regions = get_regions(all_shape_edges)
        #    faces, holes = sort_face_hole_polygons(myshape, shape_regions, plane_origin, normal_dir)
        #    exit()
        #    plot_regions(shape_regions)
    # collect all visible faces
    if rendering_mode in ["outline", "filled"]:
        visible_faces = []
        exp = TopExp_Explorer(myshape, TopAbs_FACE)
        while exp.More():
            face = apply_location_to_shape(topods.Face(exp.Current()))
            #if is_face_visible(face, myshape):
            #    visible_faces.append(face)
            #else:
            #    exp.Next()
            #    continue
            projected_edges, _ = project_shape(face)
            #polys = get_regions(projected_edges)
            face_poly = cut_hole_from_face(face, projected_edges, normal_dir)
            #fig, ax = plt.subplots()
            ##for poly in faces:
            #path = polygon_to_path_multi(face_poly)
            #patch = PathPatch(path, edgecolor='black', alpha=0.5)
            #ax.add_patch(patch)
            #x, y = face_poly.exterior.xy
            #ax.plot(x, y)
            #ax.set_aspect('equal')
            #plt.show()

            all_face_polys.append(face_poly)
            #face_regions[face_counter] = [face, polys]
            face_counter += 1
            exp.Next()
            #fig, ax = plt.subplots()
            ##for poly in all_face_polys:
            #path = polygon_to_path_multi(face_poly)
            #patch = PathPatch(path, edgecolor='black', alpha=0.5)
            #ax.add_patch(patch)
            #x, y = face_poly.exterior.xy
            ##ax.plot(x, y)
            #for line in projected_edges:
            #    ax.plot(np.array(line)[:, 0], np.array(line)[:, 1])
            ##ax.fill(x, y, alpha=0.5)  # alpha for transparency, optional

            #ax.set_aspect('equal')
            #plt.show()
        #render_faces(shape_regions, myshape)
        #exit()
        brep_faces, outline_faces, filled_faces, _ = low_res_render([], [], all_face_polys, save_file=False, 
                                                                            imposed_ax_limits=imposed_ax_limits, VERBOSE=False)
    all_edges = []
    for edge_type_key in edges_projected.keys():
        if not edges_projected[edge_type_key] is None:
            edges_2d = sample_all_edges_projected(edges_projected[edge_type_key], views[view_key])
        else:
            edges_2d = []
        all_edges += edges_2d
        
    #file_name = os.path.join("svg_views", os.path.basename(step_file).split(".")[0]+"_"+str(cut_depth)+"_"+str(hatch_step)+"_"+view_key)
    #write_svg_lines(file_name+".svg", 
    #                all_edges, width=800, height=800)
    brep, outline, filled, ax_limits = low_res_render(all_edges, [], [], save_file=False, imposed_ax_limits=imposed_ax_limits)
    if rendering_mode in ["outline", "filled"]:
        filled_faces, outline_faces = shrink_filled_faces(brep_faces, brep)
    if get_section_only:
        brep, outline_0, filled_0, ax_limits = low_res_render(all_face_edges, [], [], save_file=False, imposed_ax_limits=imposed_ax_limits)
        brep_1, outline_1, filled_1, ax_limits = low_res_render([], [], regions, save_file=False, imposed_ax_limits=imposed_ax_limits)

        merged_map = outline_0.copy()
        for i in range(outline_0.shape[0]):
            for j in range(outline_0.shape[1]):
                if np.all(filled_0[i][j] == [0,0,0,255]) and np.all(brep_1[i][j] == [0,0,0,255]):
                    merged_map[i][j] = [0,0,0,255]
        return merged_map, ax_limits

    if rendering_mode == "brep":
        return brep, ax_limits
    if rendering_mode == "outline":
        return outline_faces, ax_limits
    if rendering_mode == "filled":
        return filled_faces, ax_limits
        return filled, ax_limits

if __name__ == '__main__':
    # check for pyqt5
    #if not load_pyqt5():
    print("main")
    #    raise IOError("pyqt5 required to run this test")
    #create_orthographic_view(shape, cut_depth=0.9, view_key="top", view_mode="filled")
    #display, start_display, add_menu, add_function_to_menu = init_display("pyqt5")
    #display.DisplayShape(edges_projected["visible"], color=Quantity_NOC_BLACK, update=True)
    #display.DisplayShape(myshape, color=Quantity_NOC_BLACK, update=True)
    #start_display()