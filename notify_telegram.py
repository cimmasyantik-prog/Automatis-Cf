#!/usr/bin/env python3
"""Send signup result to Telegram"""
import sys
import json
import urllib.request
import urllib.parse

def send_telegram(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:
        print(f"Telegram send error: {e}")

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--chat-id", required=True)
    parser.add_argument("--bot-token", required=True)
    parser.add_argument("--output-file", default="output.log")
    args = parser.parse_args()

    # Read output log
    try:
        if args.output_file == "/dev/stdin":
            lines = sys.stdin.read()
        else:
            with open(args.output_file, 'r') as f:
                lines = f.read()
    except:
        send_telegram(args.bot_token, args.chat_id, "❌ Error reading output file")
        return

    # Find last JSON line with status
    result = None
    for line in reversed(lines.strip().split('\n')):
        line = line.strip()
        if line.startswith('{') and '"status"' in line:
            try:
                result = json.loads(line)
                break
            except:
                continue

    if not result:
        send_telegram(args.bot_token, args.chat_id, "❌ Signup selesai tapi tidak ada output status ditemukan.")
        return

    if result.get("status") == "success":
        msg = (
            f"✅ *Signup Berhasil!*\n\n"
            f"📧 *Email:* `{result.get('email', 'N/A')}`\n"
            f"🔑 *Password:* `{result.get('password', 'N/A')}`\n"
            f"🆔 *Account ID:* `{result.get('account_id', 'N/A')}`\n"
            f"🔐 *API Token:* `{result.get('api_token', 'N/A')[:30]}...`\n\n"
            f"_Token Workers AI siap digunakan!_"
        )
    else:
        msg = f"❌ *Signup Gagal*\n\nError: `{result.get('error', 'Unknown error')}`"

    send_telegram(args.bot_token, args.chat_id, msg)

if __name__ == "__main__":
    main()
