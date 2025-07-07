import numpy as np
import polyscope as ps
import trimesh
import cv2
from shapely.geometry import LineString
from shapely.ops import polygonize
from shapely.geometry import Polygon, MultiPolygon
from shapely.validation import explain_validity
import random
import create_hatch_lines_single_depth
import matplotlib.pyplot as plt
import networkx as nx
import plane_intersection_utils
import os
from copy import deepcopy
import svgwrite

from OCC.Extend.DataExchange import write_stl_file
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.TopoDS import topods
from OCC.Core.TopAbs import TopAbs_SOLID, TopAbs_FACE
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
from OCC.Core.BRepGProp import brepgprop_SurfaceProperties, brepgprop
from OCC.Core.GProp import GProp_GProps
from OCC.Core.BRepClass3d import BRepClass3d_SolidClassifier
from OCC.Core.TopAbs import TopAbs_IN, TopAbs_ON
from scipy.spatial import ConvexHull
from OCC.Core.TopoDS import TopoDS_Compound, TopoDS_Builder, TopoDS_Face
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeFace


def get_bbox(lines):
    pts = []
    for line in lines:
        pts += line
    pts = np.array(pts)
    return [np.min(pts[:, 0]), np.max(pts[:, 0]), np.min(pts[:, 1]), np.max(pts[:, 1])]

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

def project_face(face, proj, num_samples=20):
    exp = TopExp_Explorer(face, TopAbs_EDGE)
    all_edges_projected = []
    while exp.More():
        edge = topods.Edge(exp.Current())
        sampled_edge = plane_intersection_utils.sample_edge(edge)
        projected_edge = [proj.Project(p)[:2] for p in sampled_edge]
        all_edges_projected.append(projected_edge)
        exp.Next()

    return all_edges_projected

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

def snap_point(pt, tol=1e-5):
    return (round(pt[0] / tol) * tol, round(pt[1] / tol) * tol)

def remove_consecutive_duplicates(points):
    cleaned = [points[0]]
    for pt in points[1:]:
        if pt != cleaned[-1]:
            cleaned.append(pt)
    return cleaned

def build_valid_polygon(loop):
    loop = remove_consecutive_duplicates(loop)
    if len(loop) < 3:
        return None
    poly = Polygon(loop)
    if not poly.is_valid:
        print("Repairing invalid polygon:", explain_validity(poly))
        poly = poly.buffer(0)
    return poly if poly.is_valid and poly.area > 1e-6 else None

def handle_polygon(poly):
    if isinstance(poly, Polygon):
        return [poly]
    elif isinstance(poly, MultiPolygon):
        return list(poly.geoms)
    else:
        return []

def quantize_point(p, q=1e-1):
    return (round(p[0] / q) * q, round(p[1] / q) * q)

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
        cv2.polylines(canvas, [np.array(int_pts, dtype=np.int32)], isClosed=False, color=255, thickness=1)

    # Step 2: Find contours
    contours, _ = cv2.findContours(canvas, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)

    polygons = []
    for cnt in contours:
        pts = cnt.reshape(-1, 2)
        if len(pts) < 4:
            continue
        pts = [unscale_point(p[0], p[1], bounds, W) for p in pts]
        poly = Polygon(pts)
        # Optional: Check if polygon is valid (simple polygon)
        if poly.is_valid and poly.area > 0.01:
            polygons.append(poly)
    return polygons

def plot_regions(polygons):
    fig, ax = plt.subplots()
    for poly in polygons:
        x, y = poly.exterior.xy
        ax.plot(x, y)
        ax.fill(x, y, alpha=0.5)  # alpha for transparency, optional

    ax.set_aspect('equal')
    plt.show()

def is_face_centroid_inside_solid(face, solid, use_convex=False):
    props = GProp_GProps()
    #brepgprop_SurfaceProperties(face, props)
    brepgprop.SurfaceProperties(face, props)
    centroid = props.CentreOfMass()


    if use_convex:
        cvx_solid = compute_convex_hull_shape(solid)
        classifier = BRepClass3d_SolidClassifier(cvx_solid, centroid, 1e-1)
    else:
        classifier = BRepClass3d_SolidClassifier(solid, centroid, 1e-1)
    #print(classifier.State(), classifier.State() == TopAbs_ON)
    return classifier.State() == TopAbs_IN

def compute_convex_hull_shape(shape):
    write_stl_file(shape, "solid.stl", linear_deflection=0.1)
    solid_mesh = trimesh.load_mesh("solid.stl")
    pts = solid_mesh.vertices
    hull = ConvexHull(pts)

    # Create OCC faces from hull simplices (triangles)
    builder = TopoDS_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)

    for simplex in hull.simplices:
        tri_pts = [pts[i] for i in simplex]
        # Create a triangular face
        p1, p2, p3 = [gp_Pnt(*p) for p in tri_pts]

        poly = BRepBuilderAPI_MakePolygon(p1, p2, p3, True)
        face = BRepBuilderAPI_MakeFace(poly.Wire())
        builder.Add(compound, face.Face())
    return compound

def is_face_centroid_hidden(line_3d, face, solid):

    origin_pnt = line_3d.Location()
    direction_vec = line_3d.Direction()
    ray_origin = [origin_pnt.X(), origin_pnt.Y(), origin_pnt.Z()]
    ray_direction = [direction_vec.X(), direction_vec.Y(), direction_vec.Z()]
    print(ray_origin)
    print(ray_direction)
    write_stl_file(face, "face.stl", linear_deflection=0.1)
    write_stl_file(solid, "solid.stl", linear_deflection=0.1)
    face_mesh = trimesh.load_mesh("face.stl")
    #print(face_mesh.vertices)
    solid_mesh = trimesh.load_mesh("solid.stl")
    locations, index_ray, index_tri = solid_mesh.ray.intersects_location(
        ray_origins=[ray_origin],
        ray_directions=[ray_direction]
    )
    ps.init()
    ps.register_surface_mesh("solid", solid_mesh.vertices, solid_mesh.faces)
    ps.register_surface_mesh("face", face_mesh.vertices, face_mesh.faces)
    ps.register_point_cloud("intersections", locations)
    ps.register_point_cloud("origin", np.array([ray_origin]))
    ps.show()

def is_face_centroid_inside_solid_mesh(face, solid):
    write_stl_file(face, "face.stl", linear_deflection=0.1)
    write_stl_file(solid, "solid.stl", linear_deflection=0.1)
    face_mesh = trimesh.load_mesh("face.stl")
    #print(face_mesh.vertices)
    solid_mesh = trimesh.load_mesh("solid.stl")
    ps.init()
    ps.register_surface_mesh("solid", solid_mesh.vertices, solid_mesh.faces)
    ps.register_surface_mesh("face", face_mesh.vertices, face_mesh.faces)
    ps.show()

def apply_location_to_shape(shape):
    loc = shape.Location()
    if loc.IsIdentity():
        return shape  # no transform needed
    trsf = loc.Transformation()
    return BRepBuilderAPI_Transform(shape, trsf, True).Shape()

def create_orthographic_views(step_file, cut_depth=0.9, hatch_step=0.012):
    step_reader = STEPControl_Reader()
    step_reader.ReadFile(step_file)
    step_reader.TransferRoot()
    orig_shape = step_reader.Shape()

    ## add hatching lines on faces
    myshape = create_hatch_lines_single_depth.create_hatch_shape(orig_shape, 
        depth=0.5, hatch_step=15, with_sampling=False)
    orig_shape = plane_intersection_utils.normalize_shape_diagonal(orig_shape)

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

    for view_key in views.keys():
        print("view_key", view_key)

        trsf = gp_Trsf()
        axis = gp_Ax1(gp_Pnt(0, 0, 0), views[view_key]["dir"])  # Y-axis
        trsf.SetRotation(axis, -0.5 * 3.141592653589793)  # -90 degrees in radians
        myshape = BRepBuilderAPI_Transform(myshape, trsf, True).Shape()
        orig_shape = BRepBuilderAPI_Transform(orig_shape, trsf, True).Shape()

        bbox = Bnd_Box()
        brepbndlib.Add(myshape, bbox)
        xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
        # center bbox for projection
        trsf = gp_Trsf()
        trsf.SetTranslation(gp_Vec(-(xmin+xmax)/2.0, -(ymin+ymax)/2.0, -(zmin+zmax)/2.0,))
        myshape = BRepBuilderAPI_Transform(myshape, trsf, True).Shape()
        orig_shape = BRepBuilderAPI_Transform(orig_shape, trsf, True).Shape()

        all_shape_edges, projector = project_shape(myshape)

        shape_regions = get_regions(all_shape_edges)
        #plot_regions(shape_regions)
        exp = TopExp_Explorer(myshape, TopAbs_FACE)
        all_edges = []
        face_regions = {}
        face_counter = 0
        all_face_polys = []
        while exp.More():
            face = apply_location_to_shape(topods.Face(exp.Current()))

            #face_global = apply_location_to_shape(face)
            projected_edges, _ = project_shape(face)
            all_edges += projected_edges

            polys = get_regions(projected_edges)
            all_face_polys += polys
            face_regions[face_counter] = [face, polys]
            face_counter += 1
            #plot_regions(polys)
            exp.Next()
        
        print(len(shape_regions))
        plot_regions(shape_regions)
        for shape_region in shape_regions:
            if shape_region.area < 0.01:
                continue
            plot_regions([shape_region])
            best_poly = [-1, -1]
            best_iou = -1
            for face_id in face_regions.keys():
                for i, face_region in enumerate(face_regions[face_id][1]):
                    iou = face_region.intersection(shape_region).area / face_region.union(shape_region).area
                    #print(iou)
                    #plot_regions([shape_region, face_region])
                    if iou > best_iou:
                        best_poly = [face_id, i]
                        best_iou = iou
            #print(shape_region.area)
            #print(best_iou, best_poly)
            #print(face_regions[best_poly[0]][1][best_poly[1]])
            center_2d = face_regions[best_poly[0]][1][best_poly[1]].representative_point()
            print(center_2d)
            internal_face = is_face_centroid_inside_solid(face_regions[best_poly[0]][0], orig_shape)
            cvx_internal_face = is_face_centroid_inside_solid(face_regions[best_poly[0]][0], orig_shape, use_convex=True)
            hidden_face = (not internal_face) and cvx_internal_face
            #internal_face = is_face_centroid_hidden(projector.Shoot(center_2d.x, center_2d.y), 
            #                                        face_regions[best_poly[0]][0], orig_shape)
            print("internal_face", internal_face)
            #plot_regions([shape_region, face_regions[best_poly[0]][1][best_poly[1]]])


        write_svg_lines(os.path.join("svg_views", 
                        os.path.basename(step_file).split(".")[0]+"_"+str(cut_depth)+"_"+str(hatch_step)+"_"+view_key+".svg"),
                        all_shape_edges, width=800, height=800)

if __name__ == '__main__':
    # check for pyqt5
    #if not load_pyqt5():
    #    raise IOError("pyqt5 required to run this test")
    create_orthographic_views(os.path.join("..", "models", "brep", "lighter.step"), cut_depth=0.9, hatch_step=0.10)
    #display, start_display, add_menu, add_function_to_menu = init_display("pyqt5")
    #display.DisplayShape(edges_projected["visible"], color=Quantity_NOC_BLACK, update=True)
    #display.DisplayShape(myshape, color=Quantity_NOC_BLACK, update=True)
    #start_display()