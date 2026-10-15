# Create a k3s cluster on Proxmox VMs with Flatcar

## Dev

You need to include the lint and infra groups for dependencies.

`uv sync --all-groups`

Also if coming back after python was updated redo the .venv with `uv venv` `source .venv/bin/activate` `uv sync --active --group lint`

### Lint

Formatting is black + isort, configured in `pyproject.toml` (`[tool.isort]` uses
`profile = "black"`, so the two never fight) and run through
[pre-commit](https://pre-commit.com) — see `.pre-commit-config.yaml`.

Git hooks are local state, so enable them once per clone:

```bash
make hooks       # uv run --only-group lint pre-commit install
```

```bash
make lint        # run the hooks over every file
make lint-ci     # exactly what CI runs
```

The hooks fix files in place and then fail, so a failing commit leaves the
correction applied — re-stage and commit again. They check what is *staged*, not
your working tree, so unrelated edits in progress are stashed and restored.

The hook definitions use `repo: local` with the tools from the `lint` group in
`pyproject.toml`/`uv.lock`, so there is no second `rev:` pin to keep in sync.
CI (`.gitlab-ci.yml`) runs the same `pre-commit run --all-files`, so a clone
without hooks still cannot merge unformatted code.

## Components

### Flatcar

Flatcar Container Linux is deployed as a Proxmox VM template using the `proxmox-flatcar` tool. This tool:

- Creates a minimal, immutable Flatcar VM template on Proxmox 8.x
- Uses Ignition for system configuration through Proxmox's CloudInit integration
- Configures base system settings including:
  - Kernel message logging levels
  - Systemd service management (masking unnecessary services)
  - Network configuration through Proxmox CloudInit
- Supports template versioning and recreation
- Automatically handles VM provisioning with proper disk sizing and configuration

The template is designed to be cloned for new k3s nodes, with each node inheriting the base configuration while allowing for node-specific customization through Proxmox's CloudInit interface.

### Pyinfra

Pyinfra is an automation tool used to deploy and manage the k3s cluster. It provides:

- Infrastructure as code capabilities for cluster deployment
- Inventory management for cluster nodes
- Automated deployment of k3s across multiple nodes
- Configuration management for cluster components

The project uses Pyinfra to orchestrate the deployment of k3s across multiple Flatcar nodes, ensuring consistent configuration and automated setup of the cluster.

### Pulumi

This the code to manage the kubernetes cluster using Pulumi. It provides a declarative way to define the infrastructure for the Kubernetes cluster, including the nodes, networking, and storage. The project uses Pulumi to automate the deployment of k3s across multiple Flatcar nodes, ensuring consistent configuration and automated setup of the cluster.

Only the infra-apps and infra-bootstrap are used. infra-bootstrap is for the Pulumi Kubernetes Operator, which is responsible for managing the infrastructure for the Kubernetes cluster. The infra-apps is for managing the base applications that will be deployed on the nodes. For example, kured, piraeus, and system-upgrade.

### Ansible

This is no longer used for settting up the Flatcar nodes. It was replaced by Pyinfra for automated deployment of k3s across multiple Flatcar nodes. But Ansible was just bash command in YAML so I moved to Pyinfra.
