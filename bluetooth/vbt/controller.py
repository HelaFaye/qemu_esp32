"""A virtual controller that behaves like a given chip's, for the host stack in the firmware:
Bumble's controller, plus what Bluedroid and NimBLE expect of an ESP32's.

- Configuration commands Bumble doesn't implement (page scan activity, link policy, event
  filters, ...) are accepted, as a real controller does: Bluedroid's startup stops on an
  error for some of them.
- Inquiry: classic controllers on the link with inquiry scan on answer with an Extended
  Inquiry Result (address, class of device, name), as a Switch finds a Pro Controller.
- Flow control: a packet from the host is "completed" (Number Of Completed Packets) when
  the link delivers or flushes it, not when the host hands it over, so the host's buffer
  credits run out the way they do on hardware; with the chip's buffer count and size.
- Packets to the host are cut to the chip's ACL packet length.
"""
import asyncio
import dataclasses
import logging
import struct
import time

from bumble import hci
from bumble.controller import Controller

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class ChipProfile:
    """What the controller reports and how big its buffers are.

    The ESP32 values must come from a capture on the real chip (HCI_Read_Local_Version,
    HCI_Read_Buffer_Size, the features) for each ESP-IDF version: until then the defaults
    below are Bumble's, with Espressif's company ID, and `measured` says so.
    """
    name: str = "esp32"
    manufacturer: int = 0x02E5                 # Espressif
    hci_version: int = 0x08                    # 4.2
    lmp_version: int = 0x08
    acl_data_packet_length: int = 1021
    total_num_acl_data_packets: int = 9
    inquiry_rssi: int = -50
    measured: bool = False


PROFILES = {"esp32": ChipProfile(), "generic": ChipProfile(name="generic", manufacturer=0xFFFF)}


# Commands a host sends to configure the controller; accepted with no effect
ACCEPTED = {
    "HCI_WRITE_PAGE_SCAN_ACTIVITY_COMMAND", "HCI_WRITE_INQUIRY_SCAN_ACTIVITY_COMMAND",
    "HCI_WRITE_INQUIRY_MODE_COMMAND", "HCI_WRITE_PAGE_TIMEOUT_COMMAND",
    "HCI_WRITE_DEFAULT_LINK_POLICY_SETTINGS_COMMAND", "HCI_WRITE_INQUIRY_SCAN_TYPE_COMMAND",
    "HCI_WRITE_PAGE_SCAN_TYPE_COMMAND", "HCI_SET_EVENT_FILTER_COMMAND", "HCI_WRITE_VOICE_SETTING_COMMAND",
    "HCI_WRITE_DEFAULT_ERRONEOUS_DATA_REPORTING_COMMAND", "HCI_WRITE_CONNECTION_ACCEPT_TIMEOUT_COMMAND",
    "HCI_WRITE_CURRENT_IAC_LAP_COMMAND", "HCI_WRITE_SECURE_CONNECTIONS_HOST_SUPPORT_COMMAND",
    "HCI_WRITE_AUTHENTICATION_ENABLE_COMMAND", "HCI_WRITE_HOLD_MODE_ACTIVITY_COMMAND",
    "HCI_SET_AFH_HOST_CHANNEL_CLASSIFICATION_COMMAND", "HCI_WRITE_AFH_CHANNEL_ASSESSMENT_MODE_COMMAND",
    "HCI_WRITE_PIN_TYPE_COMMAND", "HCI_WRITE_INQUIRY_RESPONSE_TRANSMIT_POWER_LEVEL_COMMAND",
}
# ... and those whose reply carries the connection handle
ACCEPTED_WITH_HANDLE = {
    "HCI_WRITE_LINK_POLICY_SETTINGS_COMMAND", "HCI_WRITE_LINK_SUPERVISION_TIMEOUT_COMMAND",
    "HCI_WRITE_AUTOMATIC_FLUSH_TIMEOUT_COMMAND", "HCI_SNIFF_SUBRATING_COMMAND",
    "HCI_LE_SET_DATA_LENGTH_COMMAND",
}


class ChipController(Controller):
    def __init__(self, name, profile=None, **kw):
        super().__init__(name, **kw)
        self.profile = profile or PROFILES["esp32"]
        p = self.profile
        self.manufacturer_name = p.manufacturer
        self.hci_version = p.hci_version
        self.lmp_version = p.lmp_version
        self.acl_data_packet_length = p.acl_data_packet_length
        self.total_num_acl_data_packets = p.total_num_acl_data_packets
        self._fragments = {}            # handle -> host fragments not yet part of a sent PDU
        self.unsupported = []           # Commands answered "unknown", for the logs
        self.inquiry_task = None
        self.trace = []                 # (time, sent by the controller?, H4 packet): see write_btsnoop

    # --- The HCI trace -----------------------------------------------------------
    def on_packet(self, packet):
        self.trace.append((time.time(), False, bytes(packet)))
        return super().on_packet(packet)

    def send_hci_packet(self, packet):
        self.trace.append((time.time(), True, bytes(packet)))
        return super().send_hci_packet(packet)

    def write_btsnoop(self, path):
        """The trace as a btsnoop file (Wireshark, `btmon -r`): H4 framing (type 1002)."""
        with open(path, "wb") as f:
            f.write(b"btsnoop\0" + struct.pack(">II", 1, 1002))
            for t, from_controller, pkt in self.trace:
                flags = (1 if from_controller else 0) | (2 if pkt[:1] in (b"\x01", b"\x04") else 0)
                ts = int(t * 1e6) + 0x00DCDDB30F2F8000        # Microseconds since year 0
                f.write(struct.pack(">IIIIq", len(pkt), len(pkt), flags, 0, ts) + pkt)

    def trace_text(self, last=200):
        """The last packets, one line each, for a test's output."""
        lines = []
        t0 = self.trace[0][0] if self.trace else 0
        for t, from_controller, pkt in self.trace[-last:]:
            lines.append("%8.3f %s %s" % (t - t0, "C>H" if from_controller else "H>C", pkt[:40].hex()))
        return "\n".join(lines)

    # --- Commands Bumble lacks -----------------------------------------------------
    def on_hci_command(self, command):
        name = command.name
        if name in ACCEPTED:
            return hci.HCI_StatusReturnParameters(hci.HCI_ErrorCode.SUCCESS)
        if name in ACCEPTED_WITH_HANDLE:
            handle = getattr(command, "connection_handle", 0)
            self.send_hci_packet(hci.HCI_Command_Complete_Event(
                num_hci_command_packets=1, command_opcode=command.op_code,
                return_parameters=hci.HCI_StatusAndConnectionHandleReturnParameters(
                    status=hci.HCI_ErrorCode.SUCCESS, connection_handle=handle)))
            return None
        self.unsupported.append(name)
        return super().on_hci_command(command)

    def on_hci_read_remote_version_information_command(self, command):
        """Bumble has no handler for it (an async command: none means no answer at all, and
        NimBLE waits 20 s for one when an LE link comes up). Status, then the peer's version."""
        connection = self.find_connection_by_handle(command.connection_handle)
        status = hci.HCI_ErrorCode.SUCCESS if connection else hci.HCI_ErrorCode.UNKNOWN_CONNECTION_IDENTIFIER_ERROR
        self.send_hci_packet(hci.HCI_Command_Status_Event(
            status=status, num_hci_command_packets=1, command_opcode=command.op_code))
        if connection is None:
            return None
        peer = None
        if self.link is not None:
            find = (self.link.find_le_controller if connection.transport == connection.transport.LE
                    else self.link.find_classic_controller)
            peer = find(connection.peer_address)
        profile = getattr(peer, "profile", self.profile)
        self.send_hci_packet(hci.HCI_Read_Remote_Version_Information_Complete_Event(
            status=hci.HCI_ErrorCode.SUCCESS, connection_handle=connection.handle,
            version=profile.lmp_version, manufacturer_name=profile.manufacturer,
            subversion=getattr(profile, "lmp_subversion", 0)))
        return None

    def on_hci_command_packet(self, command):
        if (isinstance(command, hci.HCI_AsyncCommand) and
                not hasattr(self, "on_" + command.name.lower()) and command.name not in ACCEPTED_WITH_HANDLE):
            # An async command Bumble doesn't know: a controller answers Command Status with
            # "unknown command" (Bumble would send nothing, and the host would wait)
            self.unsupported.append(command.name)
            self.send_hci_packet(hci.HCI_Command_Status_Event(
                status=hci.HCI_ErrorCode.UNKNOWN_HCI_COMMAND_ERROR, num_hci_command_packets=1,
                command_opcode=command.op_code))
            return
        if command.name in ACCEPTED_WITH_HANDLE:
            self.on_hci_command(command)             # Its own Command Complete
            return
        if command.name == "HCI_SET_CONNECTION_ENCRYPTION_COMMAND":
            self.on_hci_set_connection_encryption_command(command)
            return
        if command.name == "HCI_INQUIRY_COMMAND":
            self.on_hci_inquiry_command(command)
            return
        if command.name == "HCI_INQUIRY_CANCEL_COMMAND":
            if self.inquiry_task:
                self.inquiry_task.cancel()
                self.inquiry_task = None
            self.send_hci_packet(hci.HCI_Command_Complete_Event(
                num_hci_command_packets=1, command_opcode=command.op_code,
                return_parameters=hci.HCI_StatusReturnParameters(hci.HCI_ErrorCode.SUCCESS)))
            return
        super().on_hci_command_packet(command)

    # --- Classic encryption (after pairing) ------------------------------------------
    def on_hci_set_connection_encryption_command(self, command):
        connection = self.find_connection_by_handle(command.connection_handle)
        if connection is None:
            self._send_hci_command_status(hci.HCI_ErrorCode.UNKNOWN_CONNECTION_IDENTIFIER_ERROR, command.op_code)
            return None
        self._send_hci_command_status(hci.HCI_ErrorCode.SUCCESS, command.op_code)
        on = command.encryption_enable

        def changed():
            self.send_hci_packet(hci.HCI_Encryption_Change_Event(
                status=hci.HCI_ErrorCode.SUCCESS, connection_handle=connection.handle, encryption_enabled=on))
            peer = self.link.find_classic_controller(connection.peer_address)
            peer_conn = peer.classic_connections.get(self.public_address) if peer else None
            if peer_conn is not None:
                peer.send_hci_packet(hci.HCI_Encryption_Change_Event(
                    status=hci.HCI_ErrorCode.SUCCESS, connection_handle=peer_conn.handle, encryption_enabled=on))
        asyncio.get_running_loop().call_later(0.005, changed)
        return None

    # --- Inquiry -------------------------------------------------------------------
    def on_hci_inquiry_command(self, command):
        self._send_hci_command_status(hci.HCI_ErrorCode.SUCCESS, command.op_code)
        length_s = command.inquiry_length * 1.28
        limit = command.num_responses or 255

        async def inquire():
            found = 0
            await asyncio.sleep(0.01)
            for c in list(self.link.controllers):
                if c is self or not (getattr(c, "classic_scan_enable", 0) & 0x01):
                    continue
                name = (getattr(c, "local_name", "") or "").encode()[:240]
                eir = bytes([len(name) + 1, 0x09]) + name if name else b""
                self.send_hci_packet(hci.HCI_Extended_Inquiry_Result_Event(
                    num_responses=1, bd_addr=c.public_address, page_scan_repetition_mode=1,
                    reserved=0, class_of_device=getattr(c, "class_of_device", 0), clock_offset=0,
                    rssi=self.profile.inquiry_rssi, extended_inquiry_response=eir.ljust(240, b"\0")))
                found += 1
                if found >= limit:
                    break
            await asyncio.sleep(length_s)
            self.send_hci_packet(hci.HCI_Inquiry_Complete_Event(status=hci.HCI_ErrorCode.SUCCESS))
            self.inquiry_task = None
        self.inquiry_task = asyncio.get_running_loop().create_task(inquire())

    def on_hci_write_class_of_device_command(self, command):
        self.class_of_device = command.class_of_device
        return hci.HCI_StatusReturnParameters(hci.HCI_ErrorCode.SUCCESS)

    # --- Flow control: completed when the link delivers --------------------------
    def on_hci_acl_data_packet(self, packet):
        connection = self.find_connection_by_handle(packet.connection_handle)
        if connection is None:
            logger.warning("no connection for handle 0x%04X", packet.connection_handle)
            return
        self._fragments[connection.handle] = self._fragments.get(connection.handle, 0) + 1
        connection.assembler.feed_packet(packet)       # A whole PDU goes to the link

    def take_fragments(self, peer_address):
        """The host fragments that made the PDU now going to `peer_address`."""
        for c in self.classic_connections.values():
            if c.peer_address == peer_address:
                return c.handle, self._fragments.pop(c.handle, 1)
        for c in self.le_connections.values():
            if c.peer_address == peer_address:
                return c.handle, self._fragments.pop(c.handle, 1)
        return None, 0

    def packets_completed(self, handle, count, flushed=False):
        if handle is None or not count or self.find_connection_by_handle(handle) is None:
            return
        if flushed:
            self.send_hci_packet(hci.HCI_Flush_Occurred_Event(connection_handle=handle))
        self.send_hci_packet(hci.HCI_Number_Of_Completed_Packets_Event(
            connection_handles=[handle], num_completed_packets=[count]))

    # --- To the host: cut to the ACL packet length -------------------------------
    def on_link_acl_data(self, sender_address, transport, data):
        if transport == transport.LE:
            connection = self.le_connections.get(sender_address)
        else:
            connection = self.classic_connections.get(sender_address)
        if connection is None:
            logger.warning("no connection for %s", sender_address)
            return
        size = self.acl_data_packet_length
        for i in range(0, len(data), size):
            chunk = data[i:i + size]
            pb = 2 if i == 0 else 1                    # First automatically flushable, continuation
            self.send_hci_packet(hci.HCI_AclDataPacket(connection.handle, pb, 0, len(chunk), chunk))
