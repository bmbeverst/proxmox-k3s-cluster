"""User-facing applications deployed on the k3s cluster.

This module is a thin orchestrator. The actual workloads live in their own
modules and are registered here (so Pulumi constructs every resource from the
`__main__.py` entrypoint):

- `ghost.py`        -> `ghost.register(ghost_namespace)`
- `vintagestory.py` -> `vintagestory.register(vints_namespace)`
- `paseo.py`        -> `paseo.register(paseo_namespace)`
- `spoolman.py`     -> `spoolman.register(spoolman_namespace)`

Each app lives in its own Namespace for isolation: `ghost`, `vintagestory`,
`paseo` and `spoolman`.

my-apps does not need to wait on the infrastructure stacks (infra-bootstrap,
infra-apps) inside this program anymore: that ordering is expressed declaratively
on the PKO `Stack` custom resource (`my-apps` depends on `infra-bootstrap` and
`infra-apps`) via `spec.prerequisites` in `infra-bootstrap/__main__.py`, so PKO
runs this stack only once the infra stacks have succeeded.
"""

import pulumi_kubernetes as k8s
from pulumi_kubernetes.meta.v1 import ObjectMetaArgs

import ghost
import paseo
import spoolman
import vintagestory

# Each app lives in its own Namespace for isolation. The name for each app is
# owned by its own module (ghost.NAMESPACE / vintagestory.NAMESPACE).
ghost_namespace = k8s.core.v1.Namespace(
    "ghost",
    metadata=ObjectMetaArgs(name=ghost.NAMESPACE),
)
vints_namespace = k8s.core.v1.Namespace(
    "vintagestory",
    metadata=ObjectMetaArgs(name=vintagestory.NAMESPACE),
)
paseo_namespace = k8s.core.v1.Namespace(
    "paseo",
    metadata=ObjectMetaArgs(name=paseo.NAMESPACE),
)
spoolman_namespace = k8s.core.v1.Namespace(
    "spoolman",
    metadata=ObjectMetaArgs(name=spoolman.NAMESPACE),
)

ghost.register(ghost_namespace)
vintagestory.register(vints_namespace)
paseo.register(paseo_namespace)
spoolman.register(spoolman_namespace)
