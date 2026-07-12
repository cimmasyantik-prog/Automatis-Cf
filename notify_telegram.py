import sys
import json
import argparse
import urllib.request
import urllib.parse

def send_telegram_message(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode('utf-8'))
    except Exception as e:
        print(f"Error sending Telegram notification: {e}")
        return None

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--chat-id", required=True)
    parser.add_argument("--bot-token", required=True)
    parser.add_argument("--output-file", required=True)
    args = parser.parse_args()

    # Read output content
    try:
        with open(args.output_file, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        send_telegram_message(
            args.bot_token,
            args.chat_id,
            f"❌ **Error membaca output dari Runner:** {e}"
        )
        return

    # Find the last JSON line
    result = None
    lines = content.strip().split('\n')
    for line in reversed(lines):
        try:
            parsed = json.loads(line)
            if "status" in parsed:
                result = parsed
                break
        except Exception:
            continue

    if result:
        status = result.get("status")
        if status == "success":
            msg = (
                f"✅ **Cloudflare Auto-Signup Berhasil!**\n\n"
                f"📧 **Email:** `{result.get('email')}`\n"
                f"🆔 **Account ID:** `{result.get('account_id')}`\n"
                f"🔑 **Workers AI API Token:**\n`{result.get('api_key')}`"
            )
        else:
            msg = f"❌ **Gagal melakukan Signup Cloudflare!**\n\n⚠️ **Error:** `{result.get('error', 'Unknown Error')}`"
    else:
        # Fallback to output logs if JSON not found
        msg = f"❌ **Runner selesai dengan output tidak terstruktur:**\n\n```\n{content[-3000:]}\n```"

    send_telegram_message(args.bot_token, args.chat_id, msg)

if __name__ == "__main__":
    main()
