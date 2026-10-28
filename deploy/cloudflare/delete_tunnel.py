import urllib.request
from pathlib import Path

cfg = Path.home() / "AppData/Roaming/xdg.config/.wrangler/config/default.toml"
token = ""
for line in cfg.read_text(encoding="utf-8").splitlines():
    if line.strip().startswith("oauth_token"):
        token = line.split("=", 1)[1].strip().strip('"')
        break
req = urllib.request.Request(
    "https://api.cloudflare.com/client/v4/accounts/5233709f0fa81577637ea522fb408278/cfd_tunnel/8a05e4f4-44f8-44dc-95f9-6a37bdbfb390",
    method="DELETE",
    headers={"Authorization": f"Bearer {token}"},
)
try:
    with urllib.request.urlopen(req, timeout=30) as res:
        print("deleted", res.status)
except urllib.error.HTTPError as exc:
    raw = exc.read().decode(errors="replace")
    print("delete", exc.code, raw[:300])
