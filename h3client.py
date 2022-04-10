import logging
import typing as T
from urllib.parse import ParseResult as URL

from aioquic.asyncio import QuicConnectionProtocol
from aioquic.h3.connection import H3Connection
from aioquic.h3.events import DatagramReceived, H3Event, HeadersReceived
from aioquic.quic.events import QuicEvent


class H3Client(QuicConnectionProtocol):
    _logger = logging.getLogger("ndn-quic-gateway.H3Client")
    _last_id = 0

    def __init__(self, url: URL, origin: str, mtu: int, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        H3Client._last_id += 1
        self._id = H3Client._last_id
        self._http = H3Connection(self._quic, enable_webtransport=True)
        self._url = url
        self._origin = origin
        self._mtu = mtu
        self._datagram_flow = 0
        self._webtr_connect_stream: T.Optional[int] = None
        self._webtr_connected = False
        self._send_queue: T.List[bytes] = []
        self.received: T.List[bytes] = []
        self._send_connect()

    def _send_connect(self):
        self._webtr_connect_stream = self._quic.get_next_available_stream_id()
        self._datagram_flow = self._webtr_connect_stream // 4
        headers = [
            (b":method", b"CONNECT"),
            (b":scheme", b"https"),
            (b":authority", self._url.netloc.encode()),
            (b":path", self._url.path.encode()),
            (b":protocol", b"webtransport"),
            (b"origin", self._origin.encode()),
        ]
        self._http.send_headers(
            stream_id=self._webtr_connect_stream, headers=headers)

    def quic_event_received(self, event: QuicEvent) -> None:
        try:
            for http_event in self._http.handle_event(event):
                self.http_event_received(http_event)

        except Exception as e:
            H3Client._logger.warning(
                '[%d] QUIC event error: %s', self._id, str(e))
            self.close()

    def http_event_received(self, event: H3Event) -> None:
        if isinstance(event, HeadersReceived):
            self._handle_h3_headers(event)
        elif isinstance(event, DatagramReceived):
            self._handle_h3_datagram(event)

    def _handle_h3_headers(self, event: HeadersReceived):
        if event.stream_id != self._webtr_connect_stream:
            return
        self._webtr_connected = True
        for pkt in self._send_queue:
            self.send(pkt)
        self._send_queue.clear()

    def _handle_h3_datagram(self, event: DatagramReceived) -> None:
        # if event.flow_id != self._datagram_flow:
        #     return
        self.received.append(event.data)

    def send(self, pkt: bytes) -> bool:
        if len(pkt) > self._mtu:
            return False
        if not self._webtr_connected:
            self._send_queue.append(pkt)
            return True
        self._http.send_datagram(self._datagram_flow, pkt)
        self.transmit()
        return True
