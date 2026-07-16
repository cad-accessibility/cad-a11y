import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Rectangle, Circle

def save_binary_array_as_vector_pdf(array, filename="low_res.pdf"):
    height, width = array.shape
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
            if array[y, x] == 255:
                #ax.add_patch(Rectangle((x, height - y - 1), 1, 1, facecolor='black'))
                #if y == height-1:
                #    ax.add_patch(Circle((x, height - y), 0.3, facecolor='black'))
                #else:
                ax.add_patch(Circle((x, height - y - 1), 0.3, facecolor='black'))

    fig.savefig(filename, format='pdf', bbox_inches='tight', pad_inches=0)
    #fig.savefig(filename, format='pdf')
    plt.close(fig)

# vectorized version
def get_outlines(img_np):
    h, w = img_np.shape[:2]

    #white = np.all(img_np == [255, 255, 255, 255], axis=-1)
    #non_white = img_np[..., 0] < 255
    white = img_np[..., 0] >= 254
    non_white = img_np[..., 0] <= 253
    white_padded = np.pad(white, 1, mode='constant', constant_values=False)

    # white mask neighbors
    neighbor_white = (
        white_padded[0:h,     0:w]   | 
        white_padded[0:h,     1:w+1] | 
        white_padded[0:h,     2:w+2] | 
        white_padded[1:h+1,   0:w]   | 
        white_padded[1:h+1,   2:w+2] | 
        white_padded[2:h+2,   0:w]   | 
        white_padded[2:h+2,   1:w+1] | 
        white_padded[2:h+2,   2:w+2]    
    )

    outline_mask = non_white & neighbor_white

    outline_pixels_rgba = np.empty((h, w, 4), dtype=np.uint8)
    outline_pixels_rgba[outline_mask] = [0, 0, 0, 255]
    outline_pixels_rgba[~outline_mask] = [255, 255, 255, 255]

    return outline_pixels_rgba, outline_mask

if __name__ == "__main__":
    print("test")