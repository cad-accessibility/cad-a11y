import matplotlib.pyplot as plt
from pybraille import convertText
import numpy as np
import os
import src.converter.plane_intersection_utils as plane_inter_utils
from src.converter.single_view_stl import get_single_view
from src.converter.two_brep_to_svg import get_superposition_view, get_juxtaposition_view
from src.converter.render_low_res import save_binary_array_as_vector_pdf
from OCC.Core.STEPControl import STEPControl_Reader

#before_model = os.path.join("src", "models", "brep", "vase_pattern_circle_low_before.step")
#after_model = os.path.join("src", "models", "brep", "vase_pattern_circle_low_after.step")
before_model = os.path.join("src", "models", "brep", "cup.step")
after_model = os.path.join("src", "models", "brep", "cup_higher.step")
#before_model = os.path.join("src", "models", "brep", "mug_before.step")
#after_model = os.path.join("src", "models", "brep", "mug_after.step")

def char_to_braille_array(char):
    """Convert a single character to a 6-dot boolean Braille array."""
    # Unicode Braille offset
    code = ord(char) - 0x2800
    # 6-dot Braille pattern (ignore dots 7 and 8)
    dots = [(code >> i) & 1 for i in range(6)]
    # Reorder to 2x3 format: [[1,4],[2,5],[3,6]]
    return [[dots[0], dots[3]],
            [dots[1], dots[4]],
            [dots[2], dots[5]]]

def string_to_braille_array(s):
    """Convert a string of Braille Unicode characters into boolean arrays."""
    return np.array([char_to_braille_array(c) for c in s])

# load both models
shapes = []
for step_file in [before_model, after_model]:
    step_reader = STEPControl_Reader()
    step_reader.ReadFile(step_file)
    step_reader.TransferRoot()
    shapes.append(step_reader.Shape())
print(shapes)

# normalize both shapes
shape_before, shape_after = plane_inter_utils.normalize_shapes_diagonal(shapes)
shapes = [shape_before, shape_after]
# get common bounds
xmin, ymin, zmin, xmax, ymax, zmax = plane_inter_utils.get_bbox_from_shapes([shape_before, shape_after])
bbox = [xmin, ymin, zmin, xmax, ymax, zmax]
print(xmin, ymin, zmin, xmax, ymax, zmax)
print(xmax-xmin, ymax-ymin, zmax-zmin)

current_model = shape_after

selected_shape = ["before edit", "after edit"]
shape_key_i = 0
view_key = "top"
cut_depth = 0.0
superposition = False
juxtaposition = False

rendering_modes = ["outline", "filled", "brep", "slice"]
rendering_mode_i = 2

view_keys = ["top", "front", "side"]
view_key_i = 0
view_limits = [
    [[xmin, xmax], [ymin, ymax]], #top
    [[xmin, xmax], [ymin, ymax]], #front
    [[xmin, xmax], [ymin, ymax]], #side
    ]

superposition_keys = ["outline", "intersection", "difference before", "difference after"]
superposition_key_i = 0

# get axis limits for all views for both shapes
for i, view_key in enumerate(view_keys):
    img_array, ax_limits_before = get_single_view(shape_before, bbox, 1.0-cut_depth, 
                                view_keys[i], 
                                rendering_modes[rendering_mode_i])
    img_array, ax_limits_after = get_single_view(shape_after, bbox, 1.0-cut_depth, 
                                view_keys[i], 
                                rendering_modes[rendering_mode_i])
    view_limits[i][0][0] = min(ax_limits_before[0][0], ax_limits_after[0][0])
    view_limits[i][0][1] = max(ax_limits_before[0][1], ax_limits_after[0][1])
    view_limits[i][1][0] = min(ax_limits_before[1][0], ax_limits_after[1][0])
    view_limits[i][1][1] = max(ax_limits_before[1][1], ax_limits_after[1][1])
print(view_limits)
view_key_i = 0
rendering_mode_i = 0
img_array, ax_limits_before = get_single_view(shape_before, bbox, 1.0-cut_depth, 
                            view_keys[view_key_i], 
                            rendering_modes[rendering_mode_i])

# Initial view of before_model, top-view

# Example data
#x = np.linspace(0, 2*np.pi, 200)
#y = np.sin(x)
#
fig, ax = plt.subplots()
#line, = ax.plot(x, y, label="sin(x)")
braille_mask = string_to_braille_array(convertText("side"))
for i in range(len(braille_mask)):
    img_array[1:4,i*3+1:i*3+3][braille_mask[i].astype(bool)] = [0,0,0,255]
ax.imshow(img_array)
#print(string_to_braille_array(convertText("hello")))
if not os.path.exists("renders"):
    os.mkdir("renders")

def set_legend(ax):
    global rendering_mode_i
    #func_legend = ax.legend(loc="upper right")

    # Add a dummy second legend for controls
    if not superposition and not juxtaposition:
        first_line = ";".join([selected_shape[shape_key_i], view_keys[view_key_i], rendering_modes[rendering_mode_i], 
                  "{:.2f}".format(cut_depth)])
    elif superposition:
        first_line = "superposition (" + superposition_keys[superposition_key_i]+"); "+view_keys[view_key_i]+"; "+"{:.2f}".format(cut_depth)
    elif juxtaposition:
        first_line = "juxtaposition ; "+view_keys[view_key_i]+"; "+rendering_modes[rendering_mode_i]+"; "+"{:.2f}".format(cut_depth)
    controls = [
        first_line,
        "t: toggle rendering "+str(rendering_modes),
        "r: toggle shape "+str(selected_shape),
        "g: de/activate superposition [True, False]",
        "h: toggle superposition mode "+str(superposition_keys),
        "g: de/activate juxtaposition [True, False]",
        "v: toggle view point "+str(view_keys),
        "up/down: +/- 0.1 slice depth",
        "p: save as PDF in renders folder",
        "q: quit"
    ]
    control_texts = [plt.Line2D([0], [0], color="none")] * len(controls)
    control_legend = ax.legend(control_texts, controls, loc="upper center", bbox_to_anchor=(1.0, 1.10))
    ax.add_artist(control_legend)  # keep both legends

# State variable
mode = "sin"
set_legend(ax)

def update_plot():
    """Update the line data depending on the current mode."""
    global mode
    global img_array
    global rendering_mode_i
    global view_key_i
    global superposition_key_i
    global shape_key_i
    global juxtaposition
    global superposition
    ax.cla()
    set_legend(ax)
    if not superposition and not juxtaposition:
        img, ax_limits = get_single_view(shapes[shape_key_i], bbox, 1.0-cut_depth, view_keys[view_key_i], rendering_modes[rendering_mode_i], 
                              imposed_ax_limits=view_limits[view_key_i])
    elif superposition:
        print("get_superposition_view")
        img = get_superposition_view(shapes, bbox, 1.0-cut_depth, view_keys[view_key_i], 
                                               rendering_modes[rendering_mode_i], 
                                               imposed_ax_limits=view_limits[view_key_i],
                                               superposition_key=superposition_keys[superposition_key_i])
    elif juxtaposition:
        print("get_juxtaposition")
        img, ax_limits = get_juxtaposition_view(shapes, bbox, 1.0-cut_depth, view_keys[view_key_i], 
                                               rendering_modes[rendering_mode_i], 
                                               imposed_ax_limits=view_limits[view_key_i],
                                               superposition_key=superposition_keys[superposition_key_i])
    img_array = img
    ax.imshow(img)
    fig.canvas.draw_idle()  # refresh the plot

def on_key(event):
    global mode
    global rendering_mode_i
    global view_key_i
    global shape_key_i
    global cut_depth
    global superposition
    global img_array
    global superposition_key_i
    global juxtaposition
    print(f"Key pressed: {event.key}")
    if event.key == "up":
        cut_depth = min(cut_depth+0.05, 1.0)
    if event.key == "down":
        cut_depth = max(cut_depth-0.05, 0.0)
    elif event.key == "t":
        rendering_mode_i = (rendering_mode_i+1)%len(rendering_modes)
    elif event.key == "v":
        view_key_i = (view_key_i+1)%len(view_keys)
    elif event.key == "r":
        shape_key_i = (shape_key_i+1)%len(selected_shape)
    elif event.key == "g":
        superposition = not superposition
    elif event.key == "j":
        juxtaposition = not juxtaposition
    elif event.key == "h":
        superposition_key_i = (superposition_key_i+1)%len(superposition_keys)
    elif event.key == "q":
        plt.close(fig)
        return
    update_plot()

    if event.key == "p":
        max_num = -1
        for x in os.listdir("renders"):
            if ".pdf" in x:
                max_num = max(max_num, int(x.split(".pdf")[0]))

        for i in range(len(braille_mask)):
            img_array[1:4,i*3+1:i*3+3][braille_mask[i].astype(bool)] = [0,0,0,255]
        save_binary_array_as_vector_pdf(img_array, "renders/"+str(max_num+1)+".pdf")

# Connect key press event
fig.canvas.mpl_connect("key_press_event", on_key)

plt.show()

# TODO:
# 2) fix filled