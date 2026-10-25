#!/usr/bin/env bash
# Recover a PDD code-read crash loop without changing the database volume.
set -Eeuo pipefail
cd /opt/n13-pdd
[[ $(id -u) == 0 && -d .git && -s .env ]] || { echo 'Existing root-managed PDD checkout and .env required'; exit 1; }
[[ ${1:-} =~ ^[0-9a-f]{40}$ ]] || { echo 'Pass a verified full commit SHA'; exit 1; }
[[ $(git remote get-url origin) == https://github.com/Marlett13/n13-pdd-bot.git ]] || { echo 'Wrong repository'; exit 1; }
[[ -z $(git status --porcelain --untracked-files=no) ]] || { echo 'Tracked local changes found; nothing overwritten. Reconcile first.'; exit 1; }
docker inspect n13-pdd-pdd-1 | python3 -c '
import json,sys
c=json.load(sys.stdin)[0]; labels=c["Config"].get("Labels",{})
assert labels.get("com.docker.compose.project")=="n13-pdd", "Wrong project"
assert labels.get("com.docker.compose.service")=="pdd", "Wrong service"
assert any(m.get("Name")=="n13-pdd_pdd_data" and m["Destination"]=="/srv/n13/data" for m in c["Mounts"]), "Unexpected database mount; stop for investigation"
assert not any(m["Destination"] in ("/srv/n13", "/srv/n13/app", "/srv/n13/app/bot.py", "/srv/n13/tools") for m in c["Mounts"]), "Code mount detected; image permissions cannot repair this layout"
'
docker network inspect n13_default >/dev/null
git fetch origin main
git merge-base --is-ancestor "$1" origin/main
git merge --ff-only "$1"
compose=(docker compose -p n13-pdd -f compose.yml -f compose.proxy.yml)
"${compose[@]}" config -q
old_image=$(docker inspect --format '{{.Image}}' n13-pdd-pdd-1)
stamp=$(date -u +%Y%m%dT%H%M%SZ)
rollback_tag="n13-pdd-permissions-rollback:$stamp"
docker tag "$old_image" "$rollback_tag"
# Build/permission test first: no running container has been stopped yet.
"${compose[@]}" build pdd
# Use the default image tag built for the fixed project/service.
image=n13-pdd-pdd
docker run --rm --pull never --network none --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges:true "$image" python -c 'import os; assert os.getuid()==10001; import app.bot; import tools.prepare_data; print("Unprivileged code-read test passed")'
umask 077
install -d -m 700 /opt/n13-pdd/backups
rollback_file=$(mktemp /opt/n13-pdd/.permissions-rollback-XXXXXX.yml)
printf 'services:\n  pdd:\n    image: %s\n' "$rollback_tag" > "$rollback_file"
rollback() {
  echo 'Recovery failed; keeping the database and restoring the previous PDD image'
  "${compose[@]}" -f "$rollback_file" up -d --no-deps --no-build pdd
  rm -f "$rollback_file"
}
trap rollback ERR
docker stop -t 30 n13-pdd-pdd-1 >/dev/null
# A stopped process cannot mutate SQLite/WAL; archive the entire existing data dir.
backup="/opt/n13-pdd/backups/pre-code-permission-repair-$stamp.tar"
docker cp n13-pdd-pdd-1:/srv/n13/data - > "$backup"
tar -tf "$backup" >/dev/null
[[ -s "$backup" ]] || { echo 'Backup is empty'; false; }
echo 'Stopped PDD data directory archived; original volume and .env retained'
"${compose[@]}" up -d --no-deps --no-build pdd
for check in 1 2 3; do
  sleep 5
  docker exec n13-pdd-pdd-1 python -c 'import app.bot; print("PDD code readable")'
done
# Read-only DB/Telegram probes: getMe is not an end-to-end command test.
report="/opt/n13-pdd/backups/post-code-permission-repair-$stamp.json"
python3 tools/diagnose_vps.py > "$report"
python3 - "$report" <<'PY'
import json,sys
r=json.load(open(sys.argv[1])); rt=r.get('runtime',{})
print(json.dumps({'container':r.get('container'), 'restart_count':r.get('restart_count'),
                 'runtime':rt, 'code_matches_checkout':r.get('code_matches_checkout')},indent=2))
assert r['container']['Status']=='running', 'Container is not stable'
assert rt.get('sqlite_quick_check_ok'), 'SQLite integrity not confirmed'
assert rt.get('telegram_identity_matches'), 'Telegram connection not confirmed'
assert rt.get('proxy_configured'), 'Proxy configuration missing'
assert rt.get('webhook_configured') is False, 'Webhook blocks polling'
assert all(r.get('code_matches_checkout',{}).values()), 'Container code mismatch'
PY
trap - ERR
rm -f "$rollback_file"
echo 'PDD runtime checks passed. Now verify /start, /review and mistake navigation in Telegram.'
