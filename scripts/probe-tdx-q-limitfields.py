#!/usr/bin/env python3
"""Read-only MAC dynamic-field probe for the Q-LIMIT research record."""
import socket
import struct
import sys
import zlib

HOST = "121.36.248.138"
PORT = 7709
SYMBOLS = ((0, "000001"), (1, "600519"), (0, "300750"), (1, "688981"), (2, "920000"))
BITS = (0x1B, 0x20, 0x21, 0x58, 0x59, 0x5C, 0x5D, 0x5E, 0x66, 0x67, 0x7A, 0x88, 0x8B)
NAMES = {0x1B: "turnover", 0x20: "limit_up", 0x21: "limit_down", 0x58: "annual_limit_up_days",
         0x59: "activity", 0x5C: "consecutive_up_days", 0x5D: "bit_0x5d", 0x5E: "bit_0x5e",
         0x66: "auction_buy_limit", 0x67: "auction_sell_limit", 0x7A: "auction_vol_ratio",
         0x88: "up_count", 0x8B: "down_count"}
INTEGER_BITS = {0x58, 0x59, 0x5C, 0x5D, 0x5E, 0x88, 0x8B}
SIGNED_BITS = {0x5C}


def bitmap(bits):
    out = bytearray(20)
    for bit in bits:
        out[bit // 8] |= 1 << (bit % 8)
    return bytes(out)


def request():
    payload = bytearray(bitmap(BITS)) + struct.pack("<H", len(SYMBOLS))
    for market, code in SYMBOLS:
        payload += struct.pack("<H22s", market, code.encode() + bytes(22 - len(code)))
    body = struct.pack("<H", 0x122B) + payload
    return struct.pack("<BIBHH", 1, 0, 1, len(body), len(body)) + body


def read(sock, size):
    data = bytearray()
    while len(data) < size:
        data.extend(sock.recv(size - len(data)))
    return bytes(data)


def main():
    with socket.create_connection((HOST, PORT), timeout=5) as sock:
        sock.sendall(request())
        header = read(sock, 16)
        zipped, plain = struct.unpack_from("<HH", header, 12)
        body = read(sock, zipped)
    body = zlib.decompress(body) if zipped != plain else body
    count = struct.unpack_from("<H", body, 24)[0]
    fields = [bit for bit in range(160) if body[bit // 8] & (1 << (bit % 8))]
    stride = 68 + 4 * len(fields)
    rows = []
    for index in range(count):
        pos = 26 + index * stride
        values = {NAMES.get(bit, f"bit_0x{bit:02x}"): struct.unpack_from("<i" if bit in SIGNED_BITS else "<I" if bit in INTEGER_BITS else "<f",
                                                                          body, pos + 68 + 4 * offset)[0]
                  for offset, bit in enumerate(fields)}
        rows.append((body[pos + 2:pos + 24].split(b"\0", 1)[0].decode(), values))
    if not rows:
        return 1
    for symbol, values in rows:
        print(symbol, " ".join(f"{key}={value:g}" for key, value in values.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
