# Copyright (C) 2011, 2012 Nippon Telegraph and Telephone Corporation.
# Copyright (C) 2011 Isaku Yamahata <yamahata at valinux co jp>
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

from __future__ import annotations

import os
import sys
import sysconfig

import setuptools
from setuptools.command.develop import develop as _develop

import ryu.hooks

BASE_REQUIRES = [
    "msgpack>=0.4.0",
    "netaddr",
    "oslo.config>=2.5.0",
    "ovs>=2.6.0",
    "packaging>=20.9",
    "routes",
    "six>=1.4.0",
    "tinyrpc==1.0.4",
    "webob>=1.2",
]


def _python_requires() -> tuple[int, int]:
    return sys.version_info[:2]


def _dependency_overrides() -> list[str]:
    major_minor = _python_requires()
    if major_minor < (3, 10):
        return ["eventlet==0.30.2", "dnspython<2.0.0"]
    return ["eventlet==0.33.0", "dnspython>=2.0.0"]


def _write_compat_pth() -> None:
    site_dir = sysconfig.get_paths().get("purelib")
    if not site_dir:
        site_dir = sysconfig.get_paths().get("platlib")
    if not site_dir:
        return
    pth_path = os.path.join(site_dir, "ryu_compat.pth")
    with open(pth_path, "w", encoding="utf-8") as handle:
        handle.write(
            "import ryu.lib.dnspython_compat as _ryu_compat;"
            " _ryu_compat.patch_eventlet_dnspython()\n"
        )


class DevelopWithCompat(_develop):
    """Ensure compatibility patches are installed for editable installs."""

    def run(self) -> None:
        super().run()
        _write_compat_pth()


ryu.hooks.save_orig()
setuptools.setup(name='ryu',
                 setup_requires=['pbr'],
                 pbr=True)
setuptools.setup(
    name="ryu",
    setup_requires=["pbr"],
    pbr=True,
    install_requires=BASE_REQUIRES + _dependency_overrides(),
    extras_require={
        "compat": [
            "eventlet>=0.33.0",
            "dnspython>=2.0.0",
        ],
    },
    include_package_data=True,
    package_data={
        "ryu": [
            "*.xsd",
            "**/*.patch",
            "lib/dnspython_compat.py",
        ]
    },
    cmdclass={"develop": DevelopWithCompat},
)
