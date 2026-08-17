# infra-bootstrap & Pulumi Kubernetes Operator (PKO)

Bootstraps the Kubernetes Pulumi operator and the namespaces/RBAC/secrets it
needs, then declares the `Stack` CRs PKO reconciles from `main`:

- `infra-apps` — cluster infrastructure apps.
- `infra-bootstrap` — **PKO self-manages its own release** from this program
  (operator Release, namespaces, RBAC, secrets, and the Stack CRs below).
- `my-apps` — user-facing apps (Ghost blog), isolated from infra stacks.

## PKO self-management & recovery

Because PKO reconciles its *own* Helm release, a restart can wedge the operator
(known issues #1292 / #1294 / #1105). Do **not** casually run
`pulumi destroy` on this stack.

**Last-resort recovery:** apply the pinned seed manifest

```sh
kubectl apply -f docs/infra-bootstrap-pko-seed.yaml
```

The seed is a `helm template` of the `pulumi-kubernetes-operator` chart pinned
at the version in `Chart.yaml`. It is **generated** — if the chart version
changes, regenerate it (see the header comment in the seed file).

## Renovate

The PKO chart version is declared in `Chart.yaml` (dependencies), so Renovate's
built-in `helmv3` manager bumps it automatically — no customManager needed.

## Configuration

Secrets are stored as Pulumi stack config (`require_secret`) in
`Pulumi.dev.yaml` for the `dev` stack (`bmbeverst/infra-bootstrap/dev`).
