# email_utils.py 
import requests
import time
import re
import random
import string
from typing import Tuple, Optional

ACCOUNTS = {}

def create_test_email() -> Tuple[str, str]:
    """创建随机域名临时邮箱"""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
        }
        resp = requests.get("https://api.mail.tm/domains", headers=headers, timeout=15)
        if resp.status_code != 200:
            raise Exception(f"获取域名失败 {resp.status_code}")

        data = resp.json()
        members = data.get("hydra:member", []) or data.get("member", [])
        if not members:
            raise Exception("无可用域名")

        domain = random.choice(members)["domain"]
        username = ''.join(random.choices(string.ascii_lowercase + string.digits, k=12))
        email = f"{username}@{domain}"
        password = "TempPass123!"

        acc_data = {"address": email, "password": password}
        create_resp = requests.post("https://api.mail.tm/accounts", json=acc_data, headers=headers, timeout=15)
        if create_resp.status_code not in (201, 200):
            raise Exception(f"创建失败 {create_resp.status_code}")

        token_resp = requests.post("https://api.mail.tm/token", json=acc_data, headers=headers, timeout=15)
        token = token_resp.json()["token"]
        ACCOUNTS[email] = token

        print(f"✅ 创建临时邮箱成功: {email}")
        return email, token

    except Exception as e:
        print(f"❌ 创建邮箱异常: {type(e).__name__} - {e}")
        raise Exception("Failed to generate temp email")


def fetch_verification_code(email: str, timeout: int = 180) -> Optional[str]:
    """超级增强版：收到邮件必打印调试信息"""
    token = ACCOUNTS.get(email)
    if not token:
        return None

    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    print(f"🔍 正在监听 {email} 的验证码...（最多 {timeout} 秒，每 4 秒查一次）")

    start_time = time.time()
    seen_ids = set()
    checks = 0

    while time.time() - start_time < timeout:
        checks += 1
        try:
            list_resp = requests.get("https://api.mail.tm/messages", headers=headers, timeout=10)
            if list_resp.status_code == 200:
                msgs = list_resp.json().get("hydra:member", []) or list_resp.json().get("member", [])
                for msg in msgs:
                    msg_id = msg.get("id")
                    if not msg_id or msg_id in seen_ids:
                        continue
                    seen_ids.add(msg_id)

                    read_resp = requests.get(f"https://api.mail.tm/messages/{msg_id}", headers=headers, timeout=10)
                    if read_resp.status_code == 200:
                        data = read_resp.json()
                        subject = data.get("subject", "") or ""
                        text = data.get("text", "") or ""
                        html = " ".join(data.get("html", [])) if isinstance(data.get("html"), list) else str(data.get("html", ""))
                        full_text = f"{subject} {text} {html}".lower()

                        # 优先从主题提取 xAI 确认码（格式: "CODE xAI confirmation code"）
                        subj_match = re.match(r'^([A-Za-z0-9\-]{5,10})\s+xAI\s+confirmation\s+code', subject, re.IGNORECASE)
                        if subj_match:
                            code = subj_match.group(1)
                            print(f"🎉 验证码已找到: {code} （主题: {subject}）")
                            return code

                        # 回退：纯数字验证码
                        patterns = [
                            r'(?i)code[:\s-]*(\d{6,8})',
                            r'(?i)verification[:\s-]*(\d{6,8})',
                            r'\b(\d{6,8})\b',
                        ]

                        for pat in patterns:
                            match = re.search(pat, full_text)
                            if match:
                                code = match.group(1)
                                print(f"🎉 验证码已找到: {code} （主题: {subject}）")
                                return code

                        # 关键调试：收到邮件但没匹配到码
                        if full_text.strip():
                            print(f"📧 收到新邮件但未提取到验证码 → 主题: {subject}")
                            print(f"   正文预览: {full_text[:350]}...")
                            print("   --- 请复制上面内容给我优化正则 ---")

        except:
            pass

        time.sleep(4)

    print(f"⏰ 超时（共检查 {checks} 次），未收到验证码")
    return None


def destroy_test_email(email: str) -> None:
    """mail.tm 版本占位函数；由主流程统一调用以保持接口一致。"""
    if email:
        ACCOUNTS.pop(email, None)