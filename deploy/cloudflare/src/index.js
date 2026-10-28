export default {
  async fetch(request) {
    const incoming = new URL(request.url);
    const target = new URL(request.url);
    target.protocol = "http:";
    target.hostname = "await-origin.fotoce.app";
    target.port = "8003";

    const headers = new Headers(request.headers);
    headers.set("Host", incoming.hostname);
    headers.set("X-Forwarded-Proto", "https");
    headers.set("X-Forwarded-Host", incoming.hostname);
    const ip = request.headers.get("CF-Connecting-IP");
    if (ip) headers.set("X-Forwarded-For", ip);
    headers.delete("cf-connecting-ip");
    headers.delete("cf-ray");
    headers.delete("cf-visitor");

    return fetch(target.toString(), {
      method: request.method,
      headers,
      body: request.body,
      redirect: "manual",
    });
  },
};
