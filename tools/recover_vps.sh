#!/usr/bin/env bash
# Update only an existing N13 PDD install. Never remove/restore user data.
set -Eeuo pipefail
cd /opt/n13-pdd
[[ $(id -u) == 0 && -d .git && -s .env ]] || { echo 'Existing root-managed PDD checkout and .env required'; exit 1; }
[[ ${1:-} =~ ^[0-9a-f]{40}$ ]] || { echo 'Pass a verified full commit SHA'; exit 1; }
[[ $(git remote get-url origin) == https://github.com/Marlett13/n13-pdd-bot.git ]] || { echo 'Wrong repository'; exit 1; }
[[ -z $(git status --porcelain --untracked-files=no) ]] || { echo 'Tracked VPS edits found; reconcile before deployment'; exit 1; }
# Inspect ONLY the explicitly named PDD container, without printing environment.
docker inspect n13-pdd-pdd-1 | python3 -c '
import json,sys
c=json.load(sys.stdin)[0]; labels=c["Config"].get("Labels",{})
assert labels.get("com.docker.compose.project")=="n13-pdd"
assert labels.get("com.docker.compose.service")=="pdd"
assert any(m.get("Name")=="n13-pdd_pdd_data" and m["Destination"]=="/srv/n13/data" for m in c["Mounts"]), "Unexpected data mount; refusing to replace it"
assert c["State"]["Running"], "Running PDD container required for online SQLite backup"
'
stamp=$(date -u +%Y%m%dT%H%M%SZ)
rollback_tag="n13-pdd-rollback:$stamp"
old_image=$(docker inspect --format '{{.Image}}' n13-pdd-pdd-1)
docker tag "$old_image" "$rollback_tag"
# SQLite backup API supports a live writer; includes committed WAL contents.
docker exec -i n13-pdd-pdd-1 python - <<'PY'
import os, sqlite3
from datetime import datetime, timezone
from pathlib import Path
os.umask(0o077)
p=Path(os.environ.get('DATABASE_PATH','/srv/n13/data/n13-pdd.sqlite3')).resolve()
if not p.is_file():
    raise SystemExit('Existing SQLite file missing; refusing deployment')
source=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True)
if source.execute('PRAGMA quick_check').fetchall()!=[('ok',)]:
    raise SystemExit('SQLite integrity check failed; refusing deployment')
folder=p.parent/'backups'; folder.mkdir(mode=0o700,exist_ok=True)
backup=folder/('pre-recovery-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.sqlite3')
with sqlite3.connect(backup) as target:
    source.backup(target)
    assert target.execute('PRAGMA quick_check').fetchall()==[('ok',)]
source.close()
print('Consistent SQLite backup created inside persistent PDD volume; integrity OK')
PY
git fetch origin main
git merge-base --is-ancestor "$1" origin/main
# Never reset, checkout --force, or overwrite local modifications.
git merge --ff-only "$1"
docker network inspect n13_default >/dev/null
compose=(docker compose -p n13-pdd -f compose.yml -f compose.proxy.yml)
"${compose[@]}" config -q
python3 tools/diagnose_vps.py
"${compose[@]}" build pdd
rollback_file=$(mktemp /opt/n13-pdd/.rollback-XXXXXX.yml)
printf 'services:\n  pdd:\n    image: %s\n' "$rollback_tag" > "$rollback_file"
rollback() {
  echo 'Runtime check failed; restoring previous PDD image, keeping the live SQLite volume'
  "${compose[@]}" -f "$rollback_file" up -d --no-deps --no-build pdd
  rm -f "$rollback_file"
}
trap rollback ERR
"${compose[@]}" up -d --no-deps --no-build pdd
# Confirm new code, retained DB, proxy route and bot identity. No getUpdates.
expected_hash=$(sha256sum app/bot.py | cut -d' ' -f1)
for check in 1 2 3; do
  sleep 5
  docker exec -i -e PDD_EXPECTED_HASH="$expected_hash" n13-pdd-pdd-1 python - <<'PY'
import asyncio,hashlib,os,sqlite3
from pathlib import Path
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
async def check():
    assert hashlib.sha256(Path('app/bot.py').read_bytes()).hexdigest()==os.environ['PDD_EXPECTED_HASH']
    assert os.environ.get('TELEGRAM_PROXY_URL')=='http://mihomo:7890'
    p=Path(os.environ['DATABASE_PATH']).resolve()
    with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as db:
        assert db.execute('PRAGMA quick_check').fetchall()==[('ok',)]
    bot=Bot(os.environ['BOT_TOKEN'],session=AiohttpSession(proxy=os.environ['TELEGRAM_PROXY_URL']),timeout=15)
    try:
        assert (await bot.get_me()).username=='N13_PDD_Trainer_Bot'
        assert not (await bot.get_webhook_info()).url
    finally:
        await bot.session.close()
try:
    asyncio.run(check())
except Exception as e:
    raise SystemExit('PDD runtime check failed: '+type(e).__name__)
PY
done
trap - ERR
rm -f "$rollback_file"
python3 tools/diagnose_vps.py
echo 'PDD runtime checks passed; real /start, /review and navigation still require Telegram verification.'
