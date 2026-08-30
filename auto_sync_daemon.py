import os
import sys
import time
import json
import sqlite3
import urllib.request
import urllib.error
from datetime import datetime

# Configuration
RENDER_PROXY_URL = os.getenv("RENDER_PROXY_URL", "https://kiro-proxy-hut7.onrender.com")
PROXY_API_KEY = os.getenv("PROXY_API_KEY", "kiro-master-key-2026")
# Sync every 30 minutes (well before the 60-minute token expiry)
SYNC_INTERVAL_SECONDS = int(os.getenv("SYNC_INTERVAL_SECONDS", "1800"))

def log(msg):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}", flush=True)

def extract_and_refresh_account(acc_num, sqlite_path):
    if not os.path.exists(sqlite_path):
        return None
    
    conn = sqlite3.connect(sqlite_path)
    c = conn.cursor()
    c.execute('SELECT key, value FROM auth_kv')
    kv = dict(c.fetchall())
    
    tok_raw = kv.get('kirocli:odic:token')
    reg_raw = kv.get('kirocli:odic:device-registration')
    if not tok_raw or not reg_raw:
        conn.close()
        return None
        
    tok = json.loads(tok_raw)
    reg = json.loads(reg_raw)
    
    # Request fresh live token from AWS SSO OIDC
    payload = json.dumps({
        'grantType': 'refresh_token',
        'clientId': reg.get('client_id'),
        'clientSecret': reg.get('client_secret'),
        'refreshToken': tok.get('refresh_token'),
    }).encode('utf-8')
    
    req = urllib.request.Request(
        'https://oidc.us-east-1.amazonaws.com/token',
        data=payload,
        headers={'Content-Type': 'application/json'}
    )
    
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        log(f"Account {acc_num} token refresh failed: {e}")
        conn.close()
        return None
        
    # Write back rotated tokens to SQLite backup file
    new_tok = dict(tok)
    new_tok['access_token'] = data['accessToken']
    new_tok['refresh_token'] = data['refreshToken']
    c.execute('UPDATE auth_kv SET value = ? WHERE key = ?', (json.dumps(new_tok), 'kirocli:odic:token'))
    conn.commit()
    conn.close()
    
    return {
        "name": f"Account {acc_num}",
        "type": "sso",
        "api_key": f"sk-kiro-acc-{acc_num}",
        "profileArn": "arn:aws:codewhisperer:us-east-1:403380691017:profile/9GW3YC77NQGM",
        "accessToken": data['accessToken'],
        "refreshToken": data['refreshToken'],
        "clientId": reg.get('client_id'),
        "clientSecret": reg.get('client_secret'),
        "region": "us-east-1",
        "enabled": True
    }

def sync_once():
    log(f"Running automated token refresh & sync cycle to {RENDER_PROXY_URL}...")
    local_app_data = os.environ.get('LOCALAPPDATA', '')
    accounts = []
    
    for acc in range(1, 9):
        sqlite_path = os.path.join(local_app_data, 'Kiro-Cli', f'data.sqlite3.acc{acc}.bak')
        acc_data = extract_and_refresh_account(acc, sqlite_path)
        if acc_data:
            accounts.append(acc_data)
            
    if not accounts:
        log("No accounts could be refreshed. Retrying on next interval.")
        return False
        
    # Save locally to credentials.json
    with open('credentials.json', 'w', encoding='utf-8') as f:
        json.dump(accounts, f, indent=2)
    
    # Push to Render proxy
    url = f"{RENDER_PROXY_URL.rstrip('/')}/api/sync-credentials"
    data = json.dumps(accounts).encode('utf-8')
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {PROXY_API_KEY}'
        }
    )
    
    try:
        with urllib.request.urlopen(req) as resp:
            res = json.loads(resp.read().decode('utf-8'))
            log(f"Successfully synced {len(accounts)} accounts to Render! Response: {res.get('message', 'OK')}")
            return True
    except urllib.error.HTTPError as e:
        log(f"HTTP error syncing to Render ({e.code}): {e.read().decode('utf-8')}")
        return False
    except Exception as e:
        log(f"Network error syncing to Render: {e}")
        return False

def main():
    log(f"Auto-Sync Daemon started. Interval: {SYNC_INTERVAL_SECONDS // 60} minutes.")
    while True:
        try:
            sync_once()
        except Exception as e:
            log(f"Unexpected error in sync cycle: {e}")
        time.sleep(SYNC_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()
