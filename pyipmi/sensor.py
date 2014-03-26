# Copyright (c) 2014  Kontron Europe GmbH
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA

import math
import errors
import array
import time
from pyipmi.errors import DecodingError, CompletionCodeError, RetryError
from pyipmi.utils import check_completion_code, ByteBuffer
from pyipmi.msgs import create_request_by_name
from pyipmi.msgs import constants

import sdr


# THRESHOLD BASED STATES
EVENT_READING_TYPE_CODE_THRESHOLD = 0x01
# DMI-based "Usage States" STATES
EVENT_READING_TYPE_CODE_DISCRETE = 0x02
# DIGITAL/DISCRETE EVENT STATES
EVENT_READING_TYPE_CODE_STATE = 0x03
EVENT_READING_TYPE_CODE_PREDICTIVE_FAILURE = 0x04
EVENT_READING_TYPE_CODE_LIMIT = 0x05
EVENT_READING_TYPE_CODE_PERFORMANCE = 0x06

# Sensor Types
SENSOR_TYPE_TEMPERATURE = 0x01
SENSOR_TYPE_VOLTAGE = 0x02
SENSOR_TYPE_CURRENT = 0x03
SENSOR_TYPE_FAN = 0x04
SENSOR_TYPE_CHASSIS_INTRUSION = 0x05
SENSOR_TYPE_PLATFORM_SECURITY = 0x06
SENSOR_TYPE_PROCESSOR = 0x07
SENSOR_TYPE_POWER_SUPPLY = 0x08
SENSOR_TYPE_POWER_UNIT = 0x09
SENSOR_TYPE_COOLING_DEVICE = 0x0a
SENSOR_TYPE_OTHER_UNITS_BASED_SENSOR = 0x0b
SENSOR_TYPE_MEMORY = 0x0c
SENSOR_TYPE_DRIVE_SLOT = 0x0d
SENSOR_TYPE_POST_MEMORY_RESIZE = 0x0e
SENSOR_TYPE_SYSTEM_FIRMWARE_PROGRESS = 0x0f
SENSOR_TYPE_EVENT_LOGGING_DISABLED = 0x10
SENSOR_TYPE_WATCHDOG_1 = 0x11
SENSOR_TYPE_SYSTEM_EVENT = 0x12
SENSOR_TYPE_CRITICAL_INTERRUPT = 0x13
SENSOR_TYPE_BUTTON = 0x14
SENSOR_TYPE_MODULE_BOARD = 0x15
SENSOR_TYPE_MICROCONTROLLER_COPROCESSOR = 0x16
SENSOR_TYPE_ADD_IN_CARD = 0x17
SENSOR_TYPE_CHASSIS = 0x18
SENSOR_TYPE_CHIP_SET = 0x19
SENSOR_TYPE_OTHER_FRU = 0x1a
SENSOR_TYPE_CABLE_INTERCONNECT = 0x1b
SENSOR_TYPE_TERMINATOR = 0x1c
SENSOR_TYPE_SYSTEM_BOOT_INITIATED = 0x1d
SENSOR_TYPE_BOOT_ERROR = 0x1e
SENSOR_TYPE_OS_BOOT = 0x1f
SENSOR_TYPE_OS_CRITICAL_STOP = 0x20
SENSOR_TYPE_SLOT_CONNECTOR = 0x21
SENSOR_TYPE_SYSTEM_ACPI_POWER_STATE = 0x22
SENSOR_TYPE_WATCHDOG_2 = 0x23
SENSOR_TYPE_PLATFORM_ALERT = 0x24
SENSOR_TYPE_ENTITY_PRESENT = 0x25
SENSOR_TYPE_MONITOR_ASIC_IC = 0x26
SENSOR_TYPE_LAN = 0x27
SENSOR_TYPE_MANGEMENT_SUBSYSTEM_HEALTH = 0x28
SENSOR_TYPE_BATTERY = 0x29
SENSOR_TYPE_SESSION_AUDIT = 0x2a
SENSOR_TYPE_VERSION_CHANGE = 0x2b
SENSOR_TYPE_FRU_STATE = 0x2c
SENSOR_TYPE_FRU_HOT_SWAP = 0xf0
SENSOR_TYPE_IPMB_PHYSICAL_LINK = 0xf1
SENSOR_TYPE_MODULE_HOT_SWAP = 0xf2
SENSOR_TYPE_POWER_CHANNEL_NOTIFICATION = 0xf3
SENSOR_TYPE_TELCO_ALARM_INPUT = 0xf4

SENSOR_TYPE_OEM_KONTRON_FRU_INFORMATION_AGENT = 0xc5
SENSOR_TYPE_OEM_KONTRON_POST_VALUE = 0xc6
SENSOR_TYPE_OEM_KONTRON_FW_UPGRADE = 0xc7
SENSOR_TYPE_OEM_KONTRON_DIAGNOSTIC = 0xc9
SENSOR_TYPE_OEM_KONTRON_SYSTEM_FIRMWARE_UPGRADE = 0xca
SENSOR_TYPE_OEM_KONTRON_POWER_DENIED = 0xcd
SENSOR_TYPE_OEM_KONTRON_RESET = 0xcf

class Sensor:
    def reserve_device_sdr_repository(self):
        req = create_request_by_name('ReserveDeviceSdrRepository')
        rsp = self.send_message(req)
        check_completion_code(rsp.completion_code)
        return  rsp.reservation_id

    def _get_device_sdr_chunk(self, reservation_id, record_id, offset, length):
        req = create_request_by_name('GetDeviceSdr')
        req.reservation_id = reservation_id
        req.record_id = record_id
        req.offset = offset
        req.length = length
        retry = 5

        while True:
            retry -= 1
            if retry == 0:
                raise RetryError()
            rsp = self.send_message(req)
            if rsp.completion_code == 0:
                break
            elif rsp.completion_code == constants.CC_RES_CANCELED:
                req.reservation_id = self.reserve_device_sdr_repository()
                time.sleep(0.1)
                continue
            elif rsp.completion_code == constants.CC_TIMEOUT:
                time.sleep(0.1)
                continue
            elif rsp.completion_code == constants.CC_RESP_COULD_NOT_BE_PRV:
                time.sleep(0.1 * retry)
                continue
            else:
                check_completion_code(rsp.completion_code)

        return (rsp.next_record_id, rsp.record_data)

    def get_device_sdr(self, record_id, reservation_id=None):
        """Collects all data for the given SDR record ID and returns
        the decoded SDR object.

        `record_id` the Record ID.

        `reservation_id=None` can be set. if None the reservation ID will
        be determined.
        """
        if reservation_id is None:
            reservation_id = self.reserve_device_sdr_repository()


        (next_record_id, data) = self._get_device_sdr_chunk(reservation_id, record_id, 0, 5)

        header = ByteBuffer(data)
        record_id = header.pop_unsigned_int(2)
        record_version = header.pop_unsigned_int(1)
        record_type = header.pop_unsigned_int(1)
        record_payload_length = header.pop_unsigned_int(1)
        record_length = record_payload_length + 5
        record_data = ByteBuffer(data)

        offset = len(record_data)
        self.max_req_len = 20
        retry = 20

        # now get the other record data
        while True:
            retry -= 1
            if retry == 0:
                raise RetryError()

            length = self.max_req_len
            if (offset + length) > record_length:
                length = record_length - offset

            try:
                (next_record_id, data) = self._get_device_sdr_chunk(reservation_id, record_id, offset, length)
            except CompletionCodeError, e:
                if e.cc == constants.CC_CANT_RET_NUM_REQ_BYTES:
                    # reduce max lenght
                    self.max_req_len -= 4
                    if self.max_req_len <= 0:
                        retry = 0
                else:
                    Assert

            record_data.append_array(data[:])
            offset = len(record_data)
            if len(record_data) >= record_length:
                break

        return sdr.create_sdr(record_data, next_record_id)

    def device_sdr_entries(self):
        """A generator that returns the SDR list. Starting with ID=0x0000 and
        end when ID=0xffff is returned.
        """
        reservation_id = self.reserve_device_sdr_repository()
        record_id = 0

        while True:
            s = self.get_device_sdr(record_id, reservation_id)
            yield s
            if s.next_id == 0xffff:
                break
            record_id = s.next_id

    def get_device_sdr_list(self, reservation_id=None):
        """Returns the complete SDR list.
        """
        return list(self.device_sdr_entries())

    def rearm_sensor_events(self, sensor_number):
        """Rearm sensor events for the given sensor number.
        """
        req = create_request_by_name('RearmSensorEvents')
        req.sensor_number = sensor_number
        rsp = self.send_message(req)
        check_completion_code(rsp.completion_code)

    def get_sensor_reading(self, sensor_number, lun=0):
        """Returns the sensor reading at the assertion states for the given
        sensor number.

        `sensor_number`

        Returns a tuple with `raw reading`and `assertion states`.
        """
        req = create_request_by_name('GetSensorReading')
        req.sensor_number = sensor_number
        req.lun =  lun
        rsp = self.send_message(req)
        check_completion_code(rsp.completion_code)

        reading = rsp.sensor_reading
        if rsp.config.initial_update_in_progress:
            reading = None

        states = None
        if rsp.states1 is not None:
            states = rsp.states1
        if rsp.states2 is not None:
            states |= (rsp.states2 << 8)
        return (reading, states)

    def set_sensor_thresholds(self, sensor_number, lun=0, unr=None, ucr=None,
                unc=None, lnc=None, lcr=None, lnr=None):
        """Set the sensor thresholds that are not 'None'

        `sensor_number`
        `unr` for upper non-recoverable
        `ucr` for upper critical
        `unc` for upper non-critical
        `lnc` for lower non-critical
        `lcr` for lower critical
        `lnr` for lower non-recoverable
        """
        req = create_request_by_name('SetSensorThresholds')
        req.sensor_number = sensor_number
        req.lun = lun
        if unr is not None:
            req.set_mask.unr = 1
            req.threshold.unr = unr
        if ucr is not None:
            req.set_mask.ucr = 1
            req.threshold.ucr = ucr
        if unc is not None:
            req.set_mask.unc = 1
            req.threshold.unc = unc
        if lnc is not None:
            req.set_mask.lnc = 1
            req.threshold.lnc = lnc
        if lcr is not None:
            req.set_mask.lcr = 1
            req.threshold.lcr = lcr
        if lnr is not None:
            req.set_mask.lnr = 1
            req.threshold.lnr = lnr
        rsp = self.send_message(req)
        check_completion_code(rsp.completion_code)

    def get_sensor_thresholds(self, sensor_number, lun=0):
        req = create_request_by_name('GetSensorThresholds')
        req.sensor_number = sensor_number
        req.lun = lun
        rsp = self.send_message(req)
        check_completion_code(rsp.completion_code)
        thresholds = {}
        if rsp.readable_mask.unr:
            thresholds['unr'] = rsp.threshold.unr
        if rsp.readable_mask.ucr:
            thresholds['ucr'] = rsp.threshold.ucr
        if rsp.readable_mask.unc:
            thresholds['unc'] = rsp.threshold.unc
        if rsp.readable_mask.lnc:
            thresholds['lnc'] = rsp.threshold.lnc
        if rsp.readable_mask.lcr:
            thresholds['lcr'] = rsp.threshold.lcr
        if rsp.readable_mask.lnr:
            thresholds['lnr'] = rsp.threshold.lnr
        return thresholds

