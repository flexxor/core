"""Constants for the ENOcean integration."""
import logging

from homeassistant.const import Platform

DOMAIN = "enocean"
DATA_ENOCEAN = "enocean"
ENOCEAN_DONGLE = "dongle"

ERROR_INVALID_DONGLE_PATH = "invalid_dongle_path"

SIGNAL_RECEIVE_MESSAGE = "enocean.receive_message"
SIGNAL_SEND_MESSAGE = "enocean.send_message"

STATE_BASE_ID_TO_USE = "enocean.service_teachin_base_id_to_use"
EVENT_BASE_ID_TO_USE_SET = "enocean_baseidtousesetevent"

LOGGER = logging.getLogger(__package__)

PLATFORMS = [
    Platform.LIGHT,
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SWITCH,
]
