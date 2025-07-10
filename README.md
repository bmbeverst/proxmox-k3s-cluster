# Create a k3s cluster on Proxmox VMs with Flatcar

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
