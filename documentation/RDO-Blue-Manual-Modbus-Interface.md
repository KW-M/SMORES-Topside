# Modbus PLC Interface

## Overview

The Modbus PLC Interface is a simplified method of communicating with the RDO Blue using the Modbus protocol. For information about the specific Modbus registers and Unit IDs for your RDO Blue, see

Appendices A and B. The RDO Blue conforms to the Modbus standard. For more information about Modbus communication, see [www.modbus.org](http://www.modbus.org/).

## Setting Up Instrument

1. Connect power, and wire the instrument.
2. The setup below is using the instrument's factory default settings. Use VuSitu to reset the instrument to factory defaults if they have been changed. Take note of any changes in default units setup.

## Programming the PLC

1. Set up the serial communication to match the instrument communication settings. Communication settings can be changed with the VuSitu mobile app. The default communication settings are:

| Mode | Start Bit | Baud Rate | Data Bits | Parity | Stop Bit |
| ---- | --------- | --------- | --------- | ------ | -------- |
| RTU  | 1         | 19200     | 8         | Even   | 1        |

1. Set the device address match the instrument address. The default device address is 1.
2. Set the PLC to wake-up the device by sending a carriage return (0x0D) or any Modbus command.
   1. Allow one second before sending a second command. The instrument needs this time to wake up.
   2. After the wake-up command, the next reading must be taken before the end of session timeout. If the reading interval exceeds the end of session timeout, send a new wake-up command before requesting a new reading. The default end of session timeout is 5 seconds, and may be longer if the instrument has been connected to VuSitu.
3. Select the register to read on the PLC using the information in the following sections.
   1. Some PLC devices use the register number directly in programming statements, others use register addresses, which are one less than the register number. Refer to PLC manufacturer instructions to determine which programming style to use.
   2. Each register is a holding register. Some PLCs require you to add 40000 to the register number or address. For example: 5451 would be 45451.
4. Set the type of register to: 32-bit float
   1. If asked by the PLC this is 2 registers
5. Set the byte order to: Big Endian (MSB)
   1. This should be the default and may not be configurable on all PLCs

**_Reading Device Information_**

Use the following registers to read general information about the instrument.

| **Holding Register Number** | **Holding Register Address** | **Size (Registers)** | **Data Type** | **Description**               |
| --------------------------- | ---------------------------- | -------------------- | ------------- | ----------------------------- |
| 9001                        | 9000                         | 1                    | uint16        | Device Id: 35 = RDO Blue      |
| 9002                        | 9001                         | 2                    | uint32        | Serial Number                 |
| 9007                        | 9006                         | 1                    | uint16        | Firmware version (100 = 1.00) |

# Reading Parameters

Each parameter contains a block of 7 registers as shown in the table below. To read measurements for a specific parameter, look up the starting register for that parameter from the list of Parameter Numbers and Locations in Appendix A. Once you have the starting register, add the number of offset registers for additional information about the reading.

| **Register Offset** | **Size (Registers)** | **Mode**<br><br>**(R/W)** | **Data Type** | **Description**                                                                                                                                                                         |
| ------------------- | -------------------- | ------------------------- | ------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 0                   | 2                    | R                         | float         | The measured value from sensor                                                                                                                                                          |
| 2                   | 1                    | R                         | uint16        | Data Quality ID:<br><br>0 = No errors or warnings<br><br>3 = Error reading parameter<br><br>5 = RDO Cap expired<br><br>For additional errors or information, contact technical support. |
| 3                   | 1                    | R/W                       | uint16        | Units ID for this parameter. See: Appendix B.                                                                                                                                           |
| 4                   | 1                    | R                         | uint16        | Parameter ID for this parameter. See: Appendix A.                                                                                                                                       |
| 5                   | 2                    | R/W                       | float         | Off line sentinel value: The value that's returned on error or if the parameter isn't available. The default sentinel is 0.0                                                            |

For example, you can apply this information to collect a reading for DO Concentration.

From the list in Appendix A, you can find that the starting register for DO Concentration is 0038. A reading from register number 0038 (register address 0037) will return the measured value of DO Concentration.

Some PLC devices use the register number directly in programming statements, others use register addresses. Refer to PLC manufacturer instructions to determine which programming style to use.

You can use the register offsets listed in the table above to collect additional information about the reading. Adding the register offset of 2 to the starting register, you can find that register number 0040 (register address 0039) will return the Data Quality ID for the most recent DO Concentration measurement. Likewise, register number 0041 (register address 0040) will return the Units ID, which can be interpreted from Appendix B. Register number 0042 (register address 0041) will return the Parameter ID, which can be interpreted from Appendix A. Register number 0043 (register address 0042) will return the sentinel value.

The Units ID and Sentinel Value are writeable registers. Measurements can be changed to other units using the Units ID as shown in Appendix B. For example, if register number 0041 (DO Concentration Units ID) returns 117, DO Concentration is configured to report in mg/L. Looking at Appendix B, you can find that μg/L is also a valid unit which can be set by writing Units ID 118 to register number 0041.

**_Appendix A: Parameter Numbers and Locations_**

| **ID** | **Parameter Name**      | **Holding Register**<br><br>**Number** | **Holding Register Address** | **Default Units**  |
| ------ | ----------------------- | -------------------------------------- | ---------------------------- | ------------------ |
| 1      | Temperature             | 0046                                   | 0045                         | 1 = °C             |
| 20     | DO Concentration        | 0038                                   | 0037                         | 117 = mg/L         |
| 21     | DO Percent Saturation   | 0054                                   | 0053                         | 177 = % Saturation |
| 30     | Oxygen Partial Pressure | 0062                                   | 0061                         | 26 = torr          |

**_Appendix B: Unit IDs_**

| **ID**  | **Abbreviation** | **Units**                                  |
| ------- | ---------------- | ------------------------------------------ |
| **1**   | **C**            | **Celsius**                                |
| **2**   | **F**            | **Fahrenheit**                             |
| **26**  | **torr**         | **Torr**                                   |
| **117** | **mg/L**         | **Milligrams per liter**                   |
| **118** | **μg/L**         | **Micrograms per liter**                   |
| **177** | **% sat**        | **Percent saturation of dissolved oxygen** |