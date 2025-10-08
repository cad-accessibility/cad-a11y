/*******************************************************
 HIDAPI - Multi-Platform library for
 communication with HID devices.

 Alan Ott
 Signal 11 Software

 libusb/hidapi Team

 Copyright 2022.

 This contents of this file may be used by anyone
 for any reason without any conditions and may be
 used as a starting point for your own applications
 which use HIDAPI.
********************************************************/

#include <stdio.h>
#include <wchar.h>
#include <string.h>
#include <stdlib.h>
#include <stdint.h>

#include <hidapi.h>

// Headers needed for sleeping.
#ifdef _WIN32
	#include <windows.h>
#else
	#include <unistd.h>
#endif

// Fallback/example
#ifndef HID_API_MAKE_VERSION
#define HID_API_MAKE_VERSION(mj, mn, p) (((mj) << 24) | ((mn) << 8) | (p))
#endif
#ifndef HID_API_VERSION
#define HID_API_VERSION HID_API_MAKE_VERSION(HID_API_VERSION_MAJOR, HID_API_VERSION_MINOR, HID_API_VERSION_PATCH)
#endif

//
// Sample using platform-specific headers
#if defined(__APPLE__) && HID_API_VERSION >= HID_API_MAKE_VERSION(0, 12, 0)
#include <hidapi_darwin.h>
#endif

#if defined(_WIN32) && HID_API_VERSION >= HID_API_MAKE_VERSION(0, 12, 0)
#include <hidapi_winapi.h>
#endif

#if defined(USING_HIDAPI_LIBUSB) && HID_API_VERSION >= HID_API_MAKE_VERSION(0, 12, 0)
#include <hidapi_libusb.h>
#endif
//

const char *hid_bus_name(hid_bus_type bus_type) {
	static const char *const HidBusTypeName[] = {
		"Unknown",
		"USB",
		"Bluetooth",
		"I2C",
		"SPI",
	};

	if ((int)bus_type < 0)
		bus_type = HID_API_BUS_UNKNOWN;
	if ((int)bus_type >= (int)(sizeof(HidBusTypeName) / sizeof(HidBusTypeName[0])))
		bus_type = HID_API_BUS_UNKNOWN;

	return HidBusTypeName[bus_type];
}

void print_device(struct hid_device_info *cur_dev) {
	printf("Device Found\n  type: %04hx %04hx\n  path: %s\n  serial_number: %ls", cur_dev->vendor_id, cur_dev->product_id, cur_dev->path, cur_dev->serial_number);
	printf("\n");
	printf("  Manufacturer: %ls\n", cur_dev->manufacturer_string);
	printf("  Product:      %ls\n", cur_dev->product_string);
	printf("  Release:      %hx\n", cur_dev->release_number);
	printf("  Interface:    %d\n",  cur_dev->interface_number);
	printf("  Usage (page): 0x%hx (0x%hx)\n", cur_dev->usage, cur_dev->usage_page);
	printf("  Bus type: %u (%s)\n", (unsigned)cur_dev->bus_type, hid_bus_name(cur_dev->bus_type));
	printf("\n");
}

void print_hid_report_descriptor_from_device(hid_device *device) {
	unsigned char descriptor[HID_API_MAX_REPORT_DESCRIPTOR_SIZE];
	int res = 0;

	printf("  Report Descriptor: ");
#if HID_API_VERSION >= HID_API_MAKE_VERSION(0, 14, 0)
	res = hid_get_report_descriptor(device, descriptor, sizeof(descriptor));
#else
	(void)res;
#endif
	if (res < 0) {
		printf("error getting: %ls", hid_error(device));
	}
	else {
		printf("(%d bytes)", res);
	}
	for (int i = 0; i < res; i++) {
		if (i % 10 == 0) {
			printf("\n");
		}
		printf("0x%02x, ", descriptor[i]);
	}
	printf("\n");
}

void print_hid_report_descriptor_from_path(const char *path) {
	hid_device *device = hid_open_path(path);
	if (device) {
		print_hid_report_descriptor_from_device(device);
		hid_close(device);
	}
	else {
		printf("  Report Descriptor: Unable to open device by path\n");
	}
}

void print_devices(struct hid_device_info *cur_dev) {
	for (; cur_dev; cur_dev = cur_dev->next) {
		print_device(cur_dev);
	}
}

void print_devices_with_descriptor(struct hid_device_info *cur_dev) {
	for (; cur_dev; cur_dev = cur_dev->next) {
		print_device(cur_dev);
		print_hid_report_descriptor_from_path(cur_dev->path);
	}
}

int main(int argc, char* argv[])
{
	(void)argc;
	(void)argv;

	/* --- HIDAPI R&D: this is just to force the compiler to ensure
	       each of those functions are implemented (even as a stub)
	       by each backend. --- */
	(void)&hid_open;
	(void)&hid_open_path;
	(void)&hid_read_timeout;
	(void)&hid_get_input_report;
#if HID_API_VERSION >= HID_API_MAKE_VERSION(0, 15, 0)
	(void)&hid_send_output_report;
#endif
	(void)&hid_get_feature_report;
	(void)&hid_send_feature_report;
#if HID_API_VERSION >= HID_API_MAKE_VERSION(0, 14, 0)
	(void)&hid_get_report_descriptor;
#endif
	/* --- */

	int res;
	uint8_t buf[481];
	#define MAX_STR 255
	wchar_t wstr[MAX_STR];
	hid_device *handle;
	int i;

	struct hid_device_info *devs;

	printf("hidapi test/example tool. Compiled with hidapi version %s, runtime version %s.\n", HID_API_VERSION_STR, hid_version_str());
	if (HID_API_VERSION == HID_API_MAKE_VERSION(hid_version()->major, hid_version()->minor, hid_version()->patch)) {
		printf("Compile-time version matches runtime version of hidapi.\n\n");
	}
	else {
		printf("Compile-time version is different than runtime version of hidapi.\n]n");
	}

	if (hid_init())
		return -1;

#if defined(__APPLE__) && HID_API_VERSION >= HID_API_MAKE_VERSION(0, 12, 0)
	// To work properly needs to be called before hid_open/hid_open_path after hid_init.
	// Best/recommended option - call it right after hid_init.
	hid_darwin_set_open_exclusive(0);
#endif

	devs = hid_enumerate(0x0, 0x0);
	print_devices_with_descriptor(devs);
	hid_free_enumeration(devs);

	// Set up the command buffer.
	memset(buf,0x00,sizeof(buf));
	buf[0] = 0x01;
	buf[1] = 0x81;


	// Open the device using the VID, PID,
	// and optionally the Serial number.
	////handle = hid_open(0x4d8, 0x3f, L"12345");
	handle = hid_open(0x1C71, 0xD110, NULL);
	if (!handle) {
		printf("unable to open device\n");
		hid_exit();
 		return 1;
	}

#if defined(_WIN32) && HID_API_VERSION >= HID_API_MAKE_VERSION(0, 15, 0)
	hid_winapi_set_write_timeout(handle, 5000);
#endif

	// Read the Manufacturer String
	wstr[0] = 0x0000;
	res = hid_get_manufacturer_string(handle, wstr, MAX_STR);
	if (res < 0)
		printf("Unable to read manufacturer string\n");
	printf("Manufacturer String: %ls\n", wstr);

	// Read the Product String
	wstr[0] = 0x0000;
	res = hid_get_product_string(handle, wstr, MAX_STR);
	if (res < 0)
		printf("Unable to read product string\n");
	printf("Product String: %ls\n", wstr);

	// Read the Serial Number String
	wstr[0] = 0x0000;
	res = hid_get_serial_number_string(handle, wstr, MAX_STR);
	if (res < 0)
		printf("Unable to read serial number string\n");
	printf("Serial Number String: (%d) %ls\n", wstr[0], wstr);

	print_hid_report_descriptor_from_device(handle);

	struct hid_device_info* info = hid_get_device_info(handle);
	if (info == NULL) {
		printf("Unable to get device info\n");
	} else {
		print_devices(info);
	}

	// Read Indexed String 1
	wstr[0] = 0x0000;
	res = hid_get_indexed_string(handle, 1, wstr, MAX_STR);
	if (res < 0)
		printf("Unable to read indexed string 1\n");
	printf("Indexed String 1: %ls\n", wstr);

	// Set the hid_read() function to be non-blocking.
	//hid_set_nonblocking(handle, 1);

	// Try to read from the device. There should be no
	// data here, but execution should not block.
	//res = hid_read(handle, buf, 17);


	memset(buf, 0, sizeof(buf));

	// Send a Feature Report to the device
	buf[0] = 0x21;

	//line 0
	buf[1 + 23] = 0xFF;
	
	//line 1
	buf[49 + 8] = 0xE0;
	buf[49 + 9] = 0xFF;
	buf[49 + 10] = 0xFF;
	buf[49 + 11] = 0xC4;

	buf[49 +23] = 0xFF;

	//line 2
	buf[97 + 8] = 0x19;
	buf[97 + 9] = 0xFF;
	buf[97 + 10] = 0xFF;
	buf[97 + 11] = 0xB;

	buf[97 +23] = 0xFF;

	//line 3
	buf[145 +23] = 0xFF;

	//line 4

	buf[193 + 33] = 0xE0;
	buf[193 + 34] = 0xFF;
	buf[193 + 35] = 0xFF;
	buf[193 + 36] = 0xC4;

	buf[193 +23] = 0xFF;

	//line 5
	buf[241 + 33] = 0x19;
	buf[241 + 34] = 0xFF;
	buf[241 + 35] = 0xFF;
	buf[241 + 36] = 0xB;

	buf[241 +23] = 0xFF;

	//line 6
	buf[289 +23] = 0xFF;

	//line 7
	buf[337 + 15] = 0xE0;
	buf[337 + 16] = 0xFF;
	buf[337 + 17] = 0xFF;
	buf[337 + 18] = 0xC4;

	buf[337 +23] = 0xFF;

	//line8

	buf[385 + 15] = 0x19;
	buf[385 + 16] = 0xFF;
	buf[385 + 17] = 0xFF;
	buf[385 + 18] = 0xB;

	buf[385 +23] = 0xFF;

	//line9
	buf[433 +23] = 0xFF;


	//res = hid_write(handle, buf, 33);
	res = hid_write(handle, buf, 481);
	if (res < 0) {
		printf("Unable to send a graphic report.error= %d\n",res );
	}

	hid_close(handle);

	/* Free static HIDAPI objects. */
	hid_exit();

#ifdef _WIN32
	system("pause");
#endif

	return 0;
}
