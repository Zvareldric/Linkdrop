# Public Access with Cloudflare Tunnel

**English** · [Bahasa Indonesia](CLOUDFLARE_TUNNEL.id.md)

Cloudflare Tunnel publishes Linkdrop from your own computer at a public HTTPS address. You do not need a VPS, a static IP, or open router ports. The `cloudflared` connector opens an outbound connection to Cloudflare, and Cloudflare forwards visitors through it to Linkdrop on `localhost`.

Running Linkdrop at home has another advantage: platforms such as YouTube block data-center IP addresses far more often than home connections. The trade-off is that Linkdrop is only reachable while your computer is on and awake.

> **Before you go public.** Anyone who has the address can use your bandwidth, CPU, and disk. Always set `ACCESS_PASSWORD`, share it only with people you trust, and use Linkdrop only for media you own or are permitted to download. Cloudflare's terms of service restrict some uses of its network for serving video and large files, so read the current terms before you open Linkdrop to many people.

## Requirements

- Linkdrop running with Docker (see [Installation](INSTALLATION.md)) or natively
- A free [Cloudflare account](https://dash.cloudflare.com/sign-up)
- For a permanent address: a domain whose DNS is managed by Cloudflare. A Quick Tunnel (step 3) works without a domain, for testing only.

## 1. Run Linkdrop with access protection

Bind Linkdrop to `127.0.0.1` so that only `cloudflared` on the same computer can reach it.

Windows PowerShell:

```powershell
$secret = [Convert]::ToHexString((1..32 | ForEach-Object { Get-Random -Maximum 256 }))
docker rm --force linkdrop 2>$null
docker run --detach --name linkdrop --restart unless-stopped `
  --publish 127.0.0.1:5050:8080 `
  --env "DOWNLOAD_TOKEN_SECRET=$secret" `
  --env "ACCESS_PASSWORD=replace-with-a-long-password" `
  --env TRUSTED_PROXY_HEADER=CF-Connecting-IP `
  --env MAX_MEDIA_BYTES=2147483648 `
  linkdrop
```

macOS and Linux:

```bash
docker rm --force linkdrop 2>/dev/null
docker run --detach --name linkdrop --restart unless-stopped \
  --publish 127.0.0.1:5050:8080 \
  --env DOWNLOAD_TOKEN_SECRET="$(openssl rand -hex 32)" \
  --env ACCESS_PASSWORD="replace-with-a-long-password" \
  --env TRUSTED_PROXY_HEADER=CF-Connecting-IP \
  --env MAX_MEDIA_BYTES=2147483648 \
  linkdrop
```

- `ACCESS_PASSWORD` turns on the browser sign-in prompt. The username is `linkdrop` unless you set `ACCESS_USERNAME`.
- `TRUSTED_PROXY_HEADER=CF-Connecting-IP` lets the rate limiter see each visitor's real address instead of `cloudflared`'s local address. Set it only when Linkdrop is reachable exclusively through Cloudflare, as above; otherwise visitors could forge the header.

Open [http://127.0.0.1:5050](http://127.0.0.1:5050) and confirm that the browser asks for the username and password.

## 2. Install cloudflared

Windows (PowerShell):

```powershell
winget install --id Cloudflare.cloudflared
```

macOS:

```bash
brew install cloudflared
```

Linux: follow the package instructions on Cloudflare's [cloudflared downloads page](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/).

## 3. Try a Quick Tunnel (optional, no domain)

```bash
cloudflared tunnel --url http://localhost:5050
```

`cloudflared` prints a random `https://….trycloudflare.com` address. The address changes every time the command starts and stops working when you close the terminal, so use it only to test the setup.

## 4. Create a permanent tunnel

1. In the [Cloudflare dashboard](https://dash.cloudflare.com/), open **Zero Trust**, then the **Tunnels** page under networks. Cloudflare occasionally renames these menus; Cloudflare's tunnel documentation always links to the current location.
2. Create a tunnel of type **Cloudflared** and name it `linkdrop`.
3. Choose your operating system. Cloudflare shows a command of the form `cloudflared service install <token>`. Run it in an administrator PowerShell (Windows) or with `sudo` (macOS/Linux). The connector is installed as a service and starts with the computer.
4. Add a **public hostname**:
   - Subdomain: `linkdrop`, domain: your domain
   - Service type: `HTTP`, URL: `localhost:5050`
5. Save. After a few seconds, `https://linkdrop.your-domain` serves Linkdrop.

The token in step 3 grants control of the tunnel. Treat it like a password and never commit it to Git.

## 5. Verify

- `https://linkdrop.your-domain/api/health` returns `"ok": true` without a password.
- `https://linkdrop.your-domain/` asks for the username and password.
- Analyze one link you are allowed to download and finish the download.

## Stronger sign-in with Cloudflare Access (optional)

Instead of sharing one password, you can put a Cloudflare Access application in front of the hostname so that visitors sign in with an email one-time code or an identity provider. Cloudflare Access has a free tier for small teams. Keep `ACCESS_PASSWORD` set as a second layer.

## Operating notes

- **Keep the computer awake.** Disable sleep while Linkdrop should be online. Docker's `--restart unless-stopped` and the `cloudflared` service bring everything back after a reboot.
- **Bandwidth.** Each download travels from the source to your computer and then from your computer to the visitor, so your connection's upload speed limits how fast visitors receive files.
- **Long downloads.** Cloudflare closes requests whose origin sends nothing for about 100 seconds. Linkdrop's interface uses a progress stream that sends status updates immediately, so long downloads and conversions stay connected.
- **Rate limits.** By default each visitor can analyze 30 links per minute and start 20 downloads per 10 minutes. Adjust with `RATE_LIMIT_INFO` and `RATE_LIMIT_DOWNLOAD` (`count/seconds`, or `off`). Ten wrong passwords block that address for 10 minutes.
- **Outbound proxy.** Linkdrop blocks media requests that resolve to private or local network addresses. If you configure an outbound HTTP proxy, the proxy resolves hostnames instead, so the proxy must enforce the same restriction.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `502 Bad Gateway` or error 1033 | Linkdrop is not running or the tunnel points to the wrong port. Check `docker ps` and that the service URL is `localhost:5050`. |
| The password prompt keeps returning | The username or password is wrong. The default username is `linkdrop`. |
| `429 Too Many Requests` | A rate limit or the failed-sign-in lock was reached. Wait for the time shown in the message. |
| Every visitor shares one rate limit | `TRUSTED_PROXY_HEADER=CF-Connecting-IP` is missing. |
| YouTube asks to confirm you are not a bot | The platform is limiting your IP address. Try again later or configure cookies as described in [Deployment](DEPLOYMENT.md#cookies). |
