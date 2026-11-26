"""Exercise the installed workbench on a disposable Windows runner."""
import json
import os
import secrets
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import win32api
import win32con
import win32event
import win32process
from win32com.shell import shell, shellcon
from playwright.sync_api import expect, sync_playwright

root = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "WAT G01"
url = "http://localhost:8080"
password = secrets.token_urlsafe(24)
with sync_playwright() as driver:
    browser = driver.chromium.launch(channel="msedge")
    for first_run in (True, False):
        if first_run:
            process = subprocess.Popen([root / "python.exe", "-X", "utf8", "-B", root / "launch.py"],
                                       stdin=subprocess.PIPE, text=True, encoding="utf-8",
                                       cwd=root, env={**os.environ, "PATH": os.environ["SystemRoot"]})
            process.stdin.write(f"operator\n{password}\n{password}\n")
            process.stdin.close()
            handle = win32api.OpenProcess(win32con.SYNCHRONIZE | win32con.PROCESS_TERMINATE |
                                          win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, process.pid)
        else:
            subprocess.run([str(Path("installer/WAT-G01-Setup-x64.exe").resolve()), "/S"], check=True)
            handle = shell.ShellExecuteEx(fMask=shellcon.SEE_MASK_NOCLOSEPROCESS,
                                          lpFile=os.environ["WAT_SHORTCUT"], nShow=1)["hProcess"]
        try:
            for _ in range(80):
                if win32event.WaitForSingleObject(handle, 0) == win32event.WAIT_OBJECT_0:
                    raise RuntimeError(f"Workbench exited with code {win32process.GetExitCodeProcess(handle)}")
                try:
                    with urllib.request.urlopen(url + "/api/health", timeout=1) as result:
                        assert json.load(result)["status"] == "ok"
                    break
                except urllib.error.URLError:
                    time.sleep(0.25)
            else:
                raise RuntimeError("Workbench did not start")
            page = browser.new_page()
            page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(url + "/") else route.abort())
            page.goto(url)
            page.get_by_label("账号", exact=True).fill("operator")
            page.get_by_label("密码", exact=True).fill(password)
            page.get_by_role("button", name="登录", exact=True).click()
            if first_run:
                page.get_by_role("button", name="新建产品", exact=True).click()
                page.get_by_label("产品型号").fill("Offline-WAT-01")
                page.get_by_role("button", name="保存产品").click()
            expect(page.locator("#workbench-product option:checked")).to_have_text("Offline-WAT-01")
            page.close()
        finally:
            if win32event.WaitForSingleObject(handle, 0) == win32event.WAIT_TIMEOUT:
                win32api.TerminateProcess(handle, 0)
            win32event.WaitForSingleObject(handle, 10000)
            handle.Close()
    browser.close()
