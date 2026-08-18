export default async function handler(request, response) {
  const upstream = process.env.RAILWAY_API_URL;
  if (!upstream) return response.status(500).json({ error: "RAILWAY_API_URL is not configured." });
  if (!["GET", "POST", "PATCH", "DELETE"].includes(request.method)) return response.status(405).end();
  if (Number(request.headers["content-length"] || 0) > 65536) return response.status(413).json({ error: "Request body is too large." });

  const path = Array.isArray(request.query.path) ? request.query.path.join("/") : request.query.path || "";
  if (!/^(sources|watchlists|alerts|notices|health)(\/|$)/.test(path)) return response.status(404).end();
  const target = new URL(`/${path}`, upstream);
  for (const [key, value] of Object.entries(request.query)) {
    if (key !== "path" && typeof value === "string") target.searchParams.set(key, value);
  }

  const headers = {};
  for (const key of ["authorization", "content-type", "accept"]) {
    if (request.headers[key]) headers[key] = request.headers[key];
  }
  const body = ["GET", "HEAD"].includes(request.method) ? undefined : JSON.stringify(request.body ?? {});
  try {
    const upstreamResponse = await fetch(target, { method: request.method, headers, body });
    const data = Buffer.from(await upstreamResponse.arrayBuffer());
    for (const key of ["content-type", "www-authenticate", "cache-control"]) {
      const value = upstreamResponse.headers.get(key);
      if (value) response.setHeader(key, value);
    }
    response.status(upstreamResponse.status).send(data);
  } catch {
    response.status(502).json({ error: "The Railway service is unavailable." });
  }
}
