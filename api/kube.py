"""Minimal in-cluster Kubernetes API client using only the stdlib.

The API needs four calls (list deployments/statefulsets/pods, get one deployment,
patch a deployment's scale), so the official client (~50 MB of dependencies)
isn't worth it. Auth is the pod's ServiceAccount token; RBAC is in k8s/60-api.yaml.
"""
import json
import os
import ssl
import urllib.request

SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"


class Kube:
    def __init__(self):
        host = os.environ["KUBERNETES_SERVICE_HOST"]
        port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
        self.base = f"https://{host}:{port}"
        with open(f"{SA_DIR}/namespace") as f:
            self.ns = f.read().strip()
        self.ctx = ssl.create_default_context(cafile=f"{SA_DIR}/ca.crt")

    def _request(self, method, path, body=None, content_type="application/json"):
        with open(f"{SA_DIR}/token") as f:
            token = f.read().strip()  # re-read every call: projected tokens rotate
        req = urllib.request.Request(
            self.base + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json", "Content-Type": content_type},
        )
        with urllib.request.urlopen(req, context=self.ctx, timeout=5) as resp:
            return json.load(resp)

    def snapshot(self):
        """(deployments, statefulsets, pods) in our namespace."""
        apps = f"/apis/apps/v1/namespaces/{self.ns}"
        return (
            self._request("GET", f"{apps}/deployments")["items"],
            self._request("GET", f"{apps}/statefulsets")["items"],
            self._request("GET", f"/api/v1/namespaces/{self.ns}/pods")["items"],
        )

    def deployment(self, name):
        return self._request("GET", f"/apis/apps/v1/namespaces/{self.ns}/deployments/{name}")

    def scale(self, name, replicas):
        return self._request(
            "PATCH", f"/apis/apps/v1/namespaces/{self.ns}/deployments/{name}/scale",
            {"spec": {"replicas": replicas}}, content_type="application/merge-patch+json",
        )
