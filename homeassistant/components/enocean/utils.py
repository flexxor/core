"""Hold utility functions."""
from __future__ import annotations

import logging

from enoceanx.communicators import Communicator
from enoceanx.protocol.constants import RORG, PACKET

import homeassistant.components.enocean as ec  # import DATA_ENOCEAN, ENOCEAN_DONGLE, EnOceanDongle
from homeassistant.core import HomeAssistant

LOGGER = logging.getLogger(__name__)


def get_communicator_reference(hass: HomeAssistant) -> object | Communicator:
    """Get a reference to the communicator (dongle/pihat)."""
    enocean_data = hass.data.get(ec.DATA_ENOCEAN, {})
    dongle: ec.EnOceanDongle = enocean_data[ec.ENOCEAN_DONGLE]
    if not dongle:
        LOGGER.error("No EnOcean Dongle configured or available. No teach-in possible")
        return None
    communicator: Communicator = dongle.communicator
    return communicator


def int_to_list(int_value):
    """Convert integer to list of values."""
    result = []
    while int_value > 0:
        result.append(int_value % 256)
        int_value = int_value // 256
    result.reverse()
    return result


def hex_to_list(hex_value):
    """Convert hexadecimal value to a list of int values."""
    # it FFD97F81 has to be [FF, D9, 7F, 81] => [255, 217, 127, 129]
    result = []
    if hex_value is None:
        return result

    while hex_value > 0:
        result.append(hex_value % 0x100)
        hex_value = hex_value // 256
    result.reverse()
    return result


async def get_communicator_base_id(logger, communicator: Communicator):
    """Determine the communicators base id."""
    try:
        # store the originally set callback to restore it after
        # the end of the teach-in process.
        logger.debug("Storing existing callback function")
        # cb_to_restore = communicator.callback
        # the "correct" way would be to add a property to the communicator
        # to get access to the communicator. But, the enocean library seems abandoned
        cb_to_restore = communicator.callback

        communicator.callback = None

        # get the base id of the transceiver module
        base_id = communicator.base_id
        logger.debug("Base ID of EnOcean transceiver module: %s", str(base_id))

    finally:
        # restore the callback
        communicator.callback = cb_to_restore
    return base_id, cb_to_restore


async def determine_rorg_type(packet):
    """Determine the type of packet."""
    if packet is None:
        return None

    result = None
    if packet.data[0] == RORG.UTE:
        return RORG.UTE

    if packet.packet_type == PACKET.RADIO and packet.rorg == RORG.BS4:
        return RORG.BS4

    return result
