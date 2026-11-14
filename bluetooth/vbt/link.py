"""The radio link between virtual controllers, with a model of what a real BR/EDR or LE
link does to a host's packets.

Classic Bluetooth (BR/EDR) retransmits lost baseband packets until they get through
(ARQ), in order: a host never sees a lost packet, it sees a late one. So loss here is
retransmission delay; a packet that can't get through within the flush timeout is
dropped (only when a flush timeout is set, as for HID interrupt data), and a link that
gets nothing through for the supervision timeout is lost (both ends disconnect, reason
0x08). LE is modeled the same way, per connection event.
"""
import asyncio
import dataclasses
import random
from typing import Optional

from bumble import core, hci
from bumble.link import LocalLink


@dataclasses.dataclass
class LinkModel:
    """Timing of one link direction, in milliseconds.

    base_ms + jitter_ms: the delay of a packet that gets through at the first try (the
    sender's scheduling, the slot it waits for: in sniff mode, up to the sniff interval).
    loss: the chance one transmission attempt fails; each retry costs retry_ms (the next
    slot pair, or the next sniff anchor). flush_timeout_ms: drop a packet not delivered by
    then (None: retransmit forever, the BR/EDR default). supervision_timeout_ms: lose the
    link after this long with nothing getting through. outage: (start_ms, end_ms) windows,
    from the link's creation, when nothing gets through (out of range, interference).
    """
    base_ms: float = 0.0
    jitter_ms: float = 0.0
    loss: float = 0.0
    retry_ms: float = 1.25
    flush_timeout_ms: Optional[float] = None
    supervision_timeout_ms: float = 20000.0
    outages: tuple = ()
    seed: Optional[int] = None

    @classmethod
    def ideal(cls):
        return cls()


class ModeledLink(LocalLink):
    """Bumble's LocalLink, with a LinkModel applied to ACL data (in order, per direction):
    what the hosts exchange. Link management (LMP: connecting, pairing, encryption) stays
    immediate, as Bumble's controllers expect it in order with their other link calls."""

    def __init__(self, model: Optional[LinkModel] = None):
        super().__init__()
        self.model = model or LinkModel.ideal()
        self.models = {}            # (sender address, receiver address) -> LinkModel
        self._rng = random.Random(self.model.seed)
        self._last_delivery = {}    # direction -> loop time of the last scheduled delivery
        self._t0 = None
        self.stats = {"sent": 0, "delivered": 0, "retries": 0, "flushed": 0, "lost_links": 0}

    def set_model(self, sender: hci.Address, receiver: hci.Address, model: LinkModel):
        """A model for one direction (defaults to the link's)."""
        self.models[(str(sender), str(receiver))] = model

    def _now_ms(self):
        loop = asyncio.get_running_loop()
        if self._t0 is None:
            self._t0 = loop.time()
        return (loop.time() - self._t0) * 1000.0

    def _in_outage(self, model, t_ms):
        return any(a <= t_ms < b for a, b in model.outages)

    def delay_for(self, sender, receiver):
        """(delay in seconds, or None when the packet is flushed or the link is lost)."""
        model = self.models.get((str(sender), str(receiver)), self.model)
        now = self._now_ms()
        delay = model.base_ms + (self._rng.random() * model.jitter_ms if model.jitter_ms else 0.0)
        # Retransmissions until an attempt gets through, outside any outage
        while True:
            t = now + delay
            if not self._in_outage(model, t) and not (model.loss and self._rng.random() < model.loss):
                break
            self.stats["retries"] += 1
            delay += model.retry_ms
            if model.flush_timeout_ms is not None and delay > model.flush_timeout_ms:
                self.stats["flushed"] += 1
                return None, False
            if delay > model.supervision_timeout_ms:
                return None, True
        # In order: never before the previous packet of this direction
        key = (str(sender), str(receiver))
        # (strictly later: asyncio's timers don't keep the order of equal times)
        at = max(now + delay, self._last_delivery.get(key, -1.0) + 0.001)
        self._last_delivery[key] = at
        self._last_at = at
        return (at - now) / 1000.0, False

    def _deliver_later(self, sender_address, receiver_address, fn, lose_link, completed=None):
        """completed(flushed): the sender's controller tells its host the packet is done."""
        self.stats["sent"] += 1
        delay, lost = self.delay_for(sender_address, receiver_address)
        loop = asyncio.get_running_loop()
        if lost:
            self.stats["lost_links"] += 1
            loop.call_soon(lose_link)
            return
        if delay is None:
            if completed:
                loop.call_soon(completed, True)
            return

        def deliver():
            self.stats["delivered"] += 1
            fn()
            if completed:
                completed(False)
        # At an absolute time: a relative delay would drift by however long scheduling takes
        loop.call_at(self._t0 + self._last_at / 1000.0, deliver)

    def _lose_link(self, a, b):
        """Both ends see the connection time out (supervision timeout)."""
        def go():
            for c, peer in ((self.find_classic_controller(a), b), (self.find_classic_controller(b), a)):
                if c is not None and peer in c.classic_connections:
                    c.on_classic_disconnected(peer, hci.HCI_ErrorCode.CONNECTION_TIMEOUT_ERROR)
        return go

    def send_acl_data(self, sender_controller, destination_address, transport, data):
        if transport == core.PhysicalTransport.LE:
            destination = self.find_le_controller(destination_address)
            # The address the receiver knows this link by: a peripheral advertising with its
            # public address (own_addr_type 0, as NimBLE on an ESP32 does) connects with it,
            # not with a random one
            source = sender_controller.random_address
            if destination is not None and source not in destination.le_connections and \
                    sender_controller.public_address in destination.le_connections:
                source = sender_controller.public_address
        elif transport == core.PhysicalTransport.BR_EDR:
            destination = self.find_classic_controller(destination_address)
            source = sender_controller.public_address
        else:
            raise ValueError("unsupported transport type")
        handle, count = (sender_controller.take_fragments(destination_address)
                         if hasattr(sender_controller, "take_fragments") else (None, 0))
        completed = ((lambda flushed: sender_controller.packets_completed(handle, count, flushed))
                     if handle is not None else None)
        if destination is None:
            return
        self._deliver_later(source, destination_address,
                            lambda: destination.on_link_acl_data(source, transport, data),
                            self._lose_link(source, destination_address), completed)
