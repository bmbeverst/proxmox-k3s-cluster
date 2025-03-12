init:
	ansible-playbook -i inventory.yaml init-cluster.yaml

lint:
	ansible-lint *.yaml

vault-k3s:
	ansible-vault edit group_vars/k3s/vault

debug-vars:
	ansible -m debug -a 'var=hostvars[inventory_hostname]' k3s -i inventory.yaml
