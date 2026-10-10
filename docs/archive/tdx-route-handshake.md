# TDX Route R1 handshake experiment (2026-10-09)

## Scope and method

This is a read-only protocol probe for `000001.SZ` against the first three
`DEFAULT_HOSTS` in `quant-service/app/datasources/sources/tdx_protocol.py`:

```
60.191.117.167:7709
218.75.126.9:7709
123.125.108.14:7709
```

The probe is `scripts/tdx_handshake_experiments.py`. It opens one connection at
a time per host and request variant, uses a five-second socket timeout, prints response length and
the first bytes, and treats a two-byte body as an error rather than as a count.
It tries the existing three-packet setup, Go-style `LOGIN_ONE + LOGIN_TWO`,
the same with the historical extra `LOGIN_ONE`, and `LOGIN_ONE` alone. Request
variants cover legacy/modern `QUOTE`, batch `0x054c`, encrypted `0x0547`,
`KLINE 0x052d`, the newer `0x0523` spelling, `KMINUTE 0x0537`,
`KMINUTE_OLD 0x0fb4`, and `BIDD 0x056a`.

## Go client comparison

The sources were cloned read-only into `/tmp/tdx-go-iqbClL` from:

* `quant1x/gotdx`
* `injoyai/tdx`
* `godlikefu/tdx` (the current Go module source is the same protocol family as
  `injoyai/tdx`)
* `bensema/gotdx`

Observed source revisions: `quant1x/gotdx` `aa4ea36651ce`, `injoyai/tdx`
`a16d0a67c1eb`, `godlikefu/tdx` `19ebfa22200b`, and `bensema/gotdx`
`d2b25b98e97e`.

The common modern request header is 12 bytes:

```
0c | uint32 sequence | uint8 packet type | uint16 length | uint16 length |
uint16 method | payload
```

The current Python builders use pytdx's older, command-specific header layout.
For example, the checked-in quote request starts
`0c0120630002130013003e05...`, while a Go standard quote request starts with
`0c <sequence> 01 1300 1300 3e05 ...`. The method IDs are not the whole
protocol: packet type, sequence and payload framing differ too.

Handshake payloads in `quant1x/gotdx`, `injoyai/tdx`, `godlikefu/tdx`, and
`bensema/gotdx` are:

* `LOGIN_ONE (0x000d)`: one byte `01`.
* `LOGIN_TWO (0x0fdb)`: the 32-byte payload beginning
  `d5d0c9ccd6a4a8af...`, the GBK “招商证券” client identity. The checked-in
  Python third setup packet contains this same payload and the same 12-byte
  envelope shape, but has fixed sequence/packet values rather than a generated
  per-connection sequence.

Relevant request layouts:

* `KLINE 0x052d`: market (uint16), six-byte code, category, times=1, start,
  count, adjust/reserved bytes. The response parser is the same delta-price
  family used by this client.
* `KMINUTE 0x0537`: market (uint16), code, start, count; response begins with
  count and an ignored uint16, then price/average/volume varints.
* `KMINUTE_OLD 0x0fb4`: signed date, one-byte market, code; response skips
  four bytes after count before price/average/volume varints.
* `QUOTE 0x053e`: a five-mode prefix (`05 00 00 00 00 00 00 00`), uint16
  count, then market + six-byte code per item. The detailed response uses the
  existing quote varint layout.
* `bensema/gotdx` also implements `QUOTE_ENCRYPT 0x0547`: request entries are
  market + code + uint16 `22234` + uint16 `2`; every response byte is XORed
  with `0x93` before parsing the normal quote fields. This is an obfuscation,
  not cryptographic authentication.
* `0x054c` is the newer batch quote method used by `quant1x/gotdx`; its payload
  is the five-mode prefix, six reserved bytes, count, then market+code items.

## Live results

All three hosts returned the same result pattern. The response prefixes below
are representative; sequence bytes in setup responses vary per connection.

| setup variant | `QUOTE 0x053e` | `QUOTE_ENCRYPT 0x0547` | `KLINE 0x052d` | `0x054c` batch | `KMINUTE 0x0537` | `KMINUTE_OLD 0x0fb4` |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| existing `legacy_3` | 0 rows, raw 4 (`0103 0000`) | 2-byte `9393` error | 2-byte `2003` error | 0 rows, raw 4 (`0000 0000`) | 240 rows, raw 1229 | 240 rows, raw 1021 |
| Go `LOGIN_ONE + LOGIN_TWO` | 0 rows, raw 4 (`0102 0000`) | 2-byte `9393` error | 2-byte `2003` error | 0 rows, raw 4 | 240 rows, raw 1229 | 240 rows, raw 1021 |
| Go `LOGIN_ONE`, repeated `LOGIN_ONE`, then `LOGIN_TWO` | 0 rows, raw 4 | 2-byte `9393` error | 2-byte `2003` error | 0 rows | 240 rows, raw 1229 | 240 rows, raw 1021 |
| **`LOGIN_ONE` only** | **1 real row, raw 86** | **1 real row after XOR-93, raw 120** | **8 real rows, raw 146** | **1 real row, raw 108** | **240 real rows, raw 1229** | **240 real rows, raw 1021** |

With `LOGIN_ONE` only, the legacy `0x052d` request with category `8` also
returned eight parseable one-minute OHLC bars (raw length 132; the probe labels
this `bars_1m_052d`). The `0x0537`/`0x0fb4` rows are minute time-series
records (price/average/volume), not OHLC bars.

The successful `LOGIN_ONE`-only daily response begins
`08003a283501b4b6010a32d001106a39...`; it contains eight complete bar records,
not just an error/count marker. The `0x0547` response begins
`929393a3a3a3...` and decodes to count one after XOR `0x93`.

Both minute methods return 240 real rows on all three hosts under every
handshake profile tested. `BIDD 0x056a` timed out in the one-host checks. The
handshake therefore specifically gates quote and OHLC K-line methods; it is not
necessary for the minute time-series methods.

## Why the legacy route fails

The failure is not a bad `0x052d` or `0x053e` constant by itself. The public
servers appear to select an access/session profile from the login state. Sending
the `LOGIN_TWO` “招商证券” identity (whether in the legacy third setup packet
or the modern Go-framed packet) puts these hosts into the restricted behavior
seen previously: empty quote envelopes and `2003` K-line errors. A connection
that sends only `LOGIN_ONE` is accepted and serves the same methods with real
rows. This is a server-side handshake policy difference, not evidence that
the data is absent.

## Minimal production change implied (not applied here)

Do not change `tdx_protocol.py` in this branch. The smallest experimental patch
would be to make the setup sequence selectable and add a fail-closed
`login_one_only` profile that sends only the existing first login packet before
requests. Keep the current three-packet sequence as the default until a policy
decision is made, because the login payload is an unofficial broker identity
and host behavior can change.

If adopting the revived route, add separate builders/parsers rather than
changing existing bytes in place:

1. modern 12-byte header builder with a per-connection sequence and packet type;
2. `0x054c` batch quote and optional `0x0547` XOR-`0x93` quote request/parser;
3. response-shape checks that reject two-byte errors and incomplete rows;
4. a capability probe per host, recording `handshake_profile`, opcode, raw
   length, and parsed count as research evidence.

The live evidence supports `LOGIN_ONE`-only as the minimal behavior change for
quotes, daily bars, and one-minute bars. It does **not** support replacing
`0x052d` with another K-line opcode to obtain those rows.
