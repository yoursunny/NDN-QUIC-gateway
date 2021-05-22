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

from h3conn import H3Connection
from udpconn import UdpConn


class H3Server(QuicConnectionProtocol):
    """Chromium WebTransport protocol handler."""

    _logger = logging.getLogger("ndn-quic-gateway.H3Server")
    _last_id = 0

    def __init__(self, addr: T.Tuple[str, int], mtu: int, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        H3Server._last_id += 1
        self._id = H3Server._last_id
        self._udp = UdpConn(self._id, addr, self._udp_receive)
        self._http = H3Connection(self._quic)
        self._datagram_flow = -1
        self._mtu = mtu
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
            H3Server._logger.warning(
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
            H3Server._logger.info(
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
        if len(pkt) > self._mtu:
            return
        buf = QBuffer(8 + len(pkt))
        buf.push_uint_var(self._datagram_flow)
        buf.push_bytes(pkt)
        self._quic.send_datagram_frame(buf.data)
        self.transmit()

    async def _wait_disconnect(self):
        await self.wait_closed()
        H3Server._logger.info('[%d] disconnected', self._id)
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
    parser.add_argument('--mtu', type=int, default=1200,
                        help='UDP max packet size')
    parser.add_argument('--router-addr', default='127.0.0.1',
                        help='router address', metavar='ADDR')
    parser.add_argument('--router-port', type=int,
                        default=6363, help='router port', metavar='PORT')
    opts = parser.parse_args()

    configuration = QuicConfiguration(
        alpn_protocols=h3c.H3_ALPN,
        is_client=False,
        max_datagram_frame_size=32+opts.mtu,
    )
    configuration.load_cert_chain(opts.cert, opts.key)

    logging.info(
        'Starting QUIC server at [%s]:%d', opts.listen_addr, opts.listen_port)
    logging.info('NDN router is at [%s]:%d',
                 opts.router_addr, opts.router_port)

    def create_protocol(*args, **kwargs):
        return H3Server((opts.router_addr, opts.router_port), opts.mtu, *args, **kwargs)

    loop = aio.new_event_loop()
    loop.run_until_complete(
        serve(
            opts.listen_addr,
            opts.listen_port,
            configuration=configuration,
            create_protocol=create_protocol,
        ))
    loop.run_forever()
