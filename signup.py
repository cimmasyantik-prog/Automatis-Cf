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
    """Finds Turnstile iframe — fast method using JS to search shadow DOM"""
    # Method 1: JS-based shadow DOM search (fastest — single call)
    try:
        js_result = page.run_js("""
            // Find all iframes inside shadow roots
            function findShadowIframes(root) {
                let results = [];
                const allEls = root.querySelectorAll('*');
                for (const el of allEls) {
                    if (el.shadowRoot) {
                        const iframes = el.shadowRoot.querySelectorAll('iframe');
                        for (const iframe of iframes) {
                            results.push(iframe.src || iframe.getAttribute('src') || 'no-src');
                        }
                        results = results.concat(findShadowIframes(el.shadowRoot));
                    }
                }
                return results;
            }
            return JSON.stringify(findShadowIframes(document));
        """)
        if js_result and js_result.strip(' "[]') and js_result.strip(' "[]') != 'no-src':
            print(json.dumps({"step": f"Shadow DOM iframes via JS: {js_result}"}), flush=True)
    except Exception as e:
        pass

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

    # Method 3: Standard iframe lookup (fallback)
    try:
        iframe = page.ele('tag:iframe@src*=challenges.cloudflare.com', timeout=1) or page.ele('tag:iframe@src*=/cdn-cgi/challenge-platform/', timeout=1)
        if iframe:
            print(json.dumps({"step": "Turnstile iframe ditemukan via standard selector"}), flush=True)
            return iframe
    except:
        pass

    # Method 4: CDP-based shadow DOM deep search (nuclear option)
    try:
        js_deep = page.run_js("""
            function deepSearch(root, depth) {
                if (depth > 10) return null;
                const els = root.querySelectorAll('*');
                for (const el of els) {
                    // Check if element is a Turnstile container
                    if (el.tagName && el.tagName.toLowerCase().includes('turnstile')) {
                        const iframe = el.querySelector('iframe');
                        if (iframe) return iframe.src || 'found';
                    }
                    if (el.shadowRoot) {
                        const result = deepSearch(el.shadowRoot, depth + 1);
                        if (result) return result;
                        const iframe = el.shadowRoot.querySelector('iframe[src*="challenges.cloudflare"]') || el.shadowRoot.querySelector('iframe[src*="cdn-cgi"]');
                        if (iframe) return iframe.src;
                    }
                }
                return null;
            }
            return deepSearch(document, 0) || 'not-found';
        """)
        if js_deep and 'not-found' not in js_deep:
            print(json.dumps({"step": f"Deep JS search found: {js_deep}"}), flush=True)
            # Now get the element by querying DrissionPage
            iframe = page.ele('tag:iframe@src*=challenges.cloudflare.com', timeout=1) or page.ele('tag:iframe@src*=/cdn-cgi/challenge-platform/', timeout=1)
            if iframe:
                return iframe
    except:
        pass

    # Method 5: If only 1 iframe on page and Turnstile is visible, use it
    try:
        all_iframes = page.eles('tag:iframe')
        if all_iframes and len(all_iframes) == 1:
            print(json.dumps({"step": f"Satu iframe ditemukan di page, langsung pakai: src={all_iframes[0].attr('src') or 'empty'}"}), flush=True)
            return all_iframes[0]
        elif all_iframes:
            # Print all iframe srcs for debugging
            srcs = [f"iframe{i}={ifr.attr('src') or 'empty'}" for i, ifr in enumerate(all_iframes)]
            print(json.dumps({"step": f"Ditemukan {len(all_iframes)} iframe: {', '.join(srcs)}"}), flush=True)
    except:
        pass

    return None

def solve_turnstile_drission(page, timeout=45):
    """Bypasses Cloudflare Turnstile using DrissionPage's iframe navigation"""
    print(json.dumps({"step": "Mencari Turnstile checkbox..."}), flush=True)
    
    # Debug: print all iframes on the page
    try:
        iframes = page.eles('tag:iframe')
        print(json.dumps({"step": f"Ditemukan {len(iframes)} standard iframe di halaman"}), flush=True)
    except Exception as e:
        print(json.dumps({"step": f"Gagal list debug iframes: {str(e)}"}), flush=True)

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            # 1. Find the iframe element
            iframe_ele = find_turnstile_iframe(page)
            if iframe_ele:
                # 2. Get the ChromiumFrame object using get_frame()
                frame = page.get_frame(iframe_ele)
                if frame:
                    print(json.dumps({"step": "Iframe Turnstile terhubung! Mencari checkbox..."}), flush=True)
                    # Check for the checkbox inside the frame
                    checkbox = (
                        frame.ele('.mark', timeout=2) or 
                        frame.ele('.cb-i', timeout=1) or 
                        frame.ele('@type=checkbox', timeout=1) or 
                        frame.ele('#cf-stage', timeout=1) or 
                        frame.ele('#challenge-stage', timeout=1)
                    )
                    if checkbox:
                        print(json.dumps({"step": "Checkbox Turnstile ditemukan! Mengklik..."}), flush=True)
                        checkbox.click()
                        time.sleep(3)
                        # Verify if solved (check if token is generated)
                        token = page.ele('@name=cf-turnstile-response', timeout=2) or page.ele('@name=cf_challenge_response', timeout=2)
                        if token and token.value:
                            print(json.dumps({"step": "Turnstile berhasil di-solve!"}), flush=True)
                            return True
                    else:
                        # Debug: print frame HTML to see what's inside
                        try:
                            frame_html = frame.html
                            print(json.dumps({"step": f"Frame HTML (first 500): {frame_html[:500]}"}), flush=True)
                        except:
                            pass
                        
                        # Click via JS injection (bypasses ChromiumFrame lack of .click())
                        print(json.dumps({"step": "Mengklik iframe via JS..."}), flush=True)
                        try:
                            page.run_js('document.querySelector("iframe").click()')
                        except:
                            # Fallback: click at iframe coordinates via CDP
                            try:
                                rect = iframe_ele.rect
                                if rect:
                                    cx = rect['x'] + rect['width'] / 2
                                    cy = rect['y'] + rect['height'] / 2
                                    page.run_js(f'var el = document.elementFromPoint({cx}, {cy}); if(el) el.click();')
                            except:
                                pass
                        time.sleep(3)
                        
                        token = page.ele('@name=cf-turnstile-response', timeout=2) or page.ele('@name=cf_challenge_response', timeout=2)
                        if token and token.value:
                            print(json.dumps({"step": "Turnstile berhasil di-solve via iframe click!"}), flush=True)
                            return True
                else:
                    print(json.dumps({"step": "Gagal mendapatkan ChromiumFrame dari iframe element..."}), flush=True)
            else:
                print(json.dumps({"step": "Iframe Turnstile belum muncul..."}), flush=True)
        except Exception as e:
            print(json.dumps({"step": f"Error di Turnstile loop: {str(e)}"}), flush=True)
        time.sleep(2)
    print(json.dumps({"step": "Turnstile checkbox tidak ditemukan atau gagal diklik"}), flush=True)
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
