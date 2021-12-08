import typing as T

import aioquic.h3.connection as h3c

# https://datatracker.ietf.org/doc/html/draft-ietf-masque-h3-datagram-05#section-9.1
H3_DATAGRAM_05 = 0xffd277
# https://datatracker.ietf.org/doc/html/draft-ietf-httpbis-h3-websockets-00#section-5
ENABLE_CONNECT_PROTOCOL = 0x08


class H3Connection(h3c.H3Connection):
    """HTTP/3 connection with SETTINGS frame override."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

    def _validate_settings(self, settings: T.Dict[int, int]) -> None:
        if settings.get(H3_DATAGRAM_05, 0) == 1:
            settings[h3c.Setting.H3_DATAGRAM] = 1
        return super()._validate_settings(settings)

    def _get_local_settings(self) -> T.Dict[int, int]:
        settings = super()._get_local_settings()
        if self._enable_webtransport:
            settings[H3_DATAGRAM_05] = 1
            settings[ENABLE_CONNECT_PROTOCOL] = 1
        return settings
