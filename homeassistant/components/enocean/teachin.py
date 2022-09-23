"""Support for Teach-In process."""
from abc import ABC, abstractmethod
import logging

from enoceanx.communicators import Communicator
from enoceanx.protocol.packet import Packet, RadioPacket
from enoceanx.protocol.teachin import TeachInHelper

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
