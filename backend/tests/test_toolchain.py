"""The backend image ships the engine versions the spec pins (OPA 1.9.0 §8, Z3 4.15.4 §9)."""
import shutil
import subprocess

import z3


def test_opa_1_9_0_is_installed():
    assert shutil.which("opa"), "the backend image must ship the opa binary"
    version = subprocess.run(["opa", "version"], capture_output=True, text=True, check=True).stdout
    assert "Version: 1.9.0" in version


def test_z3_is_4_15_4():
    assert z3.get_version_string() == "4.15.4"
