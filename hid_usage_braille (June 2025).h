
#ifndef _USB_HID_USAGE_BAILLE_H_
#define _USB_HID_USAGE_BAILLE_H_

#ifdef __cplusplus
    extern "C" {
#endif

/**\ingroup USB_HID
 * \addtogroup USB_HID_USAGES_BRAILLE HID Usages for Braille displays
 * \brief Contains USB HID Usages definitions for Braille displays Control Page
 * \details This module based on
 * + [HID Usage Tables Version 1.11]
 * @{ */

#define HID_PAGE_BRAILLE                    0x41

#define HID_BRAILLE_DISPLAY                 0x0001
#define HID_BRAILLE_ROW                     0x0002
#define HID_BRAILLE_8_DOT_CELL              0x0003
#define HID_BRAILLE_6_DOT_CELL              0x0004
#define HID_BRAILLE_NUMBER_OF_CELL          0x0005
#define HID_BRAILLE_SCREEN_READER_CTRL      0x0006
#define HID_BRAILLE_SCREEN_READER_ID        0x0007
#define HID_BRAILLE_DO_NOT_DISTURB          0x0008

#define HID_BRAILLE_ROUTER_SET_1            0x00FA
#define HID_BRAILLE_ROUTER_SET_2            0x00FB
#define HID_BRAILLE_ROUTER_SET_3            0x00FC

#define HID_BRAILLE_ROUTER_KEY              0x0100
#define HID_BRAILLE_ROW_ROUTER_KEY          0x0101

#define HID_BRAILLE_BUTTONS                 0x0200
#define HID_BRAILLE_KEYBOARD_DOT_1          0x0201
#define HID_BRAILLE_KEYBOARD_DOT_2          0x0202
#define HID_BRAILLE_KEYBOARD_DOT_3          0x0203
#define HID_BRAILLE_KEYBOARD_DOT_4          0x0204
#define HID_BRAILLE_KEYBOARD_DOT_5          0x0205
#define HID_BRAILLE_KEYBOARD_DOT_6          0x0206
#define HID_BRAILLE_KEYBOARD_DOT_7          0x0207
#define HID_BRAILLE_KEYBOARD_DOT_8          0x0208
#define HID_BRAILLE_KEYBOARD_SPACE          0x0209
#define HID_BRAILLE_KEYBOARD_LEFT_SPACE     0x020A
#define HID_BRAILLE_KEYBOARD_RIGHT_SPACE    0x020B

#define HID_BRAILLE_FACE_CONTROL            0x020C
#define HID_BRAILLE_LEFT_CONTROL            0x020D
#define HID_BRAILLE_RIGHT_CONTROL           0x020E
#define HID_BRAILLE_TOP_CONTROL             0x020F

#define HID_BRAILLE_JOYSTICK_CENTER         0x0210
#define HID_BRAILLE_JOYSTICK_UP             0x0211
#define HID_BRAILLE_JOYSTICK_DOWN           0x0212
#define HID_BRAILLE_JOYSTICK_LEFT           0x0213
#define HID_BRAILLE_JOYSTICK_RIGHT          0x0214

#define HID_BRAILLE_D_PAD_CENTER            0x0215
#define HID_BRAILLE_D_PAD_UP                0x0216
#define HID_BRAILLE_D_PAD_DOWN              0x0217
#define HID_BRAILLE_D_PAD_LEFT              0x0218
#define HID_BRAILLE_D_PAD_RIGHT             0x0219

#define HID_BRAILLE_PAN_LEFT                0x021A
#define HID_BRAILLE_PAN_RIGHT               0x021B

#define HID_BRAILLE_ROCKER_UP               0x021C
#define HID_BRAILLE_ROCKER_DOWN             0x021D
#define HID_BRAILLE_ROCKER_PRESS            0x021E

/* Unofficial extension for braille display */
#define HID_BRAILLE_ZOOM_IN                          0x0220
#define HID_BRAILLE_ZOOM_OUT                         0x0221

/* Unofficial extension for graphic braille*/
#define HID_BRAILLE_TACTILE_GRAPHIC_AREA             0x0300
#define HID_BRAILLE_TACTILE_GRAPHIC_PIN              0x0301
#define HID_BRAILLE_TACTILE_GRAPHIC_ROW_WIDTH        0x0302
#define HID_BRAILLE_TACTILE_GRAPHIC_ASPECT_RATIO     0x0304
#define HID_BRAILLE_TACTILE_GRAPHIC_BRAILLE_FORMAT   0x0305

/* Unofficial extension for touch mode */
#define HID_BRAILLE_TOUCH_SCREEN_PIN                 0x0401
#define HID_BRAILLE_TOUCH_SCREEN_VIRTUAL_CELL_1      0x0402
#define HID_BRAILLE_TOUCH_SCREEN_VIRTUAL_CELL_256    0x0501

#define HID_BRAILLE_DISPLAY_REFRESH_TIME             0x05F1
#define HID_BRAILLE_DISPLAY_HANDS_OFF                0x05F2

/** @}  */

#ifdef __cplusplus
    }
#endif

#endif

