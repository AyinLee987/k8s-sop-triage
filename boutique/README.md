# Online Boutique on the lab cluster

`kubernetes-manifests-v0.10.3.yaml` is the unmodified release manifest of GoogleCloudPlatform/microservices-demo
v0.10.3 (Apache-2.0), the version Cloud-OpsBench recorded its Online Boutique cases on (image tags in the case
snapshots). It is deployed into the namespace `boutique`, the name the benchmark uses.

    powershell -ExecutionPolicy Bypass -File .\scripts\Setup-Boutique.ps1            # deploy and wait
    powershell -ExecutionPolicy Bypass -File .\scripts\Setup-Boutique.ps1 -Action status

12 Deployments (11 services + loadgenerator), requests 1.57 CPU / 1.4 GiB, limits 2.8 CPU / 2.5 GiB in total.
The `frontend-external` Service is of type LoadBalancer and stays `<pending>` on kind; nothing depends on it.
The script only adds things (namespace, workloads, a read-only Role for the diagnoser); it never deletes.
