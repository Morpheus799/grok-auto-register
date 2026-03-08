import time
import re
import random
import gc
import json
import subprocess
import os
import shutil
import string
import threading
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime as dt

from playwright.sync_api import sync_playwright

from email_utils import create_test_email, fetch_verification_code

# ====================== 配置 ======================
# 绕过系统代理（防止连接本地 CDP 时走代理）
os.environ["NO_PROXY"] = "localhost,127.0.0.1"
os.environ["no_proxy"] = "localhost,127.0.0.1"

file_lock = threading.Lock()
timestamp = dt.now().strftime("%m%d%H%M")

# 输出目录
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GROK_DIR = os.path.join(SCRIPT_DIR, "result_grok")
SSO_DIR = os.path.join(SCRIPT_DIR, "result_sso")
os.makedirs(GROK_DIR, exist_ok=True)
os.makedirs(SSO_DIR, exist_ok=True)

GROK_FILE = os.path.join(GROK_DIR, f"grok{timestamp}.txt")
SSO_FILE = os.path.join(SSO_DIR, f"sso{timestamp}.txt")

first_names = ["James", "John", "Robert", "Michael", "William",
               "David", "Richard", "Joseph", "Thomas", "Charles"]
last_names = ["Smith", "Johnson", "Williams", "Brown", "Jones",
              "Garcia", "Miller", "Davis", "Rodriguez", "Martinez"]


# ====================== 工具函数 ======================

def generate_password(length=14) -> str:
    upper = random.choice(string.ascii_uppercase)
    lower = random.choice(string.ascii_lowercase)
    digit = random.choice(string.digits)
    special = random.choice("!@#$%&*")
    rest = random.choices(
        string.ascii_letters + string.digits + "!@#$%&*",
        k=length - 4,
    )
    chars = list(upper + lower + digit + special + "".join(rest))
    random.shuffle(chars)
    return "".join(chars)


# ====================== 浏览器自动化 (Windows) ======================

def kill_chrome_on_port(port):
    """杀掉占用指定调试端口的残留 Chrome 进程 (Windows)"""
    try:
        # 使用 netstat 查找占用端口的 PID
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.strip().split()
                pid = parts[-1]
                if pid and pid.isdigit():
                    subprocess.run(
                        ["taskkill", "/F", "/PID", pid],
                        capture_output=True, timeout=5,
                    )
    except Exception:
        pass


def find_chrome_executable():
    """在 Windows 上查找 Chrome 可执行文件"""
    candidates = [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        # Edge (Chromium 内核，也支持 CDP)
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def restart_chrome_process(port=9222, user_data_dir=None):
    """启动 Chrome 并开启远程调试端口 (Windows)"""
    chrome_exe = find_chrome_executable()
    if not chrome_exe:
        print(f"[{port}] Chrome/Edge not found!")
        print(f"[{port}] 请安装 Google Chrome: https://www.google.com/chrome/")
        return None

    # 默认临时目录
    if user_data_dir is None:
        user_data_dir = os.path.join(tempfile.gettempdir(), "ChromeDevData")

    # 杀掉残留进程，清理用户目录
    kill_chrome_on_port(port)
    if os.path.exists(user_data_dir):
        try:
            shutil.rmtree(user_data_dir, ignore_errors=True)
        except Exception:
            pass

    args = [
        chrome_exe,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--incognito",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--disable-software-rasterizer",
        "--window-size=500,800",
        "--no-first-run",
        "--no-default-browser-check",
        '--lang=en-US',                    
        '--accept-lang=en-US,en',         
        '--disable-translate',
        '--start-minimized'
    ]

    try:
        # Windows: 使用 CREATE_NO_WINDOW 避免弹出命令行窗口
        CREATE_NO_WINDOW = 0x08000000
        proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
        import urllib.request
        for i in range(15):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1)
                return proc
            except Exception:
                time.sleep(1)
        return proc
    except Exception as e:
        print(f"[{port}] 启动 Chrome 失败: {e}")
        return None


def run_job(thread_id, task_id, timeout_sec=120):
    """
    运行一次完整的注册流程
    return: True (成功), False (失败/超时)
    """
    port = 9222 + thread_id
    user_data_dir = os.path.join(tempfile.gettempdir(), "ChromeDevData", f"Thread_{thread_id}")

    prefix = f"[T{thread_id}-#{task_id}]"

    def log(msg):
        elapsed = time.time() - job_start_time
        print(f"{prefix} [{elapsed:5.1f}s] {msg}")

    job_start_time = time.time()

    chrome_process = None
    browser = None

    try:
        # ---- 启动 Chrome ----
        log("启动 Chrome (headless)...")
        chrome_process = restart_chrome_process(port, user_data_dir)
        if not chrome_process:
            log("Chrome 启动失败")
            return False
        time.sleep(2)
        log("Chrome 已启动")

        with sync_playwright() as p:
            log(f"连接 CDP port={port}...")
            try:
                browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=10000)
            except Exception as e:
                log(f"CDP 连接失败: {e}")
                return False
            log("CDP 已连接")

            context = browser.contexts[0]
            page = context.pages[0] if context.pages else context.new_page()
            log(f"Context 已获取 (共{len(browser.contexts)}个, 页面数={len(context.pages)})")

            # 状态追踪
            grok_password = generate_password()
            state = {
                "email": "",
                "password": grok_password,
                "sso": "",
                "sso_rw": "",
                "sso_found": False,
            }

            # ---- SSO Cookie 监听 ----
            def handle_response(response):
                try:
                    if state["sso_found"]:
                        return
                    try:
                        headers = response.all_headers()
                    except Exception:
                        # 浏览器关闭时可能触发 TargetClosedError，忽略即可
                        return
                    set_cookie = headers.get("set-cookie", "")
                    if "sso=" in set_cookie:
                        sso_match = re.search(r'(?<![a-z-])sso=([^;]+)', set_cookie)
                        sso_rw_match = re.search(r'sso-rw=([^;]+)', set_cookie)
                        if sso_match:
                            state["sso"] = sso_match.group(1)
                            if sso_rw_match:
                                state["sso_rw"] = sso_rw_match.group(1)
                            state["sso_found"] = True
                            log(f"[Response监听] 捕获 SSO cookie (sso-rw={'有' if state['sso_rw'] else '无'})")
                except Exception:
                    pass

            page.on("response", handle_response)

            # ---- 步骤 1: 创建邮箱 ----
            if time.time() - job_start_time > timeout_sec:
                raise TimeoutError("Timeout")
            log("[步骤1] 创建临时邮箱...")
            try:
                email_address, username = create_test_email()
                if not email_address:
                    log("[步骤1] 邮箱创建失败 (返回空)")
                    return False
                state["email"] = email_address
                log(f"[步骤1] 邮箱: {email_address}")
            except Exception as e:
                log(f"[步骤1] 创建邮箱异常: {e}")
                return False

            # ---- 步骤 2: 打开注册页面 ----
            if time.time() - job_start_time > timeout_sec:
                raise TimeoutError("Timeout")
            log("[步骤2] 打开注册页面...")
            try:
                page.goto("https://accounts.x.ai/sign-up?redirect=grok-com", timeout=20000)
                log(f"[步骤2] 页面已加载 url={page.url}")
            except Exception as e:
                log(f"[步骤2] 页面加载异常(继续): {e}")

            # 诊断：输出页面标题和截图
            try:
                title = page.title()
                log(f"[步骤2] 页面标题: {title}")
                if "moment" in title.lower() or "challenge" in title.lower():
                    log("[步骤2] 检测到 Cloudflare 验证页，等待 10s...")
                    time.sleep(10)
                    title = page.title()
                    log(f"[步骤2] 等待后标题: {title}")
                screenshot_dir = os.path.join(tempfile.gettempdir(), "grok_debug")
                os.makedirs(screenshot_dir, exist_ok=True)
                screenshot_path = os.path.join(screenshot_dir, f"grok_debug_T{thread_id}.png")
                page.screenshot(path=screenshot_path)
                log(f"[步骤2] 截图已保存: {screenshot_path}")
            except Exception as e:
                log(f"[步骤2] 诊断异常: {e}")

            # 点击 Sign up with email
            try:
                btn = page.locator("button", has_text="Sign up with email").first
                if btn.is_visible(timeout=3000):
                    btn.click()
                    log("[步骤2] 点击 'Sign up with email'")
                    time.sleep(1)
                else:
                    log("[步骤2] 'Sign up with email' 按钮不可见，可能已在邮箱输入页")
            except Exception:
                log("[步骤2] 未找到 'Sign up with email' 按钮，继续")

            # 填入邮箱
            if time.time() - job_start_time > timeout_sec:
                raise TimeoutError("Timeout")
            log("[步骤2] 填入邮箱...")
            try:
                page.wait_for_selector("input[name='email']", timeout=5000)
                page.click("input[name='email']")
                page.keyboard.type(email_address)
                time.sleep(0.5)
                page.keyboard.press("Enter")
                log("[步骤2] 邮箱已提交")
                time.sleep(2)
            except Exception as e:
                log(f"[步骤2] 填入邮箱失败: {e}")
                return False

            # ---- 步骤 3: 等待验证码 ----
            if time.time() - job_start_time > timeout_sec:
                raise TimeoutError("Timeout")
            log("[步骤3] 等待验证码邮件...")

            code_filled = False
            for attempt in range(30):
                if time.time() - job_start_time > timeout_sec:
                    raise TimeoutError("Timeout")

                # 已经到密码页则跳过
                if page.locator("input[name='password']").count() > 0:
                    log("[步骤3] 已到密码填写页面，跳过验证码步骤")
                    break

                if not code_filled and page.locator("input").count() > 0:
                    code = None
                    for poll in range(15):
                        code = fetch_verification_code(email_address)
                        if code:
                            break
                        if poll % 5 == 4:
                            log(f"[步骤3] 第{poll+1}次轮询，暂未收到验证码...")
                        time.sleep(1)

                    if code:
                        log(f"[步骤3] 获取到验证码: {code}")
                        try:
                            page.locator("input").first.click(timeout=2000)
                            page.keyboard.type(code)
                            code_filled = True
                            log("[步骤3] 验证码已填入")
                        except Exception as e:
                            log(f"[步骤3] 填入验证码失败: {e}")
                            return False
                    else:
                        log("[步骤3] 15次轮询后仍未获取验证码")
                        return False

                if code_filled:
                    break
                time.sleep(1)

            # ---- 步骤 4: 完善信息 ----
            if time.time() - job_start_time > timeout_sec:
                raise TimeoutError("Timeout")
            log("[步骤4] 等待密码输入框...")

            try:
                page.wait_for_selector("input[name='password']", timeout=15000)
                fname = random.choice(first_names)
                lname = random.choice(last_names)

                page.fill("input[name='givenName']", fname)
                page.fill("input[name='familyName']", lname)
                page.fill("input[name='password']", grok_password)
                log(f"[步骤4] 信息已填入: {fname} {lname}, 密码长度={len(grok_password)}")
            except Exception as e:
                log(f"[步骤4] 完善信息出错: {e}")
                return False

            # ---- 步骤 5: 人机验证 & 提交 ----
            if time.time() - job_start_time > timeout_sec:
                raise TimeoutError("Timeout")
            log("[步骤5] 等待 Turnstile...")
            time.sleep(2)

            # 检测 Turnstile 是否已自动通过
            def check_turnstile_passed():
                try:
                    return page.evaluate("""() => {
                        const inp = document.querySelector('input[name="cf-turnstile-response"]');
                        if (inp && inp.value && inp.value.length > 10) return true;
                        const iframes = document.querySelectorAll('iframe[src*="turnstile"]');
                        for (const f of iframes) {
                            try {
                                if (f.getAttribute('data-state') === 'ready') return true;
                            } catch(e) {}
                        }
                        return false;
                    }""")
                except Exception:
                    return False

            turnstile_auto = False
            for _ in range(3):
                if check_turnstile_passed():
                    turnstile_auto = True
                    break
                time.sleep(1)

            if turnstile_auto:
                log("[步骤5] Turnstile 已自动通过，直接提交")
            else:
                log("[步骤5] Turnstile 未自动通过，手动点击...")
                try:
                    btn = page.locator("button", has_text="Complete sign up").first
                    if btn.is_visible(timeout=3000):
                        btn_box = btn.bounding_box()
                        if btn_box:
                            btn_x = btn_box['x'] + btn_box['width'] / 2
                            btn_y = btn_box['y'] + btn_box['height'] / 2
                            target_x = btn_x - 170
                            target_y = btn_y - 65

                            log(f"[步骤5] Turnstile 区域点击 ({target_x:.0f}, {target_y:.0f})")
                            for i in range(5):
                                if time.time() - job_start_time > timeout_sec:
                                    raise TimeoutError("Timeout")
                                page.mouse.move(target_x, target_y)
                                page.mouse.click(target_x, target_y)
                                if state["sso_found"]:
                                    log("[步骤5] 点击过程中已获取 SSO，提前结束")
                                    break
                                time.sleep(2)
                                if check_turnstile_passed():
                                    log("[步骤5] Turnstile 已通过")
                                    break
                            log("[步骤5] Turnstile 点击完成")
                except TimeoutError:
                    raise
                except Exception as e:
                    log(f"[步骤5] Turnstile 点击异常: {e}")

            # 点击提交
            log("[步骤5] 点击 'Complete sign up'...")
            try:
                btn = page.locator("button", has_text="Complete sign up").first
                if btn.is_visible(timeout=2000):
                    btn.click(timeout=3000)
                    log("[步骤5] 提交按钮已点击")
                else:
                    log("[步骤5] 提交按钮不可见")
            except Exception as e:
                log(f"[步骤5] 点击提交异常: {e}")

            # ---- 步骤 6: 等待结果 ----
            log("[步骤6] 等待注册结果 (最多15s)...")
            wait_start = time.time()
            while time.time() - wait_start < 15:
                try:
                    cookies = context.cookies()
                    cookie_dict = {c['name']: c['value'] for c in cookies}
                    if 'sso' in cookie_dict and not state["sso_found"]:
                        state["sso"] = cookie_dict['sso']
                        state["sso_rw"] = cookie_dict.get('sso-rw', '')
                        state["sso_found"] = True
                        log(f"[步骤6] 从 context cookies 捕获 SSO (sso-rw={'有' if state['sso_rw'] else '无'})")
                except Exception:
                    pass

                if state["sso_found"]:
                    break
                time.sleep(1)

            if not state["sso_found"]:
                log("[步骤6] 注册失败 - 未获取到 SSO cookie")
                return False

            sso = state["sso"]
            sso_rw = state["sso_rw"]
            email = state["email"]
            password = state["password"]

            log(f"[步骤6] 注册成功! SSO={sso[:20]}...")

            # 写入文件
            with file_lock:
                with open(GROK_FILE, "a", encoding="utf-8") as f:
                    f.write(f"Email: {email}\n")
                    f.write(f"Password: {password}\n")
                    f.write(f"SSO: {sso}\n")
                    if sso_rw:
                        f.write(f"SSO-RW: {sso_rw}\n")
                    f.write("-" * 40 + "\n")
                with open(SSO_FILE, "a", encoding="utf-8") as f:
                    f.write(f"{sso}\n")

            return True

    except TimeoutError:
        elapsed = time.time() - job_start_time
        log(f"TIMEOUT ({elapsed:.1f}s)")
        return False
    except Exception as e:
        log(f"未预期异常: {e}")
        return False
    finally:
        try:
            if browser:
                browser.close()
        except Exception:
            pass
        try:
            if chrome_process:
                chrome_process.terminate()
                try:
                    chrome_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    chrome_process.kill()
                    chrome_process.wait(timeout=3)
        except Exception:
            pass
        # 强杀残留进程
        kill_chrome_on_port(port)
        # 清理用户数据目录释放磁盘
        try:
            if os.path.exists(user_data_dir):
                shutil.rmtree(user_data_dir, ignore_errors=True)
        except Exception:
            pass
        # 触发 Python 垃圾回收
        gc.collect()


def worker(thread_id, count):
    prefix = f"[Thread-{thread_id}]"
    print(f"{prefix} 启动，目标: {count} 个账号")

    success_count = 0
    fail_streak = 0
    while success_count < count:
        current_task = success_count + 1
        success = run_job(thread_id, current_task, timeout_sec=120)
        if success:
            print(f"{prefix} 任务 {current_task} 完成! (成功 {success_count + 1}/{count})")
            success_count += 1
            fail_streak = 0
            time.sleep(3)
        else:
            fail_streak += 1
            print(f"{prefix} 任务 {current_task} 失败 (连续失败 {fail_streak} 次)，重试...")
            if fail_streak >= 5:
                print(f"{prefix} 连续失败 {fail_streak} 次，等待 10s 后重试")
                time.sleep(10)
                fail_streak = 0
            else:
                time.sleep(2)

    print(f"{prefix} 全部完成!")


def main():
    print("=" * 60)
    print("     Grok 辅助注册助手 [v6.0 - Windows]")
    print("=" * 60)

    # 检测 Chrome
    chrome_path = find_chrome_executable()
    if chrome_path:
        print(f"  Chrome 路径: {chrome_path}")
    else:
        print("  ⚠ 未检测到 Chrome/Edge，请先安装!")
        print("  下载地址: https://www.google.com/chrome/")
        return

    try:
        total_count = int(input("每个线程要注册的次数: "))
        thread_count = int(input("线程数量 (推荐 1-3): "))
    except Exception:
        total_count = 1
        thread_count = 1
        print("输入无效，默认 1 线程 x 1 次")

    print(f"\n即将启动 {thread_count} 个线程，每线程 {total_count} 次任务")
    print(f"输出文件: {GROK_FILE} / {SSO_FILE}")
    print("=" * 60)

    with ThreadPoolExecutor(max_workers=thread_count) as executor:
        futures = []
        for i in range(thread_count):
            futures.append(executor.submit(worker, i, total_count))
            time.sleep(2)

        for f in futures:
            f.result()

    print("=" * 60)
    print("所有任务结束!")
    print(f"结果文件: {GROK_FILE}")
    print("=" * 60)


if __name__ == "__main__":
    main()
