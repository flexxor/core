"""This shall be the representation of an EnOcean dongle."""
import glob
import logging
import time
from os.path import basename, normpath
from typing import List

from enocean.communicators import SerialCommunicator
from enocean.protocol.constants import PACKET, RORG
from enocean.protocol.packet import RadioPacket, Packet, UTETeachInPacket
import serial

from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect, dispatcher_send

from .const import SIGNAL_RECEIVE_MESSAGE, SIGNAL_SEND_MESSAGE, EVENT_BASE_ID_TO_USE_SET
from .teachin import TeachInHandler, UteTeachInHandler, FourBsTeachInHandler, is_bs4_teach_in_packet

_LOGGER = logging.getLogger(__name__)


class EnOceanDongle:
    """Representation of an EnOcean dongle.

    The dongle is responsible for receiving the ENOcean frames,
    creating devices if needed, and dispatching messages to platforms.
    """

    def __init__(self, hass: HomeAssistant, serial_path):
        """Initialize the EnOcean dongle."""

        self._base_id = None
        self._communicator = SerialCommunicator(
            port=serial_path, callback=self.callback
        )
        self.serial_path = serial_path
        self.identifier = basename(normpath(serial_path))
        self.hass = hass
        self.dispatcher_disconnect_handle = None
        self.is_teachin_service_running = False
        self._teachin_base_id_to_use: List[int] = None
        self.teach_in_enabled = False

        # listen for events when the id to use for the teach-in has been changed
        hass.bus.async_listen(EVENT_BASE_ID_TO_USE_SET, self.base_id_to_use_listener)

    def base_id_to_use_listener(self, event: Event):
        """Listen to base_id_to_use_events for teach_in."""
        self._teachin_base_id_to_use = event.data.get("base_id_to_use")
        _LOGGER.info("Base id to use: %s", str(self._teachin_base_id_to_use))

    async def async_setup(self):
        """Finish the setup of the bridge and supported platforms."""
        # THINK: shall we check if the port to the communicator is open?
        self._communicator.start()
        self.dispatcher_disconnect_handle = async_dispatcher_connect(
            self.hass, SIGNAL_SEND_MESSAGE, self._send_message_callback
        )

    def unload(self):
        """Disconnect callbacks established at init time."""
        if self.dispatcher_disconnect_handle:
            self.dispatcher_disconnect_handle()
            self.dispatcher_disconnect_handle = None

    def _send_message_callback(self, command):
        """Send a command through the EnOcean dongle."""
        self._communicator.send(command)

    def send_message(self, command):
        """Send a command through the EnOcean dongle (public)."""
        self._communicator.send(command)

    @property
    def communicator(self):
        """Get the communicator."""
        return self._communicator

    @communicator.setter
    def communicator(self, communicator: SerialCommunicator):
        self._communicator = communicator

    def callback(self, packet):
        """Handle EnOcean device's callback.

        This is the callback function called by python-enocean whenever there
        is an incoming packet.
        """

        if isinstance(packet, RadioPacket):
            _LOGGER.debug("Received radio packet: %s", packet)

            if self.teach_in_enabled:
                rorg_type = determine_rorg_type(packet)

                event_data: dict

                _LOGGER.info(str(packet))
                if isinstance(packet, UTETeachInPacket):
                    # THINK: handler, maybe deactivate teach in before and handle it the "handler"
                    handler: TeachInHandler = UteTeachInHandler()
                    (
                        successful_sent,
                        to_be_taught_device_id,
                    ) = handler.handle_teach_in_request(self.hass, packet, self._communicator)

                    event_data = {
                        "successful_sent": successful_sent,
                        "to_be_taught_device_id": to_be_taught_device_id
                    }
                    self.hass.bus.async_fire("enocean_event_name", event_data)

                if rorg_type == RORG.BS4 and is_bs4_teach_in_packet(packet):
                    _LOGGER.info("Received 4BS teach-in packet")
                    # get the third bit of the fourth byte and check for "0".
                    # if is_bs4_teach_in_packet(packet):
                    # we have a teach-in packet
                    # let's create a proper response
                    handler: TeachInHandler = FourBsTeachInHandler()
                    handler.set_base_id(self._teachin_base_id_to_use)

                    (
                        successful_sent,
                        to_be_taught_device_id,
                    ) = handler.handle_teach_in_request(self.hass, packet, self._communicator)

                    if successful_sent:
                        # the package was put to the transmit queue
                        _LOGGER.info("Sent teach-in response via communicator")
                        # successful_teachin = True

                    event_data = {
                        "successful_sent": successful_sent,
                        "to_be_taught_device_id": to_be_taught_device_id
                    }

                    self.hass.bus.async_fire("enocean_event_name", event_data)
                    # TODO: create notify message in handler

                else:
                    dispatcher_send(self.hass, SIGNAL_RECEIVE_MESSAGE, packet)

    def communicator_base_id(self):
        """Get the Base ID of the transceiver module.

        Stop the communicator thread to spawn one with no callback set so that one can receive
        the response to the query for the Base ID. After 10 seconds, stop this newly created
        thread and start another one, which has the callback-method set again.
        """
        if self._base_id is not None:
            return self._base_id

        if self.communicator.is_alive():
            self.communicator.stop()

        self.communicator = SerialCommunicator(port=self.serial_path, callback=None)
        self.communicator.start()

        # Send COMMON_COMMAND 0x08, CO_RD_IDBASE request to the module
        self.communicator.send(Packet(PACKET.COMMON_COMMAND, data=[0x08]))

        start_time = time.time()
        while time.time() < start_time + 10:
            if self._base_id is not None:
                break
            self._base_id = self._communicator.base_id

        # "restore" original callback
        if self.communicator.is_alive():
            self.communicator.stop()
            self.communicator = SerialCommunicator(port=self.serial_path, callback=self.callback)

        return self._base_id


def determine_rorg_type(packet):
    """Determine the type of packet."""
    if packet is None:
        return None

    result = None
    if packet.data[0] == RORG.UTE:
        return RORG.UTE

    if packet.packet_type == PACKET.RADIO_ERP1 and packet.rorg == RORG.BS4:
        return RORG.BS4

    return result


def detect():
    """Return a list of candidate paths for USB ENOcean dongles.

    This method is currently a bit simplistic, it may need to be
    improved to support more configurations and OS.
    """
    globs_to_test = ["/dev/tty*FTOA2PV*", "/dev/serial/by-id/*EnOcean*"]
    found_paths = []
    for current_glob in globs_to_test:
        found_paths.extend(glob.glob(current_glob))

    return found_paths


def validate_path(path: str):
    """Return True if the provided path points to a valid serial port, False otherwise."""
    try:
        # Creating the serial communicator will raise an exception
        # if it cannot connect
        SerialCommunicator(port=path)
        return True
    except serial.SerialException as exception:
        _LOGGER.warning("Dongle path %s is invalid: %s", path, str(exception))
        return False
