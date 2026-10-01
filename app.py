#!/usr/bin/env python3
"""
  author: Iddant ID
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import unicodedata
from html import unescape
from pathlib import Path
from urllib.parse import urlparse

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import (
        BarColumn, DownloadColumn, Progress, TextColumn,
        TimeRemainingColumn, TransferSpeedColumn,
    )
    from rich.prompt import Prompt
    from rich.table import Table
except ImportError:  
    sys.stderr.write("rich is required:  pip install rich\n")
    raise

console = Console()
OUT_DIR = "downloads"
CHUNK = 262144

APP_NAME = "FileDL"
APP_VERSION = "2.0.2"
APP_AUTHOR = "Iddant ID"
APP_BLURB = "extract real links from file hosts and download them"

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


class ReconError(RuntimeError):
    """A step failed in a way the user can act on (bad link, gate, network)."""


def sanitize_filename(name: str, fallback: str = "download.bin") -> str:
    name = unescape(name or "")
    name = unicodedata.normalize("NFC", name)
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name or fallback


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024.0
    return f"{n:.2f} GB"


def default_dir() -> Path:
    return Path(OUT_DIR)

SF_BASE = "sfile.co"
CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def sf_parse(url: str) -> str:
    url = url.strip()
    if "://" not in url:
        url = f"https://{SF_BASE}/{url.lstrip('/')}"
    p = urlparse(url)
    if SF_BASE not in p.netloc:
        raise ReconError(f"not an {SF_BASE} URL: {url}")
    parts = [s for s in p.path.split("/") if s]
    if not parts:
        raise ReconError(f"no file code in URL: {url}")
    return parts[-1]


def sf_extract_link(short: str, headless: bool = True, timeout: int = 45000) -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise ReconError("playwright is required\n  pip install playwright") from e

    file_page = f"https://{SF_BASE}/{short}"
    out: dict = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(accept_downloads=True, user_agent=CHROME_UA)
        page = ctx.new_page()
        try:
            resp = page.goto(file_page, wait_until="domcontentloaded", timeout=timeout)
            if resp is not None and resp.status == 404:
                raise ReconError(f"file not found: {short} (404)")
            if not page.locator("#download").count():
                raise ReconError(f"no download button on {file_page} — removed, expired, or bad code")
            page.wait_for_selector("#download", state="attached", timeout=timeout)

            dw = page.eval_on_selector("#download", "e => e.getAttribute('data-dw-url')")
            if not dw:
                raise ReconError("file page has no data-dw-url")

            page.goto(dw, wait_until="domcontentloaded", timeout=timeout)
            if not page.locator("#download").count():
                raise ReconError("interstitial served no download button")
            page.wait_for_selector("#download", state="attached", timeout=timeout)
            page.wait_for_timeout(1500)

            direct = page.eval_on_selector(
                "#download", "e => e.getAttribute('data-direct-download')")
            if not direct or direct == "#":
                raise ReconError("interstitial exposed no direct-download URL")

            out["url"] = unescape(direct)
            out["cookies"] = {c["name"]: c["value"] for c in ctx.cookies()}
            out["user_agent"] = page.evaluate("navigator.userAgent") or CHROME_UA
            out["title"] = (page.title() or "").strip()
        finally:
            browser.close()

    if "&k=" not in out["url"]:
        raise ReconError(
            "resolved URL is missing the session key (&k=). The site served a degraded "
            "interstitial — re-run; if it persists try --headful."
        )
    return out


def sf_probe(link: dict, timeout: int = 30) -> tuple[str, int]:
    from curl_cffi import requests as cr
    from urllib.parse import unquote
    s = cr.Session(impersonate="chrome")
    for n, v in link["cookies"].items():
        s.cookies.set(n, v, domain=SF_BASE, path="/")
    r = s.head(link["url"],
               headers={"Referer": link["url"], "User-Agent": link["user_agent"]},
               allow_redirects=True, timeout=timeout)
    cd = r.headers.get("Content-Disposition", "") or ""
    name = ""
    m = re.search(r"filename\*=UTF-8''([^;]+)", cd)
    if m:
        name = unquote(m.group(1))
    else:
        m = re.search(r'filename="([^"]+)"', cd)
        if m:
            name = m.group(1)
    return sanitize_filename(name), int(r.headers.get("Content-Length") or 0)


def sf_handle(url: str, opts) -> None:
    short = sf_parse(url)
    with console.status("[cyan]opening file page…", spinner="dots"):
        link = sf_extract_link(short, headless=not opts.headful)

    if opts.link_only:
        console.print(link["url"], soft_wrap=True)
        return

    name, size = sf_probe(link)
    if not name:
        name = sanitize_filename(link.get("title", ""), f"{short}.bin")
    out = Path(opts.output) if opts.output else Path(opts.dir) / name
    resume = not opts.no_resume
    if out.exists() and not opts.force and not (resume and out.stat().st_size):
        raise ReconError(f"{out.name} already exists (use --force to overwrite)")

    download_http(link["url"], link["cookies"], SF_BASE, link["user_agent"],
                  out, size, resume=resume)


SK_BASE = "safefileku.com"


def sk_parse(url: str) -> str:
    url = url.strip()
    if "://" not in url:
        url = f"https://{SK_BASE}/download/{url.lstrip('/')}"
    p = urlparse(url)
    if SK_BASE not in p.netloc:
        raise ReconError(f"not a {SK_BASE} URL: {url}")
    parts = [s for s in p.path.split("/") if s]
    if not parts:
        raise ReconError(f"no file code in URL: {url}")
    return parts[-1]


def sk_resolve(code: str, out_path: Path, headless: bool, opts) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise ReconError("playwright is required\n  pip install playwright") from e

    page_url = f"https://{SK_BASE}/download/{code}"
    uid = ""
    cookies: dict = {}

    with sync_playwright() as p:
        args = ["--disable-blink-features=AutomationControlled"]
        if not headless:
            args += ["--window-position=-32000,-32000", "--window-size=1280,800"]
        try:
            browser = p.chromium.launch(channel="chrome", headless=False, args=args)
        except Exception as e:
            raise ReconError(
                "safefileku needs a real Google Chrome install (Turnstile rejects "
                f"bundled Chromium): {str(e)[:100]}"
            ) from e

        ctx = browser.new_context(accept_downloads=True,
                                  viewport={"width": 1280, "height": 800})
        page = ctx.new_page()
        try:
            page.goto(page_url, wait_until="domcontentloaded", timeout=45000)
            if not page.locator("form").count():
                raise ReconError(f"no gate form on {page_url} — bad code or file removed")

            with console.status("[cyan]solving Cloudflare Turnstile…", spinner="dots") as st:
                token = ""
                for i in range(30):
                    page.wait_for_timeout(1000)
                    token = page.evaluate(
                        "()=>{const i=document.querySelector('input[name=cf-turnstile-response]');"
                        "return i?i.value:''}")
                    if token and len(token) > 20:
                        st.update(f"[cyan]Turnstile solved in {i}s")
                        break
            if not token or len(token) <= 20:
                raise ReconError(
                    "Turnstile did not issue a token. Re-run with --headful, or solve "
                    "the checkbox manually — the challenge sometimes needs a human."
                )

            page.evaluate("()=>document.querySelector('form').submit()")
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(1500)
            if not page.locator("#download").count():
                raise ReconError("gate passed but no download button appeared")
            with console.status("[cyan]waiting out the site countdown…", spinner="dots"):
                for _ in range(30):
                    page.wait_for_timeout(1000)
                    if "ready in" not in (page.locator("#download").inner_text() or "").lower():
                        break

            uid = page.evaluate("()=>typeof uid!=='undefined'?String(uid):''")
            if not uid:
                raise ReconError("could not read the session uid from the page")
            cookies = {c["name"]: c["value"] for c in ctx.cookies()}
        finally:
            browser.close()

    url = f"https://{SK_BASE}/download/file/{uid}/{code}"
    size = _http_size(url, cookies, SK_BASE)
    if not size:
        raise ReconError("file endpoint did not report a size — link may have expired")
    download_http(url, cookies, SK_BASE, CHROME_UA, out_path, size,
                  resume=not opts.no_resume, referer=page_url)


def _http_size(url: str, cookies: dict, domain: str) -> int:
    from curl_cffi import requests as cr
    s = cr.Session(impersonate="chrome")
    for n, v in cookies.items():
        s.cookies.set(n, v, domain=domain, path="/")
    try:
        r = s.get(url, headers={"Range": "bytes=0-0"}, stream=True,
                  allow_redirects=True, timeout=40)
        cr_hdr = r.headers.get("Content-Range", "")
        if not cr_hdr:
            return int(r.headers.get("Content-Length") or 0)
        m = re.search(r"/(\d+)\s*$", cr_hdr)
        return int(m.group(1)) if m else 0
    except Exception:
        return 0


def sk_handle(url: str, opts) -> None:
    if opts.link_only:
        raise ReconError(
            "--link-only is not supported for safefileku: its CDN URL is minted "
            "only for the in-browser transfer and expires within minutes."
        )

    code = sk_parse(url)
    if opts.output:
        out = Path(opts.output)
    else:
        out = default_dir() / f"{code}.bin"
        real = _sk_remote_name(code)
        if real:
            out = out.with_name(real)

    resume = not opts.no_resume
    if out.exists() and not opts.force and not (resume and out.stat().st_size):
        raise ReconError(f"{out.name} already exists (use --force to overwrite)")

    sk_resolve(code, out_path=out, headless=not opts.headful, opts=opts)


def _sk_remote_name(code: str) -> str | None:
    try:
        from curl_cffi import requests as cr
        r = cr.get(f"https://{SK_BASE}/download/{code}", impersonate="chrome", timeout=30)
        m = re.search(r'<title>(.*?)\s*-\s*Safefileku</title>', r.text, re.I | re.S)
        if m:
            return sanitize_filename(unescape(m.group(1)).strip(), f"{code}.bin")
    except Exception:
        pass
    return None

def download_http(url: str, cookies: dict, domain: str, ua: str,
                  out: Path, size: int, resume: bool = True,
                  referer: str | None = None) -> None:
    from curl_cffi import requests as cr

    out.parent.mkdir(parents=True, exist_ok=True)
    s = cr.Session(impersonate="chrome")
    for n, v in cookies.items():
        s.cookies.set(n, v, domain=domain, path="/")

    have = out.stat().st_size if (resume and out.exists()) else 0
    headers = {"Referer": referer or url, "User-Agent": ua}
    if have:
        headers["Range"] = f"bytes={have}-"

    r = s.get(url, headers=headers, stream=True, allow_redirects=True, timeout=60)

    if r.status_code == 416:
        console.print(f"  [green]already complete[/] ({human(have)})")
        return
    if have and r.status_code == 200:
        have = 0                       
    elif have and r.status_code != 206:
        have = 0
    if r.status_code not in (200, 206):
        raise ReconError(f"download failed: HTTP {r.status_code}")

    total = int(r.headers.get("Content-Length") or 0) + have
    mode = "ab" if have else "wb"

    if not total:
        total = None

    resume_note = f"  [dim](resuming at {human(have)})[/]" if have else ""
    console.print(f"\n  [bold cyan]↓[/] [white]{out.name}[/]{resume_note}")

    cols = [TextColumn("  "),
            BarColumn(bar_width=None, style="cyan", complete_style="bright_cyan",
                      finished_style="green"),
            TextColumn(" [dim]{task.percentage:>5.1f}%[/]"),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn()]
    with Progress(*cols, console=console, transient=False, expand=True) as prog:
        task = prog.add_task("dl", total=total, completed=have)
        with open(out, mode) as f:
            for chunk in r.iter_content(CHUNK):
                if not chunk:
                    continue
                f.write(chunk)
                prog.update(task, advance=len(chunk))

    got = out.stat().st_size
    console.print(f"  [green]✓ saved[/]  [white]{out.name}[/]  [dim]{human(got)}[/]")
    if total and got != total:
        console.print(f"  [yellow]! warning[/]  expected {total} bytes, got {got}")


HOSTS = {
    "1": ("SFILE", SF_BASE, sf_handle),
    "2": ("SAFILEKU", SK_BASE, sk_handle),
}

WIDTH = 62          


def header() -> None:
    body = Table.grid(padding=(0, 1))
    body.add_column(justify="left")
    body.add_row(f"[bold white]{APP_NAME}[/] [cyan]v{APP_VERSION}[/]")
    body.add_row(f"[dim]{APP_BLURB}[/]")
    body.add_row("")
    body.add_row(f"[dim]author[/]  [magenta]{APP_AUTHOR}[/]")
    body.add_row(f"[dim]output[/]  [cyan]./{OUT_DIR}[/]")
    console.print(Panel(body, border_style="cyan", width=WIDTH, padding=(1, 2)))


def menu() -> None:
    t = Table.grid(padding=(0, 2))
    t.add_column(style="bold yellow", no_wrap=True, justify="right")
    t.add_column(style="bold white", no_wrap=True)
    t.add_column(style="dim")
    t.add_row("1", "SFILE", "sfile.co/<code>")
    t.add_row("2", "SAFILEKU", "safefileku.com/download/<code>")
    console.print(Panel(t, title="[dim]choose a host[/]",
                        border_style="bright_black", width=WIDTH,
                        title_align="left", padding=(0, 2)))


def banner() -> None:
    if console.is_terminal:
        console.clear()
    console.print()
    header()
    console.print()
    menu()
    console.print()


def interactive(opts) -> None:
    banner()
    while True:
        console.print()
        raw = Prompt.ask("[bold cyan]❯[/] [dim]paste link[/]  [dim](1/2 pick host, q quit)[/]",
                         default="", show_default=False).strip()
        if raw.lower() in ("q", "quit", "exit"):
            break
        if not raw:
            continue

        if raw in HOSTS:
            label, _, _ = HOSTS[raw]
            url = Prompt.ask(f"[bold cyan]❯[/] [dim]{label} link[/]",
                             default="", show_default=False).strip()
            if url.lower() in ("q", "quit", "exit"):
                break
            if not url:
                continue
        else:
            url = raw
            key = detect_host(url)
            if not key:
                console.print("  [yellow]unrecognised host[/] [dim]— need sfile.co or "
                              "safefileku.com in the URL, or pick 1/2[/]")
                continue
            label = HOSTS[key][0]

        key = detect_host(url)
        console.print(f"\n  [bold cyan]{HOSTS[key][0]}[/]  [dim]{url[:80]}[/]")
        try:
            HOSTS[key][2](url, opts)
        except ReconError as e:
            console.print(f"  [red]✗ error[/]  {e}")
        except KeyboardInterrupt:
            console.print("\n  [yellow]interrupted[/] — partial file kept, re-run to resume")
        except Exception as e:
            console.print(f"  [red]✗ unexpected[/]  {type(e).__name__}: {str(e)[:160]}")
        console.print("  [green]─[/] [dim]ready for the next link[/]")


def detect_host(url: str) -> str | None:
    u = url.lower()
    if SF_BASE in u:
        return "1"
    if SK_BASE in u:
        return "2"
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog=APP_NAME.lower(),
        description=f"{APP_NAME} v{APP_VERSION} — {APP_BLURB}.",
        epilog=f"author: {APP_AUTHOR}   ·   run with no arguments for the menu.")
    ap.add_argument("-V", "--version", action="version",
                    version=f"{APP_NAME} v{APP_VERSION} by {APP_AUTHOR}")
    ap.add_argument("url", nargs="?", help="file URL (host is auto-detected)")
    ap.add_argument("-o", "--output", help="output file path")
    ap.add_argument("-d", "--dir", default=OUT_DIR, help=f"output directory (default: {OUT_DIR})")
    ap.add_argument("--link-only", action="store_true", help="print the real link and exit")
    ap.add_argument("--headful", action="store_true", help="show the browser window")
    ap.add_argument("--no-resume", action="store_true", help="always start from scratch")
    ap.add_argument("--force", action="store_true", help="overwrite an existing file")
    args = ap.parse_args(argv)

    globals()["OUT_DIR"] = args.dir

    def run(url: str) -> int:
        key = detect_host(url)
        if not key:
            console.print(f"[yellow]✗ unrecognised host[/]  [dim]{url!r}[/]")
            console.print("[dim]  expected sfile.co or safefileku.com in the URL[/]")
            return 1
        label, host, handler = HOSTS[key]
        console.print(f"\n[bold cyan]{label}[/]  [dim]{url[:80]}[/]")
        try:
            handler(url, args)
            return 0
        except ReconError as e:
            console.print(f"[red]error:[/] {e}")
            return 1
        except KeyboardInterrupt:
            console.print("\n[yellow]interrupted[/] — partial file kept, re-run to resume")
            return 130

    if args.url:
        outcome = run(args.url)
        return outcome if isinstance(outcome, int) else 0

    if args.output:
        console.print("[dim]note: -o does not apply to the menu; names come from the server.[/]")
        args.output = None
    interactive(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
