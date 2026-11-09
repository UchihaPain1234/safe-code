#!/usr/bin/env python3
"""Loopback HTTP CONNECT -> authenticated SOCKS5, with remote DNS and no fallback.

The CONNECT payload is copied unchanged: this process does not terminate TLS,
rewrite Claude headers, or read account credentials. Python 3.10+, stdlib only.
"""
import argparse
import asyncio
import ipaddress
import json
import re
from urllib.parse import unquote, urlsplit


def proxy_settings(value):
    parsed = urlsplit(value)
    if parsed.scheme not in ('socks5', 'socks5h') or not parsed.hostname or not parsed.port:
        raise ValueError('An explicit SOCKS5 endpoint is required')
    # Numeric upstream prevents a local DNS escape from the proxy service.
    ipaddress.ip_address(parsed.hostname)
    user = unquote(parsed.username or '').encode()
    password = unquote(parsed.password or '').encode()
    if not (1 <= len(user) <= 255 and 1 <= len(password) <= 255):
        raise ValueError('SOCKS5 authentication is required')
    return parsed.hostname, parsed.port, user, password


def connect_target(authority):
    parsed = urlsplit('https://' + authority)
    if (not parsed.hostname or parsed.port != 443 or parsed.username is not None
            or parsed.password is not None or parsed.path or parsed.query or parsed.fragment):
        raise ValueError('Only explicit CONNECT host:443 is supported')
    host = parsed.hostname
    try:
        address = ipaddress.ip_address(host)
        if not address.is_global:
            raise ValueError('Private and reserved destinations are not allowed')
    except ValueError as error:
        if ':' in host or re.fullmatch(r'[0-9.]+', host):
            raise error
        if (len(host) > 253 or '.' not in host or host.endswith(('.local', '.localhost'))
                or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', host)):
            raise ValueError('Invalid public hostname')
    encoded = host.encode('idna')
    if len(encoded) > 255:
        raise ValueError('Hostname is too long')
    return encoded, 443


async def socks_connect(settings, host, port):
    address, proxy_port, user, password = settings
    reader, writer = await asyncio.wait_for(asyncio.open_connection(address, proxy_port), 20)
    try:
        writer.write(b'\x05\x01\x02')
        await writer.drain()
        if await asyncio.wait_for(reader.readexactly(2), 20) != b'\x05\x02':
            raise ConnectionError('SOCKS5 authentication unavailable')
        writer.write(b'\x01' + bytes([len(user)]) + user + bytes([len(password)]) + password)
        await writer.drain()
        if await asyncio.wait_for(reader.readexactly(2), 20) != b'\x01\x00':
            raise ConnectionError('SOCKS5 authentication failed')
        writer.write(b'\x05\x01\x00\x03' + bytes([len(host)]) + host + port.to_bytes(2, 'big'))
        await writer.drain()
        reply = await asyncio.wait_for(reader.readexactly(4), 20)
        if reply[:3] != b'\x05\x00\x00':
            raise ConnectionError('SOCKS5 connection rejected')
        if reply[3] == 1:
            size = 4
        elif reply[3] == 4:
            size = 16
        elif reply[3] == 3:
            size = (await asyncio.wait_for(reader.readexactly(1), 20))[0]
        else:
            raise ConnectionError('Invalid SOCKS5 response')
        await asyncio.wait_for(reader.readexactly(size + 2), 20)
        return reader, writer
    except BaseException:
        writer.close()
        await writer.wait_closed()
        raise


async def relay(reader, writer):
    try:
        while True:
            block = await asyncio.wait_for(reader.read(65536), 600)
            if not block:
                if writer.can_write_eof():
                    writer.write_eof()
                    await writer.drain()
                return
            writer.write(block)
            await writer.drain()
    except (ConnectionError, asyncio.TimeoutError):
        return


class ConnectProxy:
    def __init__(self, settings):
        self.settings = settings
        self.connections = 0

    async def handle(self, reader, writer):
        upstream = None
        established = False
        tasks = []
        if self.connections >= 16:
            writer.write(b'HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\n\r\n')
            await writer.drain()
            writer.close()
            return
        self.connections += 1
        try:
            header = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 10)
            if len(header) > 8192:
                raise ValueError('Header too large')
            method, authority, protocol = header.split(b'\r\n', 1)[0].decode('ascii').split(' ')
            if method == 'GET' and authority == '/health':
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 3\r\nConnection: close\r\n\r\nok\n')
                await writer.drain()
                return
            if method != 'CONNECT' or protocol not in ('HTTP/1.0', 'HTTP/1.1'):
                raise ValueError('Only CONNECT is supported')
            host, port = connect_target(authority)
            remote_reader, upstream = await socks_connect(self.settings, host, port)
            writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
            await writer.drain()
            established = True
            tasks = [asyncio.create_task(relay(reader, upstream)),
                     asyncio.create_task(relay(remote_reader, writer))]
            _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if pending:
                _, pending = await asyncio.wait(pending, timeout=15)
                for task in pending:
                    task.cancel()
        except (ValueError, UnicodeError, ConnectionError, OSError,
                asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError):
            if not established:
                writer.write(b'HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
                try:
                    await writer.drain()
                except ConnectionError:
                    pass
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            for stream in (upstream, writer):
                if stream:
                    stream.close()
                    try:
                        await stream.wait_closed()
                    except ConnectionError:
                        pass
            self.connections -= 1


async def main(config):
    settings = proxy_settings(config['proxy_url'])
    proxy = ConnectProxy(settings)
    server = await asyncio.start_server(proxy.handle, '127.0.0.1', config.get('listen_port', 31289), limit=8192)
    async with server:
        await server.serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    with open(args.config) as source:
        configuration = json.load(source)
    try:
        asyncio.run(main(configuration))
    except KeyboardInterrupt:
        pass
