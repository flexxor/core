"""EnOcean services."""
import logging
import time
from typing import Union, List

from enocean.communicators import Communicator
from enocean.protocol.constants import PACKET, RORG
import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall

from .const import DOMAIN, EVENT_BASE_ID_TO_USE_SET
from .utils import get_communicator_reference, hex_to_list

import homeassistant.components.enocean as ec


TEACH_IN_DEVICE = "teach_in_device"  # service name
SERVICE_CALL_ATTR_TEACH_IN_SECONDS = "teach_in_time"
SERVICE_CALL_ATTR_TEACH_IN_SECONDS_DEFAULT_VALUE_STR = "60"
SERVICE_CALL_ATTR_TEACH_IN_SECONDS_DEFAULT_VALUE = 60
SERVICE_CALL_ATTR_TEACH_IN_BASE_ID_TO_USE = "teach_in_base_id"
SERVICE_CALL_TEACH_IN_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional(SERVICE_CALL_ATTR_TEACH_IN_SECONDS): vol.Coerce(
                int
            ),  # teach in seconds
            vol.Optional(
                SERVICE_CALL_ATTR_TEACH_IN_BASE_ID_TO_USE
            ): vol.All(  # base id to use
                vol.Length(min=8, max=8)
            ),
        }
    )
)
SERVICE_TEACHIN_MAX_RUNTIME = 600
SERVICE_TEACHIN_STATE_VALUE_RUNNING = "RUNNING"
SERVICE_TEACHIN_STATE = "enocean.service_teachin_state"

GET_NEXT_FREE_BASE_ID = "get_next_free_base_id"  # service name
# SERVICE_CALL_ATTR_GNFBI_BASE_ID = "base_id"
# SERVICE_CALL_GNFBI_SCHEMA = vol.All(
#    vol.Schema(
#        {
#            vol.Required(SERVICE_CALL_ATTR_GNFBI_BASE_ID): vol.All(
#                vol.Length(min=8, max=8)
#            )
#        }
#    )
# )
SUPPORTED_SERVICES = (TEACH_IN_DEVICE, GET_NEXT_FREE_BASE_ID)

SERVICE_TO_SCHEMA = {
    TEACH_IN_DEVICE: SERVICE_CALL_TEACH_IN_SCHEMA,
}

_LOGGER = logging.getLogger(__name__)


def async_setup_services(hass: HomeAssistant) -> None:
    """Set up services for EnOcean integration."""

    services = {
        TEACH_IN_DEVICE: handle_teach_in,
        # GET_NEXT_FREE_BASE_ID: get_next_free_base_id,
    }

    def call_enocean_service(service_call: ServiceCall) -> None:
        """Call correct EnOcean service."""
        services[service_call.service](hass, service_call)
        _LOGGER.info("Service %s has been called.", str(service_call.service))

    # register the services
    for service in SUPPORTED_SERVICES:
        hass.services.async_register(
            DOMAIN, service, call_enocean_service, schema=SERVICE_TO_SCHEMA.get(service)
        )
        _LOGGER.info("Request to register service %s has been sent.", str(service))


def get_teach_in_seconds(service_call: ServiceCall) -> int:
    """Get the time (in seconds) for how long the teach-in process should run."""
    teachin_for_seconds_str = service_call.data.get(
        SERVICE_CALL_ATTR_TEACH_IN_SECONDS,
        SERVICE_CALL_ATTR_TEACH_IN_SECONDS_DEFAULT_VALUE_STR,
    )
    try:
        teachin_for_seconds = int(teachin_for_seconds_str)
        # ensure the value is lower than the maximum
        teachin_for_seconds = min(SERVICE_TEACHIN_MAX_RUNTIME, teachin_for_seconds)
    except ValueError:
        teachin_for_seconds = SERVICE_CALL_ATTR_TEACH_IN_SECONDS_DEFAULT_VALUE

    return teachin_for_seconds


def get_base_id_from_service_call(service_call: ServiceCall) -> Union[str, None]:
    """Get the Base ID to use when pairing during BS4 teach-in."""
    base_id_from_call = service_call.data.get(SERVICE_CALL_ATTR_TEACH_IN_BASE_ID_TO_USE)
    return base_id_from_call


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


def handle_teach_in(hass: HomeAssistant, service_call: ServiceCall) -> None:
    """Handle the teach-in request of a device."""

    enocean_data = hass.data.get(ec.DATA_ENOCEAN, {})
    dongle: ec.EnOceanDongle = enocean_data[ec.ENOCEAN_DONGLE]
    if not dongle:
        _LOGGER.error(
            "No EnOcean Dongle configured or available. No teach-in possible."
        )
        return

    if dongle.is_teachin_service_running:
        return

    # set the running state to prevent the service from running twice
    dongle.is_teachin_service_running = True
    dongle.teach_in_enabled = True

    communicator: Communicator = get_communicator_reference(hass)

    # get the base id of the transceiver module
    base_id = dongle.communicator_base_id()
    _LOGGER.info("Base ID of EnOcean transceiver module: %s", str(base_id))

    try:
        # get time to run of the teach-in process from the service call
        teachin_for_seconds = get_teach_in_seconds(service_call)

        teachin_start_time_seconds = time.time()

        base_id_from_service_call = get_base_id_from_service_call(service_call)

        base_id_to_use: list[int]
        if base_id_from_service_call is None:
            base_id_to_use = base_id
        else:
            base_id_to_use = hex_to_list(base_id_from_service_call)

        # fire event
        event_data = {"base_id_to_use": base_id_to_use}
        hass.bus.async_fire(EVENT_BASE_ID_TO_USE_SET, event_data)

        # TODO: listen for event?
        successful_teachin, to_be_taught_device_id = react_to_teachin_requests(
            communicator,
            hass,
            teachin_for_seconds,
            teachin_start_time_seconds,
            base_id_to_use,
        )

    finally:
        # deactivate teach-in processing
        dongle.is_teachin_service_running = False
        # dongle.teach_in_enabled = False

    message, teach_in_result_msg = create_result_messages(
        successful_teachin, to_be_taught_device_id
    )

    _LOGGER.info("Teach-in was %s", teach_in_result_msg)

    # leave the notification message in the web interface
    hass.services.call(
        "persistent_notification",
        "create",
        service_data={
            "message": message,
            "title": "Result of Teach-In service call",
        },
    )


def create_result_messages(successful_teachin, to_be_taught_device_id):
    """Create both messages for UI and logger."""
    if successful_teachin:
        teach_in_result_msg = "successful. Device ID: " + str(to_be_taught_device_id)

        # message for persistent notification (success case)
        message = (
            f"EnOcean Teach-In-process successful with Device: "
            f"{str(to_be_taught_device_id)}"
        )
    else:
        # message for persistent notification (failure case)
        teach_in_result_msg = "not successful."
        message = "EnOcean Teach-In not successful."
    return message, teach_in_result_msg


def react_to_teachin_requests(
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
        pass

    if to_be_taught_device_id is not None:
        _LOGGER.info("Device ID of paired device: %s", to_be_taught_device_id)
    if not successful_teachin:
        _LOGGER.info("Teach-In time is over.")

    # dongle.teach_in_enabled = False  # no reference here
    return successful_teachin, to_be_taught_device_id


def get_next_free_base_id(hass: HomeAssistant, service_call: ServiceCall):
    """Determine the next free base ID which can be used from the already used IDs."""
    # next_free_base_id = 0
    # used_base_ids_so_far: list[list[int]] = []  # THINK: get from config entries

    communicator: Communicator = get_communicator_reference(hass)

    # _LOGGER.debug("Storing existing callback function")
    # cb_to_restore = communicator._Communicator__callback
    # communicator___callback = communicator.__callback
    # store the originally set callback to restore it after
    # the end of the teach-in process.
    # communicator._Communicator__callback = None

    try:
        base_id = communicator.base_id
        _LOGGER.info("Base id to use: %s", base_id)

    finally:
        pass
        # communicator._Communicator__callback = cb_to_restore

    # entries: ConfigEntries = hass.config_entries. There should be no config entries
    # at that time for devices
    # because they are configured via configuration.yaml. Only the dongle has one config entry
    # enocean_data = hass.data.get(DATA_ENOCEAN, {})
    # config = hass.config

    # hass.config_entries.options
    # enocean_ = hass.data['components']['enocean']
    # entities = hass.data['entity_platform']['enocean'][0].entities  # 0 = switch
    # hass.data['entity_platform']['enocean'][0].entities
    # hass.data['entity_platform']['enocean'][0].entities['switch.nodon_switch']
    # keys = hass.data["entity_platform"]["enocean"][0].entities.keys()
    # entities: dict = hass.data["entity_platform"]["enocean"][0].entities
    # items = entities.items()
    # key: str
    # value: EnOceanSwitch
    # for key, value in items:
    #     # entities[key].base_id
    #     _LOGGER.info(
    #         "Dev Name: %s - Base ID: %s", entities[key].dev_name, str(value.base_id)
    #     )
    # THINK: flatten all the values which can have a base id. see above
    # get_next_free_sender_id(base_id, used_base_ids_so_far)

    # return next_free_base_id
