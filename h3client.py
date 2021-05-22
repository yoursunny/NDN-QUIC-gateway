import asyncio as aio
import logging
import typing as T
from urllib.parse import ParseResult as URL
from urllib.parse import urlparse

import aioquic.h3.connection as h3c
import aioquic.h3.events as h3e
from aioquic.asyncio import QuicConnectionProtocol
from aioquic.asyncio.client import connect
from aioquic.buffer import Buffer as QBuffer
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.connection import END_STATES
from aioquic.quic.events import DatagramFrameReceived, QuicEvent

from h3conn import H3Connection


class H3Client(QuicConnectionProtocol):
    _logger = logging.getLogger("ndn-quic-gateway.H3Client")
    _last_id = 0

    def __init__(self, url: URL, origin: str, mtu: int, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        H3Client._last_id += 1
        self._id = H3Client._last_id
        self._http = H3Connection(self._quic)
        self._url = url
        self._origin = origin
        self._mtu = mtu
        self._datagram_flow = 1
        self._webtr_connect_stream: T.Optional[int] = None
        self._webtr_connected = False
        self._send_queue: T.List[bytes] = []
        self.received: T.List[bytes] = []
        self._send_connect()

    def _send_connect(self):
        self._webtr_connect_stream = self._quic.get_next_available_stream_id()
        headers = [
            (b":method", b"CONNECT"),
            (b":scheme", b"https"),
            (b":authority", self._url.netloc.encode()),
            (b":path", self._url.path.encode()),
            (b":protocol", b"webtransport"),
            (b"origin", self._origin.encode()),
            (b"datagram-flow-id", str(self._datagram_flow).encode()),
        ]
        self._http.send_headers(
            stream_id=self._webtr_connect_stream, headers=headers)

    def quic_event_received(self, event: QuicEvent) -> None:
        try:
            if self._quic._close_pending or self._quic._state in END_STATES:
                return

            for http_event in self._http.handle_event(event):
                self.http_event_received(http_event)

            if isinstance(event, DatagramFrameReceived):
                self._handle_datagram_frame(event)

        except Exception as e:
            H3Client._logger.warning(
                '[%d] QUIC event error: %s', self._id, str(e))
            self.close()

    def http_event_received(self, event: h3c.H3Event) -> None:
        if isinstance(event, h3e.HeadersReceived):
            self._handle_h3_headers(event)

    def _handle_h3_headers(self, event: h3e.HeadersReceived):
        if event.stream_id != self._webtr_connect_stream:
            return
        self._webtr_connected = True
        for pkt in self._send_queue:
            self.send(pkt)
        self._send_queue.clear()

    def _handle_datagram_frame(self, event: DatagramFrameReceived) -> None:
        buf = QBuffer(len(event.data), event.data)
        flow = buf.pull_uint_var()
        if flow != self._datagram_flow:
            return
        pkt = buf.data_slice(buf.tell(), buf.capacity)
        self.received.append(pkt)

    def send(self, pkt: bytes) -> bool:
        if len(pkt) > self._mtu:
            return False
        if not self._webtr_connected:
            self._send_queue.append(pkt)
            return True
        buf = QBuffer(8 + len(pkt))
        buf.push_uint_var(self._datagram_flow)
        buf.push_bytes(pkt)
        self._quic.send_datagram_frame(buf.data)
        self.transmit()
        return True


async def run():
    url = urlparse("https://dal.quic.g.ndn.today:6367/ndn")
    origin = "https://ndn-fch.yoursunny.dev"
    mtu = 1200
    configuration = QuicConfiguration(
        alpn_protocols=h3c.H3_ALPN,
        is_client=True,
        max_datagram_frame_size=32+mtu,
    )

    def create_protocol(*args, **kwargs):
        return H3Client(url, origin, mtu, *args, **kwargs)

    async with connect(
        host=url.hostname,
        port=url.port,
        configuration=configuration,
        create_protocol=create_protocol,
    ) as client:
        client = T.cast(H3Client, client)
        await aio.sleep(1)
        client.send(bytes.fromhex("050F070508036E646E21000A04A0A1A2A3"))
        await aio.sleep(1)
