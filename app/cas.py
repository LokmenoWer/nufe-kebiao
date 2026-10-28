"""南京财经大学统一身份认证（新版金智 CAS）登录。

登录是一个可能需要用户参与的多步流程（LoginFlow）：
    start → [captcha：把图片转给用户，用户填写] → submit → [mfa：发短信，用户填写验证码] → done
图形验证码和短信验证码都只"转发"给真人填写，程序不做任何识别或绕过。
后台刷新没有真人在场，遇到这两步分别抛 CaptchaRequired / MfaRequired。
"""
from __future__ import annotations

import base64
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

AUTH_HOST = "authserver.nufe.edu.cn"
AUTH_BASE = f"https://{AUTH_HOST}/authserver"
COURSE_TABLE_URL = "https://jwxt.nufe.edu.cn/student/for-std/course-table"
JW_SSO_URL = "https://jwxt.nufe.edu.cn/student/sso/login"  # 302 → authserver，service 已带好

# 金智二次认证的默认接口；进入二次认证页后会优先从页面脚本里解析实际路径
REAUTH_SEND_SMS = f"{AUTH_BASE}/dynamicCode/getDynamicCodeByReauth.do"
REAUTH_SUBMIT = f"{AUTH_BASE}/reAuthCheck/reAuthSubmit.do"
FP_COOKIE = "MULTIFACTOR_BROWSER_FINGERPRINT"

_AES_CHARS = "ABCDEFGHJKMNPQRSTWXYZabcdefhijkmnprstwxyz2345678"


class CasError(Exception):
    """登录失败（密码错误、账号锁定、验证码错误等）。"""


class CaptchaRequired(CasError):
    """需要图形验证码，而当前没有真人可以填写（后台刷新）。"""


class MfaRequired(CasError):
    """需要手机短信二次认证，而当前没有真人可以填写（后台刷新）。"""


def new_device_id() -> str:
    """本服务作为一台"设备"的标识，格式同页面 generateFingerPrint()（32 位大写 MD5 十六进制）。

    同一用户应始终复用同一个值：学校的"非常用设备"判断和"信任此设备"都绑定在它上面。
    """
    return secrets.token_hex(16).upper()


_AJAX = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json, text/javascript, */*; q=0.01"}


def random_string(n: int) -> str:
    return "".join(secrets.choice(_AES_CHARS) for _ in range(n))


def encrypt_password(password: str, salt: str) -> str:
    """复刻 encrypt.js：AES-CBC-Pkcs7(randomString(64)+pwd, key=salt, iv=randomString(16))。"""
    key = salt.strip().encode()
    iv = random_string(16).encode()
    padder = padding.PKCS7(128).padder()
    data = padder.update((random_string(64) + password).encode()) + padder.finalize()
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return base64.b64encode(enc.update(data) + enc.finalize()).decode()


def is_auth_login_url(url: str) -> bool:
    u = urlparse(url)
    return u.hostname == AUTH_HOST and u.path.rstrip("/").endswith("/login")


def is_jw_login_url(url: str) -> bool:
    """教务自己的登入页（未登录访问业务页时会先落到这里，而不是直接跳 CAS）。"""
    u = urlparse(url)
    return u.hostname == "jwxt.nufe.edu.cn" and u.path.rstrip("/") == "/student/login"


def is_logged_out_url(url: str) -> bool:
    return is_auth_login_url(url) or is_jw_login_url(url)


def is_reauth_page(url: str, html: str) -> bool:
    u = urlparse(url)
    return u.hostname == AUTH_HOST and (
        "reAuth" in u.path or "isMultifactor=true" in u.query or "reAuthSubmit" in html or "二次认证" in html)


def parse_login_form(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    # 新版页面有多个表单（账号密码/短信/扫码），优先取账号密码表单
    form = soup.find("form", id="pwdFromId") or soup

    def val(**attrs) -> str | None:
        el = form.find("input", attrs=attrs) or soup.find("input", attrs=attrs)
        return el.get("value", "") if el else None

    salt = val(id="pwdEncryptSalt")
    execution = val(name="execution")
    if not salt or not execution:
        raise CasError("登录页结构异常：未找到 pwdEncryptSalt / execution")
    return {"salt": salt, "execution": execution, "lt": val(name="lt") or ""}


def parse_qr_form(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", id="qrLoginForm")
    if not form:
        raise CasError("学校登录页未提供扫码登录")
    fields = {el.get("name"): el.get("value", "") for el in form.find_all("input") if el.get("name")}
    if not fields.get("execution"):
        raise CasError("登录页结构异常：扫码表单缺少 execution")
    return fields


def parse_error_tip(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for sel in ("#showErrorTip", "#formErrorTip", ".form-error", "#msg"):
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            return el.get_text(strip=True)
    return "登录失败（未能解析错误提示）"


def _resolve(url: str, path: str) -> str:
    # 页面脚本常写成 contextPath + '/dynamicCode/...'，此时缺 /authserver 前缀
    if path.startswith("/") and not path.startswith("/authserver/"):
        return AUTH_BASE + path
    return urljoin(url, path)


def pin_auth_url(url: str) -> str:
    """二次认证的短信接口必须落在学校认证服务器上，不跟随页面里的绝对外链。"""
    u = urlparse(url)
    if u.scheme != "https" or u.hostname != AUTH_HOST:
        raise CasError("二次认证地址不在学校认证服务器上")
    return url


def safe_image_type(header: str) -> str | None:
    """只接受位图。拒绝 SVG（可带脚本）和缺 Content-Type 的响应。"""
    ctype = (header or "").split(";", 1)[0].strip().lower()
    if ctype.startswith("image/") and ctype != "image/svg+xml":
        return ctype
    return None


def _reauth_param(html: str, key: str) -> str:
    m = re.search(rf'"{key}"\s*:\s*"((?:[^"\\]|\\.)*)"', html)
    return m.group(1).replace("\\/", "/") if m else ""


def parse_reauth_page(url: str, html: str) -> dict:
    """从二次认证页提取 reAuthParams（与 reAuth.js 提交时使用的字段一致）。"""
    info: dict = {"send_url": REAUTH_SEND_SMS, "submit_url": REAUTH_SUBMIT, "phone": ""}
    m = re.search(r"['\"]([^'\"]*getDynamicCodeByReauth\.do)['\"]", html)
    if m:
        info["send_url"] = pin_auth_url(_resolve(url, m.group(1)))
    m = re.search(r"['\"]([^'\"]*reAuthSubmit\.do)['\"]", html)
    if m:
        info["submit_url"] = pin_auth_url(_resolve(url, m.group(1)))
    m = re.search(r"1\d{2}\*{4}\d{4}", html)
    if m:
        info["phone"] = m.group(0)
    info["reAuthType"] = _reauth_param(html, "reAuthType") or "3"
    info["isMultifactor"] = _reauth_param(html, "isMultifactor") or "true"
    info["userId"] = _reauth_param(html, "reAuthUserId")
    info["service"] = (_reauth_param(html, "service")
                       or (parse_qs(urlparse(url).query).get("service") or [""])[0])
    return info


@dataclass
class LoginFlow:
    session: requests.Session
    username: str
    password: str
    stage: str = "init"            # init | captcha | qr | mfa | done
    login_url: str = ""
    fields: dict = field(default_factory=dict)
    reauth: dict = field(default_factory=dict)
    sms_ready_at: float = 0.0
    device_id: str = field(default_factory=new_device_id)
    qr_uuid: str = ""
    qr_fields: dict = field(default_factory=dict)
    qr_status: str = ""            # waiting | scanned | expired
    course_html: str | None = None  # _finish 已经拿到的课表页，避免登录后再 GET 一次
    debug_dir: Path | None = None
    _step: int = 0

    # ---- 调试：把每一步落地页存下来，便于对照学校页面调整 ----
    def _dump(self, name: str, r: requests.Response) -> None:
        if not self.debug_dir:
            return
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        self._step += 1
        p = self.debug_dir / f"{self._step:02d}-{name}"
        body = r.content if "image" in r.headers.get("content-type", "") else r.text.encode()
        set_cookies = [c.split("=", 1)[0] for h in [*r.history, r]
                       for c in h.raw.headers.getlist("Set-Cookie")] if hasattr(r.raw, "headers") else []
        p.with_suffix(".txt").write_text(
            f"{r.request.method} {r.url}\nstatus={r.status_code}\n"
            + "\n".join(f"  redirect: {h.status_code} {h.url}" for h in r.history)
            + f"\n  set-cookie: {set_cookies}\n  jar: {sorted({c.name for c in self.session.cookies})}\n", "utf-8")
        p.with_suffix(".html").write_bytes(body)

    def wipe_password(self) -> None:
        self.password = ""

    # ---- 步骤 ----
    def _open_login_page(self) -> str | None:
        """打开 CAS 登录页并登记设备指纹；返回登录页 HTML。已登录（TGC 有效）则直接完成并返回 None。"""
        s = self.session
        r = s.get(COURSE_TABLE_URL)
        if not is_logged_out_url(r.url):
            self.stage = "done"  # 已有有效登录态
            return None
        r = s.get(JW_SSO_URL)
        self._dump("login-page", r)
        if not is_auth_login_url(r.url):
            self._finish()  # CAS 的 TGC 仍有效，直接签发了 ticket
            return None
        self.login_url = r.url
        self._register_device()
        return r.text

    def start(self) -> str:
        html = self._open_login_page()
        if html is None:
            return self.stage
        self.fields = parse_login_form(html)
        if need_captcha(self.session, self.username):
            self.stage = "captcha"
            return self.stage
        return self.submit("")

    # ---- 扫码登录（复刻 qrcode.js）：二维码交给用户用手机扫，程序只轮询状态 ----
    def start_qr(self) -> str:
        html = self._open_login_page()
        if html is None:
            return self.stage
        self.qr_fields = parse_qr_form(html)
        self._new_qr_token()
        self.stage = "qr"
        return self.stage

    def _new_qr_token(self) -> None:
        params = {"ts": int(time.time() * 1000)}
        if self.qr_uuid:
            params["uuid"] = self.qr_uuid
        r = self.session.get(f"{AUTH_BASE}/qrCode/getToken", params=params, headers=_AJAX)
        self._dump("qr-token", r)
        token = r.text.strip()
        if r.status_code != 200 or not token or len(token) > 128 or "<" in token:
            raise CasError("获取登录二维码失败，请稍后再试")
        self.qr_uuid = token
        self.qr_status = "waiting"

    def refresh_qr(self) -> None:
        if self.stage != "qr":
            raise CasError("当前不在扫码登录步骤")
        self._new_qr_token()

    def qr_image(self) -> tuple[bytes, str]:
        r = self.session.get(f"{AUTH_BASE}/qrCode/getCode", params={"uuid": self.qr_uuid})
        self._dump("qr-image", r)
        ctype = safe_image_type(r.headers.get("content-type", ""))
        if r.status_code != 200 or not ctype:
            raise CasError("获取登录二维码图片失败")
        return r.content, ctype

    def poll_qr(self) -> str:
        """getStatus.htl：1=已确认登录 2=已扫描待确认 3=已失效，其余=等待扫描。"""
        if self.stage != "qr":
            raise CasError("当前不在扫码登录步骤")
        if self.qr_status == "expired":
            return self.stage
        r = self.session.get(f"{AUTH_BASE}/qrCode/getStatus.htl",
                             params={"ts": int(time.time() * 1000), "uuid": self.qr_uuid}, headers=_AJAX)
        code = r.text.strip()
        if code == "2":
            self.qr_status = "scanned"
        elif code == "3":
            self.qr_status = "expired"
        elif code == "1":
            self._dump("qr-status-ok", r)
            return self._submit_qr()
        return self.stage

    def _submit_qr(self) -> str:
        service = (parse_qs(urlparse(self.login_url).query).get("service") or [""])[0]
        r = self.session.post(f"{AUTH_BASE}/login", params={"display": "qrLogin", "service": service}, data={
            "lt": self.qr_fields.get("lt", ""),
            "uuid": self.qr_uuid,
            "cllt": "qrLogin",
            "dllt": self.qr_fields.get("dllt", "generalLogin"),
            "execution": self.qr_fields["execution"],
            "_eventId": "submit",
            "rmShown": self.qr_fields.get("rmShown", "1"),
        })
        self._dump("after-qr-submit", r)
        if is_auth_login_url(r.url):
            self.qr_status = "expired"
            raise CasError(parse_error_tip(r.text).replace("登录失败（未能解析错误提示）", "扫码登录失败，请刷新二维码重试"))
        return self._after_login_post(r)

    def _register_device(self) -> None:
        """同页面 common-header.js：登录页加载时把设备指纹报给 /bfp/info，二次认证结果与之绑定。"""
        if self._has_fp_cookie():
            return
        r = self.session.get(f"{AUTH_BASE}/bfp/info", params={"bfp": self.device_id, "_": int(time.time() * 1000)},
                             headers=_AJAX)
        self._dump("bfp", r)
        if not self._has_fp_cookie():
            self.session.cookies.set(FP_COOKIE, self.device_id, domain=AUTH_HOST, path="/")

    def _has_fp_cookie(self) -> bool:
        return any(c.name == FP_COOKIE for c in self.session.cookies)

    def captcha_image(self) -> tuple[bytes, str]:
        r = self.session.get(f"{AUTH_BASE}/getCaptcha.htl?{int(time.time() * 1000)}")
        self._dump("captcha", r)
        ctype = safe_image_type(r.headers.get("content-type", ""))
        if r.status_code != 200 or not ctype:
            raise CasError("获取验证码图片失败")
        return r.content, ctype

    def submit(self, captcha: str) -> str:
        r = self.session.post(self.login_url, data={
            "username": self.username,
            "password": encrypt_password(self.password, self.fields["salt"]),
            "captcha": captcha,
            "rememberMe": "true",
            "_eventId": "submit",
            "cllt": "userNameLogin",
            "dllt": "generalLogin",
            "lt": self.fields["lt"],
            "execution": self.fields["execution"],
        })
        self._dump("after-submit", r)

        if is_auth_login_url(r.url):
            tip = parse_error_tip(r.text)
            if "验证码" in tip:
                # 验证码错误或新触发：页面已刷新 execution/salt，回到 captcha 阶段让用户重填
                self.fields = parse_login_form(r.text)
                self.stage = "captcha"
                raise CasError(tip)
            raise CasError(tip)
        return self._after_login_post(r)

    def _after_login_post(self, r: requests.Response) -> str:
        """账号密码与扫码登录提交后的共同处理：可能进入短信二次认证，否则完成。"""
        if is_reauth_page(r.url, r.text):
            self.reauth = parse_reauth_page(r.url, r.text)
            self.stage = "mfa"
            return self.stage
        return self._finish()

    def send_sms(self) -> str:
        if self.stage != "mfa":
            raise CasError("当前不需要短信验证")
        if self.reauth.get("reAuthType") != "3":
            raise CasError(f"学校要求的二次认证方式不是短信（reAuthType={self.reauth.get('reAuthType')}），暂不支持")
        wait = int(self.sms_ready_at - time.time())
        if wait > 0:
            raise CasError(f"请 {wait} 秒后再重新发送")
        r = self.session.post(self.reauth["send_url"], data={
            "userName": self.reauth.get("userId") or self.username, "authCodeTypeName": "reAuthDynamicCodeType"},
            headers=_AJAX)
        self._dump("send-sms", r)
        try:
            j = r.json()
        except ValueError:
            raise CasError(f"发送短信失败（HTTP {r.status_code}）") from None
        # reAuth.js：res == success/other_success 为成功；code_time_fail 表示还没到重发时间
        self.sms_ready_at = time.time() + int(j.get("codeTime") or 60)
        msg = j.get("returnMessage") or ""
        if j.get("res") in ("success", "other_success"):
            return f"{msg or '验证码已发送'}{'' if j.get('mobile', '') in msg else j.get('mobile', '')}"
        raise CasError(msg or f"发送短信失败：{j.get('res')}")

    def verify_sms(self, code: str, trust_device: bool = True) -> str:
        if self.stage != "mfa":
            raise CasError("当前不需要短信验证")
        # 字段与 reAuth.js 的 loginParams 完全一致；skipTmpReAuth=true 即页面上的"信任此设备"
        r = self.session.post(self.reauth["submit_url"], data={
            "service": self.reauth.get("service", ""),
            "reAuthType": self.reauth.get("reAuthType", "3"),
            "isMultifactor": self.reauth.get("isMultifactor", "true"),
            "password": "",
            "dynamicCode": code.strip(),
            "uuid": "",
            "answer1": "",
            "answer2": "",
            "otpCode": "",
            "skipTmpReAuth": "true" if trust_device else "false",
        }, headers=_AJAX)
        self._dump("verify-sms", r)
        try:
            j = r.json()
        except ValueError:
            raise CasError(f"短信验证失败（HTTP {r.status_code}）") from None
        if j.get("code") in ("reAuth_failed", "reAuth_unauthorized"):
            raise CasError(j.get("msg") or "短信验证码错误")
        # 与页面行为一致：通过后访问 /login?service=...，CAS 直接签发 ticket 回跳教务
        r = self.session.get(f"{AUTH_BASE}/login", params={"service": self.reauth.get("service", "")})
        self._dump("after-mfa", r)
        if is_reauth_page(r.url, r.text):
            raise CasError("短信验证未通过，请重新获取验证码")
        return self._finish()

    def _finish(self) -> str:
        # 回跳通常落在教务首页；也可能停在认证中心的完善信息/改密提示页。统一再访问一次课表页
        r = self.session.get(COURSE_TABLE_URL)
        self._dump("course-table", r)
        jw_fault = False
        for attempt in range(2):
            if not is_logged_out_url(r.url):
                break
            if not any(c.name == "CASTGC" for c in self.session.cookies):
                break
            # 已有 TGC，说明 CAS 侧已登录成功，失败出在教务验 ticket（实测某个教务节点 DNS 解析不了 authserver，
            # 返回 500）。重新走一遍 SSO 让 CAS 签发新 ticket，并丢掉教务的负载均衡 route 以换个节点
            for c in list(self.session.cookies):
                if c.name == "route" and "jwxt" in c.domain:
                    self.session.cookies.clear(c.domain, c.path, c.name)
            time.sleep(2 + attempt * 3)
            sso = self.session.get(JW_SSO_URL)
            self._dump(f"sso-retry{attempt + 1}", sso)
            jw_fault = jw_fault or sso.status_code >= 500
            r = self.session.get(COURSE_TABLE_URL)
            self._dump(f"course-table-retry{attempt + 1}", r)
        if is_logged_out_url(r.url):
            if jw_fault:
                raise CasError("统一身份认证已通过，但教务系统验证登录票据时出错（学校服务器故障），请稍后再试")
            raise CasError("CAS 登录后未能建立教务登录态（可能遇到了未识别的中间页；可用 --dump 查看）")
        self.course_html = r.text
        self.stage = "done"
        self.wipe_password()
        return self.stage


def need_captcha(session: requests.Session, username: str) -> bool:
    r = session.get(
        f"{AUTH_BASE}/checkNeedCaptcha.htl",
        params={"username": username, "_": int(time.time() * 1000)},
    )
    try:
        return bool(r.json().get("isNeed"))
    except ValueError:
        return False


def login(session: requests.Session, username: str, password: str, device_id: str | None = None) -> None:
    """非交互登录（后台刷新用）：需要真人参与的步骤直接抛异常。"""
    flow = LoginFlow(session, username, password, device_id=device_id or new_device_id())
    stage = flow.start()
    if flow.course_html:
        setattr(session, "kebiao_course_html", flow.course_html)
    flow.wipe_password()
    if stage == "captcha":
        raise CaptchaRequired("统一身份认证要求图形验证码，需要你本人在网页上重新登录一次")
    if stage == "mfa":
        raise MfaRequired("统一身份认证要求手机短信二次认证，需要你本人在网页上重新登录一次")
