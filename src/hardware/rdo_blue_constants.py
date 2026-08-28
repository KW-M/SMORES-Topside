"""Blue RDO Modbus register map, transcribed from
`documentation/RDO-Blue-Manual-Modbus-Interface.md`.

All addresses below are zero-based **Holding Register Address** values (the
doc's "Holding Register Address" column, i.e. Holding Register Number - 1),
matching what pymodbus's `address=` argument expects. Every value directly
confirmed by the vendor doc is transcribed as-is; anything inferred rather
than stated outright is tagged `# ASSUMED` with the reasoning, per
AGENTS.md's "keep assumed/unconfirmed register addresses easy to patch."
"""

from typing import Final

# --- Device information registers (doc: "Reading Device Information") ---

DEVICE_ID_REGISTER: Final[int] = 9000
"""uint16, 1 register. Expected value DEVICE_ID_RDO_BLUE (35)."""

DEVICE_ID_RDO_BLUE: Final[int] = 35

SERIAL_NUMBER_REGISTER: Final[int] = 9001
"""uint32, 2 registers."""

FIRMWARE_VERSION_REGISTER: Final[int] = 9006
"""uint16, 1 register. Value 100 means firmware version 1.00."""

# ASSUMED: the doc states 32-bit *float* parameter values use big-endian
# (high word first) register order, but doesn't restate word order for this
# uint32 register pair. We assume the same big-endian word order applies.
MULTI_REGISTER_WORD_ORDER_BIG_ENDIAN: Final[bool] = True

# --- Parameter block layout (doc: "Reading Parameters") ---
# Each parameter occupies a contiguous 7-register block starting at that
# parameter's "Holding Register Address" (below); these are offsets into
# that block, all directly stated in the doc's register-offset table.

PARAMETER_VALUE_OFFSET: Final[int] = 0
"""float32, 2 registers. The measured value."""

PARAMETER_DATA_QUALITY_OFFSET: Final[int] = 2
"""uint16, 1 register. See DATA_QUALITY_TEXT below."""

PARAMETER_UNITS_ID_OFFSET: Final[int] = 3
"""uint16, 1 register, R/W. See UNITS_TEXT below. Not written by this system."""

PARAMETER_ID_OFFSET: Final[int] = 4
"""uint16, 1 register. See PARAMETER_NAMES below."""

PARAMETER_SENTINEL_OFFSET: Final[int] = 5
"""float32, 2 registers, R/W. Value substituted by the instrument on error."""

PARAMETER_BLOCK_SIZE_REGISTERS: Final[int] = 7

# --- Parameter starting register addresses (doc: Appendix A) ---

TEMPERATURE_PARAMETER_REGISTER: Final[int] = 45
DO_CONCENTRATION_MG_L_PARAMETER_REGISTER: Final[int] = 37
DO_PERCENT_SATURATION_PARAMETER_REGISTER: Final[int] = 53
DO_PARTIAL_PRESSURE_TORR_PARAMETER_REGISTER: Final[int] = 61

PARAMETER_NAMES: Final[dict[int, str]] = {
    1: "Temperature",
    20: "DO Concentration",
    21: "DO Percent Saturation",
    30: "Oxygen Partial Pressure",
}
"""Parameter ID (register offset PARAMETER_ID_OFFSET value) -> name (Appendix A)."""

# --- Data Quality ID meanings (doc: register-offset table) ---

DATA_QUALITY_OK: Final[int] = 0
DATA_QUALITY_ERROR_READING_PARAMETER: Final[int] = 3
DATA_QUALITY_RDO_CAP_EXPIRED: Final[int] = 5

DATA_QUALITY_TEXT: Final[dict[int, str]] = {
    DATA_QUALITY_OK: "No errors or warnings",
    DATA_QUALITY_ERROR_READING_PARAMETER: "Error reading parameter",
    DATA_QUALITY_RDO_CAP_EXPIRED: "RDO Cap expired",
}
"""Known Data Quality IDs only; the doc notes other codes exist ("contact
technical support") without enumerating them. Callers should fall back to
a generic "Unknown data quality id {n}" message for codes not in this map."""

# --- Unit IDs (doc: Appendix B) ---

UNITS_TEXT: Final[dict[int, str]] = {
    1: "C",
    2: "F",
    26: "torr",
    117: "mg/L",
    118: "ug/L",
    177: "% sat",
}

# --- Session timing (doc: "Programming the PLC" step 3) ---

END_OF_SESSION_TIMEOUT_SECONDS: Final[float] = 5.0
"""Default: the instrument re-enters a low-power/idle state if no Modbus
command is received within this many seconds of the last one, per the doc.
ASSUMED (implementation detail, not a register address): whether/how this
system issues an explicit wake-up vs. relies on every read also acting as
the "any Modbus command" wake-up the doc describes is decided at the
hardware-layer implementation step (AGENTS.md step 9), not here.
"""
