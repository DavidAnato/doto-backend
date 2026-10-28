import json

from rest_framework.renderers import BaseRenderer


class EventStreamRenderer(BaseRenderer):
    """Accepte ``Accept: text/event-stream``.

    Sans ce renderer, DRF refuse la négociation (406) et le client
    reconnecte le flux SSE en boucle.
    """

    media_type = "text/event-stream"
    format = "txt"
    charset = None
    render_style = "binary"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if data is None or data == b"":
            return b""
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)
        if isinstance(data, str):
            return data.encode()
        return json.dumps(data).encode()
