import matplotlib.pyplot as plt
import matplotlib
matplotlib.use("Qt5Agg")   # or "TkAgg"
from pybraille import convertText
import time
import numpy as np
import os
import src.converter.plane_intersection_utils as plane_inter_utils
from src.converter.single_view_stl import get_single_view
from src.converter.juxtaposition_view_stl import get_juxtaposition_view
from src.converter.side_top_view import get_side_top_view
from src.converter.superposition_view_stl import get_superposition_view
from src.converter.render_low_res import save_binary_array_as_vector_pdf
from OCC.Core.STEPControl import STEPControl_Reader
import math

import asyncio
import threading
import bleak
import godice
import serial

cut_depth = 0.0

serial_rate_limit = 1.0

#exit()

before_model = os.path.join("src", "models", "brep", "cup.step")
after_model = os.path.join("src", "models", "brep", "cup_higher.step")
before_model = os.path.join("src", "models", "brep", "mug_before.step")
after_model = os.path.join("src", "models", "brep", "mug_after.step")

dice = None

def start_dice_loop():
    async def dice_main():
        while True:
        #await asyncio.sleep(3)
            await asyncio.sleep(3)
            print("hello")
    asyncio.run(dice_main())

async def every_10_sec():

    while True:
    #await asyncio.sleep(3)
        await asyncio.sleep(3)
        print("hello")
    
async def background_main():

    asyncio.create_task(every_10_sec())
    await asyncio.Event().wait()



async def notification_callback(number, stability_descr):
    global view_key_i
    """
    GoDice number notification callback.
    Called each time GoDice is flipped, receiving flip event data:
    :param number: a rolled number
    :param stability_descr: an additional value clarifying device movement state, ie stable, rolling...
    """
    if stability_descr in [godice.StabilityDescriptor.MOVE_STABLE, godice.StabilityDescriptor.STABLE]:
        print(f"Number: {number}, stability descriptor: {stability_descr}")
        if number in [6, 1]:
            view_key_i = 0
        elif number in [3, 4]:
            view_key_i = 1
        elif number in [2, 5]:
            view_key_i = 2
        update_plot()


def filter_godice_devices(dev_advdata_tuples):
    """
    Receives all discovered devices and returns only GoDice devices
    """
    return [
        (dev, adv_data)
        for dev, adv_data in dev_advdata_tuples
        if (dev.name and dev.name.startswith("GoDice"))
    ]


def select_closest_device(dev_advdata_tuples):
    """
    Finds the closest device based on RSSI are returns it
    """
    def _rssi_as_key(dev_advdata):
        _, adv_data = dev_advdata
        return adv_data.rssi

    return max(dev_advdata_tuples, key=_rssi_as_key)


def print_device_info(devices):
    """
    Prints short summary of discovered devices
    """
    for dev, adv_data in devices:
        print(f"Name: {dev.name}, address: {dev.address}, rssi: {adv_data.rssi}")



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

def depth_to_str(cut_depth):
    if np.isclose(cut_depth, 0.0):
        return "0"
    if np.isclose(cut_depth, 0.1):
        return "10"
    if np.isclose(cut_depth, 0.2):
        return "20"
    if np.isclose(cut_depth, 0.3):
        return "30"
    if np.isclose(cut_depth, 0.4):
        return "40"
    if np.isclose(cut_depth, 0.5):
        return "50"
    if np.isclose(cut_depth, 0.6):
        return "60"
    if np.isclose(cut_depth, 0.7):
        return "70"
    if np.isclose(cut_depth, 0.8):
        return "80"
    if np.isclose(cut_depth, 0.9):
        return "90"
    if np.isclose(cut_depth, 1.0):
        return "100"

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
superposition = False
juxtaposition = False

rendering_modes = ["outline", "shaded", "slice"]
rendering_mode_i = 1

view_keys = ["top", "front", "right"]
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
    img_array, ax_limits_before = get_single_view(shape_before, bbox, 1.0, 
                                view_keys[i], 
                                rendering_modes[rendering_mode_i])
    img_array, ax_limits_after = get_single_view(shape_after, bbox, 1.0, 
                                view_keys[i], 
                                rendering_modes[rendering_mode_i])
    view_limits[i][0][0] = min(ax_limits_before[0][0], ax_limits_after[0][0])
    view_limits[i][0][1] = max(ax_limits_before[0][1], ax_limits_after[0][1])
    view_limits[i][1][0] = min(ax_limits_before[1][0], ax_limits_after[1][0])
    view_limits[i][1][1] = max(ax_limits_before[1][1], ax_limits_after[1][1])
view_limits = np.array(view_limits)
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
braille_mask = string_to_braille_array(convertText("top"))
for i in range(len(braille_mask)):
    img_array[1:4,i*3+1:i*3+3][braille_mask[i].astype(bool)] = [0,0,0,255]

braille_mask = string_to_braille_array(convertText("outline"))
for i in range(len(braille_mask)):
    img_array[6:9,i*3+1:i*3+3][braille_mask[i].astype(bool)] = [0,0,0,255]


braille_mask = string_to_braille_array(convertText(depth_to_str(cut_depth)))
for i in range(len(braille_mask)):
    img_array[11:14,i*3+1:i*3+3][braille_mask[i].astype(bool)] = [0,0,0,255]

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
        "j: de/activate juxtaposition [True, False]",
        "v: toggle view point "+str(view_keys),
        "up/down: +/- 0.1 slice depth",
        "p: save as PDF in renders folder",
        "q: quit"
    ]
    control_texts = [plt.Line2D([0], [0], color="none")] * len(controls)
    control_legend = ax.legend(control_texts, controls, loc="upper center", bbox_to_anchor=(1.10, 1.20))
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
    global cut_depth
    ax.cla()
    set_legend(ax)
    if view_keys[view_key_i] == "front":
        cut_depth = 1.0 - cut_depth
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
        #img, ax_limits = get_juxtaposition_view(shapes, bbox, 1.0-cut_depth, view_keys[view_key_i], 
        #                                       rendering_modes[rendering_mode_i], 
        #                                       imposed_ax_limits=[],
        #                                       #imposed_ax_limits=view_limits[view_key_i],
        #                                       superposition_key=superposition_keys[superposition_key_i])
        imposed_ax_limits = [[-32.4790008,  -31.1939632 ],
                             [ -0.20081487,   0.35382712]]

        img, ax_limits = get_side_top_view(shapes[shape_key_i], bbox, 1.0-cut_depth, view_keys[view_key_i], 
                                               rendering_modes[rendering_mode_i], 
                                               imposed_ax_limits=imposed_ax_limits,
                                               #imposed_ax_limits=view_limits[view_key_i],
                                               superposition_key=superposition_keys[superposition_key_i])
    if view_keys[view_key_i] == "front":
        cut_depth = 1.0 - cut_depth

    img_array = img
    #braille_mask = string_to_braille_array(convertText(view_keys[view_key_i]))
    braille_mask = string_to_braille_array(convertText("2"))
    for i in range(len(braille_mask)):
        img_array[1:4,i*3+1:i*3+3][braille_mask[i].astype(bool)] = [0,0,0,255]

    #braille_mask = string_to_braille_array(convertText(rendering_modes[rendering_mode_i]))
    #for i in range(len(braille_mask)):
    #    img_array[6:9,i*3+1:i*3+3][braille_mask[i].astype(bool)] = [0,0,0,255]

    #braille_mask = string_to_braille_array(convertText(depth_to_str(cut_depth)))
    #for i in range(len(braille_mask)):
    #    img_array[11:14,i*3+1:i*3+3][braille_mask[i].astype(bool)] = [0,0,0,255]

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
        cut_depth = min(cut_depth+0.1, 1.0)
    if event.key == "down":
        cut_depth = max(cut_depth-0.1, 0.0)
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
        #dice.disconnect()
        return
    update_plot()

    if event.key == "p":
        max_num = -1
        for x in os.listdir("renders"):
            if ".pdf" in x:
                max_num = max(max_num, int(x.split(".pdf")[0]))

        save_binary_array_as_vector_pdf(img_array, "renders/"+str(max_num+1)+".pdf")

# Connect key press event
fig.canvas.mpl_connect("key_press_event", on_key)

current_arduino_dist = -10.0
first_dist = -10.0
last_dist = -100.0
USE_DISTANCE_SENSOR = False
ser = serial.Serial('/dev/cu.usbmodemB43A4536EC582', 9600, timeout=1)
line = ser.readline().decode('utf-8').strip()
if line:
    USE_DISTANCE_SENSOR = True

def start_arduino_loop():
    global cut_depth
    global current_arduino_dist
    last_time_serialized = time.time()
    while True:
        line = ser.readline().decode('utf-8').strip()
        if time.time() - last_time_serialized < serial_rate_limit:
            continue
        last_time_serialized = time.time()
        if line:
            try:
                current_arduino_dist = float(line)
                #print(current_arduino_dist)
                if first_dist < 0.0 or last_dist < 0.0:
                    continue
                slice_distance = (current_arduino_dist - first_dist)/(last_dist-first_dist)
                #print("slice_distance", slice_distance)
                if slice_distance < 0.0 or slice_distance > 1.0:
                    continue
                new_cut_depth = math.ceil(slice_distance*10)/10
                #print(slice_distance)
                #print(new_cut_depth, cut_depth)
                if np.isclose(new_cut_depth, cut_depth):
                    continue
                cut_depth = new_cut_depth
                update_plot()
            except ValueError:
                pass  # ignore malformed lines

if USE_DISTANCE_SENSOR:
    threading.Thread(target=start_arduino_loop, daemon=True).start()


    print("Distance calibration ...")
    print("Put slider to first position. Then press the enter key ...")
    pressed_key = False
    input()
    first_dist = current_arduino_dist
    print("Put slider to last position. Then press the enter key ...")
    input()
    last_dist = current_arduino_dist

    print("Distance limits")
    print(first_dist)
    print(last_dist)

async def godice_main():
    global dice
    #print("Discovering GoDice devices...")
    print("Discovering  devices...")
    discovery_res = await bleak.BleakScanner.discover(timeout=10, return_adv=True)
    device_advdata_tuples = discovery_res.values()
    device_advdata_tuples = filter_godice_devices(device_advdata_tuples)

    print("Discovered devices...")
    print_device_info(device_advdata_tuples)

    print("Connecting to a closest device...")
    device, _adv_data = select_closest_device(device_advdata_tuples)

    async with godice.create(device.address, godice.Shell.D6) as dice:
        print(f"Connected to {device.name}")

        color = await dice.get_color()
        battery_lvl = await dice.get_battery_level()
        print(f"Color: {color}")
        print(f"Battery: {battery_lvl}")

        print("Listening to position updates. Flip your dice")
        await dice.subscribe_number_notification(notification_callback)
        while True:
            await asyncio.sleep(30)  # sleep to keep callbacks alive
    print("end godice")

def dice_main_thread():
    asyncio.run(godice_main())
    

threading.Thread(target=dice_main_thread, daemon=True).start()
plt.show()

# TODO:
# 2) fix filled