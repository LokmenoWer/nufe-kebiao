"""单用户命令行验证：登录 CAS → 抓本学期课表 → 打印/保存 JSON。

用法: python -m app.cli 学号 [-o out.json] [--semester 161] [--dump debug_dir]
密码通过交互输入（不回显），或环境变量 NUFE_PASSWORD。
遇到图形验证码会保存图片并打开，由你本人输入；遇到短信二次认证会请你输入手机收到的验证码。
--dump 会把登录过程中每一步的落地页保存下来（不含密码），方便排查学校页面变化。
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from . import cas, jwxt
from .polite import PoliteSession


def _ask_captcha(flow: cas.LoginFlow) -> str:
    img, ctype = flow.captcha_image()
    path = Path(tempfile.gettempdir()) / f"nufe-captcha.{ctype.split('/')[-1]}"
    path.write_bytes(img)
    print(f"需要图形验证码，图片已保存到 {path}", file=sys.stderr)
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    return input("请输入图中的验证码: ").strip()


def _login(flow: cas.LoginFlow) -> None:
    stage = flow.start()
    while stage != "done":
        if stage == "captcha":
            try:
                stage = flow.submit(_ask_captcha(flow))
            except cas.CasError as e:
                if flow.stage != "captcha":
                    raise
                print(f"[验证码] {e}，请重试", file=sys.stderr)
        elif stage == "slider":
            # 终端里没法拖动拼图；也不代替用户去拖
            raise cas.CasError("统一身份认证要求滑块拼图验证，命令行无法完成，请改用网页登录")
        elif stage == "mfa" and flow.reauth.get("method") == "otp":
            while True:
                code = input("请输入身份验证器 App 上的动态口令: ").strip()
                if not code:
                    continue
                try:
                    stage = flow.verify_sms(code)
                    break
                except cas.CasError as e:
                    print(f"[动态口令] {e}", file=sys.stderr)
        elif stage == "mfa":
            phone = flow.reauth.get("phone") or "绑定的手机"
            input(f"需要短信二次认证，按回车向 {phone} 发送验证码…")
            try:
                print(flow.send_sms(), file=sys.stderr)
            except cas.CasError as e:
                print(e, file=sys.stderr)
            while True:
                code = input("请输入短信验证码（直接回车重新发送）: ").strip()
                if not code:
                    try:
                        print(flow.send_sms(), file=sys.stderr)
                    except cas.CasError as e:
                        print(e, file=sys.stderr)
                    continue
                try:
                    stage = flow.verify_sms(code)
                    break
                except cas.CasError as e:
                    print(f"[短信验证] {e}", file=sys.stderr)
        else:
            raise cas.CasError(f"登录停在了未知步骤：{stage}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("username")
    ap.add_argument("-o", "--output")
    ap.add_argument("--semester", type=int)
    ap.add_argument("--dump", type=Path, help="保存登录各步骤页面到该目录")
    args = ap.parse_args()
    password = os.environ.get("NUFE_PASSWORD") or getpass.getpass("统一身份认证密码: ")

    # 每次运行都当作同一台"设备"，否则学校每次都会判定为新设备并要求短信验证
    dev_file = Path(__file__).resolve().parent.parent / ".nufe_device_id"
    if not dev_file.exists():
        dev_file.write_text(cas.new_device_id())
    s = PoliteSession()
    flow = cas.LoginFlow(s, args.username, password, device_id=dev_file.read_text().strip(), debug_dir=args.dump)
    del password
    try:
        _login(flow)
    except cas.CasError as e:
        print(f"[登录失败] {e}", file=sys.stderr)
        if args.dump:
            print(f"各步骤页面已保存到 {args.dump}/", file=sys.stderr)
        return 1
    finally:
        flow.wipe_password()
    print("登录成功，CASTGC:", "有" if any(c.name == "CASTGC" for c in s.cookies) else "无", file=sys.stderr)

    data = jwxt.fetch_timetable(s, args.semester)
    print(f"{data['student'].get('name')} {data['semester_name']}(id={data['semester_id']}) "
          f"personId={data['person_id']} 共 {len(data['activities'])} 条排课", file=sys.stderr)
    for a in sorted(data["activities"], key=lambda a: (a["weekday"], a["startUnit"])):
        print(f"  周{a['weekday']} {a['startUnit']}-{a['endUnit']}节 {a['startTime']}-{a['endTime']} "
              f"{a['courseName']} @{a['room']} {'/'.join(a['teachers'])} [{a['weeksStr']}]", file=sys.stderr)
    out = json.dumps(data, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(out)
        print("已保存到", args.output, file=sys.stderr)
    else:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
