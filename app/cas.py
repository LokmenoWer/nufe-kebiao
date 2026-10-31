"""南京财经大学统一身份认证（新版金智 CAS）登录。

登录是一个可能需要用户参与的多步流程（LoginFlow）：
    start → [captcha：图形验证码 | slider：滑块拼图] → submit → [mfa：短信 / 动态口令二次认证] → done
学校用哪种人机验证由登录页的 captchaSwitch 决定（1=图形验证码，2=滑块）。
所有人机验证都只"转发"给真人完成：图片原样交给用户，滑块的拖动轨迹也来自用户本人的拖动，
程序不做任何识别、不生成轨迹、不绕过。
后台刷新没有真人在场，遇到这些步骤分别抛 CaptchaRequired / MfaRequired。
"""
from __future__ import annotations

import base64
import json
import logging
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

log = logging.getLogger("kebiao.cas")

AUTH_HOST = "authserver.nufe.edu.cn"
AUTH_BASE = f"https://{AUTH_HOST}/authserver"
COURSE_TABLE_URL = "https://jwxt.nufe.edu.cn/student/for-std/course-table"
JW_SSO_URL = "https://jwxt.nufe.edu.cn/student/sso/login"  # 302 → authserver，service 已带好

# 金智二次认证的默认接口；进入二次认证页后会优先从页面脚本里解析实际路径
REAUTH_SEND_SMS = f"{AUTH_BASE}/dynamicCode/getDynamicCodeByReauth.do"
REAUTH_SUBMIT = f"{AUTH_BASE}/reAuthCheck/reAuthSubmit.do"
REAUTH_CHANGE_TYPE = f"{AUTH_BASE}/reAuthCheck/changeReAuthType.do"
REAUTH_SMS = "3"   # reAuth.js：3 = 手机短信验证码（authCodeTypeName=reAuthDynamicCodeType）
REAUTH_OTP = "10"  # reAuth.js：10 = 动态口令 / 身份验证器 App（otpCode）
FP_COOKIE = "MULTIFACTOR_BROWSER_FINGERPRINT"

_AES_CHARS = "ABCDEFGHJKMNPQRSTWXYZabcdefhijkmnprstwxyz2345678"


class CasError(Exception):
    """登录失败（密码错误、账号锁定、验证码错误等）。"""


class CaptchaRequired(CasError):
    """需要图形验证码，而当前没有真人可以填写（后台刷新）。"""


class MfaRequired(CasError):
    """需要手机短信二次认证，而当前没有真人可以填写（后台刷新）。"""


class MfaUnsupported(CasError):
    """学校要求的二次认证方式本站无法转发（密保问题、邮箱、App 扫码确认等）。"""


def new_device_id() -> str:
    """本服务作为一台"设备"的标识，格式同页面 generateFingerPrint()（32 位大写 MD5 十六进制）。

    同一用户应始终复用同一个值：学校的"非常用设备"判断和"信任此设备"都绑定在它上面。
    """
    return secrets.token_hex(16).upper()


# jQuery $.ajax 按 dataType 发的请求头：json / text / 不指定
_AJAX = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json, text/javascript, */*; q=0.01"}
_AJAX_TEXT = {"X-Requested-With": "XMLHttpRequest", "Accept": "text/plain, */*; q=0.01"}
_AJAX_ANY = {"X-Requested-With": "XMLHttpRequest", "Accept": "*/*"}


def random_string(n: int) -> str:
    return "".join(secrets.choice(_AES_CHARS) for _ in range(n))


def encrypt_password(password: str, salt: str) -> str:
    """复刻 encrypt.js：AES-CBC-Pkcs7(randomString(64)+pwd, key=salt, iv=randomString(16))。

    滑块验证的 sign 也用同一个函数加密（key 换成小图末尾的 16 个字符）。
    """
    key = salt.strip().encode()
    if len(key) not in (16, 24, 32):
        raise CasError("登录页加密参数异常（密钥长度不正确）")
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


def captcha_always_shown(html: str) -> bool:
    """login.js checkNeedCaptcha：_badCredentialsCount 为 0 时验证码一直显示，不再问 checkNeedCaptcha.htl。"""
    m = re.search(r"""\b_badCredentialsCount\s*=\s*["']?(\d+)""", html or "")
    return bool(m) and int(m.group(1)) == 0


def parse_captcha_switch(html: str) -> str:
    """登录页里的 var captchaSwitch = "1"|"2"：1=图形验证码，2=滑块拼图。缺省按图形验证码处理。"""
    m = re.search(r"""\bcaptchaSwitch\s*=\s*["']?(\d)""", html or "")
    return m.group(1) if m else "1"


# 学校滑块：背景 590×360 JPEG + 93×360 的拼图竖条 PNG；页面插件把两者拉伸到 280×155 画布上，
# moveLength / canvasLength 都按这个 280 宽的坐标系上报。前端照同样的尺寸画，让用户本人拖。
SLIDER_CANVAS_WIDTH = 280
SLIDER_CANVAS_HEIGHT = 155
_SLIDER_MAX_IMAGE = 512 * 1024
_SLIDER_KEY = re.compile(rb"[0-9A-Za-z]{16}")


def _sniff_image(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return None


def parse_slider_challenge(payload) -> dict:
    """openSliderCaptcha.htl 的 JSON → 两张图 + 签名密钥（小图末尾追加的 16 个字符，同 ids-sliderCaptcha.js）。"""
    if not isinstance(payload, dict):
        raise CasError("滑块验证码加载失败，请稍后再试")
    images = {}
    for key in ("bigImage", "smallImage"):
        raw = payload.get(key)
        if not isinstance(raw, str) or not raw or len(raw) > _SLIDER_MAX_IMAGE * 4 // 3 + 4:
            raise CasError("滑块验证码加载失败，请稍后再试")
        try:
            data = base64.b64decode(raw, validate=True)
        except (ValueError, TypeError):
            raise CasError("滑块验证码图片格式异常") from None
        ctype = _sniff_image(data)
        if not ctype:
            raise CasError("滑块验证码图片格式异常")
        images[key] = (data, ctype)
    tail = images["smallImage"][0][-16:]
    if not _SLIDER_KEY.fullmatch(tail):
        raise CasError("滑块验证码格式与预期不符，请改用扫码登录")
    return {"big": images["bigImage"], "small": images["smallImage"], "key": tail.decode("ascii")}


def slider_sign(canvas_length: int, move_length, tracks: list[dict], key: str) -> str:
    """与 longbow.slidercaptcha.js 的 verify 一致：encryptPassword(JSON.stringify({...}), key)。"""
    body = {"canvasLength": canvas_length, "moveLength": move_length,
            "tracks": [{"a": t["a"], "b": t["b"], "c": t["c"]} for t in tracks]}
    return encrypt_password(json.dumps(body, separators=(",", ":")), key)


def _has_input(soup: BeautifulSoup, name: str) -> bool:
    return soup.find("input", attrs={"id": name}) is not None or soup.find("input", attrs={"name": name}) is not None


# reAuth.js 里 reAuthType 与方式的对应关系；页面模板把所有方式的输入框都放进去、按 reAuthType 只显示一种，
# 所以只能按 reAuthType 判断，不能看页面上有哪些输入框。
_REAUTH_TYPE_NAMES = {
    "2": "重新输入密码", "7": "重新输入密码", "4": "企业微信验证码", "5": "今日校园验证码",
    "6": "人脸识别", "18": "人脸识别", "11": "邮箱验证码", "12": "钉钉验证码", "13": "WeLink 验证码",
    "14": "App 扫码确认", "15": "微信服务号验证码", "CaUKey": "CA UKey",
}


def reauth_method(reauth_type: str) -> str:
    """二次认证页要求的方式。只转发只需用户输入一段代码、且我们确知字段的两种：

    sms = 短信验证码（reAuthType=3）；otp = 动态口令 / 身份验证器 App（reAuthType=10）。
    其余返回描述文字，由调用方提示用户换一种方式。
    """
    if reauth_type == REAUTH_SMS:
        return "sms"
    if reauth_type == REAUTH_OTP:
        return "otp"
    if reauth_type in _REAUTH_TYPE_NAMES:
        return _REAUTH_TYPE_NAMES[reauth_type]
    if reauth_type.lower().startswith("real_person_plugin_"):
        return "人脸识别"
    return f"未知方式（reAuthType={reauth_type or '?'}）"


def reauth_alternatives(html: str) -> set[str]:
    """页面"切换认证方式"里列出的 reAuthType。选项的 id 就是 reAuthType（reAuth.js: $("#"+reAuthType)）。"""
    soup = BeautifulSoup(html or "", "html.parser")
    found = set()
    for el in soup.select(".changeReAuthTypes [id]"):
        found.add(el["id"])
    for el in soup.find_all(attrs={"onclick": re.compile(r"(change|switch)ReAuthType")}):
        if el.get("id"):
            found.add(el["id"])
    found.update(re.findall(r"switchReAuthType\(\s*['\"]([^'\"]+)['\"]", html or ""))
    return found


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
    """只接受常见位图。拒绝 SVG（可带脚本）和缺 Content-Type 的响应。"""
    ctype = (header or "").split(";", 1)[0].strip().lower()
    return ctype if ctype in _RASTER_IMAGE_TYPES else None


_RASTER_IMAGE_TYPES = {
    "image/png", "image/jpeg", "image/jpg", "image/pjpeg", "image/gif",
    "image/webp", "image/bmp", "image/x-icon", "image/vnd.microsoft.icon",
}


def looks_like_course_page(html: str) -> bool:
    """课表页至少要有 personId 和 currentSemester 两个变量；否则多半是故障页 / 中间页。"""
    html = html or ""
    return bool(re.search(r"\bpersonId\s*=", html) and re.search(r"\bcurrentSemester\s*=", html))


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
    info["change_url"] = REAUTH_CHANGE_TYPE
    m = re.search(r"['\"]([^'\"]*changeReAuthType\.do)['\"]", html)
    if m:
        info["change_url"] = pin_auth_url(_resolve(url, m.group(1)))
    m = re.search(r"1\d{2}\*{4}\d{4}", html)
    if m:
        info["phone"] = m.group(0)
    info["reAuthType"] = _reauth_param(html, "reAuthType")
    info["isMultifactor"] = _reauth_param(html, "isMultifactor") or "true"
    info["userId"] = _reauth_param(html, "reAuthUserId")
    info["service"] = (_reauth_param(html, "service")
                       or (parse_qs(urlparse(url).query).get("service") or [""])[0])
    info["isSleepAccount"] = _reauth_param(html, "isSleepAccount")
    info["method"] = reauth_method(info["reAuthType"])
    info["noneAuthType"] = _reauth_param(html, "noneAuthType")
    if info["noneAuthType"] == "1":
        info["method"] = "无可用的认证方式"  # reAuth.js：noneAuthType=1 时页面不初始化任何方式
    info["alternatives"] = sorted(reauth_alternatives(html))
    return info


@dataclass
class LoginFlow:
    session: requests.Session
    username: str
    password: str
    stage: str = "init"            # init | captcha | slider | qr | mfa | done | failed
    login_url: str = ""
    fields: dict = field(default_factory=dict)
    reauth: dict = field(default_factory=dict)
    captcha_kind: str = "image"    # image | slider（来自登录页 captchaSwitch）
    slider_key: str = ""           # 当前滑块挑战的签名密钥；每张图只能用一次
    slider_page_loaded: bool = False  # 页面每次点登录先取 toSliderCaptcha.htl，换图只调 openSliderCaptcha
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
        if r.status_code >= 400:
            raise CasError(f"教务系统暂时不可用（HTTP {r.status_code}），请稍后再试")
        if not is_logged_out_url(r.url):
            if not looks_like_course_page(r.text):
                raise CasError("教务系统返回了无法识别的页面，请稍后再试")
            self.stage = "done"  # 已有有效登录态
            self.course_html = r.text  # 顺手留住，登录完成后不必再抓一次
            return None
        r = s.get(JW_SSO_URL)
        self._dump("login-page", r)
        if r.status_code >= 500:
            raise CasError("统一身份认证服务暂时不可用，请稍后再试")
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
        self.captcha_kind = "slider" if parse_captcha_switch(html) == "2" else "image"
        if captcha_always_shown(html) or need_captcha(self.session, self.username):
            self.stage = self._captcha_stage()
            return self.stage
        return self.submit("")

    def _captcha_stage(self) -> str:
        self.slider_page_loaded = False
        return "slider" if self.captcha_kind == "slider" else "captcha"

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
        # qrcode.js：getToken?ts=...&uuid=（首次 uuid 为空，jQuery 照样带上空参数）
        r = self.session.get(f"{AUTH_BASE}/qrCode/getToken",
                             params={"ts": int(time.time() * 1000), "uuid": self.qr_uuid}, headers=_AJAX_TEXT)
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
                             params={"ts": int(time.time() * 1000), "uuid": self.qr_uuid}, headers=_AJAX_TEXT)
        if is_auth_login_url(r.url):
            self.qr_status = "expired"
            return self.stage
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
        # 字段与学校 qrLoginForm 完全一致（该表单没有"记住我"，不要额外加 rememberMe）
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

    def slider_challenge(self) -> dict:
        """取一张新的滑块拼图（同 ids-sliderCaptcha.js 的 openSliderCaptcha）。返回两张图，密钥留在服务端。"""
        if self.stage != "slider":
            raise CasError("当前不需要滑块验证")
        if not self.slider_page_loaded:
            # login.js createSliderCaptcha：先取滑块弹窗片段，片段里的 ids-sliderCaptcha.js 再取图
            r = self.session.get(f"{AUTH_BASE}/common/toSliderCaptcha.htl", headers=_AJAX_ANY)
            self._dump("slider-page", r)
            self.slider_page_loaded = True
        r = self.session.get(f"{AUTH_BASE}/common/openSliderCaptcha.htl", params={"_": int(time.time() * 1000)},
                             headers=_AJAX)
        self._dump("slider", r)
        try:
            payload = r.json()
        except ValueError:
            raise CasError(f"滑块验证码加载失败（HTTP {r.status_code}）") from None
        challenge = parse_slider_challenge(payload)
        self.slider_key = challenge.pop("key")
        return challenge

    def submit_slider(self, canvas_length: int, move_length, tracks: list[dict]) -> str:
        """把用户本人拖动的结果交给学校校验；通过后照常提交账号密码。"""
        if self.stage != "slider" or not self.slider_key:
            raise CasError("滑块验证已失效，请重新拖动")
        key, self.slider_key = self.slider_key, ""  # 一张图只校验一次，失败就换图
        r = self.session.post(f"{AUTH_BASE}/common/verifySliderCaptcha.htl",
                              data={"sign": slider_sign(canvas_length, move_length, tracks, key)}, headers=_AJAX)
        self._dump("slider-verify", r)
        try:
            ok = r.json().get("errorCode") == 1
        except (ValueError, AttributeError):
            ok = False
        if not ok:
            raise CasError("拼图没有对齐，请再试一次")
        return self.submit("")

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
            if "验证码" in tip or "安全验证" in tip:
                # 验证码错误或新触发：页面已刷新 execution/salt，回到验证码阶段让用户重做
                self.fields = parse_login_form(r.text)
                self.captcha_kind = "slider" if parse_captcha_switch(r.text) == "2" else self.captcha_kind
                self.stage = self._captcha_stage()
                raise CasError(tip)
            raise CasError(tip)
        return self._after_login_post(r)

    def _after_login_post(self, r: requests.Response) -> str:
        """账号密码与扫码登录提交后的共同处理：可能进入短信二次认证，否则完成。"""
        if is_reauth_page(r.url, r.text):
            self.reauth = parse_reauth_page(r.url, r.text)
            log.info("reauth page: reAuthType=%r alternatives=%s",
                     self.reauth["reAuthType"], self.reauth["alternatives"])
            # 学校默认给的不是短信、但页面允许切到短信时，像页面上点"切换认证方式"一样先切过去：
            # 多数人没绑定身份验证器，短信人人能收
            if (self.reauth["method"] != "sms" and REAUTH_SMS in self.reauth["alternatives"]
                    and self.reauth["noneAuthType"] != "1"):
                self._switch_reauth_type(REAUTH_SMS)
            method = self.reauth["method"]
            if method not in ("sms", "otp"):
                self.stage = "failed"
                self.wipe_password()
                raise MfaUnsupported(
                    f"学校要求用「{method}」完成二次认证，本站暂不能转发。可以改用扫码登录，"
                    "或先在学校官网用这台设备完成一次认证并选择信任设备后再试")
            self.stage = "mfa"
            return self.stage
        return self._finish()

    @property
    def can_trust_device(self) -> bool:
        return self.reauth.get("isSleepAccount") == "0"

    def _switch_reauth_type(self, reauth_type: str) -> None:
        """与 reAuth.js 的 switchReAuthType 一致。失败就保持原方式，不重试。"""
        r = self.session.post(self.reauth["change_url"], data={
            "isMultifactor": self.reauth.get("isMultifactor", "true"),
            "reAuthType": reauth_type,
            "service": self.reauth.get("service", ""),
        }, headers=_AJAX)
        self._dump("change-reauth-type", r)
        try:
            j = r.json()
        except ValueError:
            log.warning("changeReAuthType: non-JSON response, HTTP %s", r.status_code)
            return
        data = j.get("data") if isinstance(j, dict) else None
        new_type = str((data or {}).get("reAuthType") or "")
        if str(j.get("code")) != "1" or not new_type:
            log.warning("changeReAuthType to %s refused: code=%r", reauth_type, j.get("code"))
            return
        self.reauth["reAuthType"] = new_type
        self.reauth["method"] = reauth_method(new_type)

    def send_sms(self) -> str:
        if self.stage != "mfa":
            raise CasError("当前不需要短信验证")
        # 只在学校确实要短信（reAuthType=3）时才请求发送：类型不符的发送请求会被学校判成会话异常、冻结账号
        if self.reauth.get("method") != "sms" or self.reauth.get("reAuthType") != REAUTH_SMS:
            raise CasError("这次二次认证不需要短信，请直接输入身份验证器 App 上的动态口令")
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
        msg = j.get("returnMessage") or ""
        if j.get("res") in ("success", "other_success"):
            self.sms_ready_at = time.time() + int(j.get("codeTime") or 60)
            return f"{msg or '验证码已发送'}{'' if j.get('mobile', '') in msg else j.get('mobile', '')}"
        # 失败也等一个发送间隔再允许重发，免得连续请求触发学校的风控
        self.sms_ready_at = time.time() + int(j.get("codeTime") or 60)
        log.warning("send sms failed: res=%r", j.get("res"))
        raise CasError(msg or (f"发送短信失败：{j['res']}" if j.get("res") else "学校没有发出短信，请改用扫码登录"))

    def verify_sms(self, code: str, trust_device: bool = True) -> str:
        """短信验证码或动态口令（otp）：两者都只是用户输入的一段代码，按页面要求放进对应字段。"""
        if self.stage != "mfa":
            raise CasError("当前不需要二次认证")
        otp = self.reauth.get("method") == "otp"
        # 字段与 reAuth.js 的 loginParams 完全一致
        data = {
            "service": self.reauth.get("service", ""),
            "reAuthType": self.reauth.get("reAuthType", REAUTH_SMS),
            "isMultifactor": self.reauth.get("isMultifactor", "true"),
            "password": "",
            "dynamicCode": "" if otp else code.strip(),
            "uuid": "",
            "answer1": "",
            "answer2": "",
            "otpCode": code.strip() if otp else "",
        }
        # 只有 isSleepAccount=0 时页面才弹"信任此设备"，并按选择带上 skipTmpReAuth；否则不带这个字段
        if self.can_trust_device:
            data["skipTmpReAuth"] = "true" if trust_device else "false"
        r = self.session.post(self.reauth["submit_url"], data=data, headers=_AJAX)
        self._dump("verify-sms", r)
        try:
            j = r.json()
        except ValueError:
            raise CasError(f"短信验证失败（HTTP {r.status_code}）") from None
        if j.get("code") in ("reAuth_failed", "reAuth_unauthorized"):
            raise CasError(j.get("msg") or ("动态口令错误" if otp else "短信验证码错误"))
        # 与页面行为一致：通过后访问 /login?service=...，CAS 直接签发 ticket 回跳教务
        r = self.session.get(f"{AUTH_BASE}/login", params={"service": self.reauth.get("service", "")})
        self._dump("after-mfa", r)
        if is_reauth_page(r.url, r.text):
            raise CasError("动态口令验证未通过，请重试" if otp else "短信验证未通过，请重新获取验证码")
        return self._finish()

    def _finish(self) -> str:
        # 回跳通常落在教务首页；也可能停在认证中心的完善信息/改密提示页。统一再访问一次课表页
        r = self.session.get(COURSE_TABLE_URL)
        self._dump("course-table", r)
        jw_fault = r.status_code >= 500
        # 已有 CASTGC 时，教务验 ticket 失败（学校某个节点解析不了 authserver 等）可以重走 SSO 换个节点
        for attempt in range(2):
            if r.status_code < 400 and not is_logged_out_url(r.url) and looks_like_course_page(r.text):
                break
            if not any(c.name == "CASTGC" for c in self.session.cookies):
                break
            for c in list(self.session.cookies):
                if c.name == "route" and "jwxt" in c.domain:
                    self.session.cookies.clear(c.domain, c.path, c.name)
            time.sleep(2 + attempt * 3)
            sso = self.session.get(JW_SSO_URL)
            self._dump(f"sso-retry{attempt + 1}", sso)
            jw_fault = jw_fault or sso.status_code >= 500
            r = self.session.get(COURSE_TABLE_URL)
            self._dump(f"course-table-retry{attempt + 1}", r)
            jw_fault = jw_fault or r.status_code >= 500
        if is_logged_out_url(r.url):
            if jw_fault:
                raise CasError("统一身份认证已通过，但教务系统验证登录票据时出错（学校服务器故障），请稍后再试")
            raise CasError("CAS 登录后未能建立教务登录态（可能遇到了未识别的中间页；可用 --dump 查看）")
        if r.status_code >= 400:
            raise CasError(f"统一身份认证已通过，但教务系统返回 HTTP {r.status_code}，请稍后再试")
        if not looks_like_course_page(r.text):
            raise CasError("统一身份认证已通过，但教务系统返回了无法识别的页面（可能正在维护），请稍后再试")
        self.course_html = r.text
        self.stage = "done"
        self.wipe_password()
        return self.stage


def need_captcha(session: requests.Session, username: str) -> bool:
    # login.js：$.ajax(checkNeedCaptcha.htl, {data: {username: trim}, cache: false, dataType: "json"})
    r = session.get(
        f"{AUTH_BASE}/checkNeedCaptcha.htl",
        params={"username": username.strip(), "_": int(time.time() * 1000)},
        headers=_AJAX,
    )
    try:
        return bool(r.json().get("isNeed"))
    except ValueError:
        return False


def renew_with_tgc(session: requests.Session) -> bool:
    """教务会话过期、但认证中心的 CASTGC 还有效时，走一次 SSO 让认证中心静默签发新 ticket。

    教务过期后先落到自己的 /student/login，不会自动跳 CAS，所以必须主动访问 JW_SSO_URL。
    返回 True：已续上（课表页挂在 session.kebiao_course_html，fetch_timetable 会直接用）；
    返回 False：CASTGC 也失效了，或认证中心要求二次认证，需要真人重新登录。
    """
    if not any(c.name == "CASTGC" for c in session.cookies):
        return False
    r = session.get(JW_SSO_URL)
    if r.status_code >= 500:
        raise CasError("统一身份认证服务暂时不可用，请稍后再试")
    if is_auth_login_url(r.url) or is_reauth_page(r.url, r.text):
        return False
    flow = LoginFlow(session, "", "")
    flow._finish()
    session.kebiao_course_html = flow.course_html
    return True


def login(session: requests.Session, username: str, password: str, device_id: str | None = None) -> None:
    """非交互登录（后台刷新用）：需要真人参与的步骤直接抛异常。"""
    flow = LoginFlow(session, username, password, device_id=device_id or new_device_id())
    stage = flow.start()
    if flow.course_html:
        session.kebiao_course_html = flow.course_html
    flow.wipe_password()
    if stage in ("captcha", "slider"):
        raise CaptchaRequired("统一身份认证要求人机验证，需要你本人在网页上重新登录一次")
    if stage == "mfa":
        raise MfaRequired("统一身份认证要求手机短信二次认证，需要你本人在网页上重新登录一次")
