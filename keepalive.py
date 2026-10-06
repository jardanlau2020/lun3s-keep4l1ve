#!/usr/bin/env python3
"""Scheduled session keep-alive for a betadash (Lunes) panel account.

Flow (locally proven 2026-10-06): headed Chromium + SeleniumBase UC
 -> open login -> fill email/password (CDP) -> solve Turnstile
 (CDP coordinate click, then UC GUI click fallback) -> submit ->
 verify account-page markers (title + email + Log Out) -> TG notify.

Egress: optional local sing-box started from NODE_CONFIG secret.
NEVER pkill: only terminates child processes it started itself.
"""
import sys, types

# --- mouseinfo stub: tkinter may be absent; MouseInfo is never used ---
_fake = types.ModuleType("mouseinfo")
_fake.__version__ = "2.0.0"
def _mi_getattr(name):
    if name.startswith("__"):
        raise AttributeError(name)
    def _blocked(*a, **k):
        raise RuntimeError("mouseinfo disabled")
    return _blocked
_fake.__getattr__ = _mi_getattr
sys.modules["mouseinfo"] = _fake
# --- end stub ---

import os, time, json, socket, subprocess
from seleniumbase import SB

EMAIL = os.environ.get("LUNES_EMAIL", "")
PASSWORD = os.environ.get("LUNES_PASSWORD", "")
NODE_CONFIG = os.environ.get("NODE_CONFIG", "")
PROXY_PORT = int(os.environ.get("PROXY_PORT", "1081"))
TG_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TG_CHAT_ID", "")
LOGIN_URL = "https://betadash.lunes.host/login?next=/"
OUT = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(OUT, "sing-box")
CFG = os.path.join(OUT, "sing-box-config.json")
PROXY = f"socks5://127.0.0.1:{PROXY_PORT}"
PROCS = []

SOLVED_JS = "(function(){var i=document.querySelector('input[name=\"cf-turnstile-response\"]');return !!(i&&i.value&&i.value.length>20);})()"
TS_EXISTS_JS = "(function(){return document.querySelector('input[name=\"cf-turnstile-response\"]')!==null;})()"
COORDS_JS = """(function(){
 var iframes=document.querySelectorAll('iframe');
 for(var i=0;i<iframes.length;i++){var src=iframes[i].src||'';
  if(src.includes('cloudflare')||src.includes('turnstile')||src.includes('challenges')){
   var r=iframes[i].getBoundingClientRect();
   if(r.width>0&&r.height>0)return {cx:Math.round(r.x+30),cy:Math.round(r.y+r.height/2)};}}
 var inp=document.querySelector('input[name="cf-turnstile-response"]');
 if(inp){var p=inp.parentElement;for(var j=0;j<5;j++){if(!p)break;var r=p.getBoundingClientRect();
  if(r.width>100&&r.height>30)return {cx:Math.round(r.x+30),cy:Math.round(r.y+r.height/2)};p=p.parentElement;}}
 return null;})()"""


def log(*a):
    print(*a, flush=True)


def fill(sb, sel, txt):
    js = ("(function(sel,txt){var el=document.querySelector(sel); if(!el) return 'no-el';"
          " var s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;"
          " s.call(el,txt); el.dispatchEvent(new Event('input',{bubbles:true}));"
          " el.dispatchEvent(new Event('change',{bubbles:true})); return 'ok';})("
          + json.dumps(sel) + "," + json.dumps(txt) + ")")
    return sb.execute_script(js)


def cdp_click(sb, x, y):
    d = sb.driver
    d.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
    time.sleep(0.12)
    d.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y,
                                                   "button": "left", "clickCount": 1})
    time.sleep(0.05)
    d.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y,
                                                   "button": "left", "clickCount": 1})


def pass_turnstile(sb):
    time.sleep(2)
    if sb.execute_script(SOLVED_JS):
        log("turnstile: silent pass"); return True
    for attempt in range(6):
        if sb.execute_script(SOLVED_JS):
            log(f"turnstile: passed (attempt {attempt})"); return True
        try:
            coords = sb.execute_script(COORDS_JS)
        except Exception as e:
            log("coords err:", e); coords = None
        if coords:
            log(f"turnstile: CDP click {coords}")
            try:
                cdp_click(sb, coords["cx"], coords["cy"])
            except Exception as e:
                log("cdp_click err:", e)
        time.sleep(0.4)
        if not sb.execute_script(SOLVED_JS):
            try:
                log("turnstile: uc_gui_click fallback")
                sb.uc_gui_click_captcha()
            except Exception as e:
                log("uc_gui err:", str(e)[:150])
        for _ in range(8):
            time.sleep(0.5)
            if sb.execute_script(SOLVED_JS):
                log(f"turnstile: passed (attempt {attempt})"); return True
        log(f"turnstile: attempt {attempt} not yet")
    return False


def mask_email(e):
    n, _, d = e.partition("@")
    if len(n) > 4:
        return f"{n[:2]}****{n[-2:]}@{d}"
    return f"{n}****@{d}"


def tg(status, extra=""):
    if not (TG_TOKEN and TG_CHAT):
        log("TG not configured, skip notify"); return
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + 8 * 3600))
    text = f"☁️ Lunes 保活\n{status}\n👤 {mask_email(EMAIL)}\n⏱️ {ts} (UTC+8)"
    if extra:
        text += "\n" + extra
    try:
        import requests
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                          json={"chat_id": TG_CHAT, "text": text}, timeout=15)
        log("tg:", r.status_code)
    except Exception as e:
        log("tg err:", e)


def start_proxy():
    if not NODE_CONFIG:
        log("NODE_CONFIG empty -> direct connection")
        return False
    try:
        with open(CFG, "w") as f:
            f.write(NODE_CONFIG)
        logf = open(os.path.join(OUT, "sing-box.log"), "w")
        PROCS.append(subprocess.Popen(
            [BIN, "run", "-c", CFG], cwd=OUT,
            stdout=logf, stderr=subprocess.STDOUT, start_new_session=True))
    except Exception as e:
        log("proxy start err:", e); return False
    for _ in range(30):
        try:
            s = socket.create_connection(("127.0.0.1", PROXY_PORT), timeout=1)
            s.close()
            break
        except OSError:
            time.sleep(1)
    else:
        log("proxy port never opened"); return False
    r = subprocess.run(["curl", "-s", "-m", "15", "--socks5-hostname",
                        f"127.0.0.1:{PROXY_PORT}", "https://api.ip.sb/ip"],
                       capture_output=True, text=True)
    ip = (r.stdout or "").strip()
    if r.returncode == 0 and ip:
        log(f"proxy ok, egress ip={ip}")
        return True
    log(f"proxy port open but verify failed rc={r.returncode}")
    return False


def main():
    if not (EMAIL and PASSWORD):
        log("missing LUNES_EMAIL / LUNES_PASSWORD"); tg("❌ 续期失败", "阶段: 配置\n错误: 缺 creds")
        sys.exit(2)
    proxy_on = start_proxy()
    sb_kwargs = dict(uc=True, headless=False)
    if proxy_on:
        sb_kwargs["proxy"] = PROXY
    else:
        log("running WITHOUT proxy (direct)")
    chrome_bin = os.environ.get("CHROME_BIN", "")
    if chrome_bin:
        sb_kwargs["binary_location"] = chrome_bin
    step = "start"
    try:
        with SB(**sb_kwargs) as sb:
            log("browser up")
            step = "open"
            sb.open(LOGIN_URL)
            if sb.execute_script("return 1+1") != 2:
                raise RuntimeError("execute_script channel dead after open")
            step = "wait-form"
            loaded = False
            for i in range(30):
                src = sb.get_page_source() or ""
                if 'name="email"' in src:
                    loaded = True
                    log(f"login form loaded ({i+1}s)")
                    break
                time.sleep(1)
            if not loaded:
                raise RuntimeError("login form never loaded (CF challenge?)")
            step = "fill"
            log("fill email:", fill(sb, 'input[name="email"]', EMAIL))
            time.sleep(0.3)
            log("fill password:", fill(sb, 'input[name="password"]', PASSWORD))
            time.sleep(1)
            if sb.execute_script(TS_EXISTS_JS):
                step = "turnstile"
                if not pass_turnstile(sb):
                    raise RuntimeError("turnstile failed after 6 attempts")
            else:
                log("no turnstile widget found")
            step = "submit"
            sb.click('button[type="submit"]')
            t, u = "", ""
            for _ in range(15):
                time.sleep(1)
                try:
                    t = sb.get_title() or ""
                    u = sb.get_current_url()
                except Exception:
                    pass
                if "account" in t.lower() or ("login" not in u.split("?")[0].lower()
                                              and "betadash" in u):
                    break
            step = "verify"
            src = sb.get_page_source() or ""
            tl = (sb.get_title() or "").lower()
            ok = ("account" in tl) and (EMAIL.lower() in src.lower()) \
                 and ("log out" in src.lower())
            try:
                sb.save_screenshot(os.path.join(OUT, "keepalive.png"))
            except Exception as e:
                log("screenshot err:", e)
            # best-effort server page hit (same login_required decorator)
            try:
                sb.open("https://betadash.lunes.host/")
                log("extra authenticated GET / ok")
            except Exception as e:
                log("extra GET err:", e)
            if ok:
                log("SUCCESS: account page verified")
                tg("✅ 续期成功（会话已刷新）",
                   f"出口: {'sing-box 代理' if proxy_on else '直连'}")
                sys.exit(0)
            log(f"VERIFY FAIL title={t!r} url={u!r}")
            tg("❌ 续期失败", f"阶段: {step}\n标题: {t}\nURL: {u}")
            sys.exit(3)
    except SystemExit:
        raise
    except Exception as e:
        log(f"EXC at [{step}]:", e)
        try:
            import glob
            if glob.glob(os.path.join(OUT, "keepalive.png")):
                pass
        except Exception:
            pass
        tg("❌ 续期失败", f"阶段: {step}\n错误: {str(e)[:300]}")
        sys.exit(4)
    finally:
        for p in PROCS:
            try:
                p.terminate()
            except Exception:
                pass


if __name__ == "__main__":
    main()
