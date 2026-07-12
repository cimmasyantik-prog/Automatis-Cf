#!/usr/bin/env python3
"""
Cloudflare Workers AI Auto-Signup
Uses patchright (anti-detect) + isolated Turnstile solver + mail.tm temp email
No 2Captcha needed — Turnstile solved automatically via isolated page approach.
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
    """Get available mail.tm domain"""
    req = urllib.request.Request(f"{MAIL_TM_BASE}/domains", headers={"User-Agent": "Mozilla/5.0"})
    data = json.loads(urllib.request.urlopen(req, timeout=15).read().decode())
    domains = data.get("hydra:member", [])
    if not domains:
        raise Exception("No mail.tm domains available")
    return domains[0]["domain"]

def mail_tm_create_account(email: str, password: str):
    """Create temp email inbox on mail.tm"""
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
    """Get auth token for mail.tm inbox"""
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
        print(json.dumps({"step": f"Mail.tm auth error: {e}"}))
        return None

    deadline = time.time() + timeout
    seen_ids = set()
    print(json.dumps({"step": f"Menunggu email verifikasi Cloudflare ({email})..."}))

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
                if "cloudflare" in subject.lower() or "verify" in subject.lower():
                    # Fetch full message
                    req2 = urllib.request.Request(
                        f"{MAIL_TM_BASE}/messages/{mid}",
                        headers={"Authorization": f"Bearer {token}", "User-Agent": "Mozilla/5.0"}
                    )
                    full_msg = json.loads(urllib.request.urlopen(req2, timeout=10).read().decode())
                    html = full_msg.get("html", [""])[0] if isinstance(full_msg.get("html"), list) else full_msg.get("html", "")

                    # Extract verification link
                    match = re.search(r'https://dash\.cloudflare\.com/[^\s"\'<>]+verify[^\s"\'<>]*', html)
                    if match:
                        link = match.group(0).replace("&amp;", "&")
                        print(json.dumps({"step": f"Link verifikasi ditemukan!"}))
                        return link

                    # Try finding any CF link
                    match = re.search(r'(https://[^\s"\'<>]*cloudflare[^\s"\'<>]*)', html)
                    if match:
                        link = match.group(1).replace("&amp;", "&")
                        print(json.dumps({"step": f"Link Cloudflare ditemukan di email!"}))
                        return link
        except Exception as e:
            print(json.dumps({"step": f"Mail.tm poll error: {e}"}))

        time.sleep(5)

    print(json.dumps({"step": f"Timeout menunggu email verifikasi ({timeout}s)"}))
    return None

# ── Turnstile Solver (Isolated Page Approach) ───────────────────────────────

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
    """Extract Turnstile sitekey from page"""
    try:
        # Method 1: data-sitekey attribute
        el = page.query_selector('[data-sitekey]')
        if el:
            sk = el.get_attribute('data-sitekey')
            if sk:
                return sk

        # Method 2: in scripts
        for script in page.query_selector_all('script'):
            content = script.inner_text()
            match = re.search(r'sitekey["\s:]+["\']?([0-9A-Za-z_-]+)', content)
            if match:
                return match.group(1)

        # Method 3: page source
        content = page.content()
        match = re.search(r'data-sitekey=["\']([0-9A-Za-z_-]+)', content)
        if match:
            return match.group(1)

        # Method 4: iframe src
        iframe = page.query_selector('iframe[src*="challenges.cloudflare.com"]')
        if iframe:
            src = iframe.get_attribute('src')
            match = re.search(r'sitekey=([0-9A-Za-z_-]+)', src)
            if match:
                return match.group(1)
    except Exception as e:
        print(json.dumps({"step": f"Sitekey extraction error: {e}"}))
    return None

def solve_turnstile_isolated(context, url: str, sitekey: str) -> Optional[str]:
    """Solve Turnstile in an isolated page (free, no 2Captcha)"""
    print(json.dumps({"step": f"Mencoba solve Turnstile (isolated page, sitekey: {sitekey[:20]}...)"}))
    solver_page = context.new_page()

    try:
        turnstile_div = f'<div class="cf-turnstile" data-sitekey="{sitekey}"></div>'
        page_data = TURNSTILE_HTML.replace("<!-- cf turnstile -->", turnstile_div)

        url_with_slash = url + "/" if not url.endswith("/") else url
        solver_page.route(url_with_slash, lambda route: route.fulfill(body=page_data, status=200))
        solver_page.goto(url_with_slash)

        for attempt in range(15):
            solver_page.wait_for_timeout(2000)
            try:
                td = solver_page.query_selector('.cf-turnstile')
                if td:
                    td.click()
            except:
                pass

            try:
                token_val = solver_page.input_value('[name="cf-turnstile-response"]')
                if token_val and token_val != "":
                    print(json.dumps({"step": f"Turnstile solved! ({(attempt+1)*2}s)"}))
                    return token_val
            except:
                pass

            # Also check if button is enabled
            try:
                btn = solver_page.query_selector('button[type="submit"]')
                if btn and btn.get_attribute('disabled') is None:
                    # Button enabled = turnstile solved
                    try:
                        token_val = solver_page.input_value('[name="cf-turnstile-response"]')
                        if token_val:
                            print(json.dumps({"step": f"Turnstile solved via button check! ({(attempt+1)*2}s)"}))
                            return token_val
                    except:
                        pass
            except:
                pass

            if attempt < 8:
                print(json.dumps({"step": f"Turnstile solving... ({(attempt+1)*2}s)"}))

        print(json.dumps({"step": "Turnstile tidak solved setelah 30s"}))
        return None
    except Exception as e:
        print(json.dumps({"step": f"Turnstile solver error: {e}"}))
        return None
    finally:
        try:
            solver_page.close()
        except:
            pass

# ── Main Flow ────────────────────────────────────────────────────────────────

def random_password(length=14):
    """Generate random password meeting CF requirements"""
    chars = string.ascii_letters + string.digits
    pw = ''.join(random.choice(chars) for _ in range(length - 2))
    # Ensure at least 1 number and 1 special char
    pw = pw[:2] + '@' + pw[3:] + '1'
    return pw

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Cloudflare Workers AI Auto-Signup")
    parser.add_argument("--email", required=True, help="Email for Cloudflare signup")
    parser.add_argument("--password", default="", help="Password (auto-generated if empty)")
    parser.add_argument("--headless", action="store_true", help="Run headless")
    parser.add_argument("--telegram-chat-id", default="", help="Telegram chat ID for notifications")
    args = parser.parse_args()

    email = args.email
    password = args.password or random_password()

    print(json.dumps({"step": "Memulai Cloudflare Auto-Signup", "email": email}))

    # Create mail.tm inbox
    print(json.dumps({"step": f"Membuat inbox mail.tm untuk {email}..."}))
    try:
        mail_password = f"MailPw_{random.randint(1000,9999)}"
        mail_tm_create_account(email, mail_password)
        print(json.dumps({"step": "Inbox mail.tm berhasil dibuat!"}))
    except Exception as e:
        print(json.dumps({"step": f"Mail.tm inbox warning: {e} (mungkin sudah ada)"}))
        mail_password = f"MailPw_{random.randint(1000,9999)}"

    # Start browser
    print(json.dumps({"step": "Meluncurkan browser patchright (anti-detection)..."}))
    from patchright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=args.headless)
    context = browser.new_context(
        viewport={"width": 1920, "height": 1080},
        locale="en-US",
        timezone_id="America/New_York",
    )
    page = context.new_page()

    try:
        # Step 1: Go to signup page
        print(json.dumps({"step": "Membuka halaman registrasi Cloudflare..."}))
        page.goto("https://dash.cloudflare.com/sign-up", wait_until="domcontentloaded", timeout=30000)
        time.sleep(5)

        # Wait for CF challenge to pass
        for _ in range(30):
            title = page.title()
            if "Just a moment" in title or "challenge" in title.lower():
                time.sleep(2)
            else:
                break

        # Step 2: Wait for form
        print(json.dumps({"step": "Menunggu form signup..."}))
        for attempt in range(5):
            try:
                page.wait_for_selector('input[type="email"], input[name="email"]', timeout=10000)
                break
            except:
                print(json.dumps({"step": f"Form belum muncul (attempt {attempt+1}), reload..."}))
                page.reload(wait_until="load", timeout=20000)
                time.sleep(3)

        # Step 3: Solve Turnstile (isolated page approach)
        sitekey = extract_sitekey(page)
        turnstile_token = None
        if sitekey:
            turnstile_token = solve_turnstile_isolated(context, "https://dash.cloudflare.com/sign-up", sitekey)
        else:
            print(json.dumps({"step": "Sitekey tidak ditemukan, coba manual click..."}))
            # Try to click the turnstile checkbox directly
            for _ in range(10):
                try:
                    iframe = page.query_selector('iframe[src*="challenges.cloudflare.com"]')
                    if iframe:
                        frame = iframe.content_frame()
                        if frame:
                            checkbox = frame.query_selector('input[type="checkbox"], .cb-i')
                            if checkbox:
                                checkbox.click()
                except:
                    pass
                time.sleep(2)
                try:
                    token_val = page.evaluate("""
                        () => {
                            const names = ['cf-turnstile-response', 'cf_challenge_response'];
                            for (const n of names) {
                                const el = document.querySelector(`[name="${n}"]`);
                                if (el && el.value) return el.value;
                            }
                            return null;
                        }
                    """)
                    if token_val:
                        turnstile_token = token_val
                        break
                except:
                    pass

        if turnstile_token:
            print(json.dumps({"step": "Turnstile solved, injecting token..."}))
            page.evaluate(f"""() => {{
                const input = document.querySelector('[name="cf-turnstile-response"]');
                if (input) input.value = "{turnstile_token}";
                else {{
                    const h = document.createElement("input");
                    h.type = "hidden"; h.name = "cf-turnstile-response";
                    h.value = "{turnstile_token}";
                    document.body.appendChild(h);
                }}
            }}""")
        else:
            print(json.dumps({"step": "Turnstile tidak ter-solve, mencoba submit tanpa token..."}))

        # Step 4: Fill form
        print(json.dumps({"step": f"Mengisi form: {email}..."}))
        email_filled = False
        for sel in ['input[type="email"]', 'input[name="email"]', 'input[placeholder*="mail"]']:
            try:
                page.fill(sel, email)
                email_filled = True
                break
            except:
                continue

        if not email_filled:
            print(json.dumps({"status": "error", "error": "Email input tidak ditemukan"}))
            return

        pw_filled = False
        for sel in ['input[type="password"]', 'input[name="password"]', 'input[placeholder*="assword"]']:
            try:
                page.fill(sel, password)
                pw_filled = True
                break
            except:
                continue

        if not pw_filled:
            print(json.dumps({"status": "error", "error": "Password input tidak ditemukan"}))
            return

        time.sleep(1)

        # Step 5: Submit
        print(json.dumps({"step": "Submit form registrasi..."}))
        submit_clicked = False
        for sel in ['button[type="submit"]', 'button:has-text("Sign up")', 'button:has-text("Create")']:
            try:
                page.click(sel, timeout=5000)
                submit_clicked = True
                break
            except:
                continue

        if not submit_clicked:
            # Try pressing Enter
            page.keyboard.press("Enter")

        time.sleep(10)
        page.screenshot(path="/tmp/cf_after_signup.png")

        # Check if signup succeeded
        current_url = page.url
        print(json.dumps({"step": f"Post-signup URL: {current_url}"}))

        # Step 6: Wait for verification email
        print(json.dumps({"step": "Menunggu email verifikasi..."}))
        verify_link = mail_tm_wait_verify_link(email, mail_password, timeout=180)

        if verify_link:
            print(json.dumps({"step": f"Membuka link verifikasi..."}))
            page.goto(verify_link, wait_until="domcontentloaded", timeout=30000)
            time.sleep(5)
            print(json.dumps({"step": "Email terverifikasi!"}))
        else:
            print(json.dumps({"step": "Email verifikasi tidak ditemukan, mencoba login langsung..."}))

        # Step 7: Login
        print(json.dumps({"step": "Login ke Cloudflare..."}))
        page.goto("https://dash.cloudflare.com/login", wait_until="domcontentloaded", timeout=30000)
        time.sleep(5)

        # Wait for challenge
        for _ in range(30):
            title = page.title()
            if "Just a moment" in title or "challenge" in title.lower():
                time.sleep(2)
            else:
                break

        # Solve turnstile on login page
        login_sitekey = extract_sitekey(page)
        if login_sitekey:
            login_token = solve_turnstile_isolated(context, "https://dash.cloudflare.com/login", login_sitekey)
            if login_token:
                page.evaluate(f"""() => {{
                    const input = document.querySelector('[name="cf-turnstile-response"]');
                    if (input) input.value = "{login_token}";
                }}""")

        # Fill login
        for sel in ['input[type="email"]', 'input[name="email"]', 'input[placeholder*="mail"]']:
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

        for sel in ['button[type="submit"]', 'button:has-text("Log")', 'button:has-text("Sign in")']:
            try:
                page.click(sel, timeout=5000)
                break
            except:
                continue

        time.sleep(10)

        # Check login
        current_url = page.url
        print(json.dumps({"step": f"Post-login URL: {current_url}"}))

        if "/login" in current_url:
            page_text = page.inner_text("body")[:300]
            print(json.dumps({"status": "error", "error": f"Login gagal. Page text: {page_text}"}))
            return

        # Step 8: Get Account ID
        print(json.dumps({"step": "Mengambil Account ID..."}))
        account_id = None

        # Try from current URL
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
            # Try CF API
            try:
                resp = page.request.fetch("https://api.cloudflare.com/client/v4/accounts")
                data = json.loads(resp.text())
                if data.get("success") and data.get("result"):
                    account_id = data["result"][0]["id"]
            except:
                pass

        if not account_id:
            print(json.dumps({"status": "error", "error": "Account ID tidak ditemukan"}))
            return

        print(json.dumps({"step": f"Account ID: {account_id}"}))

        # Step 9: Create Workers AI Token
        print(json.dumps({"step": "Membuat API Token Workers AI..."}))
        page.goto("https://dash.cloudflare.com/profile/api-tokens", wait_until="domcontentloaded", timeout=30000)
        time.sleep(5)

        # Click Create Token
        try:
            page.click('a:has-text("Create Token"), button:has-text("Create Token")', timeout=10000)
            time.sleep(2)
        except:
            try:
                page.click('text=Create Token', timeout=5000)
                time.sleep(2)
            except:
                print(json.dumps({"status": "error", "error": "Tidak bisa klik Create Token"}))
                return

        # Click Create Custom Token
        try:
            page.click('a:has-text("Create Custom Token"), button:has-text("Create Custom Token")', timeout=5000)
            time.sleep(2)
        except:
            try:
                page.click('text=Create Custom Token', timeout=5000)
                time.sleep(2)
            except:
                pass

        # Fill token name
        token_name = f"WorkersAI-{email.split('@')[0]}-{int(time.time())}"
        try:
            page.fill('input[name="name"]', token_name)
        except:
            try:
                page.fill('input[placeholder*="name"]', token_name)
            except:
                pass

        time.sleep(1)

        # Add permission
        try:
            page.click('button:has-text("Add Permission"), a:has-text("Add Permission")', timeout=5000)
            time.sleep(1)
        except:
            pass

        # Select Account resource
        try:
            page.select_option('select[name*="resource"], select[aria-label*="Resource"]', label="Account")
            time.sleep(500/1000)
        except:
            pass

        # Select Workers AI service
        try:
            page.select_option('select[name*="service"], select[aria-label*="Service"]', label="Workers AI")
            time.sleep(500/1000)
        except:
            pass

        # Select Edit access
        try:
            page.select_option('select[name*="access"], select[aria-label*="Access"]', label="Edit")
            time.sleep(500/1000)
        except:
            pass

        # Continue to summary
        try:
            page.click('button:has-text("Continue"), a:has-text("Continue")', timeout=5000)
            time.sleep(2)
        except:
            pass

        # Create token
        try:
            page.click('button:has-text("Create Token"), input[value="Create Token"]', timeout=5000)
            time.sleep(3)
        except:
            pass

        # Extract token
        api_token = None
        try:
            token_el = page.query_selector('input[name="token"], input[readonly], code')
            if token_el:
                tag = token_el.evaluate('el => el.tagName')
                if tag == 'INPUT':
                    api_token = token_el.input_value()
                else:
                    api_token = token_el.inner_text()
        except:
            pass

        if not api_token:
            # Try regex from page
            page_text = page.inner_text('body')
            match = re.search(r'[A-Za-z0-9_-]{40,}', page_text)
            if match:
                api_token = match.group()

        if api_token:
            result = {
                "status": "success",
                "email": email,
                "password": password,
                "account_id": account_id,
                "api_token": api_token,
            }
            print(json.dumps(result))
        else:
            print(json.dumps({"status": "error", "error": "API Token tidak ditemukan", "account_id": account_id}))

    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e)}))
    finally:
        try:
            browser.close()
            pw.stop()
        except:
            pass

if __name__ == "__main__":
    main()
