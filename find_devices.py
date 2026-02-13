import hid

# Enumerate all HID devices
for device in hid.enumerate():
		print(f"""
Vendor ID: {device['vendor_id']:04x}
Product ID: {device['product_id']:04x}
Manufacturer: {device['manufacturer_string']}
Product: {device['product_string']}
Serial Number: {device['serial_number']}
Path: {device['path'].decode() if isinstance(device['path'], bytes) else device['path']}
Release Number: {device['release_number']}
Interface Number: {device['interface_number']}
""")