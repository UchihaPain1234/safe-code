import asyncio
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('connect_proxy', Path(__file__).with_name('connect_proxy.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class TargetTests(unittest.TestCase):
    def test_public_https_only(self):
        self.assertEqual(module.connect_target('api.anthropic.com:443'), (b'api.anthropic.com', 443))
        for target in ('127.0.0.1:443', '[::1]:443', '10.1.2.3:443', 'api.anthropic.com:80',
                       'user:password@example.com:443', 'example.com:443/path', 'localhost:443'):
            with self.subTest(target=target), self.assertRaises(ValueError):
                module.connect_target(target)

class SocksTests(unittest.IsolatedAsyncioTestCase):
    async def test_authenticated_remote_dns_and_payload(self):
        observed = {}
        async def fake(reader, writer):
            observed['greeting'] = await reader.readexactly(3)
            writer.write(b'\x05\x02'); await writer.drain()
            auth = await reader.readexactly(2)
            observed['user'] = await reader.readexactly(auth[1])
            n = (await reader.readexactly(1))[0]
            observed['password'] = await reader.readexactly(n)
            writer.write(b'\x01\x00'); await writer.drain()
            header = await reader.readexactly(5)
            observed['address_type'] = header[3]
            observed['hostname'] = await reader.readexactly(header[4])
            await reader.readexactly(2)
            writer.write(b'\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00'); await writer.drain()
            content = await reader.readexactly(8)
            writer.write(content); await writer.drain()
            writer.close(); await writer.wait_closed()
        socks = await asyncio.start_server(fake, '127.0.0.1', 0)
        proxy = module.ConnectProxy(('127.0.0.1', socks.sockets[0].getsockname()[1], b'fixture', b'secret'))
        server = await asyncio.start_server(proxy.handle, '127.0.0.1', 0)
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', server.sockets[0].getsockname()[1])
            writer.write(b'CONNECT api.anthropic.com:443 HTTP/1.1\r\nHost: api.anthropic.com\r\n\r\n'); await writer.drain()
            self.assertIn(b'200 Connection Established', await reader.readuntil(b'\r\n\r\n'))
            writer.write(b'tlsbytes'); await writer.drain()
            self.assertEqual(await reader.readexactly(8), b'tlsbytes')
            self.assertEqual(observed['hostname'], b'api.anthropic.com')
            self.assertEqual(observed['address_type'], 3)
            self.assertEqual(observed['user'], b'fixture')
            writer.close(); await writer.wait_closed()
        finally:
            server.close(); socks.close()
            await server.wait_closed(); await socks.wait_closed()

    async def test_upstream_failure_is_502_without_direct_fallback(self):
        closed = await asyncio.start_server(lambda r,w: None, '127.0.0.1', 0)
        port = closed.sockets[0].getsockname()[1]
        closed.close(); await closed.wait_closed()
        proxy = module.ConnectProxy(('127.0.0.1', port, b'fixture', b'secret'))
        server = await asyncio.start_server(proxy.handle, '127.0.0.1', 0)
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', server.sockets[0].getsockname()[1])
            writer.write(b'CONNECT api.anthropic.com:443 HTTP/1.1\r\n\r\n'); await writer.drain()
            self.assertIn(b'502 Bad Gateway', await reader.readuntil(b'\r\n\r\n'))
            writer.close(); await writer.wait_closed()
        finally:
            server.close(); await server.wait_closed()

if __name__ == '__main__':
    unittest.main()
