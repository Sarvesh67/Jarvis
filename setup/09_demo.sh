#!/usr/bin/env bash
# The public demo: the question app on 127.0.0.1:8099, published at demo.lattix.co.in
# through a Cloudflare tunnel. Idempotent — safe to re-run.
#
# The tunnel connects OUTBOUND from this Mac. Nothing listens on the LAN, no port is
# forwarded at the router, and the public hostname resolves to Cloudflare rather than to
# this machine's address. That is the whole reason to use a tunnel instead of opening a
# port: the demo being reachable never means this box is.
#
# TWO STEPS ARE YOURS, because they need accounts this script has no business holding:
#
#   1. lattix.co.in must use Cloudflare's nameservers. A `demo.lattix.co.in` record
#      pointing at a tunnel is only resolvable inside Cloudflare's DNS — *.cfargotunnel.com
#      does not resolve publicly, so a CNAME from GoDaddy cannot reach it. Add the zone at
#      dash.cloudflare.com, let it import the two existing records (apex A -> Vercel,
#      www CNAME -> apex), THEN change the nameservers at GoDaddy. The waitlist site keeps
#      serving from Vercel throughout; Cloudflare is only answering DNS for it.
#   2. `cloudflared tunnel login` opens a browser for you to authorise this machine.
#
# Run it WITHOUT sudo. Every service it installs on macOS is a per-user launch agent,
# so sudo would install the tunnel against root's home and lose the config written here.
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
ensure_path
have cloudflared || fail "cloudflared not found. Install: brew install cloudflared"

TUNNEL=lattix-demo
HOSTNAME_=demo.lattix.co.in
COS_DIR="$JARVIS_ROOT/cosmetics"
PLIST=com.lattix.demo.plist

[ -f "$HOME/.cloudflared/cert.pem" ] || fail \
  "Not logged in. Run: cloudflared tunnel login  (pick lattix.co.in in the browser)"

# Read the id out of JSON rather than off a column position — `tunnel list`'s table is
# for humans and its widths shift with the longest name in it.
#
# Two shapes to survive, both learned the hard way:
#
#   * With no tunnels, the command prints the literal `null`, not `[]` — and that is the
#     state every first run is in.
#   * A LIVE tunnel carries `"deleted_at": "0001-01-01T00:00:00Z"`, Go's zero time, not
#     null and not absent. Testing the field for truthiness marks every live tunnel as
#     deleted, which is worse than not filtering at all: the tunnel gets created and then
#     cannot be found, on every run, forever.
tunnel_id() {
  cloudflared tunnel list --output json 2>/dev/null \
    | "$COS_DIR/.venv/bin/python" -c '
import json, sys
def live(t):
    d = (t.get("deleted_at") or "").strip()
    return not d or d.startswith("0001-01-01")
raw = sys.stdin.read().strip()
rows = json.loads(raw) if raw else None
print(next((t["id"] for t in (rows or [])
            if t.get("name") == sys.argv[1] and live(t)), ""))
' "$TUNNEL"
}

ID=$(tunnel_id)
if [ -z "$ID" ]; then
  log "Creating tunnel $TUNNEL..."
  # Let this one speak for itself. A create that fails because the login has no zone, or
  # because a tunnel by this name already exists under a different account, says so far
  # better than anything this script could infer from an empty id afterwards.
  cloudflared tunnel create "$TUNNEL" || fail "cloudflared could not create the tunnel (see above)"
  ID=$(tunnel_id)
fi
[ -n "$ID" ] || fail "created the tunnel but could not read its id back — \
run 'cloudflared tunnel list' and check you are logged in to the right account"
log "Tunnel $TUNNEL is $ID"

log "Pointing $HOSTNAME_ at the tunnel..."
# Re-running is normal, and "already exists" is the success case on a second run — but
# piping straight into grep would hide every OTHER failure behind grep's exit code.
if ! ROUTE_OUT=$(cloudflared tunnel route dns "$TUNNEL" "$HOSTNAME_" 2>&1); then
  case "$ROUTE_OUT" in
    *"already exists"*|*"already configured"*)
      log "  $HOSTNAME_ already points at this tunnel" ;;
    *) printf '%s\n' "$ROUTE_OUT" >&2
       fail "could not create the DNS record for $HOSTNAME_ — is lattix.co.in active on \
this Cloudflare account yet? (dig +short NS lattix.co.in)" ;;
  esac
fi

mkdir -p "$HOME/.cloudflared"
cat > "$HOME/.cloudflared/config.yml" <<YAML
tunnel: $ID
credentials-file: $HOME/.cloudflared/$ID.json

ingress:
  - hostname: $HOSTNAME_
    service: http://127.0.0.1:8099
  # Every tunnel needs a terminal rule. Anything that is not the demo hostname is
  # refused here rather than quietly reaching some other service on this machine.
  - service: http_status:404
YAML
log "Wrote ~/.cloudflared/config.yml"

log "Installing the app launch agent..."
mkdir -p "$HOME/Library/LaunchAgents"
cp "$JARVIS_ROOT/launchagents/$PLIST" "$HOME/Library/LaunchAgents/$PLIST"
launchctl unload "$HOME/Library/LaunchAgents/$PLIST" 2>/dev/null || true
# A hand-started `python -m serve.app` from testing keeps the port, the agent then fails
# to bind, KeepAlive restarts it in a loop — and the health check below passes anyway,
# because something IS answering on 8099. Clear the port first so the check proves the
# agent works rather than that the stray process is still alive.
pkill -f "serve\.app" 2>/dev/null && sleep 1
launchctl load "$HOME/Library/LaunchAgents/$PLIST"

for i in $(seq 1 20); do
  curl -sf --max-time 3 http://127.0.0.1:8099/healthz >/dev/null && break
  [ "$i" = 20 ] && fail "app did not come up — see $COS_DIR/out/demo.log"
  sleep 1
done
launchctl list com.lattix.demo >/dev/null 2>&1 \
  || fail "something answers on 8099 but launchd is not managing it — the demo would not \
survive a reboot. Check: launchctl list com.lattix.demo"
log "App healthy on 127.0.0.1:8099, managed by launchd"

# NOT sudo. `cloudflared service install` installs a USER launch agent on macOS, so under
# sudo it installs against root's home and then looks for /var/root/.cloudflared/config.yml,
# which is not the file written above.
log "Installing the tunnel as a login service..."
if ! SVC_OUT=$(cloudflared service install 2>&1); then
  case "$SVC_OUT" in
    *"already exists"*|*"already installed"*)
      log "  tunnel service already installed" ;;
    *) printf '%s\n' "$SVC_OUT" >&2
       fail "could not install the tunnel service (see above)" ;;
  esac
fi

# Nothing above proves the tunnel actually CONNECTED, and a setup script that prints a URL
# it never tried is how an afternoon disappears. Ask Cloudflare for the hostname and see
# what comes back: 530 means no tunnel is connected, 502/504 means the tunnel is up but
# the app is not answering, and anything else means the path works end to end.
log "Checking $HOSTNAME_ end to end..."
CODE=""
for i in $(seq 1 20); do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "https://$HOSTNAME_/healthz" || echo 000)
  case "$CODE" in
    530|000|502|504) sleep 3 ;;
    *) break ;;
  esac
done
case "$CODE" in
  530|000) fail "$HOSTNAME_ is not reaching a connected tunnel (HTTP $CODE). Check: \
cloudflared tunnel info $TUNNEL" ;;
  502|504) fail "the tunnel is connected but the app is not answering through it (HTTP \
$CODE). Check the ingress block in ~/.cloudflared/config.yml points at port 8099." ;;
  200)     log "  HTTP 200 — live, and not yet gated. Add Access before sharing the link." ;;
  *)       log "  HTTP $CODE — live (a redirect or 403 here means Cloudflare Access is \
already in front of it, which is what you want)." ;;
esac

cat <<TXT

  Done. https://$HOSTNAME_

  Before sharing the link, put Cloudflare Access in front of it:
    Zero Trust -> Access -> Applications -> Add -> Self-hosted
      domain      $HOSTNAME_
      policy      Allow / Emails  -> your address and your co-founder's
    Anyone else gets a Cloudflare login page and never reaches the app.

  Health:   curl https://$HOSTNAME_/healthz
  Logs:     tail -f $COS_DIR/out/demo.log
  Restart:  launchctl kickstart -k gui/\$(id -u)/com.lattix.demo
TXT
