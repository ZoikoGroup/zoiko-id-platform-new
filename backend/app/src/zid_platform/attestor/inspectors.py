"""Runtime inspectors: what is actually running, read from the runtime and never from the workload.

Each inspector turns one runtime object into `Facts`: the selectors it satisfies and the artifact digests it is running.
The Docker inspector reads the container engine (the local and CI runtime); the Kubernetes inspector reads the cluster API
with the attestor's own service account. A hardware or TEE verifier is an extension point (`HardwareQuoteVerifier`): none
ships here, so this attestor never vouches for level 3."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

import httpx

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


class InspectionError(Exception):
    """The runtime could not be read, or the object is not running. Never a reason to attest."""


@dataclass(frozen=True, slots=True)
class Facts:
    selectors: dict[str, str]
    digests: frozenset[str]
    running: bool = True


class Inspector(Protocol):
    def inspect(self, target: str) -> Facts: ...


class HardwareQuoteVerifier(Protocol):
    """Verifies hardware or TEE evidence for a runtime. An attestor with none configured cannot reach level 3."""

    def verify(self, target: str, facts: Facts) -> bool: ...


Runner = Callable[[Sequence[str]], str]


def _docker_run(args: Sequence[str]) -> str:
    argv = list(args)
    done = subprocess.run(argv, capture_output=True, text=True, timeout=15, check=False)
    if done.returncode != 0:
        raise InspectionError((done.stderr or "docker failed").strip()[:200])
    return done.stdout


@dataclass(slots=True)
class DockerInspector:
    """A container on the local engine. Selectors: `docker.container` (its name) and `docker.label.<key>`."""

    run: Runner = field(default=_docker_run)

    def inspect(self, target: str) -> Facts:
        try:
            container = json.loads(self.run(["docker", "inspect", target]))[0]
        except (ValueError, IndexError) as e:
            raise InspectionError("docker inspect returned nothing usable") from e
        image_id = str(container.get("Image", ""))
        selectors = {"docker.container": str(container.get("Name", "")).lstrip("/")}
        for key, value in ((container.get("Config") or {}).get("Labels") or {}).items():
            selectors[f"docker.label.{key}"] = str(value)
        digests = {image_id} if _DIGEST.fullmatch(image_id) else set()
        try:
            image = json.loads(self.run(["docker", "image", "inspect", image_id]))[0]
            for ref in image.get("RepoDigests") or []:
                m = _DIGEST.search(str(ref))
                if m:
                    digests.add(m.group(0))
        except (InspectionError, ValueError, IndexError):
            pass  # the image id alone is still a real, runtime-observed digest
        running = bool((container.get("State") or {}).get("Running"))
        return Facts(selectors, frozenset(digests), running)


@dataclass(slots=True)
class KubernetesInspector:
    """A pod, read with the attestor's own service-account token. `target` is `<namespace>/<pod>`. Selectors:
    `k8s.namespace`, `k8s.pod`, `k8s.service_account` and `k8s.label.<key>`."""

    api: str
    token: str
    ca_file: str | bool = True
    transport: httpx.BaseTransport | None = None

    def inspect(self, target: str) -> Facts:
        namespace, _, pod = target.partition("/")
        if not namespace or not pod:
            raise InspectionError("target must be <namespace>/<pod>")
        try:
            with httpx.Client(
                base_url=self.api,
                headers={"Authorization": f"Bearer {self.token}"},
                verify=self.ca_file,
                timeout=10,
                transport=self.transport,
            ) as c:
                res = c.get(f"/api/v1/namespaces/{namespace}/pods/{pod}")
        except httpx.HTTPError as e:
            raise InspectionError(f"cluster API unreachable: {type(e).__name__}") from e
        if res.status_code != 200:
            raise InspectionError(f"cluster API answered {res.status_code}")
        body = res.json()
        spec, status, meta = (
            body.get("spec") or {},
            body.get("status") or {},
            body.get("metadata") or {},
        )
        selectors = {
            "k8s.namespace": str(meta.get("namespace", "")),
            "k8s.pod": str(meta.get("name", "")),
            "k8s.service_account": str(spec.get("serviceAccountName", "")),
        }
        for key, value in (meta.get("labels") or {}).items():
            selectors[f"k8s.label.{key}"] = str(value)
        digests: set[str] = set()
        statuses = status.get("containerStatuses") or []
        for cs in statuses:
            m = _DIGEST.search(str(cs.get("imageID", "")))
            if m:
                digests.add(m.group(0))
        running = status.get("phase") == "Running" and all(
            (cs.get("state") or {}).get("running") is not None for cs in statuses
        )
        return Facts(selectors, frozenset(digests), bool(statuses) and running)
