from pyinfra import host

if host.data.get("init_k3s"):
    print("init_k3s")
    print(host.name) 
else:
    print("not init_k3s")
    print(host.name)       

