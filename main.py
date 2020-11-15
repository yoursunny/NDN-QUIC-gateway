"""
NDN-QUIC gateway.
"""

import argparse
import asyncio as aio
import io
import logging
import struct
import typing as T

from aioquic.asyncio import QuicConnectionProtocol, serve
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.connection import END_STATES
from aioquic.quic.events import (DatagramFrameReceived, QuicEvent,
                                 StreamDataReceived)


class ClientIndication:
    """
    WebTransport client indication parser.

    https://tools.ietf.org/html/draft-vvv-webtransport-quic-01#section-3.2
    """

    STREAM_ID = 2
    """QUIC stream ID that contains client indication."""

    ORIGIN = 0
    """Key of Origin field."""

    PATH = 1
    """Key of Path field."""

    def __init__(self):
        self._buffer = b''
        self._parsed: T.Union[None, T.Dict[int, bytes]] = None

    def append(self, data: bytes) -> None:
        """Append data from stream."""
        self._buffer += data

    def end(self) -> None:
        """Handle end of stream."""
        self._parsed = dict(ClientIndication._parse(io.BytesIO(self._buffer)))

    @staticmethod
    def _parse(bs: T.BinaryIO) -> T.Generator[T.Tuple[int, bytes], None, None]:
        while True:
            prefix = bs.read(4)
            if len(prefix) == 0:
                return
            if len(prefix) != 4:
                raise ValueError
            key, length = struct.unpack('!HH', prefix)
            value = bs.read(length)
            if len(value) != length:
                raise ValueError
            yield (key, value)

    def __getitem__(self, key: int) -> bytes:
        return self._parsed[key]

    def origin(self) -> str:
        """Return value of Origin field."""
        return self[ClientIndication.ORIGIN].decode()

    def path(self) -> str:
        """Return value of Path field."""
        return self[ClientIndication.PATH].decode()


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


class QuicProtocol(QuicConnectionProtocol):
    """Chromium QuicTransport protocol handler."""

    _logger = logging.getLogger("ndn-quic")
    _last_id = 0

    def __init__(self, addr: T.Tuple[str, int], *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        QuicProtocol._last_id += 1
        self._id = QuicProtocol._last_id
        self._client_indication = ClientIndication()
        self._udp = UdpConn(self._id, addr, self._udp_receive)
        aio.create_task(self._wait_disconnect())

    def quic_event_received(self, event: QuicEvent) -> None:
        try:
            if self._quic._close_pending or self._quic._state in END_STATES:
                return

            if isinstance(event, StreamDataReceived):
                self._handle_stream_data(event)
            elif isinstance(event, DatagramFrameReceived):
                self._handle_datagram_frame(event)

        except Exception as e:
            QuicProtocol._logger.warning(
                '[%d] QUIC event error: %s', self._id, str(e))
            self.close()

    def _handle_stream_data(self, event: StreamDataReceived) -> None:
        if event.stream_id != ClientIndication.STREAM_ID:
            return

        self._client_indication.append(event.data)
        if not event.end_stream:
            return
        self._client_indication.end()

        if self._client_indication.path() != "/ndn":
            raise ValueError
        QuicProtocol._logger.info(
            '[%d] connected from %s', self._id, self._client_indication.origin())

        aio.create_task(self._udp.open())

    def _handle_datagram_frame(self, event: DatagramFrameReceived) -> None:
        self._udp.send(event.data)

    def _udp_receive(self, pkt: bytes) -> None:
        self._quic.send_datagram_frame(pkt)
        self.transmit()

    async def _wait_disconnect(self):
        await self.wait_closed()
        QuicProtocol._logger.info('[%d] disconnected', self._id)
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
    parser.add_argument('--mtu', type=int, default=1500,
                        help='QUIC max datagram frame size')
    parser.add_argument('--router-addr', default='127.0.0.1',
                        help='router address', metavar='ADDR')
    parser.add_argument('--router-port', type=int,
                        default=6363, help='router port', metavar='PORT')
    opts = parser.parse_args()

    configuration = QuicConfiguration(
        alpn_protocols=['wq-vvv-01'],
        is_client=False,
        max_datagram_frame_size=opts.mtu,
    )
    configuration.load_cert_chain(opts.cert, opts.key)

    logging.info(
        'Starting QUIC server at [%s]:%d', opts.listen_addr, opts.listen_port)
    logging.info('NDN router is at [%s]:%d',
                 opts.router_addr, opts.router_port)

    def create_protocol(*args, **kwargs):
        return QuicProtocol((opts.router_addr, opts.router_port), *args, **kwargs)

    loop = aio.new_event_loop()
    loop.run_until_complete(
        serve(
            opts.listen_addr,
            opts.listen_port,
            configuration=configuration,
            create_protocol=create_protocol,
        ))
    loop.run_forever()