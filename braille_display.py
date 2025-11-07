#!/usr/bin/env python3
"""
Braille Display Communication Module

This module provides a simple interface to send numpy arrays to a braille display device.
It handles the HID device connection and data formatting automatically.

Dependencies:
- hidapi (pip install hidapi)
- numpy
"""

import numpy as np

import hid

class BrailleDisplayError(Exception):
    """Exception raised for braille display communication errors."""
    pass


def send_to_braille_display(array):
    """
    Send a numpy array to the braille display device.
    
    Args:
        array (np.ndarray): 2D array (H, W) or 3D array (H, W, 1) of values.
                            Values > 0 are treated as raised dots. No resizing is performed.
    
    Returns:
        int: Number of bytes written to the device.
        
    Raises:
        BrailleDisplayError: If device connection fails or data transmission fails.
        ValueError: If array shape is incorrect.
    """
    # Validate input array
    if not isinstance(array, np.ndarray):
        raise ValueError("Input must be a numpy array")
    
    # Accept 2D or (H, W, 1); do not resize
    if array.ndim == 3 and array.shape[-1] == 1:
        array_2d = array.squeeze(-1)
    elif array.ndim == 2:
        array_2d = array
    else:
        raise ValueError(f"Array must be 2D or (H, W, 1); got shape {array.shape}")
    
    # Device constants
    TARGET_VENDOR_ID = 0x1C71
    TARGET_PRODUCT_ID = 0xD110
    BUFFER_SIZE = 481
    
    # Convert numpy array to braille buffer format
    buf = [0] * BUFFER_SIZE
    buf[0] = 0x21  # Command byte
    
    # Convert pixel array to braille cells (values > 0 are considered raised)
    _convert_pixels_to_braille(buf, array_2d)
    
    # Connect to device and send data
    try:
        print("try to connect")
        device = hid.device()
        device.open(TARGET_VENDOR_ID, TARGET_PRODUCT_ID)
        
        try:
            print("DEVICE", device)
            bytes_written = device.write(bytes(buf))
            if bytes_written <= 0:
                raise BrailleDisplayError("Failed to write data to device")
            # Print short summary after successful send
            try:
                nz = int((array_2d > 0).sum())
                print(f"Braille sent: bytes={bytes_written}, shape={array_2d.shape}, dtype={array_2d.dtype}, nonzero={nz}")
            except Exception:
                pass
            return bytes_written
        finally:
            device.close()
            
    except Exception as e:
        raise BrailleDisplayError(f"Device communication failed: {e}")


def _convert_pixels_to_braille(buf, array_2d):
    """
    Convert the 2D pixel array to braille buffer format.
    Each braille cell represents a 4×2 pixel block.
    Only the pixels that fit into the 10×40 cells are used; extra pixels are ignored.
    
    Args:
        buf (list): The buffer to modify
        array_2d (np.ndarray): The 2D array (97, 40) with pixel data
    """
    # Process each braille display line (10 lines total)
    for display_line in range(10):
        line_offset = 1 + display_line * 48  # Each line starts at offset 1, 49, 97, etc.
        
        # Process each braille cell in the line (40 cells per line)
        for cell_col in range(48):
            # Calculate the starting pixel position for this 4×2 block
            pixel_row_start = display_line * 4
            pixel_col_start = cell_col * 2
            
            # Make sure we don't exceed array bounds
            if pixel_row_start + 3 >= array_2d.shape[0] or pixel_col_start + 1 >= array_2d.shape[1]:
                continue
            
            # Extract the 4×2 pixel block
            braille_byte = 0
            
            # Map each pixel to its corresponding braille dot bit
            # (0,0) → dot 1 → bit 0
            if array_2d[pixel_row_start + 0, pixel_col_start + 0] > 0:
                braille_byte |= 1 << 0
            
            # (1,0) → dot 2 → bit 1
            if array_2d[pixel_row_start + 1, pixel_col_start + 0] > 0:
                braille_byte |= 1 << 1
            
            # (2,0) → dot 3 → bit 2
            if array_2d[pixel_row_start + 2, pixel_col_start + 0] > 0:
                braille_byte |= 1 << 2
            
            # (0,1) → dot 4 → bit 3
            if array_2d[pixel_row_start + 0, pixel_col_start + 1] > 0:
                braille_byte |= 1 << 3
            
            # (1,1) → dot 5 → bit 4
            if array_2d[pixel_row_start + 1, pixel_col_start + 1] > 0:
                braille_byte |= 1 << 4
            
            # (2,1) → dot 6 → bit 5
            if array_2d[pixel_row_start + 2, pixel_col_start + 1] > 0:
                braille_byte |= 1 << 5
            
            # (3,0) → dot 7 → bit 6
            if array_2d[pixel_row_start + 3, pixel_col_start + 0] > 0:
                braille_byte |= 1 << 6
            
            # (3,1) → dot 8 → bit 7
            if array_2d[pixel_row_start + 3, pixel_col_start + 1] > 0:
                braille_byte |= 1 << 7
            
            # Store the braille byte in the buffer
            if line_offset + cell_col < len(buf):
                buf[line_offset + cell_col] = braille_byte
            # (3,1) → dot 8 → bit 7
            if array_2d[pixel_row_start + 3, pixel_col_start + 1] > 0:
                braille_byte |= 1 << 7
            
            # Store the braille byte in the buffer
            if line_offset + cell_col < len(buf):
                buf[line_offset + cell_col] = braille_byte
