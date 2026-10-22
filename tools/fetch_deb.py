# -*- coding: utf-8 -*-
"""下载某次 CI run 的 artifact 里的 deb。

用法：python tools/fetch_deb.py <repo> <run_id> [输出目录]

★ 三个必须踩对的点：
  1. artifact 下载会 302 跳到 Azure Blob。Blob 侧不接受 GitHub 的
     Authorization，带着就 403/连接被重置 —— 重定向时必须剥掉。
  2. **本机 python urllib 直连 blob.core.windows.net 会被 RST
     （WinError 10054），走环境里的透明代理也会 SSL EOF**。
     实测只有 curl --noproxy '*' 能通，所以这里用 curl 下 zip。
  3. 用 **artifact 级**接口 /actions/artifacts/{id}/zip。
"""
import io
import json
import os
import subprocess
import sys
import urllib.request
import zipfile

GCM = (r"C:/Users/fengb/AppData/Local/Programs/WorkBuddy/resources/vendor/"
       r"PortableGit/mingw64/bin/git-credential-manager.exe")


def token():
    p = subprocess.run([GCM, "get"],
                       input="protocol=https\nhost=github.com\n\n",
                       capture_output=True, text=True)
    for line in p.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("拿不到 token")


TOK = token()


class _NoAuthRedirect(urllib.request.HTTPRedirectHandler):
    """artifact 下载 302 到 Azure Blob，那里不接受 GitHub Authorization。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        r = super().redirect_request(req, fp, code, msg, headers, newurl)
        if r is not None:
            r.headers.pop("Authorization", None)
            try:
                r.remove_header("Authorization")
            except Exception:
                pass
            r.unredirected_hdrs.pop("Authorization", None)
        return r


# 本机环境变量配了代理但代理未启动，走代理会 502 —— 显式直连
_OP = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoAuthRedirect())


def api(path, raw=False):
    req = urllib.request.Request("https://api.github.com" + path)
    req.add_header("Authorization", "Bearer " + TOK)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "wb-agent")
    with _OP.open(req, timeout=180) as r:
        data = r.read()
    return data if raw else json.loads(data.decode())


def fetch_zip_via_curl(artifact_api_url, out_path):
    """用 curl 取 artifact zip。

    ★ 为什么要绕开 urllib：
      本机 python 直连 blob.core.windows.net 直接 WinError 10054，
      走环境里的透明代理又 SSL UNEXPECTED_EOF；实测只有 curl 能通。
      --noproxy '*' 是必须的，否则 curl 会去用那个不通的代理。
    """
    # 先拿到 302 的 Location（GitHub API 302 到 Blob 带签名 URL）
    no_redir = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        type("N", (urllib.request.HTTPRedirectHandler,), {
            "redirect_request": lambda *a, **k: None})())
    req = urllib.request.Request(artifact_api_url)
    req.add_header("Authorization", "Bearer " + TOK)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "wb-agent")
    loc = None
    try:
        with no_redir.open(req, timeout=60) as r:
            return r.read()          # 万一没跳转，直接就是 body
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308):
            loc = e.headers.get("Location")
        else:
            raise
    if not loc:
        raise RuntimeError("拿不到 artifact 下载地址")

    p = subprocess.run(["curl", "-sS", "-L", "--noproxy", "*",
                        "-o", out_path, "--max-time", "180", loc],
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("curl 失败: %s" % (p.stderr or p.returncode))
    with open(out_path, "rb") as f:
        return f.read()


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    repo, run_id = sys.argv[1], sys.argv[2]
    outdir = sys.argv[3] if len(sys.argv) > 3 else "."
    os.makedirs(outdir, exist_ok=True)

    arts = api("/repos/%s/actions/runs/%s/artifacts" % (repo, run_id))
    if not arts["artifacts"]:
        print("该 run 没有 artifact")
        return 1

    got = []
    for a in arts["artifacts"]:
        print("artifact: %s (%d B, expired=%s)"
              % (a["name"], a["size_in_bytes"], a["expired"]))
        if a["expired"]:
            print("  已过期，跳过")
            continue
        blob = fetch_zip_via_curl(
            "https://api.github.com/repos/%s/actions/artifacts/%d/zip"
            % (repo, a["id"]),
            os.path.join(outdir, "_artifact_%d.zip" % a["id"]))
        print("  下载 %d B" % len(blob))
        z = zipfile.ZipFile(io.BytesIO(blob))
        for n in z.namelist():
            if n.endswith("/"):
                continue
            data = z.read(n)
            dst = os.path.join(outdir, os.path.basename(n))
            with open(dst, "wb") as f:
                f.write(data)
            print("  解出 %s (%d B)" % (dst, len(data)))
            if n.endswith(".deb"):
                got.append(dst)

    print("\n共取得 %d 个 deb：" % len(got))
    for g in got:
        print("  " + g)
    return 0 if got else 1


if __name__ == "__main__":
    sys.exit(main())