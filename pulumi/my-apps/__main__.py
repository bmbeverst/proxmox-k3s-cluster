"""User-facing applications deployed on the k3s cluster.

This module is a thin orchestrator. The actual workloads live in their own
modules and are registered here (so Pulumi constructs every resource from the
`__main__.py` entrypoint):

- `ghost.py`        -> `ghost.register(ghost_namespace, infra_deps)`
- `vintagestory.py` -> `vintagestory.register(vints_namespace, infra_deps)`

Each app lives in its own Namespace for isolation: `ghost` and `vintagestory`.

my-apps waits for the infrastructure stacks (`infra-bootstrap`, `infra-apps`)
via `StackReference` before deploying anything, so Ghost/Vintage Story are not
created until PKO, the `linstor-r2` StorageClass and the base cluster apps the
workloads rely on are already up.
"""

import pulumi
import pulumi_kubernetes as k8s
from pulumi_kubernetes.meta.v1 import ObjectMetaArgs

import ghost
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

# my-apps must not deploy before the infrastructure stacks are up. These
# StackReferences are passed into every workload as an explicit inter-stack
# dependency: if infra-bootstrap/infra-apps have not been up'd, this stack's
# `pulumi up` cannot complete, so PKO keeps retrying until the infra is there.
infra_bootstrap = pulumi.StackReference("bmbeverst/infra-bootstrap/dev")
infra_apps = pulumi.StackReference("bmbeverst/infra-apps/dev")
infra_deps = [infra_bootstrap, infra_apps]

ghost.register(ghost_namespace, infra_deps)
vintagestory.register(vints_namespace, infra_deps)










                    



                            
