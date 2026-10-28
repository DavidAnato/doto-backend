import json
import socket
import ssl
import time
import urllib.parse
import urllib.request

login = urllib.request.Request(
    "https://doto-api.await-tech.com/api/auth/login/",
    data=json.dumps({"username": "medecin", "password": "mdp123"}).encode(),
    headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
)
with urllib.request.urlopen(login, timeout=20) as res:
    token = json.loads(res.read().decode())["access"]
print("login_ok", len(token))

path = "/api/hub/events/?access=" + urllib.parse.quote(token)
req = (
    "GET " + path + " HTTP/1.1\r\n"
    "Host: doto-api.await-tech.com\r\n"
    "Accept: text/event-stream\r\n"
    "Origin: https://doto-hub.surge.sh\r\n"
    "User-Agent: Mozilla/5.0\r\n"
    "Connection: close\r\n\r\n"
).encode()
ctx = ssl.create_default_context()
raw = socket.create_connection(("doto-api.await-tech.com", 443), timeout=20)
tls = ctx.wrap_socket(raw, server_hostname="doto-api.await-tech.com")
tls.settimeout(20)
tls.sendall(req)
started = time.time()
buf = b""
try:
    while time.time() - started < 8:
        buf += tls.recv(256)
        if b"\r\n\r\n" in buf and b"\n\n" in buf.split(b"\r\n\r\n", 1)[-1]:
            break
except socket.timeout:
    pass
head, _, body = buf.partition(b"\r\n\r\n")
print("elapsed", round(time.time() - started, 2))
print("status_line", head.split(b"\r\n", 1)[0])
print("first_body", body[:180])
# Garder le flux 20 s pour voir si les keepalives passent le Worker.
try:
    while time.time() - started < 20:
        buf += tls.recv(512)
except socket.timeout:
    pass
events = buf.split(b"\r\n\r\n", 1)[-1].count(b"data:")
print("events_in_20s", events)
print("tail", buf[-180:])
