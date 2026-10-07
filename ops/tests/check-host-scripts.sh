#!/bin/bash
# Runs the generated setup scripts against a fake `pveum`, in a throwaway container, as if PASTED into an interactive
# root shell. Usage (from the repo root, on a host with Docker):
#   docker run --rm -u "$(id -u):$(id -g)" -v "$PWD":/repo -w /repo node:22-alpine node --experimental-strip-types web/scripts/check-host-scripts.mjs /repo/.scripts
#   docker run --rm -v "$PWD":/repo -w /repo python:3.12-slim bash ops/tests/check-host-scripts.sh /repo/.scripts
set -uo pipefail
D="${1:-/tmp/pyxie-scripts}"
STATE=/tmp/pve-state; mkdir -p "$STATE" /stub
cat > /stub/pveum <<'STUB'
#!/bin/bash
# Minimal fake of the pveum calls the script makes. State lives in $STATE as plain files.
S=${STATE:-/tmp/pve-state}; mkdir -p "$S"; cmd="$1 $2"; shift 2 2>/dev/null || true
case "$cmd" in
  "user add") [ -e "$S/user.$1" ] && { echo "user already exists" >&2; exit 255; }; touch "$S/user.$1" ;;
  "user token")
    sub="$1"; shift
    case "$sub" in
      list) u="$1"; printf '['; first=1; for f in "$S"/token."$u".*; do [ -e "$f" ] || continue; t="${f##*.}"; [ $first = 1 ] || printf ','; first=0; printf '{"tokenid":"%s","privsep":1}' "$t"; done; printf ']\n' ;;
      add) u="$1"; t="$2"; [ -n "${STUB_FAIL_TOKEN_ADD:-}" ] && { echo "unable to create token" >&2; exit 255; }
           [ -e "$S/token.$u.$t" ] && { echo "token already exists" >&2; exit 255; }; touch "$S/token.$u.$t"
           echo "full-tokenid: $u!$t"; echo "value: SECRET-$u-$t-$RANDOM" ;;
    esac ;;
  "role add") [ -e "$S/role.$1" ] && exit 255; touch "$S/role.$1" ;;
  "role modify") exit 0 ;;
  "acl modify") echo "acl $*" >> "$S/acl.log" ;;
  *) echo "stub: unhandled $cmd" >&2; exit 2 ;;
esac
STUB
chmod +x /stub/pveum; export PATH=/stub:$PATH STATE
ok=0; bad=0
check() { if eval "$2"; then echo "PASS  $1"; ok=$((ok+1)); else echo "FAIL  $1"; bad=$((bad+1)); fi; }

for f in "$D"/*.sh; do check "bash -n $(basename "$f")" "bash -n '$f'"; done

# 1. pasted into an interactive shell: the session must survive and finish, secrets saved root-only
rm -rf "$STATE"/*; rm -f /root/pyxie-tokens.txt
OUT=$( { cat "$D/pve-both.sh"; echo 'echo SESSION-STILL-ALIVE'; } | bash -i 2>&1 )
check "paste into interactive shell: session survives" '[[ "$OUT" == *SESSION-STILL-ALIVE* ]]'
check "both tokens created" '[ -e "$STATE/token.pyxie-ro@pve.inventory" ] && [ -e "$STATE/token.pyxie-admin@pve.maintenance" ]'
check "role PyXieAdmin created" '[ -e "$STATE/role.PyXieAdmin" ]'
check "token file saved with secrets" 'grep -q "value: SECRET-pyxie-ro@pve-inventory" /root/pyxie-tokens.txt && grep -q "value: SECRET-pyxie-admin@pve-maintenance" /root/pyxie-tokens.txt'
check "token file is root-only (0600)" '[ "$(stat -c %a /root/pyxie-tokens.txt)" = "600" ]'
check "grants applied for user AND token (4 acl lines)" '[ "$(grep -c "^acl" "$STATE/acl.log")" -eq 4 ]'
check "script says DONE" 'grep -q "^DONE\." <<<"$OUT"'
check "success ends with the copy-then-delete instructions" '[[ "$OUT" == *"Copy them into PyXie, then delete the file"* ]]'

# 2. re-run: idempotent (the case the old have_token got wrong), no new secrets
OUT2=$( { cat "$D/pve-both.sh"; echo 'echo SESSION-STILL-ALIVE'; } | bash -i 2>&1 )
check "re-run: session survives and finishes" '[[ "$OUT2" == *SESSION-STILL-ALIVE* ]] && grep -q "^DONE\." <<<"$OUT2"'
check "re-run: reports existing tokens, makes no new secret" '[[ "$OUT2" == *"already exists"* && "$OUT2" != *"value: SECRET"* ]]'

# 3. a failing command: names the line, does NOT close the pasted session
rm -rf "$STATE"/*; rm -f /root/pyxie-tokens.txt
OUT3=$( { cat "$D/pve-both.sh"; echo 'echo SESSION-STILL-ALIVE'; } | STUB_FAIL_TOKEN_ADD=1 bash -i 2>&1 )
check "failure: prints STOPPED at line" '[[ "$OUT3" == *"STOPPED at line"* ]]'
check "failure: pasted session survives" '[[ "$OUT3" == *SESSION-STILL-ALIVE* ]]'
check "failure: no false DONE" '! grep -q "^DONE\." <<<"$OUT3"'
rm -rf "$STATE"/*; rm -f /root/pyxie-tokens.txt
OUT3B=$(STUB_FAIL_TOKEN_ADD=1 bash "$D/pve-both.sh" 2>&1)   # run as a file: nothing is echoed back, so we read the script's own words
check "failure: says it stopped, not 'copy your secrets'" '[[ "$OUT3B" == *"The script stopped"* && "$OUT3B" != *"Copy them into PyXie"* ]]'

# 4. separate console token variant runs
rm -rf "$STATE"/*; OUT4=$( { cat "$D/pve-separate-console.sh"; echo 'echo SESSION-STILL-ALIVE'; } | bash -i 2>&1 )
check "separate console token created" '[ -e "$STATE/token.pyxie-console@pve.console" ] && [[ "$OUT4" == *SESSION-STILL-ALIVE* ]]'

# 5. host script: pasted, with the download failing (no such host): names the line and the session survives
OUT5=$( { cat "$D/host-install.sh"; echo 'echo SESSION-STILL-ALIVE'; } | bash -i 2>&1 )
check "host script failure: STOPPED at line + session survives" '[[ "$OUT5" == *"STOPPED at line"* && "$OUT5" == *SESSION-STILL-ALIVE* ]]'
check "host script: temp file cleaned up" '[ -z "$(ls /tmp/pyxie-host-kit.* 2>/dev/null)" ]'

echo; echo "$ok passed, $bad failed"; [ "$bad" -eq 0 ]
