import asyncio as aio
import logging
import typing as T


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
