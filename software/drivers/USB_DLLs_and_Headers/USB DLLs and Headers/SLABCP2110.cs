/////////////////////////////////////////////////////////////////////////////
// SLABCP2110.cs
// For SLABHIDtoUART.dll version 1.4
// and Silicon Labs CP2110 HID to UART
/////////////////////////////////////////////////////////////////////////////

/////////////////////////////////////////////////////////////////////////////
// Namespaces
/////////////////////////////////////////////////////////////////////////////

using System;
using System.Diagnostics;
using System.Collections.Generic;
using System.Text;
using System.Runtime.InteropServices;

/////////////////////////////////////////////////////////////////////////////
// SLABHIDtoUART.dll Namespace
/////////////////////////////////////////////////////////////////////////////

namespace SLAB_HID_TO_UART
{
    /////////////////////////////////////////////////////////////////////////////
    // SLABHIDtoUART.dll Imports
    /////////////////////////////////////////////////////////////////////////////
    
    public class CP2110_DLL
    {
        /////////////////////////////////////////////////////////////////////////////
        // Return Code Definitions
        /////////////////////////////////////////////////////////////////////////////
        
        #region Return Code Definitions

        // Return Codes
        public const byte HID_UART_SUCCESS = 0x00;
        public const byte HID_UART_DEVICE_NOT_FOUND = 0x01;
        public const byte HID_UART_INVALID_HANDLE = 0x02;
        public const byte HID_UART_INVALID_DEVICE_OBJECT = 0x03;
        public const byte HID_UART_INVALID_PARAMETER = 0x04;
        public const byte HID_UART_INVALID_REQUEST_LENGTH = 0x05;

        public const byte HID_UART_READ_ERROR = 0x10;
        public const byte HID_UART_WRITE_ERROR = 0x11;
        public const byte HID_UART_READ_TIMED_OUT = 0x12;
        public const byte HID_UART_WRITE_TIMED_OUT = 0x13;
        public const byte HID_UART_DEVICE_IO_FAILED = 0x14;
        public const byte HID_UART_DEVICE_ACCESS_ERROR = 0x15;
        public const byte HID_UART_DEVICE_NOT_SUPPORTED = 0x16;

        public const byte HID_UART_UNKNOWN_ERROR = 0xFF;

        #endregion

        /////////////////////////////////////////////////////////////////////////////
        // String Definitions
        /////////////////////////////////////////////////////////////////////////////

        #region String Definitions

        // Product String Types
        public const byte HID_UART_GET_VID_STR = 0x01;
        public const byte HID_UART_GET_PID_STR = 0x02;
        public const byte HID_UART_GET_PATH_STR = 0x03;
        public const byte HID_UART_GET_SERIAL_STR = 0x04;
        public const byte HID_UART_GET_MANUFACTURER_STR = 0x05;
        public const byte HID_UART_GET_PRODUCT_STR = 0x06;

        // String Lengths
        public const uint HID_UART_DEVICE_STRLEN = 260;

        #endregion

        /////////////////////////////////////////////////////////////////////////////
        // UART Definitions
        /////////////////////////////////////////////////////////////////////////////

        #region UART Definitions

        // Error Status
        public const byte HID_UART_PARITY_ERROR = 0x01;
        public const byte HID_UART_OVERRUN_ERROR = 0x02;

        // Line Break Status
        public const byte HID_UART_LINE_BREAK_INACTIVE = 0x00;
        public const byte HID_UART_LINE_BREAK_ACTIVE = 0x01;

        // Data Bits
        public const byte HID_UART_FIVE_DATA_BITS = 0x00;
        public const byte HID_UART_SIX_DATA_BITS = 0x01;
        public const byte HID_UART_SEVEN_DATA_BITS = 0x02;
        public const byte HID_UART_EIGHT_DATA_BITS = 0x03;

        // Parity
        public const byte HID_UART_NO_PARITY = 0x00;
        public const byte HID_UART_ODD_PARITY = 0x01;
        public const byte HID_UART_EVEN_PARITY = 0x02;
        public const byte HID_UART_MARK_PARITY = 0x03;
        public const byte HID_UART_SPACE_PARITY = 0x04;

        // Stop Bits
        // Short = 1 stop bit
        // Long  = 1.5 stop bits (5 data bits)
        //       = 2 stop bits (6-8 data bits)
        public const byte HID_UART_SHORT_STOP_BIT = 0x00;
        public const byte HID_UART_LONG_STOP_BIT = 0x01;

        // Flow Control
        public const byte HID_UART_NO_FLOW_CONTROL = 0x00;
        public const byte HID_UART_RTS_CTS_FLOW_CONTROL = 0x01;

        // Read/Write Limits
        public const uint HID_UART_MIN_READ_SIZE = 1;
        public const uint HID_UART_MAX_READ_SIZE = 32768;
        public const uint HID_UART_MIN_WRITE_SIZE = 1;
        public const uint HID_UART_MAX_WRITE_SIZE = 4096;

        #endregion

        /////////////////////////////////////////////////////////////////////////////
        // Part Number Definitions
        /////////////////////////////////////////////////////////////////////////////

        #region Part Number Definitions

        // Part Numbers
        public const byte HID_UART_PART_CP2110 = 0x0A;

        #endregion

        /////////////////////////////////////////////////////////////////////////////
        // User Customization Definitions
        /////////////////////////////////////////////////////////////////////////////

        #region User Customization Definitions

        // User-Customizable Field Lock Bitmasks
        public const ushort HID_UART_LOCK_PRODUCT_STR_1 = 0x0001;
        public const ushort HID_UART_LOCK_PRODUCT_STR_2 = 0x0002;
        public const ushort HID_UART_LOCK_SERIAL_STR = 0x0004;
        public const ushort HID_UART_LOCK_PIN_CONFIG = 0x0008;
        public const ushort HID_UART_LOCK_VID = 0x0100;
        public const ushort HID_UART_LOCK_PID = 0x0200;
        public const ushort HID_UART_LOCK_POWER = 0x0400;
        public const ushort HID_UART_LOCK_POWER_MODE = 0x0800;
        public const ushort HID_UART_LOCK_RELEASE_VERSION = 0x1000;
        public const ushort HID_UART_LOCK_FLUSH_BUFFERS = 0x2000;
        public const ushort HID_UART_LOCK_MFG_STR_1 = 0x4000;
        public const ushort HID_UART_LOCK_MFG_STR_2 = 0x8000;

        // Field Lock Bit Values
        public const byte HID_UART_LOCK_UNLOCKED = 1;
        public const byte HID_UART_LOCK_LOCKED = 0;

        // Power Max Value (500 mA)
        public const byte HID_UART_BUS_POWER_MAX = 0xFA;

        // Power Modes
        public const byte HID_UART_BUS_POWER = 0x00;
        public const byte HID_UART_SELF_POWER = 0x01;

        // Flush Buffers Bitmasks
        public const byte HID_UART_FLUSH_TX_OPEN = 0x01;
        public const byte HID_UART_FLUSH_TX_CLOSE = 0x02;
        public const byte HID_UART_FLUSH_RX_OPEN = 0x04;
        public const byte HID_UART_FLUSH_RX_CLOSE = 0x08;

        // USB Config Bitmasks
        public const byte HID_UART_SET_VID = 0x01;
        public const byte HID_UART_SET_PID = 0x02;
        public const byte HID_UART_SET_POWER = 0x04;
        public const byte HID_UART_SET_POWER_MODE = 0x08;
        public const byte HID_UART_SET_RELEASE_VERSION = 0x10;
        public const byte HID_UART_SET_FLUSH_BUFFERS = 0x20;

        // USB Config Bit Values
        public const byte HID_UART_SET_IGNORE = 0;
        public const byte HID_UART_SET_PROGRAM = 1;

        // String Lengths
        public const byte HID_UART_MFG_STRLEN = 62;
        public const byte HID_UART_PRODUCT_STRLEN = 62;
        public const byte HID_UART_SERIAL_STRLEN = 30;

        #endregion
        
        /////////////////////////////////////////////////////////////////////////////
        // Pin Definitions
        /////////////////////////////////////////////////////////////////////////////

        #region Pin Definitions

        // Pin Config Mode Array Indices
        public const byte HID_UART_INDEX_GPIO_0_CLK = 0;
        public const byte HID_UART_INDEX_GPIO_1_RTS = 1;
        public const byte HID_UART_INDEX_GPIO_2_CTS = 2;
        public const byte HID_UART_INDEX_GPIO_3_RS485 = 3;
        public const byte HID_UART_INDEX_GPIO_4_TX_TOGGLE = 4;
        public const byte HID_UART_INDEX_GPIO_5_RX_TOGGLE = 5;
        public const byte HID_UART_INDEX_GPIO_6 = 6;
        public const byte HID_UART_INDEX_GPIO_7 = 7;
        public const byte HID_UART_INDEX_GPIO_8 = 8;
        public const byte HID_UART_INDEX_GPIO_9 = 9;
        public const byte HID_UART_INDEX_TX = 10;
        public const byte HID_UART_INDEX_SUSPEND = 11;
        public const byte HID_UART_INDEX_SUSPEND_BAR = 12;

        // Pin Config Modes
        public const byte HID_UART_GPIO_MODE_INPUT = 0x00;
        public const byte HID_UART_GPIO_MODE_OUTPUT_OD = 0x01;
        public const byte HID_UART_GPIO_MODE_OUTPUT_PP = 0x02;
        public const byte HID_UART_GPIO_MODE_FUNCTION = 0x03;

        // Pin Bitmasks
        public const ushort HID_UART_MASK_GPIO_0_CLK = 0x0001;
        public const ushort HID_UART_MASK_GPIO_1_RTS = 0x0002;
        public const ushort HID_UART_MASK_GPIO_2_CTS = 0x0004;
        public const ushort HID_UART_MASK_GPIO_3_RS485 = 0x0008;
        public const ushort HID_UART_MASK_TX = 0x0010;
        public const ushort HID_UART_MASK_RX = 0x0020;
        public const ushort HID_UART_MASK_GPIO_4_TX_TOGGLE = 0x0040;
        public const ushort HID_UART_MASK_GPIO_5_RX_TOGGLE = 0x0080;
        public const ushort HID_UART_MASK_SUSPEND_BAR = 0x0100;
        // NA	0x0200
        public const ushort HID_UART_MASK_GPIO_6 = 0x0400;
        public const ushort HID_UART_MASK_GPIO_7 = 0x0800;
        public const ushort HID_UART_MASK_GPIO_8 = 0x1000;
        public const ushort HID_UART_MASK_GPIO_9 = 0x2000;
        public const ushort HID_UART_MASK_SUSPEND = 0x4000;

        // Suspend Value Bit Values
        public const byte HID_UART_VALUE_SUSPEND_LO = 0;
        public const byte HID_UART_VALUE_SUSPEND_HI = 1;

        // Suspend Mode Bit Values
        public const byte HID_UART_MODE_SUSPEND_OD = 0;
        public const byte HID_UART_MODE_SUSPEND_PP = 1;

        // RS485 Active Levels
        public const byte HID_UART_MODE_RS485_ACTIVE_LO = 0x00;
        public const byte HID_UART_MODE_RS485_ACTIVE_HI = 0x01;

        #endregion

        /////////////////////////////////////////////////////////////////////////////
        // SLABHIDtoUART.dll Imported Functions
        /////////////////////////////////////////////////////////////////////////////

        #region SLABHIDtoUART.dll Imported Functions

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_GetNumDevices(ref uint numDevices, ushort vid, ushort pid);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_GetString(uint deviceNum, ushort vid, ushort pid, StringBuilder deviceString, uint options);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_GetOpenedString(IntPtr device, StringBuilder deviceString, uint options);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_GetIndexedString(uint deviceNum, ushort vid, ushort pid, uint stringIndex, StringBuilder deviceString);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_GetOpenedIndexedString(IntPtr device, uint stringIndex, StringBuilder deviceString);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_GetAttributes(uint deviceNum, ushort vid, ushort pid, ref ushort deviceVid, ref ushort devicePid, ref ushort deviceReleaseNumber);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_GetOpenedAttributes(IntPtr device, ref ushort deviceVid, ref ushort devicePid, ref ushort deviceReleaseNumber);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_Open(ref IntPtr device, uint deviceNum, ushort vid, ushort pid);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_Close(IntPtr device);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_IsOpened(IntPtr device, ref int opened);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_SetUartEnable(IntPtr device, int enable);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_GetUartEnable(IntPtr device, ref int enable);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_Read(IntPtr device, byte[] buffer, uint numBytesToRead, ref uint numBytesRead);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_Write(IntPtr device, byte[] buffer, uint numBytesToWrite, ref uint numBytesWritten);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_FlushBuffers(IntPtr device, int flushTransmit, int flushReceive);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_CancelIo(IntPtr device);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_SetTimeouts(IntPtr device, uint readTimeout, uint writeTimeout);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_GetTimeouts(IntPtr device, ref uint readTimeout, ref uint writeTimeout);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_GetUartStatus(IntPtr device, ref ushort transmitFifoSize, ref ushort receiveFifoSize, ref byte errorStatus, ref byte lineBreakStatus);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_SetUartConfig(IntPtr device, uint baudRate, byte dataBits, byte parity, byte stopBits, byte flowControl);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_GetUartConfig(IntPtr device, ref uint baudRate, ref byte dataBits, ref byte parity, ref byte stopBits, ref byte flowControl);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_StartBreak(IntPtr device, byte duration);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_StopBreak(IntPtr device);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_Reset(IntPtr device);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_ReadLatch(IntPtr device, ref ushort latchValue);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_WriteLatch(IntPtr device, ushort latchValue, ushort latchMask);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_GetPartNumber(IntPtr device, ref byte partNumber, ref byte version);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_GetLibraryVersion(ref byte major, ref byte minor, ref int release);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_GetHidLibraryVersion(ref byte major, ref byte minor, ref int release);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_GetHidGuid(ref Guid guid);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_SetLock(IntPtr device, ushort lockValue);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_GetLock(IntPtr device, ref ushort lockValue);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_SetUsbConfig(IntPtr device, ushort vid, ushort pid, byte power, byte powerMode, ushort releaseVersion, byte flushBuffers, byte mask);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_GetUsbConfig(IntPtr device, ref ushort vid, ref ushort pid, ref byte power, ref byte powerMode, ref ushort releaseVersion, ref byte flushBuffers);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_SetManufacturingString(IntPtr device, byte[] manufacturingString, byte strlen);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_GetManufacturingString(IntPtr device, StringBuilder manufacturingString, ref byte strlen);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_SetProductString(IntPtr device, byte[] productString, byte strlen);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_GetProductString(IntPtr device, StringBuilder productString, ref byte strlen);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_SetSerialString(IntPtr device, byte[] serialString, byte strlen);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_GetSerialString(IntPtr device, StringBuilder serialString, ref byte strlen);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_SetPinConfig(IntPtr device, byte[] pinConfig, int useSuspendValues, ushort suspendValue, ushort suspendMode, byte rs485Level, byte clkDiv);

        [DllImport("SLABHIDtoUART.dll")]
        public static extern int HidUart_GetPinConfig(IntPtr device, byte[] pinConfig, ref int useSuspendValues, ref ushort suspendValue, ref ushort suspendMode, ref byte rs485Level, ref byte clkDiv);

        #endregion
    }
}
