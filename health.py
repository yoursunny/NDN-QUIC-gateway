import asyncio as aio
import socket
import time
import typing as T
from dataclasses import dataclass
from urllib.parse import urlparse

import aioquic.h3.connection as h3c
from aioquic.asyncio.client import connect
from aioquic.quic.configuration import QuicConfiguration
from dataclasses_json import dataclass_json
from flask import Flask, Response, abort, request
from ndn.encoding import InterestParam, make_interest
from ndn.encoding.name import Name
from ndn.encoding.ndn_format_0_3 import parse_data

from h3client import H3Client

app = Flask(__name__)

ORIGIN = "https://health.ndn-quic-gateway.invalid"
MTU = 1200
CONNECT_TIMEOUT = 2.0
INTEREST_TIMEOUT = 4.0
INTEREST_TIMEOUT_STEP = 0.05
INTEREST_PARAMS = InterestParam(must_be_fresh=True, hop_limit=64)


@dataclass
class ProbeNameResult:
    ok: bool
    rtt: T.Optional[float] = None
    error: T.Optional[str] = None


@dataclass_json
@dataclass
class ProbeResult:
    connected: bool = False
    connectError: T.Optional[str] = None
    probes: T.Optional[T.List[ProbeNameResult]] = None


class ProbeRequest:
    def __init__(self, j: T.Any):
        assert j["transport"] == "http3"
        self._url = urlparse(j["router"])
        self._family: int = j["family"]
        self._names = [Name.from_str(name) for name in j["names"]]
        self._t0: T.List[float] = []
        self._result = ProbeResult()

    async def probe(self) -> ProbeResult:
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
                self._result.connected = True
                self._result.probes = [ProbeNameResult(
                    ok=False, error="timeout") for name in self._names]
                self._send_interests(client)
                received = 0
                for _ in range(int(INTEREST_TIMEOUT // INTEREST_TIMEOUT_STEP)):
                    await aio.sleep(INTEREST_TIMEOUT_STEP)
                    received += self._process_received(client)
                    if received >= len(self._names):
                        break
        except Exception as err:
            self._result.connectError = str(err)
        return self._result

    def _resolve_dns(self) -> T.Tuple[str, int]:
        addrs = socket.getaddrinfo(
            host=self._url.hostname,
            port=self._url.port,
            family=(socket.AF_INET if self._family == 4 else socket.AF_INET6),
            type=socket.SOCK_DGRAM,
        )
        if len(addrs) == 0:
            raise RuntimeError("DNS error")
        return addrs[0][4][:2]

    def _send_interests(self, client: H3Client) -> bool:
        for name in self._names:
            interest = bytes(make_interest(
                name, interest_param=INTEREST_PARAMS))
            self._t0.append(time.time())
            client.send(interest)

    def _process_received(self, client: H3Client) -> int:
        n = 0
        t1 = time.time()
        for pkt in client.received:
            try:
                data_name, _, _, _ = parse_data(pkt, with_tl=True)
                for i, name in enumerate(self._names):
                    if data_name == name:
                        self._result.probes[i] = ProbeNameResult(
                            ok=True, rtt=(t1 - self._t0[i]) * 1000)
                        n += 1
                        break
            except:
                pass
        client.received.clear()
        return n


@app.route("/probe", methods=["POST"])
async def handle_probe():
    req = ProbeRequest(request.json)
    res = await req.probe()
    return Response(res.to_json(), mimetype="application/json")
