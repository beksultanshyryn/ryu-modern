# Copyright (C) 2024 Nippon Telegraph and Telephone Corporation.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Compatibility patch for dnspython 2.x module renames.

Before (dnspython 1.16):
    from dns.rdtypes import tlsabase

After (dnspython 2.x compatible):
    from dns.rdtypes import tlsa_base as tlsabase
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys
from typing import Optional


_ALIAS = "dns.rdtypes.tlsabase"
_TARGET = "dns.rdtypes.tlsa_base"


def _parse_version(value: str) -> Optional["object"]:
    try:
        from packaging import version
    except ImportError:
        return None
    try:
        return version.parse(value)
    except Exception:
        return None


class _AliasModuleFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, alias: str, target: str) -> None:
        self._alias = alias
        self._target = target

    def find_spec(self, fullname, path, target=None):
        if fullname != self._alias:
            return None
        target_spec = importlib.util.find_spec(self._target)
        if target_spec is None:
            return None
        return importlib.util.spec_from_loader(fullname, self)

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        target_mod = importlib.import_module(self._target)
        sys.modules[self._alias] = target_mod


def _install_alias_importer() -> None:
    if any(
        isinstance(finder, _AliasModuleFinder)
        and finder._alias == _ALIAS
        for finder in sys.meta_path
    ):
        return
    sys.meta_path.insert(0, _AliasModuleFinder(_ALIAS, _TARGET))


def patch_eventlet_dnspython() -> bool:
    """Apply compatibility patch for eventlet's greendns import path."""
    try:
        import dns
        import dns.rdtypes as rdtypes
    except ImportError:
        return False

    parsed = _parse_version(getattr(dns, "__version__", ""))
    if parsed is not None:
        try:
            from packaging import version
        except ImportError:
            parsed = None
        else:
            if parsed < version.parse("2.0.0"):
                return False

    tlsa_base_mod = None
    try:
        tlsa_base_mod = importlib.import_module(_TARGET)
    except ImportError:
        tlsa_base_mod = None

    if hasattr(rdtypes, "tlsa_base") and not hasattr(rdtypes, "tlsabase"):
        rdtypes.tlsabase = rdtypes.tlsa_base

    if tlsa_base_mod is not None:
        sys.modules.setdefault(_ALIAS, tlsa_base_mod)

    _install_alias_importer()
    return True
