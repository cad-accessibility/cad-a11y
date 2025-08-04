"""
Monarch Braille Display Simulation Script

This script simulates how the Monarch braille display from American Printing House
would render a matplotlib figure as a tactile graphic.

The Monarch specifications:
- 32 braille cells per line
- 10 lines total  
- Each cell has 8 pins in a 2x4 orientation (2 columns, 4 rows)
- Total resolution: 64x40 pins (32*2 columns x 10*4 rows)

Features:
- Converts matplotlib figures to pin patterns
- Extracts line elements and translates them to tactile representation
- Returns a new matplotlib figure showing the tactile rendering
- Supports future color implementation (up to 3 colors)
"""

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.collections import LineCollection
from matplotlib.path import Path
import warnings


class MonarchRenderer:
    """Simulates the Monarch braille display rendering"""
    
    # Monarch display specifications
    CELLS_PER_LINE = 32
    LINES_COUNT = 10
    PINS_PER_CELL_X = 2  # columns
    PINS_PER_CELL_Y = 4  # rows
    
    # Total resolution
    TOTAL_PINS_X = CELLS_PER_LINE * PINS_PER_CELL_X  # 64 pins wide
    TOTAL_PINS_Y = LINES_COUNT * PINS_PER_CELL_Y      # 40 pins tall
    
    def __init__(self):
        self.pin_array = np.zeros((self.TOTAL_PINS_Y, self.TOTAL_PINS_X), dtype=bool)
        self.color_support = False  # Future feature - up to 3 colors
        
    def extract_lines_from_figure(self, fig):
        """
        Extract line elements from a matplotlib figure
        
        Args:
            fig: matplotlib.figure.Figure - Input figure
            
        Returns:
            list: List of line coordinates [(x1, y1, x2, y2), ...]
        """
        lines = []
        
        for ax in fig.get_axes():
            # Get data limits for coordinate transformation
            xlim = ax.get_xlim()
            ylim = ax.get_ylim()
            
            # Extract Line2D objects
            for line in ax.get_lines():
                xdata = line.get_xdata()
                ydata = line.get_ydata()
                
                # Convert line segments
                for i in range(len(xdata) - 1):
                    x1, y1 = xdata[i], ydata[i]
                    x2, y2 = xdata[i + 1], ydata[i + 1]
                    lines.append((x1, y1, x2, y2))
            
            # Extract LineCollection objects (e.g., from contour plots)
            for collection in ax.collections:
                if isinstance(collection, LineCollection):
                    for path in collection.get_paths():
                        vertices = path.vertices
                        try:
                            vertices_array = np.asarray(vertices)
                            if vertices_array.ndim == 2 and len(vertices_array) > 1:
                                for i in range(len(vertices_array) - 1):
                                    x1, y1 = float(vertices_array[i][0]), float(vertices_array[i][1])
                                    x2, y2 = float(vertices_array[i + 1][0]), float(vertices_array[i + 1][1])
                                    lines.append((x1, y1, x2, y2))
                        except (IndexError, TypeError, ValueError):
                            continue
            
            # Extract patch outlines (rectangles, circles, etc.)
            for patch in ax.patches:
                path = patch.get_path()
                if path is not None:
                    vertices = path.vertices
                    try:
                        vertices_array = np.asarray(vertices)
                        if vertices_array.ndim == 2 and len(vertices_array) > 1:
                            for i in range(len(vertices_array) - 1):
                                x1, y1 = float(vertices_array[i][0]), float(vertices_array[i][1])
                                x2, y2 = float(vertices_array[i + 1][0]), float(vertices_array[i + 1][1])
                                lines.append((x1, y1, x2, y2))
                    except (IndexError, TypeError, ValueError):
                        continue
        
        return lines
    
    def normalize_coordinates(self, lines, data_xlim, data_ylim):
        """
        Normalize line coordinates to pin array dimensions
        
        Args:
            lines: List of line coordinates in data space
            data_xlim: (min_x, max_x) data limits
            data_ylim: (min_y, max_y) data limits
            
        Returns:
            list: Lines with coordinates in pin space [0, TOTAL_PINS_X/Y]
        """
        normalized_lines = []
        
        data_width = data_xlim[1] - data_xlim[0]
        data_height = data_ylim[1] - data_ylim[0]
        
        for x1, y1, x2, y2 in lines:
            # Normalize to [0, 1] range
            norm_x1 = (x1 - data_xlim[0]) / data_width
            norm_y1 = (y1 - data_ylim[0]) / data_height
            norm_x2 = (x2 - data_xlim[0]) / data_width
            norm_y2 = (y2 - data_ylim[0]) / data_height
            
            # Scale to pin array dimensions
            pin_x1 = int(norm_x1 * (self.TOTAL_PINS_X - 1))
            pin_y1 = int(norm_y1 * (self.TOTAL_PINS_Y - 1))
            pin_x2 = int(norm_x2 * (self.TOTAL_PINS_X - 1))
            pin_y2 = int(norm_y2 * (self.TOTAL_PINS_Y - 1))
            
            # Flip Y coordinate (matplotlib Y increases upward, pin array Y increases downward)
            pin_y1 = self.TOTAL_PINS_Y - 1 - pin_y1
            pin_y2 = self.TOTAL_PINS_Y - 1 - pin_y2
            
            normalized_lines.append((pin_x1, pin_y1, pin_x2, pin_y2))
        
        return normalized_lines
    
    def draw_line_on_pins(self, x1, y1, x2, y2):
        """
        Draw a line on the pin array using Bresenham's line algorithm
        
        Args:
            x1, y1, x2, y2: Line endpoints in pin coordinates
        """
        # Bresenham's line algorithm
        dx = abs(x2 - x1)
        dy = abs(y2 - y1)
        sx = 1 if x1 < x2 else -1
        sy = 1 if y1 < y2 else -1
        err = dx - dy
        
        x, y = x1, y1
        
        while True:
            # Set pin if within bounds
            if 0 <= x < self.TOTAL_PINS_X and 0 <= y < self.TOTAL_PINS_Y:
                self.pin_array[y, x] = True
            
            if x == x2 and y == y2:
                break
                
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x += sx
            if e2 < dx:
                err += dx
                y += sy
    
    def get_data_limits(self, fig):
        """
        Get the overall data limits from all axes in the figure
        
        Args:
            fig: matplotlib.figure.Figure
            
        Returns:
            tuple: (xlim, ylim) where each is (min, max)
        """
        all_xlims = []
        all_ylims = []
        
        for ax in fig.get_axes():
            xlim = ax.get_xlim()
            ylim = ax.get_ylim()
            all_xlims.extend(xlim)
            all_ylims.extend(ylim)
        
        if not all_xlims or not all_ylims:
            return (0, 1), (0, 1)
            
        return (min(all_xlims), max(all_xlims)), (min(all_ylims), max(all_ylims))
    
    def render_figure(self, fig):
        """
        Main rendering function - converts matplotlib figure to tactile representation
        
        Args:
            fig: matplotlib.figure.Figure - Input figure to convert
            
        Returns:
            matplotlib.figure.Figure - New figure showing tactile rendering
        """
        # Reset pin array
        self.pin_array.fill(False)
        
        # Extract lines from the figure
        lines = self.extract_lines_from_figure(fig)
        
        if not lines:
            warnings.warn("No line elements found in the figure")
            return self.create_tactile_figure()
        
        # Get data limits for normalization
        data_xlim, data_ylim = self.get_data_limits(fig)
        
        # Normalize coordinates to pin space
        normalized_lines = self.normalize_coordinates(lines, data_xlim, data_ylim)
        
        # Draw lines on pin array
        for x1, y1, x2, y2 in normalized_lines:
            self.draw_line_on_pins(x1, y1, x2, y2)
        
        # Create tactile representation figure
        return self.create_tactile_figure()
    
    def create_tactile_figure(self):
        """
        Create a matplotlib figure showing the tactile representation
        
        Returns:
            matplotlib.figure.Figure - Figure showing pin states
        """
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))
        
        # Left subplot: Pin-level view
        ax1.imshow(self.pin_array, cmap='Greys', interpolation='nearest', aspect='equal')
        ax1.set_title(f'Pin Array View ({self.TOTAL_PINS_X}x{self.TOTAL_PINS_Y} pins)')
        ax1.set_xlabel('Pin X')
        ax1.set_ylabel('Pin Y')
        
        # Add grid to show individual pins
        ax1.set_xticks(np.arange(-0.5, self.TOTAL_PINS_X, 1), minor=True)
        ax1.set_yticks(np.arange(-0.5, self.TOTAL_PINS_Y, 1), minor=True)
        ax1.grid(which='minor', color='lightgray', linestyle='-', linewidth=0.2)
        
        # Right subplot: Braille cell view
        self.create_braille_cell_view(ax2)
        
        plt.tight_layout()
        return fig
    
    def create_braille_cell_view(self, ax):
        """
        Create a view showing the braille cell structure
        
        Args:
            ax: matplotlib axis for the braille cell view
        """
        ax.set_xlim(0, self.CELLS_PER_LINE)
        ax.set_ylim(0, self.LINES_COUNT)
        ax.set_aspect('equal')
        ax.set_title(f'Braille Cell View ({self.CELLS_PER_LINE}x{self.LINES_COUNT} cells)')
        ax.set_xlabel('Cell X')
        ax.set_ylabel('Cell Y')
        
        # Draw braille cells
        for cell_y in range(self.LINES_COUNT):
            for cell_x in range(self.CELLS_PER_LINE):
                # Get the pins for this cell
                pin_start_x = cell_x * self.PINS_PER_CELL_X
                pin_start_y = cell_y * self.PINS_PER_CELL_Y
                
                cell_pins = self.pin_array[
                    pin_start_y:pin_start_y + self.PINS_PER_CELL_Y,
                    pin_start_x:pin_start_x + self.PINS_PER_CELL_X
                ]
                
                # Draw the cell boundary
                rect = patches.Rectangle(
                    (cell_x, self.LINES_COUNT - cell_y - 1), 1, 1,
                    linewidth=0.5, edgecolor='gray', facecolor='none'
                )
                ax.add_patch(rect)
                
                # Draw active pins within the cell
                for pin_y in range(self.PINS_PER_CELL_Y):
                    for pin_x in range(self.PINS_PER_CELL_X):
                        if cell_pins[pin_y, pin_x]:
                            # Calculate pin position within the cell
                            pin_pos_x = cell_x + (pin_x + 0.5) / self.PINS_PER_CELL_X
                            pin_pos_y = (self.LINES_COUNT - cell_y - 1) + (self.PINS_PER_CELL_Y - pin_y - 0.5) / self.PINS_PER_CELL_Y
                            
                            circle = patches.Circle(
                                (pin_pos_x, pin_pos_y), 0.1,
                                facecolor='black', edgecolor='black'
                            )
                            ax.add_patch(circle)
        
        # Add grid lines for cells
        ax.set_xticks(range(self.CELLS_PER_LINE + 1))
        ax.set_yticks(range(self.LINES_COUNT + 1))
        ax.grid(True, alpha=0.3)
    
    def get_pin_array(self):
        """
        Get the current pin array state
        
        Returns:
            numpy.ndarray: Boolean array representing pin states (True = raised)
        """
        return self.pin_array.copy()
    
    def get_braille_cell_data(self):
        """
        Get pin data organized by braille cells
        
        Returns:
            numpy.ndarray: Shape (LINES_COUNT, CELLS_PER_LINE, PINS_PER_CELL_Y, PINS_PER_CELL_X)
        """
        cell_data = np.zeros((
            self.LINES_COUNT, self.CELLS_PER_LINE, 
            self.PINS_PER_CELL_Y, self.PINS_PER_CELL_X
        ), dtype=bool)
        
        for cell_y in range(self.LINES_COUNT):
            for cell_x in range(self.CELLS_PER_LINE):
                pin_start_x = cell_x * self.PINS_PER_CELL_X
                pin_start_y = cell_y * self.PINS_PER_CELL_Y
                
                cell_data[cell_y, cell_x] = self.pin_array[
                    pin_start_y:pin_start_y + self.PINS_PER_CELL_Y,
                    pin_start_x:pin_start_x + self.PINS_PER_CELL_X
                ]
        
        return cell_data


def simulate_monarch_render(fig):
    """
    Convenience function to render a matplotlib figure on the Monarch display
    
    Args:
        fig: matplotlib.figure.Figure - Input figure to convert
        
    Returns:
        matplotlib.figure.Figure - Figure showing tactile rendering
    """
    renderer = MonarchRenderer()
    return renderer.render_figure(fig)


# Example usage and testing
if __name__ == "__main__":
    # Create a test figure with various elements
    fig, ax = plt.subplots(figsize=(8, 6))
    
    # Add some test content
    x = np.linspace(0, 10, 100)
    y1 = np.sin(x)
    y2 = np.cos(x)
    
    ax.plot(x, y1, label='sin(x)', linewidth=2)
    ax.plot(x, y2, label='cos(x)', linewidth=2)
    ax.axhline(y=0, color='k', linestyle='--', alpha=0.5)
    ax.axvline(x=5, color='r', linestyle=':', alpha=0.7)
    
    # Add a rectangle
    rect = patches.Rectangle((2, -0.5), 3, 1, linewidth=2, edgecolor='blue', facecolor='none')
    ax.add_patch(rect)
    
    ax.set_xlabel('X axis')
    ax.set_ylabel('Y axis')
    ax.set_title('Test Figure for Monarch Rendering')
    ax.legend()
    ax.grid(True, alpha=0.3)
    
    # Render on Monarch display
    tactile_fig = simulate_monarch_render(fig)
    
    # Show both figures
    plt.show()
    
    print("Monarch simulation complete!")
    print(f"Total pins activated: {MonarchRenderer().get_pin_array().sum()}")
