# FileDL

**Extract real download links from file hosts and download them.**

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Version](https://img.shields.io/badge/version-2.0.2-green.svg)](#)
[![Author](https://img.shields.io/badge/author-Iddant%20ID-purple.svg)](#author)
[![License](https://img.shields.io/badge/license-MIT-lightgrey.svg)](#license)

Some file hosts hide the actual file behind an interstitial page, a countdown, a
client-side fingerprint, or a captcha. FileDL resolves that layer and hands you
the file — with a live progress bar, resume support, and the server's real
filename.

Two hosts are supported out of the box, each with a different resolution
strategy. Adding a third is a matter of one `*_handle` / `*_resolve` pair.

```
┌────────────────────────────────────────────────────────────┐
│                                                            │
│  FileDL v2.0.2                                             │
│  extract real links from file hosts and download them      │
│                                                            │
│  author  Iddant ID                                         │
│  output  ./downloads                                       │
│                                                            │
└────────────────────────────────────────────────────────────┘

┌─ choose a host ────────────────────────────────────────────┐
│  1  SFILE     sfile.co/<code>                              │
│  2  SAFILEKU  safefileku.com/download/<code>               │
└────────────────────────────────────────────────────────────┘

❯ paste link  (1/2 pick host, q quit):
```

---

## Features

- **Automatic link extraction** — resolves the real file URL behind
  interstitials, ad gateways and captchas
- **Two hosts** — `sfile.co` and `safefileku.com`; the host is auto-detected from
  a pasted URL
- **Resumable downloads** — interrupted transfers continue from where they
  stopped, verified byte-identical
- **Live progress** — bar, percentage, transferred size, speed, ETA
- **Real filenames** — taken from the server's `Content-Disposition`, sanitized
  against path traversal
- **Menu or CLI** — interactive menu for manual use, plain arguments for scripts
- **No window in your face** — the browser used for gated hosts runs parked
  off-screen

---

## Requirements

- **Python 3.10+**
- **Google Chrome** installed — required by `safefileku.com` (see
  [Why Chrome?](#why-google-chrome-is-required)). `sfile.co` works without it.

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

`requirements.txt`:

```
playwright>=1.40
curl_cffi>=0.6
rich>=13.0
```

> `curl_cffi` is used for HTTP transfers because it impersonates Chrome's TLS
> fingerprint — plain `requests` is rejected by Cloudflare on these hosts.

---

## Usage

### Interactive menu

```bash
python app.py
```

Paste a link and the host is detected automatically — or type `1` / `2` to pick a
host first. Press `q` to quit.

```
  SFILE  https://sfile.co/KRcDOmRs72C

  ↓ ibis Paint X 25 September.apk
  ────────────────────────────────────  100.0% 108.7/108.7 MB 1.8 MB/s 0:00:00
  ✓ saved  ibis Paint X 25 September.apk  103.64 MB
  ─ ready for the next link
```

### Command line

```bash
python app.py <url> [options]
```

```bash
# auto-detected host, default ./downloads
python app.py "https://sfile.co/KRcDOmRs72C"

# choose the output directory
python app.py "https://safefileku.com/download/4S22hbHrMGT70ZIu" -d D:/files

# just print the resolved link, don't download
python app.py "https://sfile.co/KRcDOmRs72C" --link-only

# re-download from scratch
python app.py "https://sfile.co/KRcDOmRs72C" --no-resume --force
```

### Options

| Flag | Description |
|---|---|
| `-V`, `--version` | Print version and exit |
| `-o`, `--output PATH` | Exact output file path |
| `-d`, `--dir DIR` | Output directory *(default: `downloads`)* |
| `--link-only` | Print the real link and exit *(sfile.co only)* |
| `--headful` | Show the browser window (useful for debugging) |
| `--no-resume` | Ignore a partial file and start over |
| `--force` | Overwrite an existing file |

---

## Supported hosts

### `sfile.co`

```
/<code>                          file page  → data-dw-url
/download/<code>?fid=<b64>       interstitial → data-direct-download
<direct url>?is=..&k=..          302 → downloadNNNN.sfile.co/downloadfile/…
```


### `safefileku.com`

```
/download/<code>                 page with a Cloudflare Turnstile form
POST (the form)                  gate opens → session cookies set
/download/file/<uid>/<code>      the file
```

---

---

## How it works

Both hosts follow the same shape — *resolve, then transfer*:

1. **Resolve** — a browser clears whatever gate the host applies and yields
   either the direct URL + cookies (`sfile.co`) or a session `uid` + cookies
   (`safefileku.com`).
2. **Transfer** — `curl_cffi` performs a resumable, streaming HTTP GET with the
   harvested cookies, reporting progress to `rich`.

Keeping the browser confined to step 1 is what makes resume possible: the
browser's own download stream exposes no byte-level progress, and on
`safefileku.com` the minted CDN URL is single-use, so it cannot be probed or
re-fetched separately.

### Safety details

- Server-supplied filenames are sanitized — path separators and control
  characters are stripped, so a malicious `Content-Disposition` cannot write
  outside the output directory.
- The download checks `Content-Length` against the bytes written and warns on
  mismatch.
- `--force` is required to overwrite; a partial file is resumed, not clobbered.

---

## Notes and limitations

- **`--link-only` is not supported for `safefileku.com`.** Its CDN URL is minted
  only for the in-browser transfer and expires within minutes, so printing it
  would hand you a dead link. FileDL reports this rather than failing silently.
- **Resume depends on the server honoring `Range`.** If it ignores the header,
  the transfer restarts from zero automatically.
- **The gate needs a display.** Headless fails, minimized fails — see
  [above](#why-google-chrome-is-required). On a headless server, use `xvfb-run`.
- Some hosts serve files whose archives are already damaged or password-protected
  at the source. FileDL transfers bytes faithfully; it does not repair them.
  Verify with the hash printed by the host if in doubt.
- Only hosts you are authorized to download from. FileDL reads what the site
  serves to any visitor — it does not bypass authentication or paywalls.

---

## Project layout

```
app.py       the tool (single file, no package)
requirements.txt  dependencies
README.md         this file
downloads/        default output directory (created on first run)
```


---

## Author

**Iddant ID**

## License

MIT License

Copyright (c) 2026 Iddant ID

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
