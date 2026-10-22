# -*- coding: utf-8 -*-
"""下载并打印某次 run 的失败步骤日志。用法：ci_log.py <repo> <run_id>"""
import io
import sys
import subprocess
import urllib.request
import zipfile

GCM = r"C:/Users/fengb/AppData/Local/Programs/WorkBuddy/resources/vendor/PortableGit/mingw64/bin/git-credential-manager.exe"


def token():
    p = subprocess.run([GCM, "get"], input="protocol=https\nhost=github.com\n\n",
                       capture_output=True, text=True)
    for line in p.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("no token")


TOK = token()


class NoAuthRedirect(urllib.request.HTTPRedirectHandler):
    """GitHub 的日志会 302 到 Azure Blob，那边不接受 Authorization 头。
    必须只带非鉴权头重放请求，且清掉 unredirected_hdrs。"""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        n = urllib.request.Request(newurl)
        for k, v in req.header_items():
            if k.lower() not in ("authorization", "host"):
                n.add_header(k, v)
        n.unredirected_hdrs = {}
        return n


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoAuthRedirect())

REPO, RUN = sys.argv[1], sys.argv[2]
url = "https://api.github.com/repos/%s/actions/runs/%s/logs" % (REPO, RUN)
req = urllib.request.Request(url)
req.add_header("Authorization", "Bearer " + TOK)
req.add_header("Accept", "application/vnd.github+json")
req.add_header("User-Agent", "wb")
data = _OPENER.open(req, timeout=180).read()
z = zipfile.ZipFile(io.BytesIO(data))

# 只打印 build job 里失败的那一步（或含 error 的段）
only = sys.argv[3] if len(sys.argv) > 3 else None
for n in z.namelist():
    if only and only not in n:
        continue
    txt = z.read(n).decode("utf-8", "replace")
    print("=" * 70)
    print("FILE:", n)
    print("=" * 70)
    # 去掉 GitHub 的时间戳前缀，便于阅读
    out = []
    for line in txt.splitlines():
        # 2026-10-22T01:23:45.1234567Z 前缀
        if len(line) > 28 and line[4] == "-" and line[10] == "T" and "Z " in line[:35]:
            idx = line.find("Z " )
            out.append(line[idx + 2:])
        else:
            out.append(line)
    print("\n".join(out[-3500:]))