"""Config flows for the ENOcean integration."""
from __future__ import annotations

import logging
import time
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, OptionsFlow
from homeassistant.const import CONF_DEVICE
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from . import dongle, utils
from .const import DOMAIN, ERROR_INVALID_DONGLE_PATH, LOGGER
from .teachin import react_to_teachin_requests
from .utils import get_communicator_base_id


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
            base_id, cb_to_restore = await get_communicator_base_id(self.Logger, communicator)
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
                ) = await react_to_teachin_requests(self.Logger,
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
