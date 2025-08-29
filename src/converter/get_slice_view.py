import numpy as np
import polyscope as ps
from .plane_intersection_utils import depth_peeling_single_depth_with_bbox
import trimesh
import cv2
from shapely.geometry import LineString
from shapely.ops import polygonize
from shapely.geometry import Polygon, MultiPolygon, Point
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
from OCC.Core.BRepTools import breptools
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
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

def write_svg_lines(filename, lines, width=500, height=500, stroke_width=1.0, regions=[]):
    if len(lines) == 0:
        x_min, x_max, y_min, y_max = 0, 100, 0, 100
    else:
        x_min, x_max, y_min, y_max = get_bbox(lines)

    scale_factor = np.min([width/(x_max-x_min), height/(y_max-y_min)])

    dwg = svgwrite.Drawing(filename,
        size=(f"{width}px", f"{height}px"),
    )
    dwg = svgwrite.Drawing(filename, size=(f"{width}px", f"{height}px"))

    # MASKING
    front_mask = dwg.mask(id="front_mask")
    hidden_mask = dwg.mask(id="hidden_mask")
    internal_mask = dwg.mask(id="internal_mask")

    for region_entry in reversed(regions):
        if isinstance(region_entry["region"], MultiPolygon):
            for poly in region_entry["region"].geoms:
                points = list(poly.exterior.coords)
                copied_line = deepcopy(points)
                for i in range(len(points)):
                    copied_line[i] = [scale_factor*(points[i][0]-x_min), scale_factor*(points[i][1]-y_min)]
                copied_line.append(copied_line[0])
                if region_entry["internal_face"]:
                    internal_mask.add(dwg.polygon(points=copied_line, fill='white'))
                elif region_entry["hidden_face"]:
                    hidden_mask.add(dwg.polygon(points=copied_line, fill='white'))
                else:
                    front_mask.add(dwg.polygon(points=copied_line, fill='white'))
        else:
            points = list(region_entry["region"].exterior.coords)
            copied_line = deepcopy(points)
            for i in range(len(points)):
                copied_line[i] = [scale_factor*(points[i][0]-x_min), scale_factor*(points[i][1]-y_min)]
            copied_line.append(copied_line[0])
            if region_entry["internal_face"]:
                internal_mask.add(dwg.polygon(points=copied_line, fill='white'))
            elif region_entry["hidden_face"]:
                hidden_mask.add(dwg.polygon(points=copied_line, fill='white'))
            else:
                front_mask.add(dwg.polygon(points=copied_line, fill='white'))

    
    dwg.defs.add(front_mask)
    dwg.defs.add(hidden_mask)
    dwg.defs.add(internal_mask)

    # COLORED BG
    front_bg = dwg.rect(insert=(0, 0), size=("100%", "100%"), fill='#1b9e77')
    front_bg['mask'] = 'url(#front_mask)'
    dwg.add(front_bg)

    hidden_bg = dwg.rect(insert=(0, 0), size=("100%", "100%"), fill='#7570b3')
    hidden_bg['mask'] = 'url(#hidden_mask)'
    dwg.add(hidden_bg)

    internal_bg = dwg.rect(insert=(0, 0), size=("100%", "100%"), fill='#d95f02')
    internal_bg['mask'] = 'url(#internal_mask)'
    dwg.add(internal_bg)

    # HATCHING PATTERNS
#    stipple = dwg.pattern(id="stipple", size=(10, 10), patternUnits="userSpaceOnUse")
#    stipple.add(dwg.circle(center=(5, 5), r=3.0, fill='black'))
#    dwg.defs.add(stipple)
#
#    #diagonal = dwg.pattern(id="diagonal", size=(10, 10), patternUnits="userSpaceOnUse")
#    #diagonal.add(dwg.line(start=(-5, -5), end=(15, 15), stroke="black", stroke_width=2, stroke_linecap="square"))
#    #dwg.defs.add(diagonal)
#
#    spacing = 20
#    # Draw lines from top-left to bottom-right (45°)
#    for x in range(-width, width, spacing):
#        start = (x, 0)
#        end = (x + height, height)
#        dwg.add(dwg.line(start=start, end=end,
#                         stroke="black", stroke_width=2,
#                         stroke_linecap="butt",
#                         mask="url(#front_mask)"))
#
#    vertical = dwg.pattern(id="vertical", size=(10, 10), patternUnits="userSpaceOnUse")
#    vertical.add(dwg.line(start=(5, 0), end=(5, 10), stroke="black", stroke_width=2, stroke_linecap="square"))
#    dwg.defs.add(vertical)
#
#    dwg.add(dwg.rect(insert=(0, 0), size=("100%", "100%"), fill="url(#diagonal)", mask="url(#front_mask)"))
#    dwg.add(dwg.rect(insert=(0, 0), size=("100%", "100%"), fill="url(#vertical)", mask="url(#hidden_mask)"))
#    dwg.add(dwg.rect(insert=(0, 0), size=("100%", "100%"), fill="url(#stipple)", mask="url(#internal_mask)"))
    
    
    # OUTLINES
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

def sample_points_in_polygon(polygon, n_points):
    minx, miny, maxx, maxy = polygon.bounds
    points = []
    while len(points) < n_points:
        random_point = Point(np.random.uniform(minx, maxx),
                             np.random.uniform(miny, maxy))
        if polygon.contains(random_point):
            points.append(random_point)
    return points

def is_face_visible(face, shape, VERBOSE=False):

    if os.path.exists("solid.stl"):
        os.remove("solid.stl")  # silently remove
    if os.path.exists("face.stl"):
        os.remove("face.stl")  # silently remove
    write_stl_file(face, "face.stl", linear_deflection=0.001)
    face_mesh = trimesh.load_mesh("face.stl")
    write_stl_file(shape, "solid.stl", linear_deflection=0.001)
    solid = trimesh.load_mesh("solid.stl")
  
    vertices, face_ids = trimesh.sample.sample_surface_even(face_mesh, count=1000)
    
    # create some rays
    ray_origins = np.array([v + [0,0,+1e-5] for v in vertices])
    ray_directions = np.zeros([len(vertices), 3])
    ray_directions[:, 2] = +1
    
    # Get the intersections
    hits = solid.ray.intersects_any(ray_origins=ray_origins, ray_directions=ray_directions)
    if VERBOSE:
        ps.init()
        ps.remove_all_structures()
        ps.register_surface_mesh("face", face_mesh.vertices, face_mesh.faces)
        ps.register_surface_mesh("solid", solid.vertices, solid.faces)
        nodes = np.array([[p, p+np.array([0, 0, +1])] for i, p in enumerate(ray_origins)]).reshape(-1, 3)
        ps.register_curve_network("rays", nodes, np.array([[2*i, 2*i+1] for i in range(int(len(nodes)/2))]))
        ps.show()

    return np.sum(hits == False) > 50

def render_face_region(face_region, face):

    ps.init()
    write_stl_file(face, "face.stl", linear_deflection=0.1)
    face_mesh = trimesh.load_mesh("face.stl")
    ps.register_surface_mesh("face", face_mesh.vertices, face_mesh.faces)
    sampled_points = sample_points_in_polygon(face_region, 10)
    sampled_points_3d = np.array([[p.x, p.y, -2] for p in sampled_points])
    nodes = np.array([[p, p+np.array([0, 0, 4])] for p in sampled_points_3d]).reshape(-1, 3)
    ps.register_curve_network("rays", nodes, np.array([[2*i, 2*i+1] for i in range(int(len(nodes)/2))]))
    ps.show()

def render_faces(faces, solid):
    write_stl_file(solid, "solid.stl", linear_deflection=0.1)
    solid_mesh = trimesh.load_mesh("solid.stl")
    ps.init()
    ps.remove_all_structures()
    ps.register_surface_mesh("solid", solid_mesh.vertices, solid_mesh.faces)
    for i, face in enumerate(faces):
        write_stl_file(face, "face.stl", linear_deflection=0.1)
        face_mesh = trimesh.load_mesh("face.stl")
        ps.register_surface_mesh("face_"+str(i), face_mesh.vertices, face_mesh.faces)
    ps.show()

def is_face_centroid_inside_solid(face, solid, VERBOSE=False):
    props = GProp_GProps()
    #brepgprop_SurfaceProperties(face, props)
    brepgprop.SurfaceProperties(face, props)
    #centroid = props.CentreOfMass()
    centroid = get_surface_point_on_face(face)


    classifier = BRepClass3d_SolidClassifier(solid, centroid, 1e-7)
    #print(classifier.State(), classifier.State() == TopAbs_ON)
    #if VERBOSE or classifier.State() == TopAbs_IN:
    if VERBOSE:
        write_stl_file(solid, "solid.stl", linear_deflection=0.1)
        solid_mesh = trimesh.load_mesh("solid.stl")
        write_stl_file(face, "face.stl", linear_deflection=0.1)
        face_mesh = trimesh.load_mesh("face.stl")
        ps.init()
        ps.register_surface_mesh("solid", solid_mesh.vertices, solid_mesh.faces)
        ps.register_surface_mesh("face", face_mesh.vertices, face_mesh.faces)
        ps.register_point_cloud("centroid", np.array([[centroid.X(), centroid.Y(), centroid.Z()]]))
        ps.show()
    return classifier.State() == TopAbs_IN

def get_surface_point_on_face(face):
    umin, umax, vmin, vmax = breptools.UVBounds(face)
    u = (umin + umax) / 2.0
    v = (vmin + vmax) / 2.0

    surf = BRepAdaptor_Surface(face)
    pnt = surf.Value(u, v)  # this point is guaranteed to be on the surface
    return pnt

def is_face_centroid_hidden(face, solid):

    write_stl_file(face, "face.stl", linear_deflection=0.1)
    write_stl_file(solid, "solid.stl", linear_deflection=0.1)
    props = GProp_GProps()
    brepgprop.SurfaceProperties(face, props)
    centroid = props.CentreOfMass()
    face_mesh = trimesh.load_mesh("face.stl")
    ray_origin = np.array([centroid.X(), centroid.Y(), centroid.Z()])

    dists = np.linalg.norm(face_mesh.vertices - ray_origin, axis=1)
    closest_index = np.argmin(dists)
    ray_origin = deepcopy(face_mesh.vertices[closest_index])
    ray_origin[-1] -= 0.1
    #centroid = get_surface_point_on_face(face)
    ray_direction = np.array([0, 0, -1.0])

    #print(face_mesh.vertices)
    solid_mesh = trimesh.load_mesh("solid.stl")
    locations, index_ray, index_tri = solid_mesh.ray.intersects_location(
        ray_origins=[ray_origin],
        ray_directions=[ray_direction]
    )
    if len(locations) > 0:
        ps.init()
        ps.register_surface_mesh("solid", solid_mesh.vertices, solid_mesh.faces)
        ps.register_surface_mesh("face", face_mesh.vertices, face_mesh.faces)
        ps.register_point_cloud("intersections", locations)
        ps.register_point_cloud("origin", np.array([ray_origin]))
        ps.show()
    return len(locations) > 0

def apply_location_to_shape(shape):
    loc = shape.Location()
    if loc.IsIdentity():
        return shape  # no transform needed
    trsf = loc.Transformation()
    return BRepBuilderAPI_Transform(shape, trsf, True).Shape()

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

def get_single_view(shape, bbox, cut_depth=0.9, view_key="top", rendering_mode="brep", imposed_ax_limits=[]):

    #print("cut_depth", cut_depth)
    normal_dir = views[view_key]["dir"]
    myshape = depth_peeling_single_depth_with_bbox(shape, gp_Dir(normal_dir.X(), normal_dir.Y(), normal_dir.Z()), 
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
    if rendering_mode == "brep":
        return brep, ax_limits
    if rendering_mode == "outline":
        return outline, ax_limits
    if rendering_mode == "filled":
        return filled, ax_limits

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

        # collect all visible faces
        visible_faces = []
        while exp.More():
            face = apply_location_to_shape(topods.Face(exp.Current()))
            if is_face_visible(face, myshape):
                visible_faces.append(face)
            else:
                exp.Next()
                continue
            projected_edges, _ = project_shape(face)
            polys = get_regions(projected_edges)
            all_edges += projected_edges

            all_face_polys += polys
            face_regions[face_counter] = [face, polys]
            face_counter += 1
            exp.Next()
        #render_faces(visible_faces, myshape)

        print(len(shape_regions))
        best_faces = []
        region_dicts = []
        for shape_region in shape_regions:
            if shape_region.area < 0.01:
                continue
            #plot_regions([shape_region])
            best_poly = [-1, -1]
            best_iou = -1
            for face_id in face_regions.keys():
                for i, face_region in enumerate(face_regions[face_id][1]):
                    iou = face_region.intersection(shape_region).area / face_region.union(shape_region).area
                    #if view_key == "side":
                    #    plot_regions([face_region])
                    #    render_face_region(face_region, face_regions[face_id][0])
                    #hidden_face = is_face_centroid_hidden(face_regions[face_id][0], orig_shape)
                    #if hidden_face:
                    #    continue
                    #print(iou)
                    #plot_regions([shape_region, face_region])
                    if iou > best_iou:
                        best_poly = [face_id, i]
                        best_iou = iou
            #print(shape_region.area)
            #print(best_iou, best_poly)
            #print(face_regions[best_poly[0]][1][best_poly[1]])
            #center_2d = face_regions[best_poly[0]][1][best_poly[1]].representative_point()
            #plot_regions([shape_region, face_regions[best_poly[0]][1][best_poly[1]]])
            visible_face = True
            internal_face = is_face_centroid_inside_solid(face_regions[best_poly[0]][0], orig_shape, VERBOSE=False)
            hidden_face = not is_face_visible(face_regions[best_poly[0]][0], orig_shape)
            #if not hidden_face:
            #    is_face_visible(face_regions[best_poly[0]][0], orig_shape, VERBOSE=True)
            best_faces.append(face_regions[best_poly[0]][0])
            region_dicts.append({
                "region": shape_region,
                "face": face_regions[best_poly[0]][0],
                "iou": best_iou,
                "visible_face": visible_face,
                "internal_face": internal_face,
                "hidden_face": hidden_face
            })

        fig, ax = plt.subplots()
        region_dicts = list(sorted(region_dicts, key= lambda r: r["region"].area))
        # TODO: subtract intersections from each other. Start with smallest and subtract it from all others
        for i in range(len(region_dicts)):
            for j in range(i+1, len(region_dicts)):
                region_dicts[j]["region"] = region_dicts[j]["region"] - region_dicts[j]["region"].intersection(region_dicts[i]["region"])

        # TODO: use as hatching mask
        #for region_entry in reversed(region_dicts):
        #    if isinstance(region_entry["region"], MultiPolygon):
        #        for poly in region_entry["region"].geoms:
        #            x, y = poly.exterior.xy
        #            ax.plot(x, y, c="black")
        #            if region_entry["internal_face"]:
        #                ax.fill(x, y, c="#d95f02")  # alpha for transparency, optional
        #            elif region_entry["hidden_face"]:
        #                ax.fill(x, y, c="#7570b3")  # alpha for transparency, optional
        #            else:
        #                ax.fill(x, y, c="#1b9e77")  # alpha for transparency, optional
        #    else:
        #        x, y = region_entry["region"].exterior.xy
        #        ax.plot(x, y, c="black")
        #        if region_entry["internal_face"]:
        #            ax.fill(x, y, c="#d95f02")  # alpha for transparency, optional
        #        elif region_entry["hidden_face"]:
        #            ax.fill(x, y, c="#7570b3")  # alpha for transparency, optional
        #        else:
        #            ax.fill(x, y, c="#1b9e77")  # alpha for transparency, optional

        #ax.set_aspect('equal')
        #plt.show()

        write_svg_lines(os.path.join("svg_views", 
                        os.path.basename(step_file).split(".")[0]+"_"+str(cut_depth)+"_"+str(hatch_step)+"_"+view_key+".svg"),
                        all_shape_edges, width=800, height=800, regions=region_dicts)

if __name__ == '__main__':
    # check for pyqt5
    #if not load_pyqt5():
    #    raise IOError("pyqt5 required to run this test")
    create_orthographic_views(os.path.join("..", "models", "brep", "cup.step"), cut_depth=0.5, hatch_step=0.10)
    #display, start_display, add_menu, add_function_to_menu = init_display("pyqt5")
    #display.DisplayShape(edges_projected["visible"], color=Quantity_NOC_BLACK, update=True)
    #display.DisplayShape(myshape, color=Quantity_NOC_BLACK, update=True)
    #start_display()