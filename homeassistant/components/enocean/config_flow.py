"""Config flows for the ENOcean integration."""
from __future__ import annotations

import asyncio
import logging
import queue
import time
from typing import Any

from enoceanx.communicators import Communicator
from enoceanx.protocol.constants import PACKET, RORG
from enoceanx.protocol.packet import Packet, UTETeachInPacket
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, OptionsFlow
from homeassistant.const import CONF_DEVICE
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from . import dongle, utils
from .const import DOMAIN, ERROR_INVALID_DONGLE_PATH, LOGGER


class EnoceanOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle the options flow."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry
        self.teachin_process_running = False
        self.Logger = logging.getLogger(__name__)
        self.teach_in_task = None

    async def _async_do_task(self, task):
        await task

        self.hass.async_create_task(
            self.hass.config_entries.flow.async_configure(flow_id=self.flow_id)
        )

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            items = user_input.items()
            self.Logger.debug("Items %s", items)
            # return self.async_create_entry(title="", data=user_input)

        # return await self.async_step_teach_in(user_input)
        # return self.async_show_form(
        #     step_id="init",
        #     data_schema=vol.Schema(
        #         {
        #             vol.Required(
        #                 "show_things",
        #                 default=self.config_entry.options.get("show_things"),
        #             ): bool
        #         }
        #     ),
        # )

        return self.async_show_menu(
            step_id="teach_in",
            menu_options={
                "teach_in_sth": "Teach-in new device",
            },
        )

    async def async_step_teach_in_sth(self, user_input):
        """Start the teach-in process."""

        base_id = None
        communicator = utils.get_communicator_reference(self.hass)

        # get the base id of the transceiver to fill out the default value
        if user_input is None:
            base_id, cb_to_restore = await self.get_communicator_base_id(communicator)
            # validate base id? TODO: correct format

        if base_id is None:
            self.async_abort("No base_id of communicator acquired")

        # the base_id could get determined -> the field can be prefilled with data
        data_schema = vol.Schema(
            {
                vol.Optional("teach_in_time", default=60): int,
                vol.Optional("comm_base_id", default=base_id): str,
            }
        )

        if user_input is not None:
            teach_in_time = user_input["teach_in_time"]

            try:
                self.teachin_process_running = True
                # clear the receive-queue to only listen to new teach-in packets
                with communicator.receive.mutex:
                    communicator.receive.queue.clear()

                teachin_start_time_seconds = time.time()

                base_id_from_service_call = None  # TODO: here, add base id to schema

                base_id_to_use: list[int]
                if base_id_from_service_call is None:
                    base_id_to_use = base_id
                else:
                    base_id_to_use = utils.hex_to_list(base_id_from_service_call)

                (
                    successful_teachin,
                    to_be_taught_device_id,
                ) = await self.react_to_teachin_requests(
                    communicator,
                    self.hass,
                    teach_in_time,
                    teachin_start_time_seconds,
                    base_id_to_use,
                )

            finally:
                # restore callback in any case
                self.Logger.debug("Restoring callback function")
                communicator.callback = cb_to_restore
                self.teachin_process_running = False

            message, teach_in_result_msg = await self.create_result_messages(
                successful_teachin, to_be_taught_device_id
            )
            self.Logger.debug(("Result: %s", teach_in_result_msg))
            # return self.async_create_entry(title="teach_in_time", data=teach_in_time)

        return self.async_show_form(step_id="teach_in_sth", data_schema=data_schema)

    # async def async_step_finish_teach_in(self, user_input=None):
    #     if not user_input:
    #         return self.async_show_form(step_id="finish")
    #     return self.async_create_entry(title="Some title", data={})

    async def get_communicator_base_id(self, communicator: Communicator):
        """Determine the communicators base id."""
        try:
            # store the originally set callback to restore it after
            # the end of the teach-in process.
            self.Logger.debug("Storing existing callback function")
            # cb_to_restore = communicator.callback
            # the "correct" way would be to add a property to the communicator
            # to get access to the communicator. But, the enocean library seems abandoned
            cb_to_restore = communicator.callback

            communicator.callback = None

            # get the base id of the transceiver module
            base_id = communicator.base_id
            self.Logger.debug("Base ID of EnOcean transceiver module: %s", str(base_id))

        finally:
            # restore the callback
            communicator.callback = cb_to_restore
        return base_id, cb_to_restore

    @staticmethod
    async def create_result_messages(self, successful_teachin, to_be_taught_device_id):
        """Create both messages for UI and logger."""
        if successful_teachin:
            teach_in_result_msg = "successful. Device ID: " + str(
                to_be_taught_device_id
            )

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
        # return self.async_create_entry(title="", data=user_input)

    @staticmethod
    async def is_bs4_teach_in_packet(packet):
        """Checker whether it's a 4BS packet."""
        return len(packet.data) > 3 and utils.get_bit(packet.data[4], 3) == 0

    @staticmethod
    async def determine_rorg_type(self, packet):
        """Determine the type of packet."""
        if packet is None:
            return None

        result = None
        if packet.data[0] == RORG.UTE:
            return RORG.UTE

        if packet.packet_type == PACKET.RADIO and packet.rorg == RORG.BS4:
            return RORG.BS4

        return result

    async def react_to_teachin_requests(
        self,
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

                rorg_type = await self.determine_rorg_type(packet)

                self.Logger.debug(str(packet))
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
                    self.Logger.debug("Received BS4 packet")
                    # get the third bit of the fourth byte and check for "0".
                    if await self.is_bs4_teach_in_packet(packet):
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
                            self.Logger.info("Sent teach-in response via communicator")
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
            self.Logger.info("Device ID of paired device: %s", to_be_taught_device_id)
        if not successful_teachin:
            self.Logger.info("Teach-In time is over")
        return successful_teachin, to_be_taught_device_id

    # async def async_step_abort(self):
    #     return self.async_abort(reason="Aborted by user")


class EnOceanFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the enOcean config flows."""

    VERSION = 1
    MANUAL_PATH_VALUE = "Custom path"

    def __init__(self):
        """Initialize the EnOcean config flow."""
        self.dongle_path = None
        self.discovery_info = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Create the options flow."""
        return EnoceanOptionsFlowHandler(config_entry)

    async def async_step_import(self, data=None):
        """Import a yaml configuration."""

        if not await self.validate_enocean_conf(data):
            LOGGER.warning(
                "Cannot import yaml configuration: %s is not a valid dongle path",
                data[CONF_DEVICE],
            )
            return self.async_abort(reason="invalid_dongle_path")

        return self.create_enocean_entry(data)

    async def async_step_user(self, user_input=None):
        """Handle an EnOcean config flow start."""

        # user_input is none when the form is called the for the first time

        if self._async_current_entries():
            # TODO here THINK: show menu to offer the option to teach-in a new device
            return self.async_abort(reason="single_instance_allowed")

        # no config entry was available, so try to detect a dongle
        return await self.async_step_detect()

    async def async_step_detect(self, user_input=None):
        """Propose a list of detected dongles."""
        errors = {}
        if user_input is not None:
            if user_input[CONF_DEVICE] == self.MANUAL_PATH_VALUE:
                return await self.async_step_manual(None)
            if await self.validate_enocean_conf(user_input):
                return self.create_enocean_entry(user_input)
            errors = {CONF_DEVICE: ERROR_INVALID_DONGLE_PATH}

        bridges = await self.hass.async_add_executor_job(dongle.detect)
        if len(bridges) == 0:
            return await self.async_step_manual(user_input)

        bridges.append(self.MANUAL_PATH_VALUE)
        return self.async_show_form(
            step_id="detect",
            data_schema=vol.Schema({vol.Required(CONF_DEVICE): vol.In(bridges)}),
            errors=errors,
        )

    async def async_step_manual(self, user_input=None):
        """Request manual USB dongle path."""
        default_value = None
        errors = {}
        if user_input is not None:
            if await self.validate_enocean_conf(user_input):
                return self.create_enocean_entry(user_input)
            default_value = user_input[CONF_DEVICE]
            errors = {CONF_DEVICE: ERROR_INVALID_DONGLE_PATH}

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {vol.Required(CONF_DEVICE, default=default_value): str}
            ),
            errors=errors,
        )

    async def validate_enocean_conf(self, user_input) -> bool:
        """Return True if the user_input contains a valid dongle path."""
        dongle_path = user_input[CONF_DEVICE]
        path_is_valid = await self.hass.async_add_executor_job(
            dongle.validate_path, dongle_path
        )
        return path_is_valid

    def create_enocean_entry(self, user_input):
        """Create an entry for the provided configuration."""
        return self.async_create_entry(title="EnOcean", data=user_input)
