#!/usr/bin/env python3
"""
Cloudflare Workers AI Auto-Signup
Using DrissionPage (CDP-based Chrome driver) + mail.tm
No 2Captcha needed — Turnstile solved via DrissionPage's click / shadow DOM capabilities.
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

                    match = re.search(r'https://dash\.cloudflare\.com/[^\s"\'<>]+verify[^\s"\'<>]*', html)
                    if match:
                        link = match.group(0).replace("&amp;", "&")
                        print(json.dumps({"step": "Link verifikasi ditemukan!"}), flush=True)
                        return link

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

# ── Helpers ──────────────────────────────────────────────────────────────────

def random_password(length=14):
    # CF needs: 8+ chars, upper, lower, number, special
    chars = string.ascii_letters + string.digits
    pw = ''.join(random.choice(chars) for _ in range(length - 3))
    # inject necessary character classes
    pw = pw + 'A' + '1' + '@'
    return pw

def random_email():
    domain = mail_tm_get_domain()
    username = ''.join(random.choices(string.ascii_lowercase + string.digits, k=12))
    return f"{username}@{domain}"

def wait_for_cloudflare_challenge(page, timeout=90):
    """Wait for 'Just a moment' challenge to pass on DrissionPage"""
    print(json.dumps({"step": "Menunggu Cloudflare challenge..."}), flush=True)
    for i in range(timeout):
        title = page.title
        url = page.url
        if "Just a moment" in title or "Attention Required" in title or "challenge" in title.lower():
            if i % 10 == 0:
                print(json.dumps({"step": f"CF challenge berjalan... ({i}s)"}), flush=True)
            time.sleep(2)
        else:
            print(json.dumps({"step": f"CF challenge passed! url={url}"}), flush=True)
            return True
    print(json.dumps({"step": f"CF challenge timeout ({timeout}s)"}), flush=True)
    return False

def find_turnstile_iframe(page):
    """Finds Turnstile iframe — uses CDP DOM.getDocument(pierce=true) to traverse shadow DOMs"""
    
    # Method 1: CDP DOM.getDocument with pierce=true (opens all shadow roots)
    try:
        doc = page.run_cdp('DOM.getDocument', depth=-1, pierce=True)
        if doc and 'root' in doc:
            def find_iframes_cdp(node, depth=0):
                if depth > 20:
                    return None
                name = node.get('nodeName', '').lower()
                if name == 'iframe':
                    # Check attributes for Turnstile indicators
                    attrs = node.get('attributes', [])
                    attr_dict = {}
                    for i in range(0, len(attrs), 2):
                        if i + 1 < len(attrs):
                            attr_dict[attrs[i]] = attrs[i + 1]
                    src = attr_dict.get('src', '')
                    title = attr_dict.get('title', '')
                    cls = attr_dict.get('class', '')
                    
                    # Skip OneTrust iframes
                    if 'onetrust' in cls.lower() or 'onetrust' in title.lower():
                        pass
                    elif 'challenges.cloudflare' in src or 'cdn-cgi' in src or 'turnstile' in src.lower():
                        print(json.dumps({"step": f"CDP: Turnstile iframe found! src={src[:80]}"}), flush=True)
                        # Return the node ID to get the element later
                        return node.get('nodeId')
                    elif src == '' or src == 'about:blank':
                        # Could be a dynamically created Turnstile iframe
                        # Check if it's inside a Turnstile container
                        print(json.dumps({"step": f"CDP: iframe with empty src, nodeId={node.get('nodeId')}, attrs={attr_dict}"}), flush=True)
                
                for child in node.get('children', []):
                    result = find_iframes_cdp(child, depth + 1)
                    if result:
                        return result
                # Also check shadow roots
                if 'shadowRoots' in node:
                    for sr in node['shadowRoots']:
                        result = find_iframes_cdp(sr, depth + 1)
                        if result:
                            return result
                return None
            
            node_id = find_iframes_cdp(doc['root'])
            if node_id:
                # Resolve the nodeId to a DrissionPage element
                try:
                    iframe = page.ele(f'xpath://iframe[@node-id="{node_id}"]', timeout=1)
                    if iframe:
                        return iframe
                except:
                    pass
                # Fallback: try to get element by nodeId directly
                try:
                    result = page.run_cdp('DOM.resolveNode', nodeId=node_id)
                    if result and 'object' in result:
                        print(json.dumps({"step": f"CDP: Resolved nodeId {node_id} to object"}), flush=True)
                except:
                    pass
    except Exception as e:
        print(json.dumps({"step": f"CDP DOM search error: {str(e)}"}), flush=True)

    # Method 2: Check known wrappers (fast — specific selectors)
    for wrapper_selector in ['cf-turnstile-wrapper', '.cf-turnstile', '.cf-challenge', '#turnstile-wrapper', '[data-sitekey]']:
        try:
            wrapper = page.ele(wrapper_selector, timeout=1)
            if wrapper:
                # Try shadow root first
                try:
                    if wrapper.shadow_root:
                        iframe = wrapper.shadow_root.ele('tag:iframe', timeout=1)
                        if iframe:
                            print(json.dumps({"step": f"Turnstile iframe ditemukan di shadow_root dari {wrapper_selector}"}), flush=True)
                            return iframe
                except:
                    pass
                # Try direct iframe child
                iframe = wrapper.ele('tag:iframe', timeout=1)
                if iframe:
                    print(json.dumps({"step": f"Turnstile iframe ditemukan langsung di {wrapper_selector}"}), flush=True)
                    return iframe
        except:
            pass

    # Method 3: Standard iframe lookup with Turnstile-related src
    try:
        iframe = page.ele('tag:iframe@src*=challenges.cloudflare.com', timeout=1) or page.ele('tag:iframe@src*=/cdn-cgi/challenge-platform/', timeout=1)
        if iframe:
            print(json.dumps({"step": "Turnstile iframe ditemukan via standard selector"}), flush=True)
            return iframe
    except:
        pass

    # Method 4: Check all div elements' shadow roots (targeted, not ALL elements)
    try:
        for div in page.eles('tag:div', timeout=2):
            try:
                sr = div.shadow_root
                if sr:
                    iframe = sr.ele('tag:iframe', timeout=1)
                    if iframe:
                        # Verify it's not OneTrust
                        src = iframe.attr('src') or ''
                        cls = iframe.attr('class') or ''
                        if 'onetrust' not in cls.lower() and 'onetrust' not in src.lower():
                            print(json.dumps({"step": f"Turnstile iframe di shadow_root dari div (class={div.attr('class') or ''})"}), flush=True)
                            return iframe
            except:
                pass
    except:
        pass

    return None

def solve_turnstile_drission(page, timeout=60):
    """Solves Turnstile — finds iframe via shadow DOM, tries click, falls back to waiting for auto-solve"""
    print(json.dumps({"step": "Mencari Turnstile iframe..."}), flush=True)
    
    deadline = time.time() + timeout
    found_frame = False
    
    while time.time() < deadline:
        try:
            # 1. Find the Turnstile iframe
            iframe_ele = find_turnstile_iframe(page)
            if iframe_ele:
                frame = page.get_frame(iframe_ele)
                if frame:
                    found_frame = True
                    print(json.dumps({"step": "Turnstile iframe terhubung!"}), flush=True)
                    
                    # Try to find and click checkbox (various selectors)
                    checkbox = None
                    for sel in ['tag:div@class=mark', '.cb-i', '@type=checkbox', '#cf-stage', '#challenge-stage', 'tag:div@role=checkbox', '.ctp-checkbox-container']:
                        try:
                            checkbox = frame.ele(sel, timeout=1)
                            if checkbox:
                                break
                        except:
                            pass
                    
                    if checkbox:
                        print(json.dumps({"step": f"Checkbox ditemukan ({checkbox.tag})! Mengklik..."}), flush=True)
                        checkbox.click()
                    else:
                        # No checkbox — Turnstile may auto-solve (flexible mode)
                        print(json.dumps({"step": "Checkbox tidak ada, tunggu auto-solve (flexible mode)..."}), flush=True)
        except Exception as e:
            print(json.dumps({"step": f"Error Turnstile loop: {str(e)}"}), flush=True)
        
        # Check if token appeared on parent page
        try:
            token_el = page.ele('@name=cf-turnstile-response', timeout=2) or page.ele('@name=cf_challenge_response', timeout=2)
            if token_el and token_el.value:
                print(json.dumps({"step": "Turnstile BERHASIL di-solve! Token ditemukan."}), flush=True)
                return True
        except:
            pass
        
        time.sleep(3)
    
    print(json.dumps({"step": f"Turnstile timeout ({timeout}s), frame_found={found_frame}"}), flush=True)
    return False

# ── Main Flow ────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Cloudflare Auto-Signup via DrissionPage")
    parser.add_argument("--email", default="", help="Email for signup (auto-gen if empty)")
    parser.add_argument("--password", default="", help="Password (auto-gen if empty)")
    parser.add_argument("--headless", action="store_true", help="Run headless")
    parser.add_argument("--telegram-chat-id", default="", help="Telegram chat ID")
    args = parser.parse_args()

    email = random_email() if not args.email or args.email == "auto-gen" else args.email
    password = random_password() if not args.password or args.password == "auto-gen" else args.password
    mail_password = f"Mail_{random.randint(100000,999999)}"

    print(json.dumps({"step": "Memulai Cloudflare Auto-Signup (DrissionPage)", "email": email}), flush=True)

    # 1. Create temp email
    print(json.dumps({"step": f"Membuat inbox mail.tm ({email})...."}), flush=True)
    try:
        mail_tm_create_account(email, mail_password)
        print(json.dumps({"step": "Inbox mail.tm OK!"}), flush=True)
    except Exception as e:
        print(json.dumps({"step": f"Mail.tm inbox warning: {e}"}), flush=True)

    # 2. Launch Chromium via DrissionPage
    print(json.dumps({"step": "Meluncurkan Chromium via DrissionPage..."}), flush=True)
    from DrissionPage import ChromiumPage, ChromiumOptions

    co = ChromiumOptions()
    # DO NOT set headless(True). Instead, run in headed mode inside Xvfb (virtual frame buffer)
    # This prevents Cloudflare from detecting the headless flag and blocking us.
    co.headless(False)
    
    # Auto-detect browser path on Linux/CI
    import os
    chrome_paths = [
        '/usr/bin/google-chrome',
        '/usr/bin/google-chrome-stable',
        '/usr/bin/chromium-browser',
        '/usr/bin/chromium',
        '/usr/bin/chrome'
    ]
    for path in chrome_paths:
        if os.path.exists(path):
            co.set_paths(browser_path=path)
            print(json.dumps({"step": f"Menggunakan browser Chrome di: {path}"}), flush=True)
            break
            
    # Set unique user data path to avoid conflicts
    co.set_paths(user_data_path='/tmp/chrome_user_data')
    
    # Anti-detect arguments
    co.set_argument('--no-sandbox')
    co.set_argument('--disable-gpu')
    co.set_argument('--disable-dev-shm-usage')
    co.set_argument('--start-maximized')
    co.set_argument('--no-first-run')
    co.set_argument('--no-default-browser-check')
    co.set_user_agent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

    page = ChromiumPage(co)

    try:
        # 3. Go to Signup
        print(json.dumps({"step": "Membuka registrasi Cloudflare..."}), flush=True)
        page.get("https://dash.cloudflare.com/sign-up")
        wait_for_cloudflare_challenge(page)
        time.sleep(5)

        # Solve Turnstile
        solve_turnstile_drission(page)

        # 4. Fill form
        print(json.dumps({"step": "Mengisi form registrasi..."}), flush=True)
        
        email_input = page.ele('css:input[type="email"]', timeout=10) or page.ele('css:input[name="email"]', timeout=5)
        if not email_input:
            page.get_screenshot(path="/tmp/cf_no_form.png")
            print(json.dumps({"status": "error", "error": "Form signup tidak ditemukan", "screenshot": "/tmp/cf_no_form.png"}), flush=True)
            return

        email_input.input(email)
        
        pw_input = page.ele('css:input[type="password"]', timeout=5) or page.ele('css:input[name="password"]', timeout=5)
        if pw_input:
            pw_input.input(password)

        time.sleep(1)

        # Submit
        print(json.dumps({"step": "Submit form..."}), flush=True)
        submit_btn = page.ele('css:button[type="submit"]') or page.ele('text:Sign up')
        if submit_btn:
            submit_btn.click()
        else:
            page.actions.key_down('Enter').key_up('Enter')

        time.sleep(15)
        page.get_screenshot(path="/tmp/cf_after_signup.png")
        print(json.dumps({"step": f"Post-signup URL: {page.url}"}), flush=True)

        # 5. Wait for Verification email
        print(json.dumps({"step": "Menunggu email verifikasi..."}), flush=True)
        verify_link = mail_tm_wait_verify_link(email, mail_password, timeout=180)

        if verify_link:
            print(json.dumps({"step": "Membuka link verifikasi..."}), flush=True)
            page.get(verify_link)
            wait_for_cloudflare_challenge(page)
            time.sleep(5)
            print(json.dumps({"step": "Email terverifikasi!"}), flush=True)
        else:
            print(json.dumps({"step": "Email verifikasi tidak ditemukan, mencoba login langsung..."}), flush=True)

        # 6. Login
        print(json.dumps({"step": "Login ke Cloudflare Dashboard..."}), flush=True)
        page.get("https://dash.cloudflare.com/login")
        wait_for_cloudflare_challenge(page)
        time.sleep(5)

        solve_turnstile_drission(page)

        # Fill Login
        email_login = page.ele('css:input[type="email"]') or page.ele('css:input[name="email"]')
        if email_login:
            email_login.input(email)
        
        pw_login = page.ele('css:input[type="password"]') or page.ele('css:input[name="password"]')
        if pw_login:
            pw_login.input(password)

        time.sleep(1)
        
        submit_login = page.ele('css:button[type="submit"]') or page.ele('text:Sign in')
        if submit_login:
            submit_login.click()
        
        time.sleep(10)
        wait_for_cloudflare_challenge(page)

        print(json.dumps({"step": f"Post-login URL: {page.url}"}), flush=True)

        if "/login" in page.url:
            page.get_screenshot(path="/tmp/cf_login_fail.png")
            print(json.dumps({"status": "error", "error": f"Login gagal: {page.html[:300]}", "screenshot": "/tmp/cf_login_fail.png"}), flush=True)
            return

        # 7. Get Account ID
        print(json.dumps({"step": "Mengambil Account ID..."}), flush=True)
        account_id = None
        current_url = page.url
        match = re.search(r'dash\.cloudflare\.com/([a-f0-9]{32})', current_url)
        if match:
            account_id = match.group(1)

        if not account_id:
            page.get("https://dash.cloudflare.com/")
            time.sleep(5)
            match = re.search(r'dash\.cloudflare\.com/([a-f0-9]{32})', page.url)
            if match:
                account_id = match.group(1)

        if not account_id:
            print(json.dumps({"status": "error", "error": "Account ID tidak ditemukan"}), flush=True)
            return

        print(json.dumps({"step": f"Account ID: {account_id}"}), flush=True)

        # 8. Create API Token (Workers AI)
        print(json.dumps({"step": "Membuat API Token..."}), flush=True)
        page.get("https://dash.cloudflare.com/profile/api-tokens")
        time.sleep(5)

        create_btn = page.ele('text:Create Token')
        if create_btn:
            create_btn.click()
            time.sleep(3)

        custom_btn = page.ele('text:Create Custom Token')
        if custom_btn:
            custom_btn.click()
            time.sleep(2)

        # Token Name
        token_name = f"WorkersAI-{int(time.time())}"
        name_input = page.ele('css:input[name="name"]') or page.ele('css:input[placeholder*="name"]')
        if name_input:
            name_input.input(token_name)

        time.sleep(1)

        # Add Permission: Account -> Workers AI -> Edit
        add_perm = page.ele('text:Add Permission')
        if add_perm:
            add_perm.click()
            time.sleep(1)

        # DrissionPage selects option easily
        selects = page.eles('tag:select')
        if len(selects) >= 3:
            selects[0].select('Account')
            time.sleep(0.5)
            selects[1].select('Workers AI')
            time.sleep(0.5)
            selects[2].select('Edit')
            time.sleep(0.5)

        continue_btn = page.ele('text:Continue') or page.ele('css:button:contains("Continue")')
        if continue_btn:
            continue_btn.click()
            time.sleep(2)

        final_create = page.ele('text:Create Token') or page.ele('css:button:contains("Create Token")')
        if final_create:
            final_create.click()
            time.sleep(5)

        # 9. Extract Token
        print(json.dumps({"step": "Mengekstrak API Token..."}), flush=True)
        api_token = None
        
        token_input = page.ele('css:input[readonly]') or page.ele('css:input[name="token"]')
        if token_input:
            api_token = token_input.value
        else:
            code_el = page.ele('tag:code')
            if code_el:
                api_token = code_el.text

        if not api_token:
            # Regex fallback
            match = re.search(r'[A-Za-z0-9_\-]{40}', page.html)
            if match:
                api_token = match.group()

        page.get_screenshot(path="/tmp/cf_final.png")

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

    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e)}), flush=True)
    finally:
        try:
            page.quit()
        except:
            pass

if __name__ == "__main__":
    main()
