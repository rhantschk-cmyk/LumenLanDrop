# Lumen LAN Drop

A small, local-first and cross-platform file drop for a LAN or Tailnet. It uses only Python's standard library at runtime: no account, cloud relay, discovery service or dependency is required.

## Install

```bash
python -m pip install .
```

## Quick start

On the receiving device, generate a transfer token and start a receiver:

```bash
lumen-landrop token
lumen-landrop serve --token PASTE_TOKEN --output ~/Downloads/LAN-Drop
```

The default receiver binds only to `127.0.0.1`. For a deliberate LAN/Tailnet receiver, specify a reachable address:

```bash
lumen-landrop serve --host 0.0.0.0 --port 8765 --token PASTE_TOKEN --output ~/Downloads/LAN-Drop
```

On the sending device:

```bash
lumen-landrop send --to http://receiver-host:8765 --token PASTE_TOKEN ~/Videos/export.mp4
```

Directories and multiple paths are packed into a regular-file-only tar stream. The receiver refuses symlinks and unsafe paths, extracts into a temporary directory and atomically publishes one transfer directory after validating the request. Sender and receiver compare the uploaded archive SHA-256.

## Lumen profile

`lumen-landrop` is portable on macOS, NixOS and other Linux systems. On Lumen Arch it is intended to be preinstalled and pairs with `lumen-media sync-exports`; no Arch-only behavior is required for the base transfer protocol.

## Commands

```text
lumen-landrop token
lumen-landrop serve [--host 127.0.0.1] [--port 8765] --token TOKEN --output DIRECTORY [--max-upload-bytes BYTES]
lumen-landrop send --to URL --token TOKEN PATH [PATH ...]
```

Receivers accept archives up to 10 GiB by default and reject larger declared uploads before creating staging files. Use `--max-upload-bytes` to set a different positive byte limit for a trusted receiver.

## Security model

A token authenticates one receiver session. LAN Drop deliberately does not add encryption; use it only on a trusted LAN or over Tailscale/VPN, or put it behind TLS yourself. Tokens are not written to disk by LAN Drop.

## Tests

```bash
python -m unittest discover -s tests -v
```

## License

MIT. See [LICENSE](LICENSE).
