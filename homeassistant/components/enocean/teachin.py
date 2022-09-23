"""Support for Teach-In process."""
import queue
import time
from abc import ABC, abstractmethod
import logging

from enoceanx.communicators import Communicator
from enoceanx.protocol.constants import RORG
from enoceanx.protocol.packet import Packet, RadioPacket, UTETeachInPacket
from enoceanx.protocol.teachin import TeachInHelper
from enoceanx.utils import is_bs4_teach_in_packet

from components.enocean import utils
from components.enocean.utils import determine_rorg_type
from homeassistant.core import HomeAssistant


class TeachInHandler(ABC):
    """Interface for various teach-in requests."""

    def __init__(self):
        """Init the Handler."""
        self.base_id = None
        self.logger = logging.getLogger(__name__)

    def set_base_id(self, base_id):
        """Set the base id of the teach-in handler."""
        self.base_id = base_id

    @abstractmethod
    def handle_teach_in_request(
        self, hass: HomeAssistant, packet: Packet, communicator: Communicator
    ):
        """Abstract method for handling incoming teach-in requests."""


class UteTeachInHandler(TeachInHandler):
    """Implementation to handle UTE teach-in requests."""

    def handle_teach_in_request(
        self, hass: HomeAssistant, packet: RadioPacket, communicator: Communicator
    ):
        """Handle the UTE-type teach-in request."""
        self.logger.info(
            "New device learned! The ID is Hex: %s. Sender: %s",
            packet.sender_hex,
            str(packet.sender_int),
        )

        to_be_taught_device_id = packet.sender
        successful_teachin = True

        return successful_teachin, to_be_taught_device_id


class FourBsTeachInHandler(TeachInHandler):
    """Implementation to handle 4BS teach-in requests."""

    def handle_teach_in_request(
        self, hass: HomeAssistant, packet: Packet, communicator: Communicator
    ):
        """Handle the 4BS-type teach-in request."""

        teach_in_response_packet = TeachInHelper.create_bs4_teach_in_response(
            packet, communicator
        )

        # send the packet via the communicator
        successful_sent = communicator.send(teach_in_response_packet)
        to_be_taught_device_id = teach_in_response_packet.destination

        return successful_sent, to_be_taught_device_id


async def react_to_teachin_requests(logger,
                                    communicator,
                                    hass,
                                    teachin_for_seconds,
                                    teachin_start_time_seconds,
                                    base_id,
                                    ):
    """Listen only for teachin-telegrams until time is over or the teachin was successful.

    Loop to empty the receive-queue.
    """

    successful_teachin = False
    to_be_taught_device_id = None

    while time.time() < teachin_start_time_seconds + teachin_for_seconds:
        # handle packet --> learn device
        # how? reacting to signals from alternative callback? Currently, not.
        # getting the receive-queue? yes
        # One could exchange the callback handler during the teach-in, maybe
        # Currently, there is no callback handler (we set it to None), so there can be
        # packets in the receive-queue. Try to process them.
        try:
            # get the packets from the communicator and check whether they are teachin packets
            packet: Packet = communicator.receive.get(block=True, timeout=1)

            rorg_type = await determine_rorg_type(packet)

            logger.debug(str(packet))
            if isinstance(packet, UTETeachInPacket):
                # THINK: handler, maybe deactivate teach in before and handle it the "handler"
                from components.enocean.teachin import (
                    TeachInHandler,
                    UteTeachInHandler,
                )

                handler: TeachInHandler = UteTeachInHandler()
                (
                    successful_sent,
                    to_be_taught_device_id,
                ) = handler.handle_teach_in_request(hass, packet, communicator)
                return successful_sent, to_be_taught_device_id

            # if packet.packet_type == PACKET.RADIO_ERP1 and packet.rorg == RORG.BS4:
            if rorg_type == RORG.BS4:
                logger.debug("Received BS4 packet")
                # get the third bit of the fourth byte and check for "0".
                if await is_bs4_teach_in_packet(packet):
                    # we have a teach-in packet
                    # let's create a proper response
                    from components.enocean.teachin import FourBsTeachInHandler

                    handler: TeachInHandler = FourBsTeachInHandler()
                    handler.set_base_id(base_id)

                    (
                        successful_sent,
                        to_be_taught_device_id,
                    ) = handler.handle_teach_in_request(hass, packet, communicator)

                    if successful_sent:
                        # the package was put to the transmit queue
                        logger.info("Sent teach-in response via communicator")
                        successful_teachin = True
                        break
            else:
                # packet type not relevant to teach-in process
                # drop it. Re-injection into the queue doesn't make sense here. Eventually one
                # could save them all for later usage?
                continue
        except queue.Empty:
            continue
    if to_be_taught_device_id is not None:
        logger.info("Device ID of paired device: %s", to_be_taught_device_id)
    if not successful_teachin:
        logger.info("Teach-In time is over")
    return successful_teachin, to_be_taught_device_id


async def is_bs4_teach_in_packet(data):
    """Checker whether it's a 4BS packet."""
    return len(data) > 3 and utils.get_bit(data[4], 3) == 0
