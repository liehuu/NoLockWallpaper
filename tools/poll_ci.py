# -*- coding: utf-8 -*-
"""轮询 GitHub Actions run 状态。用法：poll_ci.py <repo> <run_id>"""
import json
import subprocess
import sys
import time
import urllib.request

GCM = r"C:/Users/fengb/AppData/Local/Programs/WorkBuddy/resources/vendor/PortableGit/mingw64/bin/git-credential-manager.exe"


def token():
    p = subprocess.run([GCM, "get"], input="protocol=https\nhost=github.com\n\n",
                       capture_output=True, text=True)
    for line in p.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("no token")


TOK = token()
# 本机环境变量配了代理但代理未启动，走代理会 502。显式直连。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(path):
    req = urllib.request.Request("https://api.github.com" + path)
    req.add_header("Authorization", "Bearer " + TOK)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "wb-agent")
    with _OPENER.open(req, timeout=40) as r:
        return json.loads(r.read().decode())


REPO = sys.argv[1]
TARGET = sys.argv[2]

for i in range(200):
    r = api("/repos/%s/actions/runs/%s" % (REPO, TARGET))
    print("[%02d] %s / %s  (run %s)" % (i, r["status"], r["conclusion"], r["id"]), flush=True)
    if r["status"] == "completed":
        print("FINAL:", r["conclusion"])
        if r["conclusion"] != "success":
            j = api("/repos/%s/actions/runs/%s/jobs" % (REPO, TARGET))
            for job in j["jobs"]:
                print(" job:", job["name"], job["conclusion"])
                for s in job["steps"]:
                    mark = "  " if s["conclusion"] == "success" else "!!"
                    print("   %s %s %s" % (mark, s["number"], s["name"]), s["conclusion"])
        sys.exit(0 if r["conclusion"] == "success" else 1)
    time.sleep(8)
print("TIMEOUT")
sys.exit(2)