from .single_view_stl import get_single_view
import numpy as np

def get_superposition_view(shapes, bbox, cut_depth=0.9, view_key="top", rendering_mode="brep", imposed_ax_limits=[],
                           superposition_key="intersection", screen_size=[96,40]):
    filled_before, ax_limits = get_single_view(shapes[0], bbox, cut_depth, view_key, "filled", 
                          imposed_ax_limits=imposed_ax_limits, screen_size=screen_size)
    outlines_before, ax_limits = get_single_view(shapes[0], bbox, cut_depth, view_key, "outline", 
                          imposed_ax_limits=imposed_ax_limits, screen_size=screen_size)
    filled_after, ax_limits = get_single_view(shapes[1], bbox, cut_depth, view_key, "filled",  
                          imposed_ax_limits=imposed_ax_limits, screen_size=screen_size)
    outlines_after, ax_limits = get_single_view(shapes[1], bbox, cut_depth, view_key, "outline", 
                          imposed_ax_limits=imposed_ax_limits, screen_size=screen_size)

    filled_before_int = np.zeros(filled_before.shape[:2], dtype=int)
    mask = (np.all(filled_before[:,:,:] == [0,0,0,255], axis=2))#[:,:,3]
    filled_before_int[mask] = 1

    filled_after_int = np.zeros(filled_before.shape[:2], dtype=int)
    mask = (np.all(filled_after[:,:,:] == [0,0,0,255], axis=2))#[:,:,3]
    filled_after_int[mask] = 1


    outlines_before_int = np.zeros(filled_before.shape[:2], dtype=int)
    outline_before_mask = (np.all(outlines_before[:,:,:] == [0,0,0,255], axis=2))#[:,:,3]
    outlines_before_int[mask] = 1

    outlines_after_int = np.zeros(filled_after.shape[:2], dtype=int)
    outlines_after_mask = (np.all(outlines_after[:,:,:] == [0,0,0,255], axis=2))#[:,:,3]
    outlines_after_int[mask] = 1

    result = np.zeros((filled_before.shape[0], filled_before.shape[1], 4), dtype=np.uint8)
    result[:] = [255,255,255,255]
    result[outline_before_mask] = [0, 0, 0, 255]
    result[outlines_after_mask] = [0, 0, 0, 255]

    if superposition_key == "intersection":
        inter_region = np.logical_and(filled_before_int, filled_after_int)
        mask = inter_region == 1
    elif superposition_key == "difference before":
        diff_before = np.logical_and(filled_before_int, np.logical_not(filled_after_int))
        mask = diff_before == 1
    elif superposition_key == "difference after":
        diff_after = np.logical_and(filled_after_int, np.logical_not(filled_before_int))
        mask = diff_after == 1
    if not superposition_key == "outline":
        result[mask] = [0, 0, 0, 255]

    return result