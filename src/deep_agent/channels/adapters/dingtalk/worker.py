"""Isolate the published SDK's blocking connect and non-cancellable retry loop."""
import asyncio
import logging
import os


def run(pipe, client_id, secret):
    # SDK task exceptions may bypass logging and include callback bodies or
    # signed connection URLs. This isolated worker only reports sanitized IPC.
    with open(os.devnull, "w") as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
    # The SDK logs callback bodies and signed URLs. Never forward its raw logs.
    silent = logging.Logger('dingtalk-worker', level=logging.CRITICAL + 1)
    silent.addHandler(logging.NullHandler())
    logging.disable(logging.CRITICAL)
    import dingtalk_stream as sdk

    class Handler(sdk.ChatbotHandler):
        async def process(self, message):
            pipe.send({'type': 'message', 'data': message.data})
            return sdk.AckMessage.STATUS_OK, 'OK'

    client = sdk.DingTalkStreamClient(sdk.Credential(client_id, secret), logger=silent)
    client.register_callback_handler(sdk.ChatbotMessage.TOPIC, Handler())

    async def monitor():
        previous = None
        while True:
            # SDK first POSTs clientId/clientSecret to the connection API,
            # then uses its issued ticket in the WS handshake. OPEN therefore
            # follows credential validation, not an unauthenticated TCP open.
            socket = client.websocket
            state = getattr(socket, 'state', None)
            connected = socket is not None and (getattr(state, 'name', '') == 'OPEN' if state is not None else not getattr(socket, 'closed', True))
            if connected != previous:
                pipe.send({'type': 'status', 'connected': connected})
                previous = connected
            await asyncio.sleep(.5)

    async def main():
        await asyncio.gather(client.start(), monitor())
    try:
        asyncio.run(main())
    finally:
        pipe.close()
