import asyncio as aio
import random
import socket
import time
import typing as T
from dataclasses import dataclass
from urllib.parse import urlparse

import aioquic.h3.connection as h3c
from aioquic.asyncio.client import connect
from aioquic.quic.configuration import QuicConfiguration
from flask import Flask, Response, abort, request
from ndn.encoding import FormalName, InterestParam, make_interest
from ndn.encoding.name import Component, Name
from ndn.encoding.ndn_format_0_3 import parse_data

from h3client import H3Client

app = Flask(__name__)

ORIGIN = "https://health.ndn-quic-gateway.invalid"
MTU = 1200
CONNECT_TIMEOUT = 2.0
INTEREST_TIMEOUT = 4.0
INTEREST_TIMEOUT_STEP = 0.05
INTEREST_PARAMS = InterestParam(can_be_prefix=True, hop_limit=64)


@dataclass
class ProbeRouterResult:
    ok: bool
    rtt: T.Optional[float] = None
    error: T.Optional[str] = None


class ProbeRequest:
    def __init__(self, router: str, transport: str, name: FormalName, suffix: bool):
        self._url = urlparse(router)
        self._transport = transport
        self._name = name.copy()
        if suffix:
            self._name.append(Component.from_sequence_num(
                random.randint(0, 0xFFFFFFFF)))

    async def probe(self) -> ProbeRouterResult:
        configuration = QuicConfiguration(
            alpn_protocols=h3c.H3_ALPN,
            is_client=True,
            server_name=self._url.hostname,
            max_datagram_frame_size=32+MTU,
        )

        def create_protocol(*args, **kwargs):
            return H3Client(self._url, ORIGIN, MTU, *args, **kwargs)

        try:
            ip, port = self._resolve_dns()
            async with connect(
                host=ip,
                port=port,
                configuration=configuration,
                create_protocol=create_protocol,
                wait_connected=False,
            ) as client:
                client = T.cast(H3Client, client)
                await aio.wait_for(client.wait_connected(), CONNECT_TIMEOUT)
                interest = bytes(make_interest(
                    self._name, interest_param=INTEREST_PARAMS))
                t0 = time.time()
                client.send(interest)
                for _ in range(int(INTEREST_TIMEOUT // INTEREST_TIMEOUT_STEP)):
                    await aio.sleep(INTEREST_TIMEOUT_STEP)
                    if self._process_received(client):
                        t1 = time.time()
                        return ProbeRouterResult(ok=True, rtt=(t1 - t0) * 1000)
                return ProbeRouterResult(ok=False, error="timeout")
        except Exception as err:
            return ProbeRouterResult(ok=False, error=str(err))

    def _resolve_dns(self) -> T.Tuple[str, int]:
        addrs = socket.getaddrinfo(
            host=self._url.hostname,
            port=self._url.port,
            family=(socket.AF_INET if self._transport ==
                    "http3-ipv4" else socket.AF_INET6),
            type=socket.SOCK_DGRAM,
        )
        if len(addrs) == 0:
            raise RuntimeError("DNS error")
        return addrs[0][4][:2]

    def _process_received(self, client: H3Client) -> bool:
        for pkt in client.received:
            try:
                name, _, _, _ = parse_data(pkt, with_tl=True)
                if Name.is_prefix(self._name, name):
                    return True
            except:
                pass
        client.received.clear()
        return False


@app.route("/probe", methods=['POST'])
async def handle_probe():
    transport = request.form["transport"]
    if transport not in ["http3-ipv4", "http3-ipv6"]:
        abort(Response("bad transport", 403))
    routers = request.form.getlist("router")
    name: FormalName = request.form.get("name", type=Name.from_str)
    suffix = request.form.get("suffix", type=int, default=0) == 1
    pRequests = [ProbeRequest(router, transport, name,
                              suffix) for router in routers]
    try:
        pResults = await aio.gather(*[r.probe() for r in pRequests])
        res = {router: pRes for router, pRes in zip(routers, pResults)}
        return res
    except Exception as err:
        abort(Response(str(err), 500))
