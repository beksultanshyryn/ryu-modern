#!/usr/bin/env python3
"""Patch dnspython TLSA compatibility for Ryu/eventlet.

This script applies the same compatibility layer used by Ryu at runtime,
ensuring that imports of dns.rdtypes.tlsabase are redirected to
dns.rdtypes.tlsa_base (and vice versa) across dnspython versions.
"""

from __future__ import annotations

from ryu.lib import dnspython_compat


def main() -> None:
    dnspython_compat.apply()


if __name__ == "__main__":
    main()
