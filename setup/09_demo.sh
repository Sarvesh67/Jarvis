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
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
ensure_path
have cloudflared || fail "cloudflared not found. Install: brew install cloudflared"

TUNNEL=lattix-demo
HOSTNAME_=demo.lattix.co.in
COS_DIR="$JARVIS_ROOT/cosmetics"
PLIST=com.lattix.demo.plist

[ -f "$HOME/.cloudflared/cert.pem" ] || fail \
  "Not logged in. Run: cloudflared tunnel login  (pick lattix.co.in in the browser)"

if ! cloudflared tunnel list 2>/dev/null | awk '{print $2}' | grep -qx "$TUNNEL"; then
  log "Creating tunnel $TUNNEL..."
  cloudflared tunnel create "$TUNNEL"
fi
ID=$(cloudflared tunnel list 2>/dev/null | awk -v t="$TUNNEL" '$2==t {print $1}')
[ -n "$ID" ] || fail "could not determine the tunnel id for $TUNNEL"
log "Tunnel $TUNNEL is $ID"

log "Pointing $HOSTNAME_ at the tunnel..."
cloudflared tunnel route dns "$TUNNEL" "$HOSTNAME_" 2>&1 | grep -v "already exists" || true

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
cp "$JARVIS_ROOT/launchagents/$PLIST" "$HOME/Library/LaunchAgents/$PLIST"
launchctl unload "$HOME/Library/LaunchAgents/$PLIST" 2>/dev/null || true
launchctl load "$HOME/Library/LaunchAgents/$PLIST"

for i in $(seq 1 20); do
  curl -sf --max-time 3 http://127.0.0.1:8099/healthz >/dev/null && break
  [ "$i" = 20 ] && fail "app did not come up — see $COS_DIR/out/demo.log"
  sleep 1
done
log "App healthy on 127.0.0.1:8099"

log "Installing the tunnel as a login service..."
sudo cloudflared service install 2>/dev/null || \
  log "  (already installed, or run 'sudo cloudflared service install' yourself)"

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
