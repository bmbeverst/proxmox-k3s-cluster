# Intro
Play books to create a k3s cluster.



# Variables
Are defined per group in the `group_var` folder, the name is important. Then vars are split by group name, `k3s` for example.

## Secrets
Are defined in the same `group_var` folder but in the vault file and are prefixed with `vault_`

Password is stored in `.vault_pass` configured in the `ansible.cfg`

Edit secrets with `ansible-vault edit group_var/k3s/vault`

# Debug

## Get the vars
`ansible -m debug -a 'var=hostvars[inventory_hostname]' k3s -i inventory.yaml`

## Debug output on playbook
`ansible-playbook -vvvvv -i inventory.yaml init-cluster.yaml`

