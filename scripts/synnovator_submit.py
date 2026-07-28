#!/usr/bin/env python3
"""Synnovator/Forgejo 本地代码安全提交工具。

认证优先级：
1. 使用 Git Credential Manager 安全存储的 Forgejo 访问令牌，通过 API 验证账号、读取/创建仓库；
2. 没有可用 API 令牌时，使用 Git Credential Manager + Forgejo OAuth2 浏览器授权；
3. SSH 仅作为用户显式选择的备用诊断流程。

脚本不会在日志、URL、命令行参数或项目文件中输出/保存令牌。
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import datetime as dt
import fnmatch
import getpass
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import ssl
import subprocess
import sys
from typing import Any, Iterable, Mapping, Sequence
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest
import webbrowser

VERSION = "3.1.0"
BASE_URL = "https://www.synnovator.com"
HOST = "www.synnovator.com"
API_BASE = f"{BASE_URL}/api/v1"
SWAGGER_URL = f"{BASE_URL}/api/swagger"
APPLICATIONS_URL = f"{BASE_URL}/user/settings/applications"
LOGIN_URL = f"{BASE_URL}/user/login"
NEW_REPO_URL = f"{BASE_URL}/repo/create"
SSH_KEYS_URL = f"{BASE_URL}/user/settings/keys"
TOKEN_SCOPE_DOC = "https://forgejo.org/docs/latest/user/authentication/token-scope/"
OAUTH_DOC = "https://forgejo.org/docs/latest/user/authentication/oauth2-provider/"
OAUTH_CLIENT_ID = "e90ee53c-94e2-48ac-9358-a874fb9e0662"
OAUTH_AUTHORIZE_ENDPOINT = "/login/oauth/authorize"
OAUTH_TOKEN_ENDPOINT = "/login/oauth/access_token"
OAUTH_REDIRECT_URI = "http://127.0.0.1"
CONFIG_NAME = "synnovator-submit.json"
MAX_CONTENT_SCAN = 2 * 1024 * 1024
WARN_SIZE = 20 * 1024 * 1024
HIGH_SIZE = 50 * 1024 * 1024
BLOCK_SIZE = 100 * 1024 * 1024

GITIGNORE_BLOCK = r"""
# >>> synnovator-submit managed rules >>>
# Environment and secrets
.env
.env.*
!.env.example
!.env.sample
*.pem
*.key
*.p12
*.pfx
id_rsa
id_rsa.*
id_ed25519
id_ed25519.*
credentials.json
service-account*.json
firebase-adminsdk*.json
.aws/
.ssh/
kubeconfig
.netrc
.npmrc
.pypirc

# Dependencies, local tool copies, and generated files
.synnovator-submit-skill/
node_modules/
.venv/
venv/
env/
__pycache__/
*.py[cod]
.cache/
.pytest_cache/
.mypy_cache/
.ruff_cache/
coverage/
*.log
*.tmp
*.temp
.DS_Store
Thumbs.db
# <<< synnovator-submit managed rules <<<
""".strip()

BLOCKED_NAME_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa",
    "id_rsa.*",
    "id_ed25519",
    "id_ed25519.*",
    "credentials.json",
    "service-account*.json",
    "firebase-adminsdk*.json",
    "kubeconfig",
    ".npmrc",
    ".pypirc",
    ".netrc",
)
ALLOWED_NAME_EXCEPTIONS = (".env.example", ".env.sample")
RISK_DIRS = {
    "node_modules",
    ".venv",
    "venv",
    "env",
    "dist",
    "build",
    "target",
    "coverage",
    ".next",
    ".cache",
    "__pycache__",
}
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private-key", re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\b(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}\b")),
    ("generic-api-token", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("bearer-token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{16,}")),
    (
        "password-or-secret-assignment",
        re.compile(r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token)\s*[:=]\s*['\"]?[^\s'\"]{8,}"),
    ),
)


class ToolError(RuntimeError):
    pass


class ApiError(ToolError):
    def __init__(self, status: int | None, message: str):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class GCMInfo:
    helper: str
    version: str


@dataclass
class AuthContext:
    method: str  # pat | oauth
    gcm: GCMInfo
    username: str | None = None
    token: str | None = None  # 仅保存在当前进程内，禁止输出


def run_cmd(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    capture: bool = True,
    input_text: str | None = None,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    try:
        cp = subprocess.run(
            list(args),
            cwd=str(cwd) if cwd else None,
            check=False,
            capture_output=capture,
            text=True,
            input=input_text,
            env=merged_env,
        )
    except OSError as exc:
        raise ToolError(f"无法执行命令 {args[0]}：{exc}") from exc
    if check and cp.returncode != 0:
        stdout = (cp.stdout or "").strip() if capture else ""
        stderr = (cp.stderr or "").strip() if capture else ""
        detail = "\n".join(x for x in (stdout, stderr) if x)
        raise ToolError(f"命令执行失败：{args[0]}" + (f"\n{detail}" if detail else ""))
    return cp


def ask(prompt: str, *, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value if value else (default or "")


def confirm_exact(prompt: str, expected: str) -> bool:
    print(prompt)
    return input(f"请输入“{expected}”继续: ").strip() == expected


def open_url(url: str) -> bool:
    """使用系统默认浏览器打开 URL；失败时仅返回 False。"""
    try:
        if webbrowser.open(url, new=2):
            return True
    except Exception:
        pass

    commands: list[list[str]] = []
    system = platform.system().lower()
    if os.name == "nt":
        commands = [["cmd", "/c", "start", "", url]]
    elif "microsoft" in platform.release().lower() and shutil.which("cmd.exe"):
        commands = [["cmd.exe", "/c", "start", "", url]]
    elif system == "darwin":
        commands = [["open", url]]
    else:
        commands = [["xdg-open", url]]

    for command in commands:
        if not shutil.which(command[0]):
            continue
        try:
            subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except OSError:
            continue
    return False


def open_or_print(url: str, label: str) -> None:
    if open_url(url):
        print(f"已使用系统默认浏览器打开{label}。")
    else:
        print(f"无法自动打开浏览器，请手动访问{label}：{url}")


def print_git_install_help() -> None:
    system = platform.system().lower()
    print("未检测到 Git。请安装 Git 后重新运行。")
    if system == "windows":
        print("Windows：winget install --id Git.Git -e --source winget")
    elif system == "darwin":
        print("macOS：xcode-select --install")
        print("或：brew install git")
    else:
        print("Debian/Ubuntu：sudo apt-get update && sudo apt-get install -y git")
        print("Fedora/RHEL：sudo dnf install -y git")
        print("Arch：sudo pacman -S --needed git")
    print("中国区环境可使用当前企业代理或匹配系统版本的清华 TUNA 镜像；不得关闭 TLS 校验。")


def ensure_git() -> None:
    if not shutil.which("git"):
        print_git_install_help()
        raise ToolError("Git 未安装")
    print(run_cmd(["git", "--version"]).stdout.strip())


def parse_version(value: str) -> tuple[int, int, int]:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", value)
    if not match:
        return (0, 0, 0)
    return tuple(int(x) for x in match.groups())  # type: ignore[return-value]


def detect_gcm(*, required: bool = True) -> GCMInfo | None:
    candidates = (
        (["git", "credential-manager", "--version"], "manager"),
        (["git", "credential-manager-core", "--version"], "manager-core"),
        (["git-credential-manager", "--version"], "manager"),
        (["git-credential-manager-core", "--version"], "manager-core"),
    )
    for command, helper in candidates:
        cp = run_cmd(command, check=False)
        if cp.returncode == 0:
            version = ((cp.stdout or "") + " " + (cp.stderr or "")).strip()
            info = GCMInfo(helper=helper, version=version)
            print(f"Git Credential Manager：{version}")
            if parse_version(version) < (2, 4, 1):
                raise ToolError("Git Credential Manager 版本低于 2.4.1，无法可靠使用通用 Forgejo OAuth2。请升级 Git/GCM。")
            return info
    if required:
        raise ToolError(
            "未检测到 Git Credential Manager。Windows 请更新 Git for Windows；"
            "macOS 可运行 brew install --cask git-credential-manager；Linux 请安装 GCM 并配置安全凭据存储。"
        )
    return None


def git_base(gcm: GCMInfo, *, oauth: bool = False) -> list[str]:
    args = ["git", "-c", "credential.helper=", "-c", f"credential.helper={gcm.helper}"]
    if oauth:
        prefix = f"credential.https://{HOST}"
        args.extend(
            [
                "-c",
                f"{prefix}.provider=generic",
                "-c",
                f"{prefix}.oauthClientId={OAUTH_CLIENT_ID}",
                "-c",
                f"{prefix}.oauthRedirectUri={OAUTH_REDIRECT_URI}",
                "-c",
                f"{prefix}.oauthAuthorizeEndpoint={OAUTH_AUTHORIZE_ENDPOINT}",
                "-c",
                f"{prefix}.oauthTokenEndpoint={OAUTH_TOKEN_ENDPOINT}",
                "-c",
                f"{prefix}.oauthDefaultUserName=OAUTH_USER",
                "-c",
                f"{prefix}.oauthUseClientAuthHeader=true",
            ]
        )
    return args


def git_network(
    root: Path | None,
    gcm: GCMInfo,
    git_args: Sequence[str],
    *,
    oauth: bool = False,
    interactive: bool = False,
    capture: bool = True,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    env = {
        "GIT_TERMINAL_PROMPT": "1" if interactive else "0",
        "GCM_INTERACTIVE": "always" if interactive else "never",
        "GCM_TRACE_SECRETS": "false",
    }
    return run_cmd(
        [*git_base(gcm, oauth=oauth), *git_args],
        cwd=root,
        check=check,
        capture=capture,
        env=env,
    )


def credential_payload(*, username: str | None = None, password: str | None = None) -> str:
    fields = ["protocol=https", f"host={HOST}"]
    if username:
        fields.append(f"username={username}")
    if password:
        fields.append(f"password={password}")
    return "\n".join(fields) + "\n\n"


def credential_fill(gcm: GCMInfo, *, interactive: bool = False) -> dict[str, str]:
    env = {
        "GIT_TERMINAL_PROMPT": "1" if interactive else "0",
        "GCM_INTERACTIVE": "always" if interactive else "never",
        "GCM_TRACE_SECRETS": "false",
    }
    cp = run_cmd(
        [*git_base(gcm), "credential", "fill"],
        check=False,
        input_text=credential_payload(),
        env=env,
    )
    if cp.returncode != 0:
        return {}
    result: dict[str, str] = {}
    for line in (cp.stdout or "").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key] = value
    return result


def credential_approve(gcm: GCMInfo, username: str, token: str) -> None:
    cp = run_cmd(
        [*git_base(gcm), "credential", "approve"],
        check=False,
        input_text=credential_payload(username=username, password=token),
        env={"GCM_TRACE_SECRETS": "false"},
    )
    if cp.returncode != 0:
        raise ToolError("访问令牌验证成功，但无法写入 Git Credential Manager 的安全凭据存储。")


def credential_reject(gcm: GCMInfo, username: str | None = None) -> None:
    run_cmd(
        [*git_base(gcm), "credential", "reject"],
        check=False,
        input_text=credential_payload(username=username),
        env={"GCM_TRACE_SECRETS": "false"},
    )


def api_request(
    path: str,
    *,
    token: str | None = None,
    method: str = "GET",
    body: Mapping[str, Any] | None = None,
    timeout: int = 20,
) -> Any:
    url = path if path.startswith("https://") else f"{API_BASE}{path}"
    headers = {
        "Accept": "application/json",
        "User-Agent": f"synnovator-code-submit/{VERSION}",
    }
    data: bytes | None = None
    if token:
        headers["Authorization"] = f"token {token}"
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urlrequest.Request(url, data=data, headers=headers, method=method)
    try:
        with urlrequest.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            if not raw:
                return None
            return json.loads(raw.decode("utf-8"))
    except urlerror.HTTPError as exc:
        try:
            detail_raw = exc.read().decode("utf-8", errors="replace")
            detail_obj = json.loads(detail_raw) if detail_raw else {}
            detail = detail_obj.get("message") if isinstance(detail_obj, dict) else detail_raw
        except Exception:
            detail = ""
        message = f"Forgejo API 返回 HTTP {exc.code}"
        if detail:
            message += f"：{detail}"
        raise ApiError(exc.code, message) from exc
    except urlerror.URLError as exc:
        reason = exc.reason
        if isinstance(reason, ssl.SSLError):
            raise ApiError(None, f"TLS 校验失败：{reason}。不得关闭 SSL 校验。") from exc
        raise ApiError(None, f"无法访问 Forgejo API：{reason}") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise ApiError(None, "访问 Forgejo API 超时") from exc
    except json.JSONDecodeError as exc:
        raise ApiError(None, "Forgejo API 返回了无法解析的 JSON") from exc


def probe_api() -> str:
    data = api_request("/version")
    if not isinstance(data, dict) or not isinstance(data.get("version"), str):
        raise ToolError("Forgejo API 版本接口响应异常")
    version = data["version"]
    print(f"Forgejo API：{version}")
    print(f"API Swagger：{SWAGGER_URL}")
    return version


def validate_api_token(token: str) -> dict[str, Any]:
    data = api_request("/user", token=token)
    if not isinstance(data, dict) or not data.get("login"):
        raise ToolError("令牌通过请求但未返回有效用户信息")
    return data


def validate_repo_token(token: str, remote: str) -> dict[str, Any]:
    owner, name = repo_parts(remote)
    data = api_request(f"/repos/{urlparse.quote(owner)}/{urlparse.quote(name)}", token=token)
    if not isinstance(data, dict) or not data.get("name"):
        raise ToolError("令牌通过仓库请求但响应格式异常")
    return data


def try_stored_pat(gcm: GCMInfo, remote: str | None = None) -> AuthContext | None:
    credential = credential_fill(gcm, interactive=False)
    token = credential.get("password")
    username = credential.get("username")
    if not token:
        return None
    if (username or "").upper().replace("-", "_") in {"OAUTH_USER", "OAUTHUSER"}:
        return None
    try:
        user = validate_api_token(token)
        login = str(user["login"])
    except ApiError as exc:
        if exc.status == 403 and remote and username:
            # 特定仓库令牌只能使用 repository/issue scopes，可能不能访问 /user。
            try:
                validate_repo_token(token, remote)
            except ApiError as repo_exc:
                if repo_exc.status in {401, 403, 404}:
                    credential_reject(gcm, username)
                    print("系统凭据库中的访问令牌不能访问目标仓库，已要求 GCM 删除该凭据。")
                    return None
                raise
            login = username
            print(f"已复用特定仓库访问令牌：账号 {login}，仓库 {repo_label(remote)}")
            return AuthContext(method="pat", gcm=gcm, username=login, token=token)
        if exc.status == 403:
            print("发现访问令牌，但它没有 /user API 权限。请提供目标仓库，或为仓库列表功能使用 read:user 令牌。")
            return None
        if exc.status == 401:
            credential_reject(gcm, username)
            print("系统凭据库中的 API 令牌已失效或被撤销，已要求 GCM 删除该凭据。")
            return None
        raise
    print(f"已复用安全凭据库中的 Forgejo API 令牌：账号 {login}")
    return AuthContext(method="pat", gcm=gcm, username=login, token=token)


def print_pat_scope_guidance(*, need_create: bool, remote_known: bool) -> None:
    print("\n访问令牌权限建议（采用最小权限）：")
    if remote_known and not need_create:
        print("  - 已知目标仓库：仓库访问范围选择“特定仓库”，权限仅选 write:repository。")
        print("  - 该令牌可能无法调用 GET /api/v1/user；脚本会改用目标仓库 API 验证。")
    else:
        print("  - 读取账号及仓库列表：read:user")
        print("  - 读取并推送仓库：write:repository")
        if need_create:
            print("  - 通过 POST /api/v1/user/repos 创建个人仓库：write:user")
        print("  - 需要仓库列表/新建仓库时，不能使用只允许 repository/issue scope 的特定仓库令牌。")
    print("  - 不要授予 admin、organization、package、notification 等无关权限。")
    print(f"  - 权限说明：{TOKEN_SCOPE_DOC}")
    print(f"  - API 参考：{SWAGGER_URL}")


def create_pat_interactive(
    gcm: GCMInfo,
    *,
    need_create: bool = True,
    remote: str | None = None,
) -> AuthContext | None:
    print_pat_scope_guidance(need_create=need_create, remote_known=remote is not None)
    if not confirm_exact("将优先使用 Forgejo 访问令牌和 API，不使用 OAuth2。", "创建 API 令牌"):
        return None
    open_or_print(APPLICATIONS_URL, "访问令牌设置页")
    print("在“生成新的令牌”区域创建令牌。令牌只显示一次；请粘贴到下方隐藏输入框。")
    token = getpass.getpass("访问令牌（输入不会回显）: ").strip()
    if not token:
        raise ToolError("未输入访问令牌")

    username: str
    try:
        user = validate_api_token(token)
        username = str(user["login"])
    except ApiError as exc:
        if exc.status == 403 and remote:
            try:
                validate_repo_token(token, remote)
            except ApiError as repo_exc:
                raise ToolError(f"访问令牌不能访问目标仓库：{repo_exc}") from repo_exc
            username = ask("Synnovator 用户名（用于 HTTPS Git 认证）")
            if not username:
                raise ToolError("用户名为空")
        else:
            raise ToolError(f"访问令牌验证失败：{exc}") from exc

    credential_approve(gcm, username, token)
    print(f"访问令牌验证通过：账号 {username}")
    print("令牌已交给 Git Credential Manager 安全保存；脚本不会打印或写入项目文件。")
    return AuthContext(method="pat", gcm=gcm, username=username, token=token)


def list_repositories(auth: AuthContext) -> list[dict[str, Any]]:
    if auth.method != "pat" or not auth.token:
        raise ToolError("仓库 API 列表需要访问令牌")
    repos: list[dict[str, Any]] = []
    for page in range(1, 21):
        query = urlparse.urlencode({"page": page, "limit": 50})
        data = api_request(f"/user/repos?{query}", token=auth.token)
        if not isinstance(data, list):
            raise ToolError("仓库列表 API 返回格式异常")
        repos.extend(item for item in data if isinstance(item, dict))
        if len(data) < 50:
            break
    repos.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return repos


def create_repository(auth: AuthContext, name: str, private: bool, description: str) -> dict[str, Any]:
    if auth.method != "pat" or not auth.token:
        raise ToolError("创建仓库 API 需要访问令牌")
    body = {
        "name": name,
        "private": private,
        "description": description,
        "auto_init": False,
    }
    data = api_request("/user/repos", token=auth.token, method="POST", body=body)
    if not isinstance(data, dict) or not data.get("clone_url"):
        raise ToolError("创建仓库 API 返回格式异常")
    return data


def normalize_remote(remote: str) -> str:
    value = remote.strip()
    if not value:
        raise ToolError("仓库地址为空")
    if value.startswith("git@") and ":" in value:
        host_path = value.split("@", 1)[1]
        host, path = host_path.split(":", 1)
        if host not in {"synnovator.com", HOST}:
            raise ToolError("SSH 地址不是 Synnovator 主机")
        value = f"{BASE_URL}/{path}"
    elif value.startswith("ssh://"):
        parsed = urlparse.urlparse(value)
        if parsed.hostname not in {"synnovator.com", HOST}:
            raise ToolError("SSH 地址不是 Synnovator 主机")
        value = f"{BASE_URL}/{parsed.path.lstrip('/')}"
    elif value.startswith("https://synnovator.com/"):
        value = BASE_URL + value[len("https://synnovator.com") :]
    elif value.startswith(f"https://{HOST}/"):
        pass
    elif value.startswith("http://"):
        raise ToolError("禁止使用未加密 HTTP 远端")
    else:
        raise ToolError("请提供 Synnovator 的 HTTPS 或可转换的 SSH 克隆地址")
    value = value.rstrip("/")
    if not value.endswith(".git"):
        value += ".git"
    parsed = urlparse.urlparse(value)
    segments = [x for x in parsed.path.split("/") if x]
    if len(segments) < 2:
        raise ToolError("仓库地址缺少 owner/repository")
    return value


def repo_parts(remote: str) -> tuple[str, str]:
    parsed = urlparse.urlparse(normalize_remote(remote))
    parts = [x for x in parsed.path.split("/") if x]
    owner = parts[-2]
    name = parts[-1][:-4] if parts[-1].endswith(".git") else parts[-1]
    return owner, name


def repo_label(remote: str) -> str:
    owner, name = repo_parts(remote)
    return f"{owner}/{name}"


def is_git_repo(root: Path) -> bool:
    cp = run_cmd(["git", "rev-parse", "--is-inside-work-tree"], cwd=root, check=False)
    return cp.returncode == 0 and (cp.stdout or "").strip() == "true"


def git_output(root: Path, *args: str, check: bool = True) -> str:
    cp = run_cmd(["git", *args], cwd=root, check=check)
    return (cp.stdout or "").strip()


def ensure_git_repo(root: Path) -> None:
    if is_git_repo(root):
        return
    if not confirm_exact(f"{root} 还不是 Git 仓库，将执行 git init。", "初始化"):
        raise ToolError("用户取消初始化")
    run_cmd(["git", "init"], cwd=root, capture=False)


def ensure_identity(root: Path) -> None:
    name = git_output(root, "config", "--get", "user.name", check=False)
    email = git_output(root, "config", "--get", "user.email", check=False)
    if name and email:
        print(f"Git 提交身份：{name} <{email}>")
        return
    print("当前项目缺少 Git 提交身份；配置只写入当前仓库。")
    name = ask("提交显示名称")
    email = ask("提交邮箱")
    if not name or not email:
        raise ToolError("Git 用户名或邮箱为空")
    if not confirm_exact(f"设置当前仓库身份为 {name} <{email}>。", "确认身份"):
        raise ToolError("用户取消身份配置")
    run_cmd(["git", "config", "user.name", name], cwd=root)
    run_cmd(["git", "config", "user.email", email], cwd=root)


def get_existing_origin(root: Path) -> str:
    if not is_git_repo(root):
        return ""
    return git_output(root, "remote", "get-url", "origin", check=False)


def show_existing_repo(root: Path, origin: str) -> None:
    branch = git_output(root, "branch", "--show-current", check=False) or "（未命名/游离）"
    upstream = git_output(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False) or "（无）"
    latest = git_output(root, "log", "-1", "--oneline", check=False) or "（无提交）"
    print("\n检测到当前文件夹已经是 Git 仓库：")
    print(f"  根目录：{root}")
    print(f"  origin：{origin or '（无）'}")
    print(f"  当前分支：{branch}")
    print(f"  跟踪分支：{upstream}")
    print(f"  最近提交：{latest}")


def load_config(root: Path) -> dict[str, Any]:
    path = root / ".git" / CONFIG_NAME
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def save_config(root: Path, remote: str, mode: str, auth_method: str) -> None:
    path = root / ".git" / CONFIG_NAME
    payload = {
        "schema_version": 2,
        "remote": remote,
        "mode": mode,
        "auth_method": auth_method,
        "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def choose_repo_from_api(auth: AuthContext) -> str | None:
    try:
        repos = list_repositories(auth)
    except ApiError as exc:
        if exc.status == 403:
            print("当前令牌不能读取 /api/v1/user/repos。可为令牌增加 read:user，或直接提供仓库 HTTPS 地址。")
            return None
        raise
    if repos:
        print("\n当前账号可见仓库（按最近更新时间排序）：")
        for index, repo in enumerate(repos[:50], 1):
            full_name = repo.get("full_name") or repo.get("name")
            visibility = "private" if repo.get("private") else "public"
            print(f"  {index:>2}. {full_name} [{visibility}]")
    else:
        print("当前账号没有可见仓库。")
    print("  N. 新建个人仓库")
    print("  M. 手动输入 HTTPS 克隆地址")
    choice = ask("选择仓库编号/N/M", default="N" if not repos else "")
    if choice.lower() == "m":
        return None
    if choice.lower() == "n":
        return create_repo_interactive(auth)
    if choice.isdigit() and 1 <= int(choice) <= min(len(repos), 50):
        repo = repos[int(choice) - 1]
        clone_url = repo.get("clone_url") or repo.get("html_url")
        if not clone_url:
            raise ToolError("所选仓库没有 HTTPS 克隆地址")
        return normalize_remote(str(clone_url))
    raise ToolError("仓库选择无效")


def create_repo_interactive(auth: AuthContext) -> str:
    name = ask("新仓库名称")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        raise ToolError("仓库名称只能包含字母、数字、点、下划线和连字符")
    visibility = ask("仓库可见性 private/public", default="private").lower()
    if visibility not in {"private", "public"}:
        raise ToolError("可见性必须是 private 或 public")
    if visibility == "public" and not confirm_exact("公开仓库中的全部代码和历史可能被任何人访问。", "创建公开仓库"):
        raise ToolError("用户取消创建公开仓库")
    description = ask("仓库描述（可留空）", default="")
    if not confirm_exact(f"将通过 Forgejo API 创建 {visibility} 仓库：{name}", "创建仓库"):
        raise ToolError("用户取消创建仓库")
    try:
        repo = create_repository(auth, name, visibility == "private", description)
    except ApiError as exc:
        if exc.status == 403:
            print("当前令牌缺少创建个人仓库所需权限。根据 /user/* 路由，请为令牌增加 write:user。")
            open_or_print(NEW_REPO_URL, "新建仓库页面")
            remote = ask("网页创建完成后粘贴 HTTPS 克隆地址")
            return normalize_remote(remote)
        raise
    remote = normalize_remote(str(repo["clone_url"]))
    print(f"仓库已创建：{repo.get('full_name') or name}")
    return remote


def resolve_remote(root: Path, cli_remote: str | None, auth: AuthContext | None) -> str:
    existing = get_existing_origin(root)
    config = load_config(root) if is_git_repo(root) else {}

    if existing:
        show_existing_repo(root, existing)
        if not confirm_exact("当前文件夹已经 clone/配置过仓库，是否继续使用该仓库同步推送？", "继续同步"):
            raise ToolError("用户拒绝继续使用当前 clone 仓库")
        normalized = normalize_remote(existing)
        if normalized != existing:
            print(f"当前 origin：{existing}")
            print(f"HTTPS origin：{normalized}")
            if not confirm_exact("默认认证不使用 SSH，将把该仓库转换为 HTTPS。", "转换 HTTPS"):
                raise ToolError("用户取消转换 HTTPS")
        if cli_remote:
            requested = normalize_remote(cli_remote)
            if requested != normalized:
                print(f"命令行目标：{requested}")
                if not confirm_exact("命令行目标与当前 origin 不同，将改用命令行目标。", "改用目标仓库"):
                    raise ToolError("用户取消更换目标仓库")
                return requested
        return normalized

    if cli_remote:
        return normalize_remote(cli_remote)

    saved_remote = config.get("remote") if isinstance(config, dict) else None
    if isinstance(saved_remote, str) and saved_remote:
        normalized = normalize_remote(saved_remote)
        if confirm_exact(f"发现上次使用的仓库：{normalized}", "使用上次仓库"):
            return normalized

    if auth and auth.method == "pat":
        selected = choose_repo_from_api(auth)
        if selected:
            return selected

    print("没有可用的 API 仓库选择结果。可在网页新建仓库，或粘贴现有仓库的 HTTPS 克隆地址。")
    if confirm_exact("是否打开 Synnovator 新建仓库页面？", "打开新建仓库"):
        open_or_print(NEW_REPO_URL, "新建仓库页面")
    remote = ask("HTTPS 克隆地址")
    return normalize_remote(remote)


def configure_origin(root: Path, remote: str) -> None:
    existing = get_existing_origin(root)
    if not existing:
        run_cmd(["git", "remote", "add", "origin", remote], cwd=root)
    elif existing != remote:
        print(f"当前 origin：{existing}")
        print(f"目标 origin：{remote}")
        if not confirm_exact("将替换当前 origin 地址。", "替换远端"):
            raise ToolError("用户取消替换远端")
        run_cmd(["git", "remote", "set-url", "origin", remote], cwd=root)
    if get_existing_origin(root) != remote:
        raise ToolError("origin 配置验证失败")


def test_remote(auth: AuthContext, root: Path | None, remote: str, *, interactive: bool = False) -> tuple[bool, str]:
    cp = git_network(
        root,
        auth.gcm,
        ["ls-remote", "--heads", remote],
        oauth=auth.method == "oauth",
        interactive=interactive,
        check=False,
    )
    detail = "\n".join(x for x in ((cp.stdout or "").strip(), (cp.stderr or "").strip()) if x)
    return cp.returncode == 0, detail


def classify_git_error(detail: str) -> str:
    lower = detail.lower()
    if "could not resolve host" in lower or "could not resolve hostname" in lower:
        return "DNS 解析失败"
    if "failed to connect" in lower or "timed out" in lower or "timeout" in lower:
        return "443 网络连接失败或超时"
    if "ssl certificate problem" in lower or "certificate verify failed" in lower:
        return "TLS 证书校验失败；不得关闭 sslVerify"
    if "authentication failed" in lower or "401" in lower:
        return "认证失败或凭据已撤销"
    if "403" in lower or "forbidden" in lower:
        return "账号没有仓库访问权限"
    if "repository not found" in lower or "not found" in lower:
        return "仓库不存在、地址错误或账号不可见"
    return "远端访问失败"


def auth_oauth(remote: str, gcm: GCMInfo) -> AuthContext:
    context = AuthContext(method="oauth", gcm=gcm, username=None, token=None)
    ok, _ = test_remote(context, None, remote, interactive=False)
    if ok:
        print("已复用 Git Credential Manager 中现有的 Forgejo OAuth2 授权。")
        return context

    print("\nAPI 令牌流程未完成，将进入 OAuth2 备用授权。")
    print("Forgejo 当前 OAuth2 token 未实现细粒度 scope，授权后可能代表账号执行超出 Git 推送范围的操作。")
    print(f"授权应用应显示为 Git Credential Manager，客户端 ID：{OAUTH_CLIENT_ID}")
    print(f"不再使用时可在这里撤销：{APPLICATIONS_URL}")
    if not confirm_exact("继续后 GCM 将启动系统默认浏览器完成授权。", "使用 OAuth2"):
        raise ToolError("用户取消 OAuth2 授权")

    # 预先打开登录页，确保用户能在系统浏览器中选择正确账号；真正的授权 URL 由 GCM 生成。
    open_or_print(LOGIN_URL, "Synnovator 登录页")
    print("正在触发 Git Credential Manager OAuth2。浏览器可能再次打开授权页面，请核对账号和应用名称。")
    ok, detail = test_remote(context, None, remote, interactive=True)
    if not ok:
        raise ToolError(f"OAuth2 授权或仓库访问失败：{classify_git_error(detail)}\n{detail}")
    ok, detail = test_remote(context, None, remote, interactive=False)
    if not ok:
        raise ToolError(f"浏览器授权后无法非交互复用凭据：{classify_git_error(detail)}\n{detail}")
    print("OAuth2 授权和仓库读取验证通过。")
    return context


def auth_auto(gcm: GCMInfo, *, allow_prompt: bool, remote: str | None = None) -> AuthContext | None:
    context = try_stored_pat(gcm, remote)
    if context:
        return context
    if allow_prompt:
        context = create_pat_interactive(gcm, need_create=remote is None, remote=remote)
        if context:
            return context
    if remote:
        return auth_oauth(remote, gcm)
    return None


def ensure_gitignore(root: Path) -> None:
    path = root / ".gitignore"
    current = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
    if "# >>> synnovator-submit managed rules >>>" in current:
        return
    new_text = current.rstrip() + ("\n\n" if current.strip() else "") + GITIGNORE_BLOCK + "\n"
    path.write_text(new_text, encoding="utf-8")
    print("已追加安全 .gitignore 规则；未覆盖原有内容。")


def null_split(value: str) -> list[str]:
    return [part for part in value.split("\0") if part]


def candidate_files(root: Path) -> list[Path]:
    cp = run_cmd(
        ["git", "ls-files", "--cached", "--others", "--modified", "--exclude-standard", "-z"],
        cwd=root,
    )
    unique: dict[str, Path] = {}
    for rel in null_split(cp.stdout or ""):
        path = root / rel
        if path.is_file():
            unique[rel] = path
    return [unique[key] for key in sorted(unique)]


def relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def blocked_by_name(rel: str) -> bool:
    base = Path(rel).name
    if base in ALLOWED_NAME_EXCEPTIONS:
        return False
    parts = set(Path(rel).parts)
    if ".ssh" in parts or ".aws" in parts or ".synnovator-submit-skill" in parts:
        return True
    return any(fnmatch.fnmatch(base, pattern) for pattern in BLOCKED_NAME_PATTERNS)


def is_probably_text(path: Path) -> bool:
    try:
        sample = path.read_bytes()[:4096]
    except OSError:
        return False
    return b"\x00" not in sample


def scan_files(root: Path, files: Iterable[Path]) -> dict[str, Any]:
    blocked_names: list[str] = []
    secrets: list[tuple[str, int, str]] = []
    warnings: list[str] = []
    large: list[tuple[str, int]] = []
    total_size = 0
    file_list = list(files)

    for path in file_list:
        rel = relative(root, path)
        try:
            size = path.stat().st_size
        except OSError:
            warnings.append(f"无法读取文件：{rel}")
            continue
        total_size += size
        if blocked_by_name(rel):
            blocked_names.append(rel)
        if any(part in RISK_DIRS for part in Path(rel).parts):
            warnings.append(f"可能是依赖或构建产物：{rel}")
        if size >= WARN_SIZE:
            large.append((rel, size))
        if size > MAX_CONTENT_SCAN or not is_probably_text(path):
            continue
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as handle:
                for line_no, line in enumerate(handle, 1):
                    for kind, pattern in SECRET_PATTERNS:
                        if pattern.search(line):
                            secrets.append((rel, line_no, kind))
                            if len(secrets) >= 100:
                                break
                    if len(secrets) >= 100:
                        break
        except OSError:
            warnings.append(f"无法扫描内容：{rel}")

    return {
        "files": file_list,
        "total_size": total_size,
        "blocked_names": sorted(set(blocked_names)),
        "secrets": sorted(set(secrets)),
        "warnings": sorted(set(warnings)),
        "large": sorted(large, key=lambda item: item[1], reverse=True),
    }


def human_size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GiB"


def print_scan(result: dict[str, Any]) -> None:
    files = result["files"]
    print(f"待考虑提交文件：{len(files)} 个，合计约 {human_size(int(result['total_size']))}")
    if result["large"]:
        print("较大文件：")
        for rel, size in result["large"][:10]:
            label = "阻止" if size >= BLOCK_SIZE else ("高风险" if size >= HIGH_SIZE else "提示")
            print(f"  - [{label}] {rel}: {human_size(size)}")
    if result["warnings"]:
        print("警告：")
        for item in result["warnings"][:20]:
            print(f"  - {item}")
    if result["blocked_names"]:
        print("禁止上传的敏感文件名：")
        for item in result["blocked_names"]:
            print(f"  - {item}")
    if result["secrets"]:
        print("疑似凭证内容（匹配值已隐藏）：")
        for rel, line_no, kind in result["secrets"]:
            print(f"  - {rel}:{line_no} [{kind}]")


def enforce_scan(result: dict[str, Any]) -> None:
    oversized = [(rel, size) for rel, size in result["large"] if size >= BLOCK_SIZE]
    problems: list[str] = []
    if result["blocked_names"]:
        problems.append("存在敏感文件名")
    if result["secrets"]:
        problems.append("存在疑似凭证内容")
    if oversized:
        problems.append("存在不小于 100 MiB 的文件")
    if problems:
        raise ToolError("安全扫描未通过：" + "、".join(problems) + "。请先移除、忽略或使用 Git LFS。")


def remote_main_oid(root: Path, auth: AuthContext) -> str | None:
    cp = git_network(
        root,
        auth.gcm,
        ["ls-remote", "--heads", "origin", "refs/heads/main"],
        oauth=auth.method == "oauth",
        interactive=False,
        check=False,
    )
    if cp.returncode != 0:
        detail = "\n".join(x for x in ((cp.stdout or "").strip(), (cp.stderr or "").strip()) if x)
        raise ToolError(f"无法读取远端仓库：{classify_git_error(detail)}\n{detail}")
    output = (cp.stdout or "").strip()
    return output.split()[0] if output else None


def fetch_main(root: Path, auth: AuthContext, exists: bool) -> None:
    if not exists:
        return
    git_network(
        root,
        auth.gcm,
        ["fetch", "origin", "main", "--prune"],
        oauth=auth.method == "oauth",
        interactive=False,
        capture=False,
    )


def has_head(root: Path) -> bool:
    return run_cmd(["git", "rev-parse", "--verify", "HEAD"], cwd=root, check=False).returncode == 0


def has_changes(root: Path) -> bool:
    return bool(git_output(root, "status", "--porcelain"))


def commit_changes(root: Path, mode: str) -> None:
    if not has_changes(root) and has_head(root):
        print("工作区没有新变更，不创建空提交。")
        return
    run_cmd(["git", "add", "--all"], cwd=root)
    staged = git_output(root, "diff", "--cached", "--name-status")
    if not staged and has_head(root):
        print("没有可提交内容。")
        return
    print("已暂存变更：")
    print(staged or "  首次提交")
    check = run_cmd(["git", "diff", "--cached", "--check"], cwd=root, check=False)
    if check.returncode != 0:
        raise ToolError("暂存内容存在空白错误：\n" + ((check.stdout or "") + (check.stderr or "")))
    default = (
        "chore: publish snapshot " + dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        if mode == "snapshot"
        else ("chore: update submission" if has_head(root) else "chore: initial submission")
    )
    message = ask("提交信息", default=default)
    run_cmd(["git", "commit", "-m", message], cwd=root, capture=False)


def is_fast_forward(root: Path) -> bool:
    if run_cmd(["git", "rev-parse", "--verify", "refs/remotes/origin/main"], cwd=root, check=False).returncode != 0:
        return False
    return run_cmd(
        ["git", "merge-base", "--is-ancestor", "refs/remotes/origin/main", "HEAD"],
        cwd=root,
        check=False,
    ).returncode == 0


def archive_branch_name(root: Path, auth: AuthContext, expected_oid: str) -> str:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    base = f"archive/main-{stamp}"
    cp = git_network(
        root,
        auth.gcm,
        ["ls-remote", "--heads", "origin", f"refs/heads/{base}"],
        oauth=auth.method == "oauth",
        interactive=False,
        check=False,
    )
    return f"{base}-{expected_oid[:7]}" if (cp.stdout or "").strip() else base


def create_archive(root: Path, auth: AuthContext, expected_oid: str) -> str:
    branch = archive_branch_name(root, auth, expected_oid)
    git_network(
        root,
        auth.gcm,
        ["push", "origin", f"{expected_oid}:refs/heads/{branch}"],
        oauth=auth.method == "oauth",
        interactive=False,
        capture=False,
    )
    cp = git_network(
        root,
        auth.gcm,
        ["ls-remote", "--heads", "origin", f"refs/heads/{branch}"],
        oauth=auth.method == "oauth",
        interactive=False,
    )
    if not (cp.stdout or "").strip():
        raise ToolError("历史备份分支创建后验证失败")
    print(f"远端原 main 已保存为：{branch}")
    return branch


def push_to_main(root: Path, auth: AuthContext, mode: str, expected_oid: str | None) -> str | None:
    current_oid = remote_main_oid(root, auth)
    if current_oid != expected_oid:
        raise ToolError("远端 main 在确认后发生变化。已停止推送，请重新运行。")

    if expected_oid is None:
        git_network(
            root,
            auth.gcm,
            ["push", "-u", "origin", "HEAD:main"],
            oauth=auth.method == "oauth",
            interactive=False,
            capture=False,
        )
        return None

    fetch_main(root, auth, True)
    if mode == "incremental":
        if not is_fast_forward(root):
            raise ToolError("远端 main 不能快进到当前提交。请整合远端历史，或使用 --mode snapshot。")
        git_network(
            root,
            auth.gcm,
            ["push", "origin", "HEAD:main"],
            oauth=auth.method == "oauth",
            interactive=False,
            capture=False,
        )
        return None

    archive = create_archive(root, auth, expected_oid)
    args = ["push", "origin", "HEAD:main"] if is_fast_forward(root) else [
        "push",
        f"--force-with-lease=refs/heads/main:{expected_oid}",
        "origin",
        "HEAD:main",
    ]
    git_network(
        root,
        auth.gcm,
        args,
        oauth=auth.method == "oauth",
        interactive=False,
        capture=False,
    )
    return archive


def verify_push(root: Path, auth: AuthContext) -> str:
    local = git_output(root, "rev-parse", "HEAD")
    remote = remote_main_oid(root, auth)
    if not remote or local != remote:
        raise ToolError(f"推送后验证失败：本地 {local}，远端 {remote or '不存在'}")
    return local


def perform_push(root: Path, auth: AuthContext, remote: str, mode: str) -> None:
    ensure_git_repo(root)
    ensure_identity(root)
    configure_origin(root, remote)
    ensure_gitignore(root)

    ok, detail = test_remote(auth, root, remote, interactive=False)
    if not ok:
        raise ToolError(f"认证或仓库读取未通过：{classify_git_error(detail)}\n{detail}\n请先运行 auth。")

    files = candidate_files(root)
    scan = scan_files(root, files)
    print_scan(scan)
    enforce_scan(scan)

    expected_oid = remote_main_oid(root, auth)
    fetch_main(root, auth, expected_oid is not None)
    backup_preview = "无（远端 main 不存在）" if expected_oid is None else (
        "普通更新不创建" if mode == "incremental" else "archive/main-<时间戳>"
    )
    print("\n推送计划")
    print(f"  目标仓库：{repo_label(remote)}")
    print(f"  远端地址：{remote}")
    print(f"  认证方式：{'Forgejo API 访问令牌 + GCM' if auth.method == 'pat' else 'Forgejo OAuth2 + GCM'}")
    print("  目标分支：main")
    print(f"  发布模式：{'普通更新' if mode == 'incremental' else '快照替换'}")
    print(f"  远端 main：{expected_oid or '不存在'}")
    print(f"  历史备份：{backup_preview}")
    print(f"  文件数量：{len(files)}")
    print(f"  预计内容：{human_size(int(scan['total_size']))}")
    if scan["large"]:
        print("  风险：包含较大文件，请确认仓库体积和平台限制。")
    print("  已阻止：.env、私钥、常见凭证文件和疑似 Token。")

    if mode == "snapshot" and expected_oid:
        ok_confirm = confirm_exact(
            "远端 main 将以当前项目快照为准；原 main 会先保存到历史分支。",
            repo_parts(remote)[1],
        )
    else:
        ok_confirm = confirm_exact("确认提交并推送以上内容到正式 main。", "确认推送")
    if not ok_confirm:
        raise ToolError("用户取消推送")

    commit_changes(root, mode)
    if not has_head(root):
        raise ToolError("当前仓库没有可推送的提交")
    archive = push_to_main(root, auth, mode, expected_oid)
    oid = verify_push(root, auth)
    save_config(root, remote, mode, auth.method)

    subject = git_output(root, "log", "-1", "--pretty=%s")
    print("\n上传完成。")
    print(f"仓库：{repo_label(remote)}")
    print("分支：main")
    print(f"提交：{oid[:7]} {subject}")
    if archive:
        print(f"历史备份：{archive}")


def ssh_fallback(remote: str | None) -> None:
    print("SSH 仅用于显式备用诊断，不是默认上传方式。")
    if not remote:
        remote = ask("平台仓库页面显示的完整 SSH 克隆地址")
    if not (remote.startswith("git@") or remote.startswith("ssh://")):
        raise ToolError("SSH 备用流程必须使用平台页面显示的 SSH 克隆地址")
    print("不会假设 synnovator.com:22 是平台 Git SSH 入口。")
    print(f"请按仓库页面给出的 host/port 测试：{remote}")
    print(f"SSH 公钥管理页：{SSH_KEYS_URL}")
    open_or_print(SSH_KEYS_URL, "SSH 公钥管理页")


def command_check(args: argparse.Namespace) -> None:
    ensure_git()
    gcm = detect_gcm(required=True)
    assert gcm is not None
    probe_api()
    remote = normalize_remote(args.remote) if args.remote else None
    context = try_stored_pat(gcm, remote)
    if context:
        print(f"API/仓库令牌检查通过：{context.username}")
    else:
        print("未发现可用的 API 访问令牌；check 不会打开浏览器或要求登录。")
    if remote:
        oauth_context = AuthContext(method="oauth", gcm=gcm)
        ok, detail = test_remote(oauth_context, None, remote, interactive=False)
        if ok:
            print(f"仓库只读访问通过：{repo_label(remote)}")
        else:
            print(f"仓库只读访问未通过：{classify_git_error(detail)}")


def command_auth(args: argparse.Namespace) -> None:
    ensure_git()
    gcm = detect_gcm(required=True)
    assert gcm is not None
    probe_api()
    method = args.auth
    remote = normalize_remote(args.remote) if args.remote else None

    if method in {"auto", "pat"}:
        context = try_stored_pat(gcm, remote)
        if not context:
            context = create_pat_interactive(gcm, need_create=remote is None, remote=remote)
        if context:
            if remote:
                ok, detail = test_remote(context, None, remote, interactive=False)
                if not ok:
                    raise ToolError(f"API 令牌有效，但 Git 仓库访问失败：{classify_git_error(detail)}\n{detail}")
            print("访问令牌/API 授权完成。")
            return
        if method == "pat":
            raise ToolError("未完成访问令牌授权")

    if not remote:
        raise ToolError("OAuth2 需要真实 HTTPS 仓库地址，请提供 --remote")
    auth_oauth(remote, gcm)


def command_repos(args: argparse.Namespace) -> None:
    ensure_git()
    gcm = detect_gcm(required=True)
    assert gcm is not None
    probe_api()
    context = try_stored_pat(gcm) or create_pat_interactive(gcm, need_create=True)
    if not context:
        raise ToolError("仓库 API 操作需要访问令牌")
    remote = choose_repo_from_api(context)
    if remote:
        print(f"选择结果：{remote}")


def load_noninteractive_auth(gcm: GCMInfo, remote: str) -> AuthContext | None:
    context = try_stored_pat(gcm, remote)
    if context:
        ok, _ = test_remote(context, None, remote, interactive=False)
        if ok:
            return context
    oauth = AuthContext(method="oauth", gcm=gcm)
    ok, _ = test_remote(oauth, None, remote, interactive=False)
    return oauth if ok else None


def command_push(args: argparse.Namespace) -> None:
    root = Path(args.project).expanduser().resolve()
    if not root.is_dir():
        raise ToolError(f"项目目录不存在：{root}")
    ensure_git()
    gcm = detect_gcm(required=True)
    assert gcm is not None
    existing = get_existing_origin(root)
    candidate = args.remote or existing or (load_config(root).get("remote") if is_git_repo(root) else None)
    if not candidate:
        raise ToolError("push 阶段不会静默授权或创建仓库；请提供 --remote，或先运行 run/auth。")
    remote = resolve_remote(root, str(candidate), None)
    auth = load_noninteractive_auth(gcm, remote)
    if not auth:
        raise ToolError("没有可非交互复用的 API 令牌或 OAuth2 凭据。请先运行 auth 或 run。")
    perform_push(root, auth, remote, args.mode)


def command_run(args: argparse.Namespace) -> None:
    root = Path(args.project).expanduser().resolve()
    if not root.is_dir():
        raise ToolError(f"项目目录不存在：{root}")
    print(f"项目目录：{root}")
    ensure_git()
    gcm = detect_gcm(required=True)
    assert gcm is not None
    probe_api()

    # 先从命令行、当前 clone/origin 或本工具上次配置中确定已知目标。
    # 已知目标时允许使用仅授予 write:repository 的特定仓库令牌，
    # 令牌可能无权访问 /user，因此改用 /repos/{owner}/{repo} 验证。
    existing = get_existing_origin(root)
    saved_value = load_config(root).get("remote") if is_git_repo(root) else None
    preliminary_value = args.remote or existing or (saved_value if isinstance(saved_value, str) else None)
    preliminary_remote = normalize_remote(str(preliminary_value)) if preliminary_value else None

    # 第一优先：Forgejo 细粒度访问令牌 + API + GCM。
    auth = try_stored_pat(gcm, preliminary_remote)
    if not auth:
        auth = create_pat_interactive(
            gcm,
            need_create=preliminary_remote is None,
            remote=preliminary_remote,
        )

    remote = resolve_remote(root, args.remote, auth)
    if auth:
        ok, detail = test_remote(auth, None, remote, interactive=False)
        if not ok:
            raise ToolError(f"API 令牌有效，但目标仓库 Git 访问失败：{classify_git_error(detail)}\n{detail}")
    else:
        # 第二优先：OAuth2。真正的授权页由 GCM 通过默认浏览器打开。
        auth = auth_oauth(remote, gcm)

    perform_push(root, auth, remote, args.mode)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="通过 Forgejo API 令牌或 OAuth2 安全上传代码到 Synnovator")
    parser.add_argument("--version", action="version", version=VERSION)
    sub = parser.add_subparsers(dest="command")

    def common(name: str, help_text: str) -> argparse.ArgumentParser:
        item = sub.add_parser(name, help=help_text)
        item.add_argument("--project", default=".", help="项目目录，默认当前目录")
        item.add_argument("--remote", help="Synnovator HTTPS/SSH 克隆地址；SSH 会转换为 HTTPS")
        return item

    check = common("check", "只读检查 Git、GCM、API 和可选仓库")
    check.set_defaults(func=command_check)

    auth = common("auth", "独立认证：访问令牌/API 优先，OAuth2 备用")
    auth.add_argument("--auth", choices=("auto", "pat", "oauth"), default="auto")
    auth.set_defaults(func=command_auth)

    repos = common("repos", "通过 Forgejo API 列出或创建仓库")
    repos.set_defaults(func=command_repos)

    push_parser = common("push", "只使用已有凭据提交并推送，不静默登录")
    push_parser.add_argument("--mode", choices=("incremental", "snapshot"), default="incremental")
    push_parser.set_defaults(func=command_push)

    run_parser = common("run", "一键编排认证、仓库选择、安全扫描和推送")
    run_parser.add_argument("--mode", choices=("incremental", "snapshot"), default="incremental")
    run_parser.set_defaults(func=command_run)

    ssh = common("ssh-bind", "显式 SSH 备用诊断")
    ssh.set_defaults(func=lambda ns: ssh_fallback(ns.remote))
    bind = common("bind", "ssh-bind 的兼容别名")
    bind.set_defaults(func=lambda ns: ssh_fallback(ns.remote))

    docs = sub.add_parser("api-docs", help="打开 Synnovator Swagger 和 Forgejo token scope 文档")
    docs.set_defaults(
        func=lambda ns: (
            open_or_print(SWAGGER_URL, "Synnovator API Swagger"),
            open_or_print(TOKEN_SCOPE_DOC, "Forgejo Token Scope 文档"),
        )
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        args = parser.parse_args(["run", *(argv or [])])
    result = args.func(args)
    _ = result
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        raise SystemExit(130)
    except ToolError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(1)
