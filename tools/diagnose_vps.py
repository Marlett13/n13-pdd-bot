"""Read-only PDD diagnostics. Prints only allowlisted fields, never raw logs/env."""
import hashlib
import json
import subprocess
from pathlib import Path

CONTAINER = 'n13-pdd-pdd-1'


def run(args, stdin=None):
    return subprocess.run(args, input=stdin, text=True, capture_output=True, timeout=45)


def main():
    report = {'scope': CONTAINER, 'incident_utc': '2026-10-25T07:31:00Z'}
    result = run(['docker', 'inspect', CONTAINER])
    if result.returncode:
        report['container'] = 'unavailable'
        print(json.dumps(report))
        return 1
    obj = json.loads(result.stdout)[0]
    labels = obj['Config'].get('Labels', {})
    if labels.get('com.docker.compose.project') != 'n13-pdd' or labels.get('com.docker.compose.service') != 'pdd':
        raise SystemExit('Container scope mismatch')
    report['container'] = {k: obj['State'].get(k) for k in ('Status', 'Running', 'StartedAt', 'ExitCode', 'OOMKilled')}
    report['restart_count'] = obj['RestartCount']
    report['image_id'] = obj['Image']
    for window, options in [('incident', ['--since', '2026-10-25T07:26:00Z', '--until', '2026-10-25T07:37:00Z']), ('recent', ['--tail', '200'])]:
        logs = run(['docker', 'logs', *options, CONTAINER])
        text = logs.stdout + logs.stderr
        report[window + '_log_counts'] = {p: text.lower().count(p) for p in ('traceback', 'conflict', 'unauthorized', 'timeout', 'connection', 'unhandled', 'error')}
        report[window + '_log_lines'] = len(text.splitlines())
    # Runs inside existing PDD container: no token crosses the container boundary.
    probe = r'''
import asyncio, hashlib, json, os, sqlite3
from pathlib import Path
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
r = {'proxy_configured': bool(os.getenv('TELEGRAM_PROXY_URL'))}
for f in ('app/bot.py', 'app/storage.py'):
    p=Path(f)
    r[f+'_sha256']=hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
p=Path(os.getenv('DATABASE_PATH','data/n13-pdd.sqlite3'))
if p.exists():
    try:
        db=sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True)
        r['sqlite_quick_check_ok']=db.execute('PRAGMA quick_check').fetchall()==[('ok',)]
        r['answers_count']=db.execute('SELECT count(*) FROM answers').fetchone()[0]
        # Reconstruct legacy completed runs without exposing user identifiers/answers.
        users=db.execute('SELECT DISTINCT user_id FROM answers WHERE ticket=8').fetchall()
        completed=matching=0
        for (uid,) in users:
            rows=db.execute('SELECT ticket,position,correct FROM answers WHERE user_id=? ORDER BY rowid DESC',(uid,)).fetchall()
            for i,row in enumerate(rows):
                part=rows[i:i+20]
                if row[:2]==(8,19) and len(part)==20 and all(v[0]==8 and v[1]==19-j for j,v in enumerate(part)):
                    completed+=1
                    matching+=sum(not v[2] for v in part)==13
        r['ticket8_completed_runs']=completed
        r['ticket8_runs_with_13_errors']=matching
        db.close()
    except Exception as e:
        r['sqlite_error_type']=type(e).__name__
else:
    r['sqlite']='missing'
async def probe():
    token=os.getenv('BOT_TOKEN') or os.getenv('PDD_BOT_TOKEN')
    if not token:
        r['telegram']='missing_token'; return
    bot=Bot(token,session=AiohttpSession(proxy=os.getenv('TELEGRAM_PROXY_URL') or None),timeout=15)
    try:
        me=await bot.get_me()
        r['telegram_identity_matches']=me.username=='N13_PDD_Trainer_Bot'
        info=await bot.get_webhook_info()
        r['webhook_configured']=bool(info.url)
        r['pending_updates']=info.pending_update_count
    except Exception as e:
        r['telegram_error_type']=type(e).__name__
    finally:
        await bot.session.close()
asyncio.run(probe())
print(json.dumps(r))
'''
    result = run(['docker', 'exec', '-i', CONTAINER, 'python', '-'], probe)
    try:
        report['runtime'] = json.loads(result.stdout)
    except (ValueError, TypeError):
        report['runtime'] = {'probe_exit_code': result.returncode, 'result': 'failed_without_raw_output'}
    print(json.dumps(report, indent=2))
    return 0 if result.returncode == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
