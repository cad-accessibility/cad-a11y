import os
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeSphere
from OCC.Core.BRepTools import breptools_Write

def create_sphere(radius, output_file):
    """Create a sphere and save it as a BREP file."""
    # Create the sphere shape
    sphere = BRepPrimAPI_MakeSphere(radius).Shape()
    
    # Write the shape to a BREP file
    breptools_Write(sphere, output_file)
    print(f"Sphere saved to {output_file}")

if __name__ == "__main__":
    # Parameters for the sphere
    radius = 10.0  # Radius of the sphere
    output_file = "sphere.brep"  # Output file name

    # Ensure the output directory exists
    output_dir = os.path.dirname(output_file)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # Create and save the sphere
    create_sphere(radius, output_file)
