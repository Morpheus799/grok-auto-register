# email_utils.py 
import requests
import time
import re
import os
from typing import Tuple, Optional
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

WORKER_DOMAIN = os.getenv("WORKER_DOMAIN")
FREEMAIL_TOKEN = os.getenv("FREEMAIL_TOKEN")

if not WORKER_DOMAIN or not FREEMAIL_TOKEN:
    raise ValueError("❌ 缺少环境变量: WORKER_DOMAIN 或 FREEMAIL_TOKEN")

BASE_URL = f"https://{WORKER_DOMAIN}"
HEADERS = {"Authorization": f"Bearer {FREEMAIL_TOKEN}"}

def create_test_email() -> Tuple[str, str]:
    """创建临时邮箱（使用自建服务）"""
    try:
        resp = requests.get(
            f"{BASE_URL}/api/generate",
            headers=HEADERS,
            timeout=15
        )
        
        if resp.status_code != 200:
            raise Exception(f"创建邮箱失败 {resp.status_code}: {resp.text}")

        data = resp.json()
        email = data.get("email")
        
        if not email:
            raise Exception("API 未返回邮箱地址")

        print(f"✅ 创建临时邮箱成功: {email}")
        return email, email  # 返回 (email, email) 保持兼容性

    except Exception as e:
        print(f"❌ 创建邮箱异常: {type(e).__name__} - {e}")
        raise Exception("Failed to generate temp email")


def destroy_test_email(email: str) -> None:
    """删除临时邮箱（自建服务）。"""
    if not email:
        return

    try:
        delete_resp = requests.delete(
            f"{BASE_URL}/api/mailboxes",
            params={"address": email},
            headers=HEADERS,
            timeout=10
        )
        if delete_resp.status_code == 200:
            print(f"🗑️ 已删除邮箱: {email}")
        elif delete_resp.status_code in (404, 410):
            print(f"ℹ️ 邮箱已不存在: {email}")
        else:
            print(f"⚠️ 删除邮箱失败: {delete_resp.status_code}")
    except Exception as e:
        print(f"⚠️ 删除邮箱异常: {e}")


def fetch_verification_code(email: str, timeout: int = 180) -> Optional[str]:
    """获取验证码（使用自建服务）。"""
    print(f"🔍 正在监听 {email} 的验证码...（最多 {timeout} 秒，每 2 秒查一次）")

    start_time = time.time()
    checks = 0
    interval = 2  # 每 2 秒轮询一次

    code = None  # 用于存储验证码

    while time.time() - start_time < timeout:
        checks += 1
        try:
            resp = requests.get(
                f"{BASE_URL}/api/emails",
                params={"mailbox": email},
                headers=HEADERS,
                timeout=10
            )

            if resp.status_code == 200:
                emails = resp.json()

                if emails and len(emails) > 0:
                    first_email = emails[0]

                    # 方法1：从预解析的验证码字段获取
                    extracted_code = (
                        first_email.get("verification_code") or
                        first_email.get("code") or
                        first_email.get("verify_code")
                    )

                    if extracted_code:
                        code = extracted_code.replace("-", "")
                        subject = first_email.get("subject", "")
                        print(f"🎉 验证码已找到: {code} （主题: {subject}）")
                        return code

                    # 方法2：从主题提取 xAI 验证码（格式: "XXX-XXX xAI confirmation code"）
                    subject = first_email.get("subject", "")

                    # 匹配 XXX-XXX 格式
                    match = re.search(r'^([A-Z0-9]{3}-[A-Z0-9]{3})', subject)
                    if match:
                        code = match.group(1).replace("-", "")
                        print(f"🎉 验证码已找到: {code} （主题: {subject}）")
                        return code

                    # 方法3：匹配纯字母数字验证码（无横杠）
                    match = re.search(r'^([A-Z0-9]{5,10})\s+xAI\s+confirmation\s+code', subject, re.IGNORECASE)
                    if match:
                        code = match.group(1)
                        print(f"🎉 验证码已找到: {code} （主题: {subject}）")
                        return code

                    # 调试：收到邮件但未提取到验证码
                    if subject:
                        print(f"📧 收到邮件但未提取到验证码 → 主题: {subject}")
                        body = first_email.get("text", "") or first_email.get("html", "")
                        if body:
                            print(f"   正文预览: {str(body)[:200]}...")

        except Exception as e:
            if checks <= 3:  # 只在前几次显示错误
                print(f"⚠️ 轮询异常 (第{checks}次): {e}")

        time.sleep(interval)

    print(f"⏰ 超时（共检查 {checks} 次），未收到验证码")
    return None