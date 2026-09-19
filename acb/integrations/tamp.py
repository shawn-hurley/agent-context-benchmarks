"""Experimental Caveman engine adapter for the isolated per-task service lifecycle."""
from __future__ import annotations

from importlib.resources import files
import json
import hashlib
from pathlib import Path
import tempfile

from acb.container import container_cp_in, container_exec_capture
from acb.integrations.base import Integration, IntegrationActivation, IntegrationFailure, ModelEndpoint

REVISION = "a50aeb7cb5599f92cba333b9ee10b1fbb1d6f5fa"
SERVICE = "acb-tamp"


class TampIntegration(Integration):
    name = "tamp"
    category = "model_middleware"
    resources = frozenset({"tamp"})

    def validate(self, harness, harness_config):
        allowed = {"name", "version", "level"}
        if set(self.config) - allowed:
            raise ValueError("unknown Tamp options")
        if self.config.get("version") != REVISION:
            raise ValueError("Tamp requires the catalog's pinned source revision")
        if self.config.get("level") >= 1 and self.config.get("level") <= 9:
            raise ValueError("Caveman mode must be record or compress")
        self.metadata = {"source_revision": REVISION, "level": self.config.get("level"),
                         }

    def install(self, context):
        raise RuntimeError("not functional")

    def activate(self, context):
        raise RuntimeError("not functional")

    def verify(self, context):
        raise RuntimeError("not functional")

    def start(self, context, upstream):
        raise RuntimeError("not functional")

    def collect(self, context):
        raise RuntimeError("not functional")

    def stop(self, context):
        raise RuntimeError("not functional")
