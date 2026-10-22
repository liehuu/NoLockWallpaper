# -*- coding: utf-8 -*-
"""用 GitHub REST API 提交一次 commit（绕开 github.com:443 的 git 通道）。

本机 git 通道不通（代理不可用），但 api.github.com 可用。
做法：取本地 HEAD 相对父提交的变更 → 逐个建 blob → 建 tree → 建 commit → 更新 ref。

★ 关键坑（都踩过）：
  · 丢 executable bit：mode 必须从 git 索引取（git ls-files -s），
    不能用 os.name 判断（Windows 上 os.name == 'nt' 恒真，分支走错）
  · 422 Invalid tree info：远端 commit 不在本地对象库时 git diff 返回空，
    推前要用 git cat-file -e 确认父 commit 存在
"""
import base64
import json
import os
import subprocess
import sys
import urllib.request

REPO = "liehuu/NoLockWallpaper"
BRANCH = "master"
REPO_DIR = r"C:/Users/fengb/WorkBuddy/2026-10-17-20-12-03/NoLockWallpaper-build"
GCM = r"C:/Users/fengb/AppData/Local/Programs/WorkBuddy/resources/vendor/PortableGit/mingw64/bin/git-credential-manager.exe"
API = "https://api.github.com"

# ★ 本机环境变量里配了代理但代理未启动，走代理会失败。这里显式直连。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def token():
    p = subprocess.run([GCM, "get"], input="protocol=https\nhost=github.com\n\n",
                       capture_output=True, text=True)
    for line in p.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("no token")


TOKEN = token()


def api(method, path, body=None):
    url = API + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + TOKEN)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "wb")
    if data:
        req.add_header("Content-Type", "application/json")
    with _OPENER.open(req, timeout=120) as r:
        raw = r.read()
    return json.loads(raw.decode()) if raw else {}


def git(*args):
    p = subprocess.run(["git"] + list(args), cwd=REPO_DIR,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return p.stdout.strip()


# 0) 取索引里的 mode（★ 必须从索引取，不能靠 os.name 猜）
_idx_mode = {}
for _line in git("ls-files", "-s").splitlines():
    try:
        _meta, _p = _line.split("\t", 1)
        _idx_mode[_p] = _meta.split()[0]
    except ValueError:
        pass

head = git("rev-parse", "HEAD")
parent = git("rev-parse", "HEAD~1")
print("local head  :", head[:8])
print("local parent:", parent[:8])

# 1) 远端当前 head
# ★ 空仓库时 GitHub 返回 409 Conflict（不是 404）—— 建库后立刻推会遇到。
#   两种情况都视为「远端为空」，走新建 ref 分支。
try:
    r = api("GET", "/repos/%s/git/ref/heads/%s" % (REPO, BRANCH))
    remote_sha = r["object"]["sha"]
    print("remote head :", remote_sha[:8])
except urllib.error.HTTPError as e:
    if e.code in (404, 409):
        remote_sha = None
        print("remote head : (empty repo, HTTP %d)" % e.code)
    else:
        raise

# 2) base_tree
base_tree = None
if remote_sha:
    try:
        rc = api("GET", "/repos/%s/git/commits/%s" % (REPO, remote_sha))
        base_tree = rc["tree"]["sha"]
        print("base_tree   :", base_tree[:8])
    except urllib.error.HTTPError:
        base_tree = None

# 3) 变更文件：远端为空 → 全量；否则尝试 diff，失败则全量
changed = []
if remote_sha:
    have = subprocess.run(["git", "cat-file", "-e", remote_sha + "^{commit}"],
                          cwd=REPO_DIR, capture_output=True).returncode == 0
    if have:
        d = git("diff", "--name-status", remote_sha, head)
        changed = [l for l in d.splitlines() if l.strip()]
        print("diff 命中    :", len(changed), "个文件")

if not changed:
    # 全量推送（首次建仓 / 远端 commit 不在本地库）
    allf = git("ls-files").splitlines()
    changed = ["A\t" + f for f in allf]
    print("全量推送     :", len(changed), "个文件")

# 4) 建 blob + entries
#
# ★ 空仓库的特殊情况（实测踩到）：
#   仓库刚创建、一次 commit 都没有时，**git/blobs 也返回 409 Conflict**
#   —— 因为 git objects 数据库还没初始化。此时必须改走
#   PUT /contents/<path> 逐个建文件（每次调用会自建初始 commit）。
#   所以先在这一步之前做「空仓库引导」。
if remote_sha is None:
    print("→ 空仓库引导：用 PUT /contents 逐个建文件")
    files = git("ls-files").splitlines()
    for path in files:
        full = os.path.join(REPO_DIR, path)
        if not os.path.exists(full):
            continue
        raw = open(full, "rb").read()
        body = {
            "message": "init: " + path,
            "content": base64.b64encode(raw).decode(),
            "branch": BRANCH,
        }
        try:
            api("PUT", "/repos/%s/contents/%s" % (REPO, path), body)
            print("  init", path, len(raw), "B")
        except urllib.error.HTTPError as e:
            print("  ✗ init 失败", path, e.code, e.read().decode()[:200])
            raise SystemExit(1)

    # 引导后远端已有初始 commit，但不含我们想要的**完整** commit 消息与
    # 精确的 mode（contents API 对 .sh 可能给 100644）。
    # → 重新取 remote head，走正常的 tree 流程把它修正过来。
    r = api("GET", "/repos/%s/git/ref/heads/%s" % (REPO, BRANCH))
    remote_sha = r["object"]["sha"]
    rc = api("GET", "/repos/%s/git/commits/%s" % (REPO, remote_sha))
    base_tree = rc["tree"]["sha"]
    print("引导完成，remote head:", remote_sha[:8])

    # 重新计算变更：全部文件都要重新写一遍（修正 mode + 确保内容一致）
    changed = ["M\t" + f for f in git("ls-files").splitlines()]

entries = []
for line in changed:
    parts = line.split("\t")
    status, path = parts[0], parts[-1]
    full = os.path.join(REPO_DIR, path)
    if status.startswith("D") or not os.path.exists(full):
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
        print("  delete", path)
        continue
    raw = open(full, "rb").read()
    mode = _idx_mode.get(path, "100644")
    blob = api("POST", "/repos/%s/git/blobs" % REPO, {
        "content": base64.b64encode(raw).decode(),
        "encoding": "base64",
    })
    entries.append({"path": path, "mode": mode, "type": "blob", "sha": blob["sha"]})
    print("  blob", path, len(raw), "B", mode)

# 5) 建 tree
tree_body = {"tree": entries}
if base_tree:
    tree_body["base_tree"] = base_tree
tree = api("POST", "/repos/%s/git/trees" % REPO, tree_body)
print("tree        :", tree["sha"][:8])

# 6) 建 commit
msg = git("log", "-1", "--pretty=%B")
cb = {"message": msg, "tree": tree["sha"], "parents": [remote_sha] if remote_sha else []}
commit = api("POST", "/repos/%s/git/commits" % REPO, cb)
print("commit      :", commit["sha"])

# 7) 更新分支
if remote_sha:
    api("PATCH", "/repos/%s/git/refs/heads/%s" % (REPO, BRANCH),
        {"sha": commit["sha"], "force": False})
else:
    api("POST", "/repos/%s/git/refs" % REPO,
        {"ref": "refs/heads/" + BRANCH, "sha": commit["sha"]})
print("branch updated ->", commit["sha"])