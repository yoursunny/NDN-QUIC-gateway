"""
NDN-QUIC gateway.
"""

import argparse
import asyncio as aio
import itertools
import logging
import typing as T

import aioquic.h3.connection as h3c
import aioquic.h3.events as h3e
from aioquic.asyncio import QuicConnectionProtocol, serve
from aioquic.buffer import Buffer as QBuffer
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.connection import END_STATES, QuicConnection
from aioquic.quic.events import DatagramFrameReceived, QuicEvent


class UdpConn:
    """Connection to NDN router over UDP."""

    _logger = logging.getLogger("ndn-udp")

    class Protocol(aio.DatagramProtocol):
        def __init__(self, on_receive: T.Callable[[bytes], None]):
            self._on_receive = on_receive

        def datagram_received(self, data: bytes, addr) -> None:
            self._on_receive(data)

    def __init__(self, _id: int, addr: T.Tuple[str, int],
                 on_receive: T.Callable[[bytes], None]) -> None:
        self._id = _id
        self._addr = addr
        self._on_receive = on_receive
        self._closed = False
        self._buffer: T.List[bytes] = []
        self._transport: T.Union[None, aio.DatagramTransport] = None

    async def open(self) -> None:
        """Open the connection."""
        try:
            transport, _ = await aio.get_running_loop().create_datagram_endpoint(
                lambda: UdpConn.Protocol(self._on_receive),
                remote_addr=self._addr)
            if self._closed:
                transport.abort()
                return

            for pkt in self._buffer:
                transport.sendto(pkt)
            self._buffer = []

            self._transport = transport
        except Exception as e:
            UdpConn._logger.warning(
                '[%d] UDP open error: %s', self._id, str(e))
            self.close()

    def close(self) -> None:
        """Close the connection."""
        if self._closed:
            return
        self._closed = True

        if self._transport is not None:
            self._transport.abort()
            self._transport = None

    def send(self, pkt: bytes) -> None:
        """Send a packet."""
        if self._transport is None:
            self._buffer.append(pkt)
            return

        self._transport.sendto(pkt)


class H3Connection(h3c.H3Connection):
    """HTTP/3 connection with SETTINGS frame override."""

    def __init__(self, quic: QuicConnection):
        super().__init__(quic)

    def _init_connection(self) -> None:
        self._local_control_stream_id = self._create_uni_stream(
            h3c.StreamType.CONTROL)
        self._quic.send_stream_data(
            self._local_control_stream_id,
            h3c.encode_frame(
                h3c.FrameType.SETTINGS,
                h3c.encode_settings(
                    {
                        0x2b603742: 1,  # SETTINGS_ENABLE_WEBTRANSPORT
                        0x276: 1,  # H3_DATAGRAM
                    }
                ),
            ),
        )
        self._local_encoder_stream_id = self._create_uni_stream(
            h3c.StreamType.QPACK_ENCODER)
        self._local_decoder_stream_id = self._create_uni_stream(
            h3c.StreamType.QPACK_DECODER)


class H3Protocol(QuicConnectionProtocol):
    """Chromium WebTransport protocol handler."""

    _logger = logging.getLogger("ndn-quic")
    _last_id = 0

    def __init__(self, addr: T.Tuple[str, int], mtu: int, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        H3Protocol._last_id += 1
        self._id = H3Protocol._last_id
        self._udp = UdpConn(self._id, addr, self._udp_receive)
        self._http = H3Connection(self._quic)
        self._datagram_flow = -1
        aio.create_task(self._wait_disconnect())

    def quic_event_received(self, event: QuicEvent) -> None:
        try:
            if self._quic._close_pending or self._quic._state in END_STATES:
                return

            for http_event in self._http.handle_event(event):
                self.http_event_received(http_event)

            if isinstance(event, DatagramFrameReceived):
                self._handle_datagram_frame(event)

        except Exception as e:
            H3Protocol._logger.warning(
                '[%d] QUIC event error: %s', self._id, str(e))
            self.close()

    def http_event_received(self, event: h3c.H3Event) -> None:
        if isinstance(event, h3e.HeadersReceived):
            self._handle_h3_headers(event)

    def _handle_h3_headers(self, event: h3e.HeadersReceived):
        headers: T.Dict[str, str] = dict()
        for key, value in event.headers:
            headers[str(key, encoding="utf8")] = str(value, encoding="utf8")

        if headers[":method"] == "CONNECT" and headers[":path"] == "/ndn":
            H3Protocol._logger.info(
                '[%d] connected from %s', self._id, headers["origin"])
        self._datagram_flow = int("".join(itertools.takewhile(
            str.isdigit, headers["datagram-flow-id"])))

        self._http.send_headers(
            stream_id=event.stream_id,
            headers=[
                (b":status", b"200")
            ])

        aio.create_task(self._udp.open())

    def _handle_datagram_frame(self, event: DatagramFrameReceived) -> None:
        buf = QBuffer(len(event.data), event.data)
        flow = buf.pull_uint_var()
        if flow != self._datagram_flow:
            return
        self._udp.send(buf.data_slice(buf.tell(), buf.capacity))

    def _udp_receive(self, pkt: bytes) -> None:
        buf = QBuffer(8 + len(pkt))
        buf.push_uint_var(self._datagram_flow)
        buf.push_bytes(pkt)
        self._quic.send_datagram_frame(buf.data)
        self.transmit()

    async def _wait_disconnect(self):
        await self.wait_closed()
        H3Protocol._logger.info('[%d] disconnected', self._id)
        self._udp.close()


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S')

    parser = argparse.ArgumentParser()
    parser.add_argument('--cert', required=True,
                        help='TLS certificate file', metavar='FILE')
    parser.add_argument('--key', required=True,
                        help='TLS key file', metavar='FILE')
    parser.add_argument('--listen-addr', default='127.0.0.1',
                        help='QUIC server address', metavar='ADDR')
    parser.add_argument('--listen-port', type=int, default=6367,
                        help='QUIC server port', metavar='PORT')
    parser.add_argument('--mtu', type=int, default=1366,
                        help='QUIC max datagram frame size')
    parser.add_argument('--router-addr', default='127.0.0.1',
                        help='router address', metavar='ADDR')
    parser.add_argument('--router-port', type=int,
                        default=6363, help='router port', metavar='PORT')
    opts = parser.parse_args()

    configuration = QuicConfiguration(
        alpn_protocols=h3c.H3_ALPN,
        is_client=False,
        max_datagram_frame_size=opts.mtu,
    )
    configuration.load_cert_chain(opts.cert, opts.key)

    logging.info(
        'Starting QUIC server at [%s]:%d', opts.listen_addr, opts.listen_port)
    logging.info('NDN router is at [%s]:%d',
                 opts.router_addr, opts.router_port)

    def create_protocol(*args, **kwargs):
        return H3Protocol((opts.router_addr, opts.router_port), opts.mtu, *args, **kwargs)

    loop = aio.new_event_loop()
    loop.run_until_complete(
        serve(
            opts.listen_addr,
            opts.listen_port,
            configuration=configuration,
            create_protocol=create_protocol,
        ))
    loop.run_forever()
