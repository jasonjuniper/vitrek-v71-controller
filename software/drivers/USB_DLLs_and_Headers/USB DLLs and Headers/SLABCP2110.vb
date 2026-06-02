'/////////////////////////////////////////////////////////////////////////////
'// SLABCP2110.vb
'// For SLABHIDtoUART.dll version 1.4
'// and Silicon Labs CP2110 HID to UART
'/////////////////////////////////////////////////////////////////////////////

'/////////////////////////////////////////////////////////////////////////////
'// Namespaces
'/////////////////////////////////////////////////////////////////////////////

Imports System
Imports System.Diagnostics
Imports System.Collections.Generic
Imports System.Text
Imports System.Runtime.InteropServices

'/////////////////////////////////////////////////////////////////////////////
'// SLABHIDtoUART.dll Module
'/////////////////////////////////////////////////////////////////////////////

Module SLAB_CP2110

    '/////////////////////////////////////////////////////////////////////////////
    '// Return Code Definitions
    '/////////////////////////////////////////////////////////////////////////////

    ' Return Codes
    Public Const HID_UART_SUCCESS As Byte = &H0
    Public Const HID_UART_DEVICE_NOT_FOUND As Byte = &H1
    Public Const HID_UART_INVALID_HANDLE As Byte = &H2
    Public Const HID_UART_INVALID_DEVICE_OBJECT As Byte = &H3
    Public Const HID_UART_INVALID_PARAMETER As Byte = &H4
    Public Const HID_UART_INVALID_REQUEST_LENGTH As Byte = &H5

    Public Const HID_UART_READ_ERROR As Byte = &H10
    Public Const HID_UART_WRITE_ERROR As Byte = &H11
    Public Const HID_UART_READ_TIMED_OUT As Byte = &H12
    Public Const HID_UART_WRITE_TIMED_OUT As Byte = &H13
    Public Const HID_UART_DEVICE_IO_FAILED As Byte = &H14
    Public Const HID_UART_DEVICE_ACCESS_ERROR As Byte = &H15
    Public Const HID_UART_DEVICE_NOT_SUPPORTED As Byte = &H16

    Public Const HID_UART_UNKNOWN_ERROR As Byte = &HFF

    '/////////////////////////////////////////////////////////////////////////////
    '// String Definitions
    '/////////////////////////////////////////////////////////////////////////////

    ' Product String Types
    Public Const HID_UART_GET_VID_STR As Byte = &H1
    Public Const HID_UART_GET_PID_STR As Byte = &H2
    Public Const HID_UART_GET_PATH_STR As Byte = &H3
    Public Const HID_UART_GET_SERIAL_STR As Byte = &H4
    Public Const HID_UART_GET_MANUFACTURER_STR As Byte = &H5
    Public Const HID_UART_GET_PRODUCT_STR As Byte = &H6

    ' String Lengths
    Public Const HID_UART_DEVICE_STRLEN As UInteger = 260

    '/////////////////////////////////////////////////////////////////////////////
    '// UART Definitions
    '/////////////////////////////////////////////////////////////////////////////

    ' Error Status
    Public Const HID_UART_PARITY_ERROR As Byte = &H1
    Public Const HID_UART_OVERRUN_ERROR As Byte = &H2

    ' Line Break Status
    Public Const HID_UART_LINE_BREAK_INACTIVE As Byte = &H0
    Public Const HID_UART_LINE_BREAK_ACTIVE As Byte = &H1

    ' Data Bits
    Public Const HID_UART_FIVE_DATA_BITS As Byte = &H0
    Public Const HID_UART_SIX_DATA_BITS As Byte = &H1
    Public Const HID_UART_SEVEN_DATA_BITS As Byte = &H2
    Public Const HID_UART_EIGHT_DATA_BITS As Byte = &H3

    ' Parity
    Public Const HID_UART_NO_PARITY As Byte = &H0
    Public Const HID_UART_ODD_PARITY As Byte = &H1
    Public Const HID_UART_EVEN_PARITY As Byte = &H2
    Public Const HID_UART_MARK_PARITY As Byte = &H3
    Public Const HID_UART_SPACE_PARITY As Byte = &H4

    ' Stop Bits
    ' Short = 1 stop bit
    ' Long  = 1.5 stop bits (5 data bits)
    '       = 2 stop bits (6-8 data bits)
    Public Const HID_UART_SHORT_STOP_BIT As Byte = &H0
    Public Const HID_UART_LONG_STOP_BIT As Byte = &H1

    ' Flow Control
    Public Const HID_UART_NO_FLOW_CONTROL As Byte = &H0
    Public Const HID_UART_RTS_CTS_FLOW_CONTROL As Byte = &H1

    ' Read/Write Limits
    Public Const HID_UART_MIN_READ_SIZE As UInteger = 1
    Public Const HID_UART_MAX_READ_SIZE As UInteger = 32768
    Public Const HID_UART_MIN_WRITE_SIZE As UInteger = 1
    Public Const HID_UART_MAX_WRITE_SIZE As UInteger = 4096

    '/////////////////////////////////////////////////////////////////////////////
    '// Part Number Definitions
    '/////////////////////////////////////////////////////////////////////////////

    ' Part Numbers
    Public Const HID_UART_PART_CP2110 As Byte = &HA

    '/////////////////////////////////////////////////////////////////////////////
    '// User Customization Definitions
    '/////////////////////////////////////////////////////////////////////////////

    ' User-Customizable Field Lock Bitmasks
    Public Const HID_UART_LOCK_PRODUCT_STR_1 As UShort = &H1
    Public Const HID_UART_LOCK_PRODUCT_STR_2 As UShort = &H2
    Public Const HID_UART_LOCK_SERIAL_STR As UShort = &H4
    Public Const HID_UART_LOCK_PIN_CONFIG As UShort = &H8
    Public Const HID_UART_LOCK_VID As UShort = &H100
    Public Const HID_UART_LOCK_PID As UShort = &H200
    Public Const HID_UART_LOCK_POWER As UShort = &H400
    Public Const HID_UART_LOCK_POWER_MODE As UShort = &H800
    Public Const HID_UART_LOCK_RELEASE_VERSION As UShort = &H1000
    Public Const HID_UART_LOCK_FLUSH_BUFFERS As UShort = &H2000
    Public Const HID_UART_LOCK_MFG_STR_1 As UShort = &H4000
    Public Const HID_UART_LOCK_MFG_STR_2 As UShort = &H8000

    ' Field Lock Bit Values
    Public Const HID_UART_LOCK_UNLOCKED As Byte = 1
    Public Const HID_UART_LOCK_LOCKED As Byte = 0

    ' Power Max Value (500 mA)
    Public Const HID_UART_BUS_POWER_MAX As Byte = &HFA

    ' Power Modes
    Public Const HID_UART_BUS_POWER As Byte = &H0
    Public Const HID_UART_SELF_POWER As Byte = &H1

    ' Flush Buffers Bitmasks
    Public Const HID_UART_FLUSH_TX_OPEN As Byte = &H1
    Public Const HID_UART_FLUSH_TX_CLOSE As Byte = &H2
    Public Const HID_UART_FLUSH_RX_OPEN As Byte = &H4
    Public Const HID_UART_FLUSH_RX_CLOSE As Byte = &H8

    ' USB Config Bitmasks
    Public Const HID_UART_SET_VID As Byte = &H1
    Public Const HID_UART_SET_PID As Byte = &H2
    Public Const HID_UART_SET_POWER As Byte = &H4
    Public Const HID_UART_SET_POWER_MODE As Byte = &H8
    Public Const HID_UART_SET_RELEASE_VERSION As Byte = &H10
    Public Const HID_UART_SET_FLUSH_BUFFERS As Byte = &H20

    ' USB Config Bit Values
    Public Const HID_UART_SET_IGNORE As Byte = 0
    Public Const HID_UART_SET_PROGRAM As Byte = 1

    ' String Lengths
    Public Const HID_UART_MFG_STRLEN As Byte = 62
    Public Const HID_UART_PRODUCT_STRLEN As Byte = 62
    Public Const HID_UART_SERIAL_STRLEN As Byte = 30

    '/////////////////////////////////////////////////////////////////////////////
    '// Pin Definitions
    '/////////////////////////////////////////////////////////////////////////////

    ' Pin Config Mode Array Indices
    Public Const HID_UART_INDEX_GPIO_0_CLK As Byte = 0
    Public Const HID_UART_INDEX_GPIO_1_RTS As Byte = 1
    Public Const HID_UART_INDEX_GPIO_2_CTS As Byte = 2
    Public Const HID_UART_INDEX_GPIO_3_RS485 As Byte = 3
    Public Const HID_UART_INDEX_GPIO_4_TX_TOGGLE As Byte = 4
    Public Const HID_UART_INDEX_GPIO_5_RX_TOGGLE As Byte = 5
    Public Const HID_UART_INDEX_GPIO_6 As Byte = 6
    Public Const HID_UART_INDEX_GPIO_7 As Byte = 7
    Public Const HID_UART_INDEX_GPIO_8 As Byte = 8
    Public Const HID_UART_INDEX_GPIO_9 As Byte = 9
    Public Const HID_UART_INDEX_TX As Byte = 10
    Public Const HID_UART_INDEX_SUSPEND As Byte = 11
    Public Const HID_UART_INDEX_SUSPEND_BAR As Byte = 12

    ' Pin Config Modes
    Public Const HID_UART_GPIO_MODE_INPUT As Byte = &H0
    Public Const HID_UART_GPIO_MODE_OUTPUT_OD As Byte = &H1
    Public Const HID_UART_GPIO_MODE_OUTPUT_PP As Byte = &H2
    Public Const HID_UART_GPIO_MODE_FUNCTION As Byte = &H3

    ' Pin Bitmasks
    Public Const HID_UART_MASK_GPIO_0_CLK As UShort = &H1
    Public Const HID_UART_MASK_GPIO_1_RTS As UShort = &H2
    Public Const HID_UART_MASK_GPIO_2_CTS As UShort = &H4
    Public Const HID_UART_MASK_GPIO_3_RS485 As UShort = &H8
    Public Const HID_UART_MASK_TX As UShort = &H10
    Public Const HID_UART_MASK_RX As UShort = &H20
    Public Const HID_UART_MASK_GPIO_4_TX_TOGGLE As UShort = &H40
    Public Const HID_UART_MASK_GPIO_5_RX_TOGGLE As UShort = &H80
    Public Const HID_UART_MASK_SUSPEND_BAR As UShort = &H100
    ' NA	&H0200
    Public Const HID_UART_MASK_GPIO_6 As UShort = &H400
    Public Const HID_UART_MASK_GPIO_7 As UShort = &H800
    Public Const HID_UART_MASK_GPIO_8 As UShort = &H1000
    Public Const HID_UART_MASK_GPIO_9 As UShort = &H2000
    Public Const HID_UART_MASK_SUSPEND As UShort = &H4000

    ' Suspend Value Bit Values
    Public Const HID_UART_VALUE_SUSPEND_LO As Byte = 0
    Public Const HID_UART_VALUE_SUSPEND_HI As Byte = 1

    ' Suspend Mode Bit Values
    Public Const HID_UART_MODE_SUSPEND_OD As Byte = 0
    Public Const HID_UART_MODE_SUSPEND_PP As Byte = 1

    ' RS485 Active Levels
    Public Const HID_UART_MODE_RS485_ACTIVE_LO As Byte = &H0
    Public Const HID_UART_MODE_RS485_ACTIVE_HI As Byte = &H1

    '/////////////////////////////////////////////////////////////////////////////
    '// SLABHIDtoUART.dll Imported Functions
    '/////////////////////////////////////////////////////////////////////////////

    Public Declare Function HidUart_GetNumDevices Lib "SLABHIDtoUART.dll" (ByRef numDevices As UInteger, ByVal vid As UShort, ByVal pid As UShort) As Integer
    Public Declare Function HidUart_GetString Lib "SLABHIDtoUART.dll" (ByVal deviceNum As UInteger, ByVal vid As UShort, ByVal pid As UShort, ByVal deviceString As StringBuilder, ByVal options As UInteger) As Integer
    Public Declare Function HidUart_GetOpenedString Lib "SLABHIDtoUART.dll" (ByVal device As IntPtr, ByVal deviceString As StringBuilder, ByVal options As UInteger) As Integer
    Public Declare Function HidUart_GetIndexedString Lib "SLABHIDtoUART.dll" (ByVal deviceNum As UInteger, ByVal vid As UShort, ByVal pid As UShort, ByVal stringIndex As UInteger, ByVal deviceString As StringBuilder) As Integer
    Public Declare Function HidUart_GetOpenedIndexedString Lib "SLABHIDtoUART.dll" (ByVal device As IntPtr, ByVal stringIndex As UInteger, ByVal deviceString As StringBuilder) As Integer
    Public Declare Function HidUart_GetAttributes Lib "SLABHIDtoUART.dll" (ByVal deviceNum As UInteger, ByVal vid As UShort, ByVal pid As UShort, ByRef deviceVid As UShort, ByRef devicePid As UShort, ByRef deviceReleaseNumber As UShort) As Integer
    Public Declare Function HidUart_GetOpenedAttributes Lib "SLABHIDtoUART.dll" (ByVal device As IntPtr, ByRef deviceVid As UShort, ByRef devicePid As UShort, ByRef deviceReleaseNumber As UShort) As Integer
    Public Declare Function HidUart_Open Lib "SLABHIDtoUART.dll" (ByRef device As IntPtr, ByVal deviceNum As UInteger, ByVal vid As UShort, ByVal pid As UShort) As Integer
    Public Declare Function HidUart_Close Lib "SLABHIDtoUART.dll" (ByVal device As IntPtr) As Integer
    Public Declare Function HidUart_IsOpened Lib "SLABHIDtoUART.dll" (ByVal device As IntPtr, ByRef opened As Integer) As Integer
    Public Declare Function HidUart_SetUartEnable Lib "SLABHIDtoUART.dll" (ByVal device As IntPtr, ByVal enable As Integer) As Integer
    Public Declare Function HidUart_GetUartEnable Lib "SLABHIDtoUART.dll" (ByVal device As IntPtr, ByRef enable As Integer) As Integer
    Public Declare Function HidUart_Read Lib "SLABHIDtoUART.dll" (ByVal device As IntPtr, ByVal buffer() As Byte, ByVal numBytesToRead As UInteger, ByRef numBytesRead As UInteger) As Integer
    Public Declare Function HidUart_Write Lib "SLABHIDtoUART.dll" (ByVal device As IntPtr, ByVal buffer() As Byte, ByVal numBytesToWrite As UInteger, ByRef numBytesWritten As UInteger) As Integer
    Public Declare Function HidUart_FlushBuffers Lib "SLABHIDtoUART.dll" (ByVal device As IntPtr, ByVal flushTransmit As Integer, ByVal flushReceive As Integer) As Integer
    Public Declare Function HidUart_CancelIo Lib "SLABHIDtoUART.dll" (ByVal device As IntPtr) As Integer
    Public Declare Function HidUart_SetTimeouts Lib "SLABHIDtoUART.dll" (ByVal device As IntPtr, ByVal readTimeout As UInteger, ByVal writeTimeout As UInteger) As Integer
    Public Declare Function HidUart_GetTimeouts Lib "SLABHIDtoUART.dll" (ByVal device As IntPtr, ByRef readTimeout As UInteger, ByRef writeTimeout As UInteger) As Integer
    Public Declare Function HidUart_GetUartStatus Lib "SLABHIDtoUART.dll" (ByVal device As IntPtr, ByRef transmitFifoSize As UShort, ByRef receiveFifoSize As UShort, ByRef errorStatus As Byte, ByRef lineBreakStatus As Byte) As Integer
    Public Declare Function HidUart_SetUartConfig Lib "SLABHIDtoUART.dll" (ByVal device As IntPtr, ByVal baudRate As UInteger, ByVal dataBits As Byte, ByVal parity As Byte, ByVal stopBits As Byte, ByVal flowControl As Byte) As Integer
    Public Declare Function HidUart_GetUartConfig Lib "SLABHIDtoUART.dll" (ByVal device As IntPtr, ByRef baudRate As UInteger, ByRef dataBits As Byte, ByRef parity As Byte, ByRef stopBits As Byte, ByRef flowControl As Byte) As Integer
    Public Declare Function HidUart_StartBreak Lib "SLABHIDtoUART.dll" (ByVal device As IntPtr, ByVal duration As Byte) As Integer
    Public Declare Function HidUart_StopBreak Lib "SLABHIDtoUART.dll" (ByVal device As IntPtr) As Integer
    Public Declare Function HidUart_Reset Lib "SLABHIDtoUART.dll" (ByVal device As IntPtr) As Integer
    Public Declare Function HidUart_ReadLatch Lib "SLABHIDtoUART.dll" (ByVal device As IntPtr, ByRef latchValue As UShort) As Integer
    Public Declare Function HidUart_WriteLatch Lib "SLABHIDtoUART.dll" (ByVal device As IntPtr, ByVal latchValue As UShort, ByVal latchMask As UShort) As Integer
    Public Declare Function HidUart_GetPartNumber Lib "SLABHIDtoUART.dll" (ByVal device As IntPtr, ByRef partNumber As Byte, ByRef version As Byte) As Integer
    Public Declare Function HidUart_GetLibraryVersion Lib "SLABHIDtoUART.dll" (ByRef major As Byte, ByRef minor As Byte, ByRef release As Integer) As Integer
    Public Declare Function HidUart_GetHidLibraryVersion Lib "SLABHIDtoUART.dll" (ByRef major As Byte, ByRef minor As Byte, ByRef release As Integer) As Integer
    public declare function HidUart_GetHidGuid lib "SLABHIDtoUART.dll" (ByRef guid as Guid) As Integer
    Public Declare Function HidUart_SetLock Lib "SLABHIDtoUART.dll" (ByVal device As IntPtr, ByVal lockValue As UShort) As Integer
    Public Declare Function HidUart_GetLock Lib "SLABHIDtoUART.dll" (ByVal device As IntPtr, ByRef lockValue As UShort) As Integer
    Public Declare Function HidUart_SetUsbConfig Lib "SLABHIDtoUART.dll" (ByVal device As IntPtr, ByVal vid As UShort, ByVal pid As UShort, ByVal power As Byte, ByVal powerMode As Byte, ByVal releaseVersion As UShort, ByVal flushBuffers As Byte, ByVal mask As Byte) As Integer
    Public Declare Function HidUart_GetUsbConfig Lib "SLABHIDtoUART.dll" (ByVal device As IntPtr, ByRef vid As UShort, ByRef pid As UShort, ByRef power As Byte, ByRef powerMode As Byte, ByRef releaseVersion As UShort, ByRef flushBuffers As Byte) As Integer
    Public Declare Function HidUart_SetManufacturingString Lib "SLABHIDtoUART.dll" (ByVal device As IntPtr, ByVal manufacturingString() As Byte, ByVal strlen As Byte) As Integer
    Public Declare Function HidUart_GetManufacturingString Lib "SLABHIDtoUART.dll" (ByVal device As IntPtr, ByVal manufacturingString As StringBuilder, ByRef strlen As Byte) As Integer
    Public Declare Function HidUart_SetProductString Lib "SLABHIDtoUART.dll" (ByVal device As IntPtr, ByVal productString() As Byte, ByVal strlen As Byte) As Integer
    Public Declare Function HidUart_GetProductString Lib "SLABHIDtoUART.dll" (ByVal device As IntPtr, ByVal productString As StringBuilder, ByRef strlen As Byte) As Integer
    Public Declare Function HidUart_SetSerialString Lib "SLABHIDtoUART.dll" (ByVal device As IntPtr, ByVal serialString() As Byte, ByVal strlen As Byte) As Integer
    Public Declare Function HidUart_GetSerialString Lib "SLABHIDtoUART.dll" (ByVal device As IntPtr, ByVal serialString As StringBuilder, ByRef strlen As Byte) As Integer
    Public Declare Function HidUart_SetPinConfig Lib "SLABHIDtoUART.dll" (ByVal device As IntPtr, ByVal pinConfig() As Byte, ByVal useSuspendValues As Integer, ByVal suspendValue As UShort, ByVal suspendMode As UShort, ByVal rs485Level As Byte, ByVal clkDiv As Byte) As Integer
    Public Declare Function HidUart_GetPinConfig Lib "SLABHIDtoUART.dll" (ByVal device As IntPtr, ByVal pinConfig() As Byte, ByRef useSuspendValues As Integer, ByRef suspendValue As UShort, ByRef suspendMode As UShort, ByRef rs485Level As Byte, ByRef clkDiv As Byte) As Integer

End Module
