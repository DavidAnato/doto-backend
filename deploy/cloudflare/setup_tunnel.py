"""Create doto-api.await-tech.com via a Cloudflare tunnel. Does not print secrets."""
import json
import urllib.request
from pathlib import Path

ACCOUNT = "5233709f0fa81577637ea522fb408278"
cfg = Path.home() / "AppData/Roaming/xdg.config/.wrangler/config/default.toml"
token = ""
for line in cfg.read_text(encoding="utf-8").splitlines():
    if line.strip().startswith("oauth_token"):
        token = line.split("=", 1)[1].strip().strip('"')
        break
if not token:
    raise SystemExit("no oauth token")


def api(method, url, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            payload = json.loads(res.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        print(method, url, exc.code, raw[:500])
        return None
    print(method, url.split("?")[0], "ok", payload.get("success"))
    return payload


zones = api("GET", "https://api.cloudflare.com/client/v4/zones?name=await-tech.com")
if not zones or not zones.get("result"):
    raise SystemExit("zone lookup failed")
zone_id = zones["result"][0]["id"]
print("zone", zones["result"][0]["name"], zone_id)

existing = api(
    "GET",
    f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/cfd_tunnel?name=doto-api&is_deleted=false",
)
tunnel = None
if existing and existing.get("result"):
    tunnel = existing["result"][0]
    print("tunnel_exists", tunnel["id"], tunnel.get("status"))
else:
    created = api(
        "POST",
        f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/cfd_tunnel",
        {"name": "doto-api", "config_src": "cloudflare"},
    )
    if not created:
        raise SystemExit("tunnel create failed")
    tunnel = created["result"]
    print("tunnel_created", tunnel["id"])

token_res = api(
    "GET",
    f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/cfd_tunnel/{tunnel['id']}/token",
)
if not token_res:
    raise SystemExit("tunnel token failed")
tunnel_token = token_res["result"]
Path(r"F:\WORK\DOTO+\doto-backend\deploy\cloudflare\tunnel.token").write_text(tunnel_token, encoding="utf-8")
print("token_written", len(tunnel_token))

cfg_res = api(
    "PUT",
    f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/cfd_tunnel/{tunnel['id']}/configurations",
    {
        "config": {
            "ingress": [
                {
                    "hostname": "doto-api.await-tech.com",
                    "service": "http://127.0.0.1:8003",
                },
                {"service": "http_status:404"},
            ]
        }
    },
)
if not cfg_res:
    raise SystemExit("config failed")

dns = api(
    "POST",
    f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records",
    {
        "type": "CNAME",
        "name": "doto-api",
        "content": f"{tunnel['id']}.cfargotunnel.com",
        "proxied": True,
        "ttl": 1,
    },
)
if dns and dns.get("success"):
    print("dns_created", dns["result"]["name"])
else:
    print("dns_not_created")
    listed = api(
        "GET",
        f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?per_page=100",
    )
    domain = api(
        "POST",
        f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/workers/domains",
        {
            "hostname": "doto-api.await-tech.com",
            "service": "doto-api",
            "environment": "production",
            "zone_id": zone_id,
        },
    )
    if domain and domain.get("success"):
        print("worker_domain", domain["result"].get("hostname"), domain["result"].get("id"))
    if listed and listed.get("result"):
        for rec in listed["result"]:
            name = rec.get("name", "")
            if "await-tech.com" in name and (
                name.startswith("*") or "doto" in name or name.count(".") <= 2
            ):
                print("dns", rec.get("type"), name, "proxied", rec.get("proxied"))
