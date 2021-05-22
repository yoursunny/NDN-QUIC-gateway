import aioquic.h3.connection as h3c
from aioquic.quic.connection import QuicConnection


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
