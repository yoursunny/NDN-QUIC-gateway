"""
NDN-QUIC gateway.
"""

import argparse
import asyncio as aio
import logging
import typing as T

from aioquic.asyncio import QuicConnectionProtocol, serve
from aioquic.h3.connection import H3_ALPN
from aioquic.h3.events import (DatagramReceived, H3Event, Headers,
                               HeadersReceived)
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import QuicEvent

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
        self._http = H3Connection(self._quic, enable_webtransport=True)
        self._mtu = mtu
        self._datagram_flow = -1
        aio.create_task(self._wait_disconnect())

    def quic_event_received(self, event: QuicEvent) -> None:
        try:
            for http_event in self._http.handle_event(event):
                self.http_event_received(http_event)

        except Exception as e:
            H3Server._logger.warning(
                '[%d] QUIC event error: %s', self._id, str(e))
            self.close()

    def http_event_received(self, event: H3Event) -> None:
        if isinstance(event, HeadersReceived):
            self._handle_h3_headers(event)
        elif isinstance(event, DatagramReceived):
            self._handle_h3_datagram(event)

    def _handle_h3_headers(self, event: HeadersReceived):
        headers: T.Dict[bytes, bytes] = dict()
        for key, value in event.headers:
            headers[key] = value

        if (headers[b":path"] != b"/ndn" or
            headers[b":method"] != b"CONNECT" or
                headers[b":protocol"] != b"webtransport"):
            self._send_response(event.stream_id, 404)
            return

        H3Server._logger.info(
            '[%d] connected from %s', self._id, str(headers[b"origin"], encoding="utf8"))
        aio.create_task(self._udp.open())

        self._send_response(event.stream_id, 200, [
            (b"sec-webtransport-http3-draft", b"draft02"),
        ])

    def _send_response(self, stream: int, status: int, headers: Headers = []) -> None:
        self._http.send_headers(
            stream_id=stream,
            headers=[(b":status", str(status).encode())] + headers,
            end_stream=status >= 300
        )
        self.transmit()

    def _handle_h3_datagram(self, event: DatagramReceived) -> None:
        self._datagram_flow = event.flow_id
        self._udp.send(event.data)

    def _udp_receive(self, pkt: bytes) -> None:
        if len(pkt) > self._mtu:
            return
        self._http.send_datagram(self._datagram_flow, pkt)
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
        alpn_protocols=H3_ALPN,
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
