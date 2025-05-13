#!/usr/bin/env python3
import os
import argparse
import math
from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Edge
from OCC.Core.BRepTools import breptools
from OCC.Core.BRep import BRep_Tool, BRep_Builder
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_EDGE
from OCC.Core.GeomAdaptor import GeomAdaptor_Curve
from OCC.Core.GCPnts import GCPnts_UniformAbscissa
from OCC.Core.Message import Message_ProgressRange
from OCC.Core.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
from OCC.Core.gp import gp_Ax2, gp_Dir, gp_Pnt
import svgwrite

# Define standard views with camera direction and up vector
VIEWS = {
    'top':    {'dir': (0, 0, 1),  'up': (0, -1, 0)},
    # 'bottom': {'dir': (0, 0, -1), 'up': (0, 1, 0)},
    'front':  {'dir': (0, -1, 0), 'up': (0, 0, 1)},
    # 'back':   {'dir': (0, 1, 0),  'up': (0, 0, 1)},
    # 'left':   {'dir': (1, 0, 0),  'up': (0, 0, 1)},
    'right':  {'dir': (-1, 0, 0), 'up': (0, 0, 1)}
}

# Vector math helpers
def normalize(v):
    length = math.sqrt(v[0]**2 + v[1]**2 + v[2]**2)
    return (v[0]/length, v[1]/length, v[2]/length)

def dot(u, v):
    return u[0]*v[0] + u[1]*v[1] + u[2]*v[2]

def cross(u, v):
    return (u[1]*v[2] - u[2]*v[1],
            u[2]*v[0] - u[0]*v[2],
            u[0]*v[1] - u[1]*v[0])

def cross2(o, a, b):
    # 2D cross product for convex hull test
    return (a[0]-o[0])*(b[1]-o[1]) - (a[1]-o[1])*(b[0]-o[0])

def convex_hull(points):
    pts = sorted(set(points))
    if len(pts) <= 1:
        return pts
    lower = []
    for p in pts:
        while len(lower) >= 2 and cross2(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross2(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]

# Extract silhouette edges using HLRBRep
def extract_silhouette(shape, view_dir, up_vec):
    # Set up HLR algorithm for silhouette detection
    hlr_algo = HLRBRep_Algo()
    hlr_algo.Add(shape)
    
    # Set the projection direction using gp_Dir
    dir_vec = gp_Dir(view_dir[0], view_dir[1], view_dir[2])
    up = gp_Dir(up_vec[0], up_vec[1], up_vec[2])
    
    # Create projection plane
    origin = gp_Pnt(0, 0, 0)
    proj_plane = gp_Ax2(origin, dir_vec, up)
    
    # Set up the projector
    hlr_algo.Projector(proj_plane)
    hlr_algo.Update()
    hlr_algo.Hide()
    
    # Extract the edges
    hlr_to_shape = HLRBRep_HLRToShape(hlr_algo)
    
    # Get visible, hidden and outline edges
    visible_edges = hlr_to_shape.VCompound()
    outline_edges = hlr_to_shape.OutLineVCompound()
    
    return visible_edges, outline_edges

# Generate SVG for one view
def generate_svg(shape, view_name, view, out_path):
    # compute basis for projection plane
    vd = normalize(view['dir'])
    up = normalize(view['up'])
    u = normalize(cross(vd, up))
    v_vec = normalize(cross(u, vd))
    
    # Get silhouette edges
    try:
        visible_edges, outline_edges = extract_silhouette(shape, vd, up)
        use_silhouette = True
    except Exception as e:
        print(f"Warning: Silhouette extraction failed for view {view_name}: {e}")
        use_silhouette = False
    
    # Process all edges (for normal view rendering)
    all_proj = []
    edges_to_process = []
    
    if use_silhouette:
        # Process visible and outline edges
        explorer = TopExp_Explorer(visible_edges, TopAbs_EDGE)
        while explorer.More():
            edges_to_process.append(explorer.Current())
            explorer.Next()
            
        if not outline_edges.IsNull():
            explorer = TopExp_Explorer(outline_edges, TopAbs_EDGE)
            while explorer.More():
                edges_to_process.append(explorer.Current())
                explorer.Next()
    else:
        # Fallback to processing all edges
        explorer = TopExp_Explorer(shape, TopAbs_EDGE)
        while explorer.More():
            edges_to_process.append(explorer.Current())
            explorer.Next()
            
    # Process the collected edges
    for edge in edges_to_process:
        # get 3d curve and parameters
        curve_handle, first, last = BRep_Tool.Curve(edge)
        if curve_handle is None:
            continue
        adapter = GeomAdaptor_Curve(curve_handle, first, last)
        # GeomAdaptor_Curve.Length is unavailable in this wrapper; approximate by param range
        length = last - first
        # uniform sampling
        spacing = length / 50.0 if length > 0 else 1.0
        discret = GCPnts_UniformAbscissa(adapter, spacing)
        params = []
        if discret.IsDone():
            for i in range(1, discret.NbPoints() + 1):
                params.append(discret.Parameter(i))
        else:
            params = [first, last]
        pts2d = []
        for p in params:
            pt = adapter.Value(p)
            x3, y3, z3 = pt.X(), pt.Y(), pt.Z()
            # project to plane axes u,v
            px = dot((x3, y3, z3), u)
            py = dot((x3, y3, z3), v_vec)
            pts2d.append((px, py))
            all_proj.append((px, py))
        yield pts2d

def write_svg(edges, out_file):
    # compute bounds
    xs = [p[0] for e in edges for p in e]
    ys = [p[1] for e in edges for p in e]
    if not xs or not ys:
        return
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    width, height = max_x - min_x, max_y - min_y
    # scale to fit within 1000px
    max_px = 1000.0
    scale = max_px / max(width, height) if max(width, height) > 0 else 1.0
    margin = 10
    w_px = width * scale + 2*margin
    h_px = height * scale + 2*margin

    dwg = svgwrite.Drawing(out_file, size=(w_px, h_px), profile='tiny')
    # Draw all edges
    for edge in edges:
        pts = [((x-min_x)*scale + margin, (max_y - y)*scale + margin) for x, y in edge]
        dwg.add(dwg.polyline(points=pts, stroke='black', fill='none', stroke_width=1))
    
    dwg.save()

def main():
    parser = argparse.ArgumentParser(description='Convert BREP to SVG views')
    parser.add_argument('input', help='BREP file path')
    parser.add_argument('-o', '--out_dir', default='svg_views', help='Output directory for SVGs')
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        raise FileNotFoundError(f"Input file not found: {args.input}")
    os.makedirs(args.out_dir, exist_ok=True)

    # read shape
    shape = TopoDS_Shape()
    builder = BRep_Builder()
    if not breptools.Read(shape, args.input, builder, Message_ProgressRange()):
        raise RuntimeError(f"Failed to read BREP: {args.input}")

    base = os.path.splitext(os.path.basename(args.input))[0]
    for name, view in VIEWS.items():
        edges = list(generate_svg(shape, name, view, None))
        out_file = os.path.join(args.out_dir, f"{base}_{name}.svg")
        write_svg(edges, out_file)
        print(f"Saved view '{name}' to {out_file}")

if __name__ == '__main__':
    main()
