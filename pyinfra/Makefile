hello-world:
	uv run --group infra pyinfra --debug X.X.X.X exec -- echo "hello world"

test:
	uv run --group infra pyinfra --debug inventory.py test.py -vvv

inventory:
	uv run --group infra pyinfra --debug inventory.py inventory.py -vvv

uninstall:
	uv run --group infra pyinfra --debug inventory.py uninstall_k3s.py -vvv

install:
	uv run --group infra pyinfra --debug inventory.py install_k3s.py -vvv