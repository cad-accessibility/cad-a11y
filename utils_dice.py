import asyncio
import bleak
import threading
import godice
from pyautogui import press

# Question for Carlos: How to modify the website status from here without hotkey?
# Maybe with requests.post ?

@app.route('/get_data')
async def notification_callback(number, stability_descr):
    #global view_key_i
    """
    GoDice number notification callback.
    Called each time GoDice is flipped, receiving flip event data:
    :param number: a rolled number
    :param stability_descr: an additional value clarifying device movement state, ie stable, rolling...
    """
    if stability_descr in [godice.StabilityDescriptor.MOVE_STABLE, godice.StabilityDescriptor.STABLE]:
        print(f"Number: {number}, stability descriptor: {stability_descr}")
        if number == 6:
            #view_key_i = 0
            press("t")
        elif number == 1:
            #view_key_i = 0
            press("b")
        elif number == 3:
            press("l")
        elif number == 4:
            press("r")
            #view_key_i = 1
        elif number == 2:
            press("u")
        elif number == 5:
            press("i")
            #view_key_i = 2
        #update_plot()
    return {'cube_value': "top"}

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