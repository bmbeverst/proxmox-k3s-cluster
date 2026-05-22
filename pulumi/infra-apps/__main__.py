from pulumi_kubernetes.yaml.v2 import ConfigFile

import pulumi

SUC_VERSION = "v0.19.2"

suc_crd = ConfigFile(
    "system-upgrade-controller-crd",
    file=f"https://github.com/rancher/system-upgrade-controller/releases/download/{SUC_VERSION}/crd.yaml",
)

suc = ConfigFile(
    "system-upgrade-controller",
    file=f"https://github.com/rancher/system-upgrade-controller/releases/download/{SUC_VERSION}/system-upgrade-controller.yaml",
    opts=pulumi.ResourceOptions(depends_on=[suc_crd]),
)
