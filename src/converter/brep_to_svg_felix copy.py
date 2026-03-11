import numpy as np
import create_hatch_lines_single_depth
import os
from copy import deepcopy
import svgwrite

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

def create_orthographic_views(step_file, view_key, hatch_step=0.012):
    step_reader = STEPControl_Reader()
    step_reader.ReadFile(step_file)
    step_reader.TransferRoot()
    myshape = step_reader.Shape()

    views = {
        "main": {
            "eye": gp_Pnt(0, 0, -1000),
            "dir": gp_Dir(1, 1, 0)
        },
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

    for cut_depth in np.arange(1.0, -0.01, -0.1):
        if view_key == 'main':
            trsf = gp_Trsf()
            axis = gp_Ax1(gp_Pnt(0, 0, 0), views[view_key]["dir"])  # Y-axis
            trsf.SetRotation(axis, 3.141592653589793 / 6)  # -30 degrees in radians
            myshape = BRepBuilderAPI_Transform(myshape, trsf, True).Shape()
        else:
            trsf = gp_Trsf()
            axis = gp_Ax1(gp_Pnt(0, 0, 0), views[view_key]["dir"])  # Y-axis
            trsf.SetRotation(axis, -0.5 * 3.141592653589793)  # -90 degrees in radians
            myshape = BRepBuilderAPI_Transform(myshape, trsf, True).Shape()

        bbox = Bnd_Box()
        brepbndlib.Add(myshape, bbox)
        xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
        # center bbox for projection
        trsf = gp_Trsf()
        trsf.SetTranslation(gp_Vec(-(xmin+xmax)/2.0, -(ymin+ymax)/2.0, -(zmin+zmax)/2.0,))
        myshape = BRepBuilderAPI_Transform(myshape, trsf, True).Shape()

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
        
        write_pdf_lines(
            os.path.join("svg_views", os.path.basename(step_file).split(".")[0]+"_"+str(cut_depth)+"_"+str(hatch_step)+"_"+view_key+".pdf"),
            all_edges, page_width=800, page_height=800
        )

def write_pdf_lines(pdf_filename, lines, page_width=612, page_height=792, stroke_width=1.0):
    """
    Appends a page to the PDF with the given lines.
    page_width and page_height are in points (1 pt = 1/72 inch), default is letter size (8.5x11 inches).
    """
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
    import os

    # If file exists, we need to copy existing pages and add a new one
    # Otherwise, just create a new PDF
    from PyPDF2 import PdfReader, PdfWriter
    import io

    # Prepare the drawing area
    if len(lines) == 0:
        x_min, x_max, y_min, y_max = 0, 100, 0, 100
    else:
        x_min, x_max, y_min, y_max = get_bbox(lines)

    # Calculate scale to fit the lines into the page with some margin
    margin = 36  # 0.5 inch margin
    draw_width = page_width - 2 * margin
    draw_height = page_height - 2 * margin
    scale_factor = min(draw_width / (x_max - x_min), draw_height / (y_max - y_min))

    # Centering offset
    x_offset = margin + (draw_width - scale_factor * (x_max - x_min)) / 2
    y_offset = margin + (draw_height - scale_factor * (y_max - y_min)) / 2

    # Draw lines to a PDF in memory
    packet = io.BytesIO()
    c = canvas.Canvas(packet, pagesize=(page_width, page_height))
    c.setLineWidth(stroke_width)

    for line in lines:
        if len(line) < 2:
            continue
        pts = [
            (
                x_offset + scale_factor * (pt[0] - x_min),
                y_offset + scale_factor * (pt[1] - y_min)
            )
            for pt in line
        ]
        c.moveTo(*pts[0])
        for pt in pts[1:]:
            c.lineTo(*pt)
        c.strokePath()

    c.showPage()
    c.save()
    packet.seek(0)

    # Merge with existing PDF if it exists, else create new
    if os.path.exists(pdf_filename):
        reader = PdfReader(pdf_filename)
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        new_page = PdfReader(packet).pages[0]
        writer.add_page(new_page)
        with open(pdf_filename, "wb") as f:
            writer.write(f)
    else:
        with open(pdf_filename, "wb") as f:
            f.write(packet.getvalue())

if __name__ == '__main__':
    # check for pyqt5
    #if not load_pyqt5():
    #    raise IOError("pyqt5 required to run this test")
    for view in ['main', 'top', 'front', 'side']:
        create_orthographic_views(
            os.path.join("..", "..", "cup.STEP"),
            view,
            hatch_step=0.10
        )
    #display, start_display, add_menu, add_function_to_menu = init_display("pyqt5")
    #display.DisplayShape(edges_projected["visible"], color=Quantity_NOC_BLACK, update=True)
    #display.DisplayShape(myshape, color=Quantity_NOC_BLACK, update=True)
    #start_display()