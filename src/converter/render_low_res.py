import matplotlib.pyplot as plt
import io, PIL
import numpy as np
from matplotlib.patches import Rectangle, Circle
from matplotlib.lines import Line2D
from matplotlib.collections import LineCollection
import matplotlib as mpl
from shapely.geometry import Polygon, MultiPolygon, Point
from matplotlib.patches import PathPatch
from matplotlib.path import Path

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

def draw_custom_hatching(ax, clip_path, spacing=0.1, angle=45, linewidth=0.1, color='black'):
    """
    Draws manually spaced, angled hatch lines and clips them to clip_path.
    
    - spacing: in data units
    - angle: in degrees
    """
    angle_rad = np.deg2rad(angle)
    dx = spacing * np.cos(angle_rad)
    dy = spacing * np.sin(angle_rad)

    # Bounding box of the clip path in data coordinates
    bbox = clip_path.get_extents()
    x0, x1 = bbox.xmin, bbox.xmax
    y0, y1 = bbox.ymin, bbox.ymax

    print(x0, x1, y0, y1)

    # Create a grid of parallel lines that fully cover the bounding box
    lines = []
    length = np.hypot(x1 - x0, y1 - y0) * 2  # Over-long lines to cover rotation
    print(length)

    # Start from bottom-left corner and move along perpendicular to the angle
    #ax.add_patch(Rectangle((x0, y0), x1-x0, y1-y0, color='red'))
    d = -length
    while d < length * 2:
        cx = x0 + d * np.cos(angle_rad + np.pi/2)
        cy = y0 + d * np.sin(angle_rad + np.pi/2)

        x_start = cx - length * np.cos(angle_rad) / 2
        y_start = cy - length * np.sin(angle_rad) / 2
        x_end = cx + length * np.cos(angle_rad) / 2
        y_end = cy + length * np.sin(angle_rad) / 2

        print(x_start, x_end)
        print(y_start, y_end)
        lines.append([(x_start, y_start), (x_end, y_end)])
        #line = Line2D([x_start, y_start], [x_end, y_end], color=color, 
        #              linewidth=linewidth)
        #ax.plot([x_start, x_end], [y_start, y_end], color=color, 
        #              linewidth=0.1)

        #ax.plot([-31.17826193, -31.22809947 ], [ 31.94070721, 31.93247553], color="red")
        #ax.plot([x_start, x_end], [ 31.94070721, 31.93247553], color="red")
        #ax.plot([-31], [31], color=color, 
        #              linewidth=0.1)
        #line.set_clip_path(clip_path)
        #break
        #ax.add_line(line)
        d += spacing

    lc = LineCollection(lines, colors=color, linewidths=linewidth)
    ax.add_collection(lc)
    lc.set_clip_path(clip_path.get_path(), transform=ax.transData)


def save_binary_array_as_vector_pdf(array, filename="low_res.pdf"):
    height, width, _ = array.shape
    fig = plt.figure(figsize=(width / 100, height / 100), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, width)
    ax.set_ylim(0, height)
    ax.axis('off')

    # Draw white background
    ax.add_patch(Rectangle((0, 0), width, height, color='white'))

    # Draw black pixels only
    for y in range(height):
        for x in range(width):
            if np.all(array[y, x] == [0,0,0,255]):
                #ax.add_patch(Rectangle((x, height - y - 1), 1, 1, facecolor='black'))
                #if y == height-1:
                #    ax.add_patch(Circle((x, height - y), 0.3, facecolor='black'))
                #else:
                ax.add_patch(Circle((x, height - y - 1), 0.3, facecolor='black'))

    fig.savefig(filename, format='pdf', bbox_inches='tight', pad_inches=0)
    #fig.savefig(filename, format='pdf')
    plt.close(fig)

def save_array_as_pdf(array, filename="low_res.pdf", dpi=100):
    height_px, width_px, _ = array.shape

    with mpl.rc_context({
        "image.interpolation": "none",
        "image.cmap": "gray",
        "figure.facecolor": "white",
        "savefig.facecolor": "white",
        "savefig.edgecolor": "white",
    }):
        fig = plt.figure(figsize=(width_px / dpi, height_px / dpi), dpi=dpi)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.axis("off")

        ax.imshow(array, cmap="gray", interpolation="none")

        fig.savefig(filename, format='pdf', bbox_inches='tight', pad_inches=0)
        plt.close(fig)

def low_res_render(lines_0, lines_1, shape_regions_0, bounds=[0,0,1,1], filename="low_res", save_file=True, 
                   imposed_ax_limits=[], VERBOSE=False):
    with mpl.rc_context({
        "lines.antialiased": False,
        "patch.antialiased": False,
        "path.simplify": False,
        "figure.facecolor": "white",
        "savefig.facecolor": "white",
        "savefig.edgecolor": "white",
        'hatch.linewidth': 0.1,
        'hatch.color': 'black',
    }):

        # Target pixel resolution
        width_px, height_px = 96, 40
        dpi = 100  # Dots per inch

        # Create figure with correct size in inches
        fig = plt.figure(figsize=(width_px / dpi, height_px / dpi), dpi=dpi)

        ax = fig.add_axes([0, 0, 1, 1])  # Fill entire figure
        ax.axis('off')  # Turn off axes, ticks, labels

        # Draw something
        #ax.plot([0.2, 0.8], [0.2, 0.8], linewidth=1, color="black")
        for line in lines_0:
            ax.plot(np.array(line)[:,0], np.array(line)[:,1], linewidth=0.01, color="black")
        for line in lines_1:
            ax.plot(np.array(line)[:,0], np.array(line)[:,1], linewidth=0.01, color="black")
        #ax.plot(lines_0[-1][:,0], lines_0[-1][:,1], linewidth=0.01, color="red")
        #ax.plot([-31.17826193, -31.22809947 ], [ 31.94070721, 31.93247553], color="red")

        # hatchings
        #if len(shape_regions_0) > 0:
        for shape_region in shape_regions_0:
            #draw_custom_hatching(ax, shape_regions_0[0], spacing=0.1, linewidth=0.05)
            #path = polygon_to_path(shape_region)
            path = polygon_to_path_multi(shape_region)
            patch = PathPatch(path, color="black", edgecolor='black', alpha=1.0)
            ax.add_patch(patch)
            x, y = shape_region.exterior.coords.xy
            ax.plot(x, y, c="black")
            #ax.add_patch(shape_regions_0[0])
            #hatch_rect = Rectangle((bounds[0], bounds[1]), bounds[2]-bounds[0], bounds[3]-bounds[1], 
            #                       facecolor='none', edgecolor='black',
            #                        hatch='oo', linewidth=0)
            ##for region in shape_regions_0:
            #hatch_rect.set_clip_path(shape_regions_0[0])
            #ax.add_patch(hatch_rect)
        #if len(shape_regions_0) > 0:
        #    plt.show()
        ax.set_aspect('equal')
        ax = plt.gca()
        #print("ax limits")
        #print(imposed_ax_limits)
        if len(imposed_ax_limits) > 0:
            ax.set_xlim(imposed_ax_limits[0])
            ax.set_ylim(imposed_ax_limits[1])
        ax_limits = [ax.get_xlim(), ax.get_ylim()]
        #print("ax_limits")
        #print(ax_limits)
        #print(ax.get_xlim())
        #print(ax.get_ylim())
        if VERBOSE:
            plt.show()

        #fig.savefig("low_res.pdf", dpi=dpi, bbox_inches='tight', pad_inches=0)
        buf = io.BytesIO()
        #fig.savefig(buf, format='png', dpi=dpi, bbox_inches='tight', pad_inches=0)
        fig.savefig(buf, format='png', dpi=dpi, pad_inches=0)
        fig.clear()
        buf.seek(0)

        from PIL import Image
        img = Image.open(buf)
        img_np = np.array(img)
        #plt.imshow(img_np, cmap='gray', interpolation='none')
        #plt.axis('off')
        #plt.show()

        # extract outline
        outlines_np, filled_np = get_outlines_and_filled(img_np)

        if save_file:
            save_binary_array_as_vector_pdf(img_np, filename=filename+".pdf")

        #img_rgba = np.zeros((img_np.shape[0], img_np.shape[1], 4), dtype=np.uint8)
        #mask = img_np[:,:,0] == 1
        #print(mask)
        #img_rgba[mask] = [0, 0, 0, 255]
        return img_np, outlines_np, filled_np, ax_limits

def get_outlines(img_np):
    outline_pixels = np.zeros(img_np.shape[:2], dtype=int)
    for i in range(img_np.shape[0]):
        for j in range(img_np.shape[1]):
            if np.all(img_np[i][j] == [0,0,0,255]):
                # check if there's a white_pixel neighbor
                neighbors = [[i-1, j-1],
                             [i-1, j],
                             [i-1, j+1],
                             [i, j-1],
                             [i, j+1],
                             [i+1, j-1],
                             [i+1, j],
                             [i+1, j+1]]
                for n in neighbors:
                    if n[0] >= 0 and n[0] < img_np.shape[0] and n[1] >= 0 and n[1] < img_np.shape[1]:
                        if np.all(img_np[n[0]][n[1]] == [255,255,255,255]): 
                            outline_pixels[i][j] = 1

    #for i in range(img_np.shape[0]):
    #    if img_np[i][0][0] == 0:
    #        outline_pixels[i][0] = 1
    #    if img_np[i][-1][0] == 0:
    #        outline_pixels[i][-1] = 1
    #for j in range(img_np.shape[1]):
    #    if img_np[0][j][0] == 0:
    #        outline_pixels[0][j] = 1
    #    if img_np[-1][j][0] == 0:
    #        outline_pixels[-1][j] = 1

    outline_pixels_rgba = np.zeros((img_np.shape[0], img_np.shape[1], 4), dtype=np.uint8)
    mask = outline_pixels == 1
    outline_pixels_rgba[mask] = [0, 0, 0, 255]
    outline_pixels_rgba[~mask] = [255, 255, 255, 255]
    return outline_pixels_rgba

def get_outlines_and_filled(img_np):
    # fill from four corners
    outline_pixels = np.zeros(img_np.shape[:2], dtype=int)
    white_pixels = np.zeros(img_np.shape[:2], dtype=int)
    for corner in [[0, 0], [img_np.shape[0]-1, 0], [img_np.shape[0]-1, img_np.shape[1]-1], [0, img_np.shape[1]-1]]:
        if not img_np[corner[0]][corner[1]][0] == 255:
            continue
        # start marking from corner
        #white_pixels[corner] = 1
        fifo = [corner]
        already_seen = set()
        while len(fifo) > 0:
            #break
            pixel_curr = fifo[0]
            del fifo[0]
            already_seen.add((pixel_curr[0], pixel_curr[1]))
            if img_np[pixel_curr[0]][pixel_curr[1]][0] == 255:
                white_pixels[pixel_curr[0]][pixel_curr[1]] = 1
                neighbors = [[pixel_curr[0]-1, pixel_curr[1]-1],
                             [pixel_curr[0]-1, pixel_curr[1]],
                             [pixel_curr[0]-1, pixel_curr[1]+1],
                             [pixel_curr[0], pixel_curr[1]-1],
                             [pixel_curr[0], pixel_curr[1]+1],
                             [pixel_curr[0]+1, pixel_curr[1]-1],
                             [pixel_curr[0]+1, pixel_curr[1]],
                             [pixel_curr[0]+1, pixel_curr[1]+1]]
                for n in neighbors:
                    if (n[0], n[1]) in already_seen:
                        continue
                    if n[0] >= 0 and n[0] < img_np.shape[0] and n[1] >= 0 and n[1] < img_np.shape[1]:
                        if not white_pixels[n[0]][n[1]] == 1: 
                            fifo.append(n)
                            already_seen.add((n[0], n[1]))
    #plt.imshow(img_np)
    #plt.imshow(white_pixels)
    #plt.show()

    for i in range(img_np.shape[0]):
        for j in range(img_np.shape[1]):
            if img_np[i][j][0] == 0:
                # check if there's a white_pixel neighbor
                neighbors = [[i-1, j-1],
                             [i-1, j],
                             [i-1, j+1],
                             [i, j-1],
                             [i, j+1],
                             [i+1, j-1],
                             [i+1, j],
                             [i+1, j+1]]
                for n in neighbors:
                    if n[0] >= 0 and n[0] < img_np.shape[0] and n[1] >= 0 and n[1] < img_np.shape[1]:
                        if white_pixels[n[0]][n[1]] == 1: 
                            outline_pixels[i][j] = 1
    for i in range(img_np.shape[0]):
        if img_np[i][0][0] == 0:
            outline_pixels[i][0] = 1
        if img_np[i][-1][0] == 0:
            outline_pixels[i][-1] = 1
    for j in range(img_np.shape[1]):
        if img_np[0][j][0] == 0:
            outline_pixels[0][j] = 1
        if img_np[-1][j][0] == 0:
            outline_pixels[-1][j] = 1

    outline_pixels_rgba = np.zeros((img_np.shape[0], img_np.shape[1], 4), dtype=np.uint8)
    mask = outline_pixels == 1
    outline_pixels_rgba[mask] = [0, 0, 0, 255]
    #plt.imshow(outline_pixels_rgba)
    #plt.show()

    filled_pixels = np.zeros(img_np.shape[:2])
    for i in range(img_np.shape[0]):
        for j in range(img_np.shape[1]):
            if white_pixels[i][j] == 0:
                filled_pixels[i][j] = 1

    filled_pixels_rgba = np.zeros((img_np.shape[0], img_np.shape[1], 4), dtype=np.uint8)
    mask = filled_pixels == 1
    filled_pixels_rgba[mask] = [0, 0, 0, 255]
    #plt.imshow(filled_pixels_rgba)
    #plt.show()
    return outline_pixels_rgba, filled_pixels_rgba

if __name__ == "__main__":
    low_res_render([])