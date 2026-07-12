#!/usr/bin/env python3
"""
Cloudflare Workers AI Auto-Signup
Camoufox (anti-detect Firefox) + isolated Turnstile solver + mail.tm
No 2Captcha needed — Turnstile solved via isolated page approach from Theyka.
"""

import sys
import re
import time
import json
import random
import string
import urllib.request
import urllib.parse
from typing import Optional

# ── Mail.tm Temp Email API ───────────────────────────────────────────────────

MAIL_TM_BASE = "https://api.mail.tm"

def mail_tm_get_domain():
    req = urllib.request.Request(f"{MAIL_TM_BASE}/domains", headers={"User-Agent": "Mozilla/5.0"})
    data = json.loads(urllib.request.urlopen(req, timeout=15).read().decode())
    domains = data.get("hydra:member", [])
    if not domains:
        raise Exception("No mail.tm domains available")
    return domains[0]["domain"]

def mail_tm_create_account(email: str, password: str):
    payload = json.dumps({"address": email, "password": password}).encode()
    req = urllib.request.Request(
        f"{MAIL_TM_BASE}/accounts",
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
        method="POST"
    )
    resp = urllib.request.urlopen(req, timeout=15)
    return json.loads(resp.read().decode())

def mail_tm_get_token(email: str, password: str):
    payload = json.dumps({"address": email, "password": password}).encode()
    req = urllib.request.Request(
        f"{MAIL_TM_BASE}/token",
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
        method="POST"
    )
    resp = urllib.request.urlopen(req, timeout=15)
    data = json.loads(resp.read().decode())
    return data.get("token")

def mail_tm_wait_verify_link(email: str, password: str, timeout: int = 180) -> Optional[str]:
    """Poll mail.tm inbox for Cloudflare verification link"""
    try:
        token = mail_tm_get_token(email, password)
    except Exception as e:
        print(json.dumps({"step": f"Mail.tm auth error: {e}"}), flush=True)
        return None

    deadline = time.time() + timeout
    seen_ids = set()
    print(json.dumps({"step": f"Menunggu email verifikasi Cloudflare ({email})..."}), flush=True)

    while time.time() < deadline:
        try:
            req = urllib.request.Request(
                f"{MAIL_TM_BASE}/messages",
                headers={"Authorization": f"Bearer {token}", "User-Agent": "Mozilla/5.0"}
            )
            data = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
            messages = data.get("hydra:member", [])

            for msg in messages:
                mid = msg.get("id", "")
                if mid in seen_ids:
                    continue
                seen_ids.add(mid)

                subject = msg.get("subject", "")
                print(json.dumps({"step": f"Email masuk: {subject}"}), flush=True)

                if "cloudflare" in subject.lower() or "verify" in subject.lower() or "account" in subject.lower():
                    req2 = urllib.request.Request(
                        f"{MAIL_TM_BASE}/messages/{mid}",
                        headers={"Authorization": f"Bearer {token}", "User-Agent": "Mozilla/5.0"}
                    )
                    full_msg = json.loads(urllib.request.urlopen(req2, timeout=10).read().decode())
                    html = full_msg.get("html", [""])[0] if isinstance(full_msg.get("html"), list) else full_msg.get("html", "")

                    # Try direct verify link
                    match = re.search(r'https://dash\.cloudflare\.com/[^\s"\'<>]+verify[^\s"\'<>]*', html)
                    if match:
                        link = match.group(0).replace("&amp;", "&")
                        print(json.dumps({"step": "Link verifikasi ditemukan!"}), flush=True)
                        return link

                    # Any CF link
                    match = re.search(r'(https://[^\s"\'<>]*cloudflare[^\s"\'<>]*)', html)
                    if match:
                        link = match.group(1).replace("&amp;", "&")
                        print(json.dumps({"step": "Link Cloudflare ditemukan di email!"}), flush=True)
                        return link
        except Exception as e:
            print(json.dumps({"step": f"Mail.tm poll error: {e}"}), flush=True)

        time.sleep(5)

    print(json.dumps({"step": f"Timeout menunggu email ({timeout}s)"}), flush=True)
    return None

# ── Turnstile Solver (Isolated Page Approach — from Theyka) ──────────────────

TURNSTILE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Turnstile Solver</title>
    <script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async></script>
</head>
<body>
    <!-- cf turnstile -->
</body>
</html>"""


def extract_sitekey(page) -> Optional[str]:
    """Extract Turnstile sitekey from page via multiple methods"""
    try:
        # Method 1: data-sitekey
        el = page.query_selector('[data-sitekey]')
        if el:
            sk = el.get_attribute('data-sitekey')
            if sk:
                return sk

        # Method 2: page source
        content = page.content()
        for pattern in [
            r'data-sitekey=["\']([0-9A-Za-z_-]+)',
            r'sitekey["\s:]+["\']?([0-9A-Za-z_-]+)',
        ]:
            match = re.search(pattern, content)
            if match:
                return match.group(1)

        # Method 3: iframe src
        iframe = page.query_selector('iframe[src*="challenges.cloudflare.com"]')
        if iframe:
            src = iframe.get_attribute('src')
            match = re.search(r'sitekey=([0-9A-Za-z_-]+)', src)
            if match:
                return match.group(1)
    except Exception as e:
        print(json.dumps({"step": f"Sitekey extraction error: {e}"}), flush=True)
    return None


def solve_turnstile_isolated(context, url: str, sitekey: str) -> Optional[str]:
    """
    Solve Turnstile in an isolated page (FREE — no 2Captcha).
    Based on Theyka/Turnstile-Solver isolated page approach.
    """
    print(json.dumps({"step": f"Solve Turnstile (isolated, sitekey={sitekey[:25]}...)"}), flush=True)
    solver_page = context.new_page()

    try:
        turnstile_div = f'<div class="cf-turnstile" data-sitekey="{sitekey}"></div>'
        page_data = TURNSTILE_HTML.replace("<!-- cf turnstile -->", turnstile_div)

        url_with_slash = url + "/" if not url.endswith("/") else url
        solver_page.route(url_with_slash, lambda route: route.fulfill(body=page_data, status=200))
        solver_page.goto(url_with_slash)

        for attempt in range(20):
            solver_page.wait_for_timeout(2000)

            # Try clicking the checkbox
            try:
                td = solver_page.query_selector('.cf-turnstile')
                if td:
                    td.click()
            except:
                pass

            # Check for token
            try:
                token_val = solver_page.input_value('[name="cf-turnstile-response"]')
                if token_val and len(token_val) > 10:
                    print(json.dumps({"step": f"Turnstile solved! ({(attempt+1)*2}s)"}), flush=True)
                    return token_val
            except:
                pass

            if attempt < 10:
                print(json.dumps({"step": f"Turnstile solving... ({(attempt+1)*2}s)"}), flush=True)

        print(json.dumps({"step": "Turnstile timeout (40s)"}), flush=True)
        return None
    except Exception as e:
        print(json.dumps({"step": f"Turnstile solver error: {e}"}), flush=True)
        return None
    finally:
        try:
            solver_page.close()
        except:
            pass


def inject_turnstile_token(page, token: str):
    """Inject solved Turnstile token into the page"""
    page.evaluate("""(token) => {
        const names = ['cf-turnstile-response', 'cf_challenge_response'];
        for (const n of names) {
            const el = document.querySelector(`[name="${n}"]`);
            if (el) { el.value = token; return; }
        }
        const h = document.createElement("input");
        h.type = "hidden"; h.name = "cf-turnstile-response"; h.value = token;
        document.body.appendChild(h);
    }""", token)


# ── Helpers ──────────────────────────────────────────────────────────────────

def random_password(length=14):
    chars = string.ascii_letters + string.digits
    pw = ''.join(random.choice(chars) for _ in range(length))
    return pw

def wait_for_cloudflare(page, timeout=120):
    """Wait for Cloudflare 'Just a moment...' challenge to pass"""
    print(json.dumps({"step": "Menunggu Cloudflare challenge..."}), flush=True)
    for i in range(timeout):
        title = page.title()
        url = page.url
        if "Just a moment" in title or "Attention Required" in title:
            if i % 10 == 0:
                print(json.dumps({"step": f"CF challenge berjalan... ({i}s)"}), flush=True)
            time.sleep(2)
        else:
            if i > 0:
                print(json.dumps({"step": f"CF challenge passed! ({i}s) url={url}"}), flush=True)
            else:
                print(json.dumps({"step": "Tidak ada CF challenge, langsung lanjut"}), flush=True)
            return True
    print(json.dumps({"step": f"CF challenge timeout ({timeout}s)"}), flush=True)
    return False

def random_email():
    """Generate random email using available mail.tm domain"""
    domain = mail_tm_get_domain()
    username = ''.join(random.choices(string.ascii_lowercase + string.digits, k=12))
    return f"{username}@{domain}"


# ── Main Flow ────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Cloudflare Workers AI Auto-Signup")
    parser.add_argument("--email", default="", help="Email for signup (auto-gen if empty)")
    parser.add_argument("--password", default="", help="Password (auto-gen if empty)")
    parser.add_argument("--headless", action="store_true", help="Run headless (needs Xvfb)")
    parser.add_argument("--telegram-chat-id", default="", help="Telegram chat ID")
    args = parser.parse_args()

    email = args.email or random_email()
    password = args.password or random_password()
    mail_password = f"Mail_{random.randint(100000,999999)}"

    print(json.dumps({"step": "Memulai Cloudflare Auto-Signup", "email": email}), flush=True)

    # ── Step 1: Create mail.tm inbox ──
    print(json.dumps({"step": f"Membuat inbox mail.tm ({email})..."}), flush=True)
    try:
        mail_tm_create_account(email, mail_password)
        print(json.dumps({"step": "Inbox mail.tm OK!"}), flush=True)
    except Exception as e:
        print(json.dumps({"step": f"Mail.tm inbox warning: {e} (might exist already)"}), flush=True)

    # ── Step 2: Launch Camoufox browser ──
    print(json.dumps({"step": "Meluncurkan Camoufox (anti-detect Firefox)..."}), flush=True)
    from camoufox.sync_api import Camoufox

    with Camoufox(headless=args.headless) as browser:
        page = browser.new_page()

        # ── Step 3: Open signup page ──
        print(json.dumps({"step": "Membuka dash.cloudflare.com/sign-up..."}), flush=True)
        page.goto("https://dash.cloudflare.com/sign-up", wait_until="domcontentloaded", timeout=60000)

        # ── Step 4: Wait for CF challenge ──
        wait_for_cloudflare(page)
        time.sleep(5)

        # ── Step 5: Wait for form ──
        print(json.dumps({"step": "Menunggu form signup..."}), flush=True)
        form_found = False
        for attempt in range(6):
            try:
                page.wait_for_selector(
                    'input[type="email"], input[name="email"], input[placeholder*="mail"]',
                    timeout=10000
                )
                print(json.dumps({"step": "Form signup ditemukan!"}), flush=True)
                form_found = True
                break
            except:
                print(json.dumps({"step": f"Form belum muncul (attempt {attempt+1}), reload..."}), flush=True)
                page.reload(wait_until="load", timeout=30000)
                time.sleep(5)
                wait_for_cloudflare(page, timeout=60)
                time.sleep(3)

        if not form_found:
            # Take debug screenshot
            page.screenshot(path="/tmp/cf_debug_signup.png")
            print(json.dumps({"status": "error", "error": "Form signup tidak ditemukan setelah 6 attempts", "screenshot": "/tmp/cf_debug_signup.png"}), flush=True)
            return

        # ── Step 6: Solve Turnstile (isolated page) ──
        sitekey = extract_sitekey(page)
        turnstile_token = None

        if sitekey:
            turnstile_token = solve_turnstile_isolated(browser, page.url, sitekey)
        else:
            print(json.dumps({"step": "Sitekey tidak ditemukan, coba inject default sitekey..."}), flush=True)
            # Try known Cloudflare signup sitekey
            for known_sk in [
                "0x4AAAAAAAJel0iaAR3mgkjp",
                "0x4AAAAAAADnPIDROrmt1Wwj",
                "0x4AAAAAAAB4RPRnHlHv8V3Q",
            ]:
                turnstile_token = solve_turnstile_isolated(browser, page.url, known_sk)
                if turnstile_token:
                    break

        if turnstile_token:
            inject_turnstile_token(page, turnstile_token)
            print(json.dumps({"step": "Turnstile token injected!"}), flush=True)
        else:
            print(json.dumps({"step": "Turnstile tidak ter-solve, lanjut tanpa token..."}), flush=True)

        # ── Step 7: Fill form ──
        print(json.dumps({"step": f"Mengisi form: {email}"}), flush=True)

        email_filled = False
        for sel in ['input[type="email"]', 'input[name="email"]', 'input[placeholder*="mail"]']:
            try:
                page.fill(sel, email)
                email_filled = True
                print(json.dumps({"step": "Email terisi!"}), flush=True)
                break
            except:
                continue

        if not email_filled:
            page.screenshot(path="/tmp/cf_debug_noemail.png")
            print(json.dumps({"status": "error", "error": "Email input tidak ditemukan", "screenshot": "/tmp/cf_debug_noemail.png"}), flush=True)
            return

        pw_filled = False
        for sel in ['input[type="password"]', 'input[name="password"]', 'input[placeholder*="assword"]']:
            try:
                page.fill(sel, password)
                pw_filled = True
                print(json.dumps({"step": "Password terisi!"}), flush=True)
                break
            except:
                continue

        if not pw_filled:
            print(json.dumps({"status": "error", "error": "Password input tidak ditemukan"}), flush=True)
            return

        time.sleep(2)

        # ── Step 8: Submit ──
        print(json.dumps({"step": "Submit form registrasi..."}), flush=True)
        submit_clicked = False
        for sel in ['button[type="submit"]', 'button:has-text("Sign up")', 'button:has-text("Create")']:
            try:
                page.click(sel, timeout=5000)
                submit_clicked = True
                break
            except:
                continue

        if not submit_clicked:
            page.keyboard.press("Enter")

        time.sleep(15)
        page.screenshot(path="/tmp/cf_after_signup.png")

        current_url = page.url
        print(json.dumps({"step": f"Post-signup URL: {current_url}"}), flush=True)

        # ── Step 9: Wait for verification email ──
        print(json.dumps({"step": "Menunggu email verifikasi dari Cloudflare..."}), flush=True)
        verify_link = mail_tm_wait_verify_link(email, mail_password, timeout=180)

        if verify_link:
            print(json.dumps({"step": "Membuka link verifikasi..."}), flush=True)
            page.goto(verify_link, wait_until="domcontentloaded", timeout=30000)
            wait_for_cloudflare(page, timeout=60)
            time.sleep(5)
            print(json.dumps({"step": "Email terverifikasi!"}), flush=True)
        else:
            print(json.dumps({"step": "Email verifikasi tidak ditemukan, coba login langsung..."}), flush=True)

        # ── Step 10: Login ──
        print(json.dumps({"step": "Login ke Cloudflare Dashboard..."}), flush=True)
        page.goto("https://dash.cloudflare.com/login", wait_until="domcontentloaded", timeout=60000)
        wait_for_cloudflare(page, timeout=60)
        time.sleep(5)

        # Solve turnstile on login page too
        login_sitekey = extract_sitekey(page)
        if login_sitekey:
            login_token = solve_turnstile_isolated(browser, page.url, login_sitekey)
            if login_token:
                inject_turnstile_token(page, login_token)

        # Fill login form
        for sel in ['input[type="email"]', 'input[name="email"]']:
            try:
                page.fill(sel, email)
                break
            except:
                continue

        for sel in ['input[type="password"]', 'input[name="password"]']:
            try:
                page.fill(sel, password)
                break
            except:
                continue

        time.sleep(1)
        for sel in ['button[type="submit"]', 'button:has-text("Log")']:
            try:
                page.click(sel, timeout=5000)
                break
            except:
                continue

        time.sleep(10)
        wait_for_cloudflare(page, timeout=60)

        current_url = page.url
        print(json.dumps({"step": f"Post-login URL: {current_url}"}), flush=True)

        if "/login" in current_url:
            page.screenshot(path="/tmp/cf_debug_login.png")
            page_text = page.inner_text("body")[:500]
            print(json.dumps({"status": "error", "error": f"Login gagal: {page_text}", "screenshot": "/tmp/cf_debug_login.png"}), flush=True)
            return

        # ── Step 11: Get Account ID ──
        print(json.dumps({"step": "Mengambil Account ID..."}), flush=True)
        account_id = None

        match = re.search(r'dash\.cloudflare\.com/([a-f0-9]{32})', current_url)
        if match:
            account_id = match.group(1)

        if not account_id:
            page.goto("https://dash.cloudflare.com/", wait_until="domcontentloaded", timeout=30000)
            time.sleep(5)
            current_url = page.url
            match = re.search(r'dash\.cloudflare\.com/([a-f0-9]{32})', current_url)
            if match:
                account_id = match.group(1)

        if not account_id:
            try:
                resp = page.request.get("https://api.cloudflare.com/client/v4/accounts")
                data = json.loads(resp.text())
                if data.get("success") and data.get("result"):
                    account_id = data["result"][0]["id"]
            except:
                pass

        if not account_id:
            print(json.dumps({"status": "error", "error": "Account ID tidak ditemukan"}), flush=True)
            return

        print(json.dumps({"step": f"Account ID: {account_id}"}), flush=True)

        # ── Step 12: Create Workers AI API Token ──
        print(json.dumps({"step": "Membuat API Token (Workers AI)..."}), flush=True)
        page.goto("https://dash.cloudflare.com/profile/api-tokens", wait_until="domcontentloaded", timeout=30000)
        time.sleep(5)

        try:
            page.click('button:has-text("Create Token"), a:has-text("Create Token")', timeout=10000)
            time.sleep(3)
        except:
            try:
                page.click('text=Create Token', timeout=5000)
                time.sleep(3)
            except:
                print(json.dumps({"status": "error", "error": "Tidak bisa klik Create Token"}), flush=True)
                return

        try:
            page.click('button:has-text("Create Custom Token"), a:has-text("Create Custom Token")', timeout=5000)
            time.sleep(2)
        except:
            try:
                page.click('text=Create Custom Token', timeout=5000)
                time.sleep(2)
            except:
                pass

        # Fill token name
        token_name = f"WorkersAI-{int(time.time())}"
        for sel in ['input[name="name"]', 'input[placeholder*="name"]', 'input[placeholder*="Name"]']:
            try:
                page.fill(sel, token_name)
                break
            except:
                continue

        time.sleep(1)

        # Add Workers AI permission
        try:
            page.click('button:has-text("Add Permission"), a:has-text("Add Permission")', timeout=5000)
            time.sleep(1)
        except:
            pass

        try:
            page.select_option('select >> nth=0', label="Account")
            time.sleep(1)
        except:
            pass

        try:
            page.select_option('select >> nth=1', label="Workers AI")
            time.sleep(1)
        except:
            pass

        try:
            page.select_option('select >> nth=2', label="Edit")
            time.sleep(1)
        except:
            pass

        try:
            page.click('button:has-text("Continue"), a:has-text("Continue")', timeout=5000)
            time.sleep(3)
        except:
            pass

        try:
            page.click('button:has-text("Create Token"), input[value="Create Token"]', timeout=5000)
            time.sleep(5)
        except:
            pass

        # ── Step 13: Extract Token ──
        print(json.dumps({"step": "Mengekstrak API Token..."}), flush=True)
        api_token = None

        for sel in ['input[name="token"]', 'input[readonly]', 'code']:
            try:
                el = page.query_selector(sel)
                if el:
                    tag = el.evaluate('el => el.tagName')
                    if tag == 'INPUT':
                        val = el.input_value()
                    else:
                        val = el.inner_text()
                    if val and len(val) > 20:
                        api_token = val
                        break
            except:
                continue

        if not api_token:
            page_text = page.inner_text('body')
            # Look for long token-like strings
            for match in re.finditer(r'[A-Za-z0-9_\-]{30,}', page_text):
                candidate = match.group()
                # Skip known non-token strings
                if candidate.startswith('http') or 'cloudflare' in candidate.lower():
                    continue
                api_token = candidate
                break

        page.screenshot(path="/tmp/cf_final.png")

        if api_token:
            result = {
                "status": "success",
                "email": email,
                "password": password,
                "account_id": account_id,
                "api_token": api_token,
            }
            print(json.dumps(result), flush=True)
        else:
            print(json.dumps({
                "status": "error",
                "error": "API Token tidak ditemukan",
                "account_id": account_id,
                "screenshot": "/tmp/cf_final.png"
            }), flush=True)


if __name__ == "__main__":
    main()
