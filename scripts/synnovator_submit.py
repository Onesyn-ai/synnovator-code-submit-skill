#!/usr/bin/env python3
"""使用 HTTPS + Git Credential Manager OAuth2 安全发布代码到 Synnovator。

默认认证顺序：
1. 复用 Git Credential Manager 已保存的 Forgejo OAuth2 授权；
2. 没有有效授权时，通过真实 HTTPS 仓库请求触发浏览器授权；
3. 只有用户明确选择时才进入 SSH 备用流程。

脚本不会读取、打印或保存 OAuth access token、refresh token、密码或私钥。
"""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import urllib.parse
import webbrowser
from typing import Iterable, Sequence

BASE_URL = "https://www.synnovator.com"
WEB_HOST = "www.synnovator.com"
APEX_HOST = "synnovator.com"
OAUTH_SETTINGS_URL = f"{BASE_URL}/user/settings/applications"
SSH_SETTINGS_URL = f"{BASE_URL}/user/settings/keys"
REPO_CREATE_URL = f"{BASE_URL}/repo/create"
CONFIG_NAME = "synnovator-submit.json"
GCM_MIN_VERSION = (2, 4, 1)
FORGEJO_GCM_CLIENT_ID = "e90ee53c-94e2-48ac-9358-a874fb9e0662"
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

# Dependencies and generated files
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
PUBLIC_KEY_PREFIXES = (
    "ssh-ed25519 ",
    "ssh-rsa ",
    "ecdsa-sha2-",
    "sk-ecdsa-sha2-",
    "sk-ssh-ed25519@openssh.com ",
)


class ToolError(RuntimeError):
    pass


def run(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    capture: bool = True,
    text: bool = True,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        cp = subprocess.run(
            list(args),
            cwd=str(cwd) if cwd else None,
            check=False,
            capture_output=capture,
            text=text,
            input=input_text,
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"命令超时：{args[0]}") from exc
    except OSError as exc:
        raise ToolError(f"无法执行命令 {args[0]}：{exc}") from exc
    if check and cp.returncode != 0:
        stdout = (cp.stdout or "").strip() if capture else ""
        stderr = (cp.stderr or "").strip() if capture else ""
        detail = "\n".join(x for x in (stdout, stderr) if x)
        raise ToolError(f"命令失败：{' '.join(args)}" + (f"\n{detail}" if detail else ""))
    return cp


def ask(prompt: str, *, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value if value else (default or "")


def confirm_exact(prompt: str, expected: str) -> bool:
    print(prompt)
    return input(f"请输入“{expected}”继续: ").strip() == expected


def section(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def print_git_install_help() -> None:
    system = platform.system().lower()
    print("未检测到 Git。请先安装 Git，然后重新运行。")
    if system == "windows":
        print("Windows：winget install --id Git.Git -e --source winget")
        print("Git for Windows 通常同时包含 Git Credential Manager。")
    elif system == "darwin":
        print("macOS：xcode-select --install 或 brew install git")
        print("GCM：brew install --cask git-credential-manager")
    else:
        print("Debian/Ubuntu：sudo apt-get update && sudo apt-get install -y git")
        print("Fedora/RHEL：sudo dnf install -y git")
        print("Arch：sudo pacman -S --needed git")
    print("中国区下载失败时先检查现有镜像和代理；不得关闭 SSL 校验。")


def ensure_git() -> None:
    if not shutil.which("git"):
        print_git_install_help()
        raise ToolError("Git 未安装")
    print(f"Git：{run(['git', '--version']).stdout.strip()}")


def parse_version(text: str) -> tuple[int, ...] | None:
    match = re.search(r"(?<!\d)(\d+)\.(\d+)(?:\.(\d+))?", text)
    if not match:
        return None
    return tuple(int(value or 0) for value in match.groups())


def detect_gcm() -> tuple[list[str], tuple[int, ...], str]:
    candidates = (
        ["git", "credential-manager", "--version"],
        ["git-credential-manager", "--version"],
        ["git", "credential-manager-core", "--version"],
    )
    for command in candidates:
        cp = run(command, check=False)
        if cp.returncode != 0:
            continue
        output = "\n".join(x for x in ((cp.stdout or "").strip(), (cp.stderr or "").strip()) if x)
        version = parse_version(output)
        if version:
            return command[:-1], version, output
    raise ToolError(
        "未检测到 Git Credential Manager。OAuth2 是默认且最高优先级的认证方式。\n"
        "Windows 请更新或重新安装 Git for Windows；macOS 可使用 "
        "brew install --cask git-credential-manager；Linux 请按 GCM 官方安装说明安装。"
    )


def ensure_gcm() -> tuple[list[str], tuple[int, ...]]:
    command, version, output = detect_gcm()
    normalized = version + (0,) * (3 - len(version))
    if normalized[:3] < GCM_MIN_VERSION:
        raise ToolError(
            f"Git Credential Manager 版本过低：{output}。"
            "Forgejo/Gitea 通用 OAuth2 支持要求至少 2.4.1，请先升级。"
        )
    print(f"Git Credential Manager：{output}")
    print("Forgejo OAuth2 客户端：Git Credential Manager（平台预注册应用）")
    return command, normalized[:3]


def gcm_git_prefix(*, interactive: bool) -> list[str]:
    # 空 helper 先清除当前命令继承的 helper 链，再只使用 GCM。
    return [
        "git",
        "-c",
        "credential.helper=",
        "-c",
        "credential.helper=manager",
        "-c",
        f"credential.{BASE_URL}.provider=generic",
        "-c",
        f"credential.{BASE_URL}.useHttpPath=false",
        "-c",
        f"credential.{BASE_URL}.oauthClientId={FORGEJO_GCM_CLIENT_ID}",
        "-c",
        f"credential.{BASE_URL}.oauthRedirectUri=http://127.0.0.1/",
        "-c",
        f"credential.{BASE_URL}.oauthAuthorizeEndpoint=/login/oauth/authorize",
        "-c",
        f"credential.{BASE_URL}.oauthTokenEndpoint=/login/oauth/access_token",
        "-c",
        f"credential.{BASE_URL}.oauthDefaultUserName=OAUTH_USER",
        "-c",
        f"credential.{BASE_URL}.oauthUseClientAuthHeader=true",
        "-c",
        f"credential.interactive={'true' if interactive else 'false'}",
    ]


def gcm_env(*, interactive: bool) -> dict[str, str]:
    env = os.environ.copy()
    env["GCM_PROVIDER"] = "generic"
    env["GCM_INTERACTIVE"] = "true" if interactive else "false"
    env["GCM_GUI_PROMPT"] = "true" if interactive else "false"
    env["GIT_TERMINAL_PROMPT"] = "1" if interactive else "0"
    # 禁止 curl/Git 在命令行或日志中显示认证头。
    env.pop("GIT_TRACE_CURL", None)
    env.pop("GIT_CURL_VERBOSE", None)
    return env


def is_git_repo(root: Path) -> bool:
    cp = run(["git", "rev-parse", "--is-inside-work-tree"], cwd=root, check=False)
    return cp.returncode == 0 and (cp.stdout or "").strip() == "true"


def ensure_git_repo(root: Path) -> None:
    if is_git_repo(root):
        return
    if not confirm_exact(f"{root} 还不是 Git 仓库，将在此目录执行 git init。", "初始化"):
        raise ToolError("用户取消初始化")
    run(["git", "init"], cwd=root, capture=False)


def git_output(root: Path, *args: str, check: bool = True) -> str:
    cp = run(["git", *args], cwd=root, check=check)
    return (cp.stdout or "").strip()


def ensure_identity(root: Path) -> None:
    name = git_output(root, "config", "--get", "user.name", check=False)
    email = git_output(root, "config", "--get", "user.email", check=False)
    if name and email:
        print(f"Git 提交身份：{name} <{email}>")
        return
    print("当前项目缺少 Git 提交身份。只配置当前仓库，不修改全局配置。")
    name = ask("提交显示名称")
    email = ask("提交邮箱")
    if not name or not email:
        raise ToolError("Git 用户名或邮箱为空")
    if not confirm_exact(f"将当前仓库身份设置为 {name} <{email}>。", "确认身份"):
        raise ToolError("用户取消身份配置")
    run(["git", "config", "user.name", name], cwd=root)
    run(["git", "config", "user.email", email], cwd=root)


def load_config(root: Path) -> dict[str, str]:
    path = root / ".git" / CONFIG_NAME
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def save_config(root: Path, remote: str, mode: str, auth: str) -> None:
    path = root / ".git" / CONFIG_NAME
    payload = {
        "remote": remote,
        "mode": mode,
        "auth": auth,
        "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def get_existing_origin(root: Path) -> str:
    if not is_git_repo(root):
        return ""
    return git_output(root, "remote", "get-url", "origin", check=False)


def parse_remote_path(remote: str) -> str:
    value = remote.strip()
    if value.startswith(("https://", "http://", "ssh://")):
        parsed = urllib.parse.urlsplit(value)
        return parsed.path.lstrip("/")
    match = re.match(r"^[^@\s]+@[^:\s]+:(.+)$", value)
    if match:
        return match.group(1).lstrip("/")
    raise ToolError("无法解析仓库地址")


def normalize_https_remote(remote: str) -> str:
    value = remote.strip()
    if not value:
        raise ToolError("仓库地址为空")
    if re.search(r"https?://[^/]*@", value):
        raise ToolError("仓库 URL 不得包含用户名、密码或 Token")
    path = parse_remote_path(value)
    if not path or path.endswith("/"):
        raise ToolError("仓库地址缺少 owner/repository 路径")
    if not path.endswith(".git"):
        path += ".git"

    if value.startswith(("https://", "http://")):
        parsed = urllib.parse.urlsplit(value)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https":
            raise ToolError("OAuth2 推送只允许 HTTPS，不允许明文 HTTP")
        if host not in {WEB_HOST, APEX_HOST}:
            raise ToolError(f"不是受支持的平台主机：{host}")
    elif value.startswith("ssh://") or re.match(r"^[^@\s]+@[^:\s]+:", value):
        pass
    else:
        raise ToolError("请提供平台实际显示的 HTTPS 或 SSH 克隆地址")

    # 平台网页和 HTTPS Git 统一使用可访问的 www 主机。
    return f"{BASE_URL}/{path}"


def is_https_remote(remote: str) -> bool:
    return remote.startswith(f"{BASE_URL}/")


def repo_label(remote: str) -> str:
    path = parse_remote_path(remote).rstrip("/")
    tail = path.rsplit("/", 1)[-1]
    return tail[:-4] if tail.endswith(".git") else tail


def show_repo_context(root: Path, origin: str) -> None:
    print("检测到当前文件夹已经关联仓库：")
    print(f"  仓库根目录：{root}")
    print(f"  origin：{origin}")
    if is_git_repo(root):
        print(f"  当前分支：{git_output(root, 'branch', '--show-current', check=False) or '(未命名)'}")
        print(f"  跟踪分支：{git_output(root, 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}', check=False) or '(未设置)'}")
        last = git_output(root, "log", "-1", "--oneline", check=False)
        print(f"  最近提交：{last or '(暂无提交)'}")


def prompt_new_repository() -> str:
    section("创建新仓库")
    name = ask("新仓库名称")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        raise ToolError("仓库名称只能使用字母、数字、点、下划线和连字符")
    visibility = ask("仓库可见性 private/public", default="private").lower()
    if visibility not in {"private", "public"}:
        raise ToolError("可见性必须是 private 或 public")
    print(f"将创建仓库：{name}，可见性：{visibility}")
    print("脚本不会猜测或调用未确认的平台创建 API。")
    if not confirm_exact("将打开平台的新建仓库页面，请按以上名称和可见性创建。", "打开创建页面"):
        raise ToolError("用户取消创建仓库")
    try:
        webbrowser.open(REPO_CREATE_URL)
    except Exception:
        pass
    remote = ask("创建完成后，粘贴页面显示的 HTTPS 克隆地址")
    return normalize_https_remote(remote)


def choose_remote(root: Path, cli_remote: str | None, *, auth: str, confirm_existing: bool) -> str:
    config = load_config(root) if is_git_repo(root) else {}
    existing = get_existing_origin(root)

    if existing and cli_remote is None:
        show_repo_context(root, existing)
        if confirm_existing and not confirm_exact("是否继续使用该仓库同步并推送？", "继续同步"):
            existing = ""
        elif not confirm_existing:
            pass

    candidate = cli_remote or existing or config.get("remote", "")
    if candidate:
        if auth == "oauth":
            normalized = normalize_https_remote(candidate)
            if normalized != candidate:
                print(f"OAuth2 需要 HTTPS 远端，将使用：{normalized}")
                if confirm_existing and not confirm_exact("将仓库远端转换为 HTTPS。", "转换 HTTPS"):
                    raise ToolError("用户取消 HTTPS 转换")
            return normalized
        return candidate

    choice = ask("选择仓库：existing（已有）/new（新建）", default="existing").lower()
    if choice == "new":
        return prompt_new_repository()
    remote = ask("粘贴平台仓库页面显示的克隆地址")
    return normalize_https_remote(remote) if auth == "oauth" else remote.strip()


def test_https_remote(remote: str, *, interactive: bool) -> tuple[bool, str]:
    remote = normalize_https_remote(remote)
    args = gcm_git_prefix(interactive=interactive) + ["ls-remote", "--heads", remote]
    cp = run(args, check=False, env=gcm_env(interactive=interactive), timeout=300 if interactive else 30)
    output = "\n".join(x for x in ((cp.stdout or "").strip(), (cp.stderr or "").strip()) if x)
    return cp.returncode == 0, output


def print_https_failure(output: str) -> None:
    lower = output.lower()
    if "could not resolve host" in lower or "could not resolve hostname" in lower:
        print("失败原因：DNS 无法解析 www.synnovator.com。")
    elif "failed to connect" in lower or "connection timed out" in lower:
        print("失败原因：HTTPS 连接失败或超时。请检查 443 端口、代理和网络。")
    elif "certificate" in lower or "ssl" in lower:
        print("失败原因：TLS/证书校验失败。不得关闭 http.sslVerify。")
    elif "authentication failed" in lower or "401" in lower:
        print("失败原因：OAuth2 凭据无效、已撤销或尚未授权。")
    elif "403" in lower or "not allowed" in lower:
        print("失败原因：当前账号没有目标仓库权限。")
    elif "repository not found" in lower or "not found" in lower:
        print("失败原因：仓库不存在、地址错误，或账号无权查看。")
    else:
        print("HTTPS 仓库读取失败。")
    if output:
        safe_lines = [
            line for line in output.splitlines()
            if not re.search(r"(?i)(authorization:|password=|access[_-]?token|refresh[_-]?token)", line)
        ]
        if safe_lines:
            print("\n".join(safe_lines[-12:]))


def ensure_oauth(remote: str, *, allow_interactive: bool) -> None:
    ensure_gcm()
    remote = normalize_https_remote(remote)
    print(f"OAuth2 验证仓库：{remote}")

    ok, output = test_https_remote(remote, interactive=False)
    if ok:
        print("HTTPS 仓库读取成功。已复用现有 Git Credential Manager 凭据或无需重新授权。")
        return

    if not allow_interactive:
        print_https_failure(output)
        raise ToolError("OAuth2 访问未通过。请先运行 auth 子命令。")

    print_https_failure(output)
    print("下一步将由 Git Credential Manager 打开浏览器。")
    print("请登录正确的 Synnovator/Forgejo 账号，并授权“Git Credential Manager”。")
    print(f"授权记录可在此查看或撤销：{OAUTH_SETTINGS_URL}")
    print("脚本不会读取、显示或保存 OAuth access token/refresh token。")
    if not confirm_exact("开始 OAuth2 浏览器授权。", "授权 OAuth2"):
        raise ToolError("用户取消 OAuth2 授权")

    ok, output = test_https_remote(remote, interactive=True)
    if not ok:
        print_https_failure(output)
        raise ToolError("OAuth2 授权后仍无法读取仓库。请检查账号、仓库权限或平台 OAuth2 服务。")

    ok, output = test_https_remote(remote, interactive=False)
    if not ok:
        print_https_failure(output)
        raise ToolError("OAuth2 凭据未能被安全复用，已停止。")
    print("OAuth2 授权与仓库读取验证通过。")


def configure_repo_gcm(root: Path) -> None:
    # 仅修改当前仓库，避免影响用户的其他 Git 主机。
    run(["git", "config", "--local", "--unset-all", "credential.helper"], cwd=root, check=False)
    run(["git", "config", "--local", "--add", "credential.helper", ""], cwd=root)
    run(["git", "config", "--local", "--add", "credential.helper", "manager"], cwd=root)
    settings = {
        f"credential.{BASE_URL}.provider": "generic",
        f"credential.{BASE_URL}.useHttpPath": "false",
        f"credential.{BASE_URL}.oauthClientId": FORGEJO_GCM_CLIENT_ID,
        f"credential.{BASE_URL}.oauthRedirectUri": "http://127.0.0.1/",
        f"credential.{BASE_URL}.oauthAuthorizeEndpoint": "/login/oauth/authorize",
        f"credential.{BASE_URL}.oauthTokenEndpoint": "/login/oauth/access_token",
        f"credential.{BASE_URL}.oauthDefaultUserName": "OAUTH_USER",
        f"credential.{BASE_URL}.oauthUseClientAuthHeader": "true",
    }
    for key, value in settings.items():
        run(["git", "config", "--local", key, value], cwd=root)
    print("已将 Forgejo Git Credential Manager OAuth2 配置写入当前仓库；未修改全局 Git 配置。")


def configure_origin(root: Path, remote: str) -> None:
    existing = get_existing_origin(root)
    if not existing:
        run(["git", "remote", "add", "origin", remote], cwd=root)
    elif existing != remote:
        print(f"当前 origin：{existing}")
        print(f"目标 origin：{remote}")
        if not confirm_exact("将替换当前 origin 地址。", "替换远端"):
            raise ToolError("用户取消替换远端")
        run(["git", "remote", "set-url", "origin", remote], cwd=root)
    if get_existing_origin(root) != remote:
        raise ToolError("origin 配置验证失败")


def parse_ssh_endpoint(remote: str) -> tuple[str, int]:
    value = remote.strip()
    if value.startswith("ssh://"):
        parsed = urllib.parse.urlsplit(value)
        if not parsed.hostname:
            raise ToolError("SSH 地址缺少主机")
        return parsed.hostname, parsed.port or 22
    match = re.match(r"^[^@\s]+@([^:\s]+):.+$", value)
    if match:
        return match.group(1), 22
    raise ToolError("SSH 备用流程必须使用平台实际显示的 SSH 克隆地址")


def copy_to_clipboard(text: str) -> bool:
    commands: list[list[str]]
    if os.name == "nt":
        commands = [["clip"]]
    elif platform.system().lower() == "darwin":
        commands = [["pbcopy"]]
    else:
        commands = [["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]
    for command in commands:
        if not shutil.which(command[0]):
            continue
        try:
            subprocess.run(command, input=text, text=True, check=True)
            return True
        except (OSError, subprocess.CalledProcessError):
            continue
    return False


def read_public_key(pub_path: Path) -> str:
    if pub_path.suffix != ".pub" or not pub_path.exists():
        raise ToolError(f"无效公钥文件：{pub_path}")
    value = pub_path.read_text(encoding="utf-8", errors="strict").strip()
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if len(lines) != 1 or not lines[0].startswith(PUBLIC_KEY_PREFIXES):
        raise ToolError("读取到的内容不是完整单行 SSH 公钥")
    if "PRIVATE KEY" in lines[0]:
        raise ToolError("检测到私钥内容，拒绝展示")
    return lines[0]


def public_key_fingerprint(pub_path: Path) -> str | None:
    cp = run(["ssh-keygen", "-lf", str(pub_path), "-E", "sha256"], check=False)
    if cp.returncode != 0:
        return None
    for part in (cp.stdout or "").split():
        if part.startswith("SHA256:"):
            return part
    return None


def test_ssh(remote: str, key_path: Path | None = None) -> tuple[bool, str]:
    host, port = parse_ssh_endpoint(remote)
    args = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "KbdInteractiveAuthentication=no",
        "-o",
        "PreferredAuthentications=publickey",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "ConnectTimeout=15",
        "-p",
        str(port),
    ]
    if key_path:
        args += ["-i", str(key_path)]
    args += ["-T", f"git@{host}"]
    cp = run(args, check=False)
    output = "\n".join(x for x in ((cp.stdout or "").strip(), (cp.stderr or "").strip()) if x)
    lower = output.lower()
    success = cp.returncode == 0 or any(x in lower for x in ("authenticated", "welcome", "success", "shell access is disabled"))
    return success, output


def ssh_bind(remote: str, email: str) -> None:
    section("SSH 备用绑定（仅用户明确选择时使用）")
    if not shutil.which("ssh-keygen") or not shutil.which("ssh"):
        raise ToolError("未检测到 OpenSSH 客户端")
    host, port = parse_ssh_endpoint(remote)
    key_path = Path.home() / ".ssh" / "id_ed25519_synnovator"
    pub_path = Path(str(key_path) + ".pub")
    if not key_path.exists() or not pub_path.exists():
        if not confirm_exact(f"将生成专用 SSH 密钥：{key_path}", "生成密钥"):
            raise ToolError("用户取消生成 SSH 密钥")
        key_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        run(["ssh-keygen", "-t", "ed25519", "-C", email, "-f", str(key_path)], capture=False)
    public_key = read_public_key(pub_path)
    copied = copy_to_clipboard(public_key)
    fingerprint = public_key_fingerprint(pub_path)
    print("\n请复制两条边界线之间的完整一行到平台 SSH 公钥输入框：")
    print("----- SSH PUBLIC KEY BEGIN -----")
    print(public_key)
    print("----- SSH PUBLIC KEY END -------")
    print("已复制到剪贴板。" if copied else "无法访问剪贴板，请手动复制。")
    print(f"公钥文件：{pub_path}")
    if fingerprint:
        print(f"仅供核验、不要粘贴的指纹：{fingerprint}")
    print(f"绑定页面：{SSH_SETTINGS_URL}")
    print(f"将验证实际端点：{host}:{port}")
    input("绑定完成后按 Enter 验证……")
    ok, output = test_ssh(remote, key_path)
    if not ok:
        if output:
            print(output)
        raise ToolError("SSH 认证未通过。OAuth2 HTTPS 仍是推荐方式。")
    print("SSH 备用认证通过。")


def ensure_gitignore(root: Path) -> None:
    path = root / ".gitignore"
    current = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
    if "# >>> synnovator-submit managed rules >>>" in current:
        return
    new_text = current.rstrip() + ("\n\n" if current.strip() else "") + GITIGNORE_BLOCK + "\n"
    path.write_text(new_text, encoding="utf-8")
    print("已追加安全 `.gitignore` 规则；未覆盖原有内容。")


def null_split(value: str) -> list[str]:
    return [part for part in value.split("\0") if part]


def candidate_files(root: Path) -> list[Path]:
    cp = run(["git", "ls-files", "--cached", "--others", "--modified", "--exclude-standard", "-z"], cwd=root)
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
    if ".ssh" in parts or ".aws" in parts:
        return True
    return any(fnmatch.fnmatch(base, pattern) for pattern in BLOCKED_NAME_PATTERNS)


def is_probably_text(path: Path) -> bool:
    try:
        sample = path.read_bytes()[:4096]
    except OSError:
        return False
    return b"\x00" not in sample


def scan_files(root: Path, files: Iterable[Path]) -> dict[str, object]:
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


def print_scan(result: dict[str, object]) -> None:
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


def enforce_scan(result: dict[str, object]) -> None:
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


def remote_main_oid(root: Path) -> str | None:
    cp = run(["git", "ls-remote", "--heads", "origin", "refs/heads/main"], cwd=root, check=False)
    if cp.returncode != 0:
        detail = "\n".join(x for x in ((cp.stdout or "").strip(), (cp.stderr or "").strip()) if x)
        raise ToolError("无法读取远端仓库。" + (f"\n{detail}" if detail else ""))
    output = (cp.stdout or "").strip()
    return output.split()[0] if output else None


def fetch_main(root: Path, exists: bool) -> None:
    if exists:
        run(["git", "fetch", "origin", "main", "--prune"], cwd=root, capture=False)


def has_head(root: Path) -> bool:
    return run(["git", "rev-parse", "--verify", "HEAD"], cwd=root, check=False).returncode == 0


def has_changes(root: Path) -> bool:
    return bool(git_output(root, "status", "--porcelain"))


def commit_changes(root: Path, mode: str) -> None:
    if not has_changes(root) and has_head(root):
        print("工作区没有新变更，不创建空提交。")
        return
    run(["git", "add", "--all"], cwd=root)
    staged = git_output(root, "diff", "--cached", "--name-status")
    if not staged and has_head(root):
        print("没有可提交内容。")
        return
    print("已暂存变更：")
    print(staged or "  首次提交")
    check = run(["git", "diff", "--cached", "--check"], cwd=root, check=False)
    if check.returncode != 0:
        raise ToolError("暂存内容存在空白错误：\n" + ((check.stdout or "") + (check.stderr or "")))
    default = (
        "chore: publish snapshot " + dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        if mode == "snapshot"
        else ("chore: update submission" if has_head(root) else "chore: initial submission")
    )
    message = ask("提交信息", default=default)
    run(["git", "commit", "-m", message], cwd=root, capture=False)


def is_fast_forward(root: Path) -> bool:
    if run(["git", "rev-parse", "--verify", "refs/remotes/origin/main"], cwd=root, check=False).returncode != 0:
        return False
    return run(["git", "merge-base", "--is-ancestor", "refs/remotes/origin/main", "HEAD"], cwd=root, check=False).returncode == 0


def archive_branch_name(root: Path, expected_oid: str) -> str:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    base = f"archive/main-{stamp}"
    cp = run(["git", "ls-remote", "--heads", "origin", f"refs/heads/{base}"], cwd=root, check=False)
    return f"{base}-{expected_oid[:7]}" if (cp.stdout or "").strip() else base


def create_archive(root: Path, expected_oid: str) -> str:
    branch = archive_branch_name(root, expected_oid)
    run(["git", "push", "origin", f"{expected_oid}:refs/heads/{branch}"], cwd=root, capture=False)
    cp = run(["git", "ls-remote", "--heads", "origin", f"refs/heads/{branch}"], cwd=root)
    if not (cp.stdout or "").strip():
        raise ToolError("历史备份分支创建后验证失败")
    print(f"远端原 main 已保存为：{branch}")
    return branch


def push_main(root: Path, mode: str, expected_oid: str | None) -> str | None:
    archive: str | None = None
    if remote_main_oid(root) != expected_oid:
        raise ToolError("远端 main 在确认后发生变化。已停止推送，请重新运行并确认。")
    if expected_oid is None:
        run(["git", "push", "-u", "origin", "HEAD:main"], cwd=root, capture=False)
        return None
    fetch_main(root, True)
    if mode == "incremental":
        if not is_fast_forward(root):
            raise ToolError("远端 main 不能快进到当前提交。请先整合远端历史，或选择 snapshot。")
        run(["git", "push", "origin", "HEAD:main"], cwd=root, capture=False)
        return None
    archive = create_archive(root, expected_oid)
    if is_fast_forward(root):
        run(["git", "push", "origin", "HEAD:main"], cwd=root, capture=False)
    else:
        run(
            ["git", "push", f"--force-with-lease=refs/heads/main:{expected_oid}", "origin", "HEAD:main"],
            cwd=root,
            capture=False,
        )
    return archive


def verify_push(root: Path) -> str:
    local = git_output(root, "rev-parse", "HEAD")
    remote = remote_main_oid(root)
    if not remote or local != remote:
        raise ToolError(f"推送后验证失败：本地 {local}，远端 {remote or '不存在'}")
    return local


def push_flow(root: Path, remote: str, *, mode: str, auth: str, auth_checked: bool = False) -> None:
    ensure_git_repo(root)
    ensure_identity(root)

    if auth == "oauth":
        remote = normalize_https_remote(remote)
        if not auth_checked:
            ensure_oauth(remote, allow_interactive=False)
        configure_origin(root, remote)
        configure_repo_gcm(root)
    else:
        ok, output = test_ssh(remote)
        if not ok:
            if output:
                print(output)
            raise ToolError("SSH 认证未通过。请单独执行 ssh-bind，或改用默认 OAuth2。")
        configure_origin(root, remote)

    ensure_gitignore(root)
    files = candidate_files(root)
    scan = scan_files(root, files)
    print_scan(scan)
    enforce_scan(scan)

    expected_oid = remote_main_oid(root)
    fetch_main(root, expected_oid is not None)
    backup_preview = "无（远端 main 不存在）" if expected_oid is None else (
        "普通更新不创建" if mode == "incremental" else "archive/main-<时间戳>"
    )
    section("推送计划与风险确认")
    print(f"目标仓库：{repo_label(remote)}")
    print(f"远端地址：{remote}")
    print(f"认证方式：{'Git Credential Manager OAuth2 / HTTPS' if auth == 'oauth' else 'SSH（备用）'}")
    print("目标分支：main")
    print(f"发布模式：{'普通更新' if mode == 'incremental' else '快照替换'}")
    print(f"远端 main：{expected_oid or '不存在'}")
    print(f"历史备份：{backup_preview}")
    print(f"文件数量：{len(files)}")
    print(f"预计内容：{human_size(int(scan['total_size']))}")
    print("已阻止：.env、私钥、常见凭证文件、疑似 Token 和超大文件。")
    if scan["warnings"] or scan["large"]:
        print("风险提醒：上方列出的依赖目录、构建目录或大文件可能被上传。")

    if mode == "snapshot" and expected_oid:
        ok = confirm_exact(
            "远端 main 将以当前文件夹为准；原 main 会先保存到历史分支。",
            repo_label(remote),
        )
    else:
        ok = confirm_exact("确认将上述文件提交并推送到正式 main。", "确认推送")
    if not ok:
        raise ToolError("用户取消推送")

    commit_changes(root, mode)
    if not has_head(root):
        raise ToolError("当前仓库没有可推送的提交")
    archive = push_main(root, mode, expected_oid)
    oid = verify_push(root)
    save_config(root, remote, mode, auth)
    subject = git_output(root, "log", "-1", "--pretty=%s")
    section("上传完成")
    print(f"仓库：{repo_label(remote)}")
    print("分支：main")
    print(f"提交：{oid[:7]} {subject}")
    if archive:
        print(f"历史备份：{archive}")


def cmd_check(args: argparse.Namespace, root: Path) -> int:
    section("阶段 A：只读访问检查")
    ensure_git()
    remote = choose_remote(root, args.remote, auth=args.auth, confirm_existing=False)
    if args.auth == "oauth":
        ensure_gcm()
        ok, output = test_https_remote(remote, interactive=False)
        if not ok:
            print_https_failure(output)
            raise ToolError("只读检查未通过；没有执行授权或项目写操作。")
        print("HTTPS 仓库读取成功。")
    else:
        ok, output = test_ssh(remote)
        if not ok:
            if output:
                print(output)
            raise ToolError("SSH 只读认证检查未通过")
        print("SSH 认证检查通过。")
    return 0


def cmd_auth(args: argparse.Namespace, root: Path) -> int:
    section("阶段 B：独立认证")
    ensure_git()
    remote = choose_remote(root, args.remote, auth=args.auth, confirm_existing=False)
    if args.auth == "oauth":
        ensure_oauth(remote, allow_interactive=True)
    else:
        email = "synnovator-user"
        if is_git_repo(root):
            email = git_output(root, "config", "--get", "user.email", check=False) or email
        ssh_bind(remote, email)
    return 0


def cmd_push(args: argparse.Namespace, root: Path) -> int:
    section("阶段 C：独立提交推送")
    ensure_git()
    remote = choose_remote(root, args.remote, auth=args.auth, confirm_existing=True)
    push_flow(root, remote, mode=args.mode, auth=args.auth, auth_checked=False)
    return 0


def cmd_run(args: argparse.Namespace, root: Path) -> int:
    section("一键编排：检查 → 必要时授权 → 推送")
    ensure_git()
    remote = choose_remote(root, args.remote, auth=args.auth, confirm_existing=True)
    if args.auth == "oauth":
        section("阶段 A/B：OAuth2 访问检查与必要授权")
        ensure_oauth(remote, allow_interactive=True)
    else:
        section("阶段 A/B：SSH 备用检查")
        ok, _ = test_ssh(remote)
        if not ok:
            email = git_output(root, "config", "--get", "user.email", check=False) if is_git_repo(root) else "synnovator-user"
            ssh_bind(remote, email or "synnovator-user")
    section("阶段 C：安全提交推送")
    push_flow(root, remote, mode=args.mode, auth=args.auth, auth_checked=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="优先使用 Git Credential Manager OAuth2，通过 HTTPS 安全发布项目到 Synnovator main"
    )
    subparsers = parser.add_subparsers(dest="command")

    def add_common(sub: argparse.ArgumentParser, *, with_mode: bool = False) -> None:
        sub.add_argument("--project", default=".", help="项目目录，默认当前目录")
        sub.add_argument("--remote", help="平台页面显示的克隆地址；OAuth2 默认转换为 www 主机的 HTTPS 地址")
        sub.add_argument("--auth", choices=("oauth", "ssh"), default="oauth", help="认证方式；默认 oauth，ssh 仅备用")
        if with_mode:
            sub.add_argument("--mode", choices=("incremental", "snapshot"), default="incremental")

    add_common(subparsers.add_parser("check", help="只读检查 OAuth2/SSH 与仓库读取权限"))
    add_common(subparsers.add_parser("auth", help="独立完成 OAuth2 授权或 SSH 备用绑定"))
    add_common(subparsers.add_parser("push", help="认证通过后独立扫描、提交并推送"), with_mode=True)
    add_common(subparsers.add_parser("run", help="一键执行检查、必要授权和推送"), with_mode=True)

    bind = subparsers.add_parser("bind", help="兼容旧命令：等同 auth --auth ssh")
    add_common(bind)
    ssh_bind_parser = subparsers.add_parser("ssh-bind", help="显式进入 SSH 备用绑定")
    add_common(ssh_bind_parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    values = list(argv if argv is not None else sys.argv[1:])
    commands = {"check", "auth", "push", "run", "bind", "ssh-bind"}
    if values and values[0] in {"-h", "--help"}:
        pass
    elif not values or values[0] not in commands:
        values.insert(0, "run")
    parser = build_parser()
    args = parser.parse_args(values)
    if args.command in {"bind", "ssh-bind"}:
        args.auth = "ssh"
        args.command = "auth"

    root = Path(args.project).expanduser().resolve()
    if not root.is_dir():
        raise ToolError(f"项目目录不存在：{root}")
    print(f"项目目录：{root}")

    if args.command == "check":
        return cmd_check(args, root)
    if args.command == "auth":
        return cmd_auth(args, root)
    if args.command == "push":
        return cmd_push(args, root)
    return cmd_run(args, root)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        raise SystemExit(130)
    except ToolError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(1)
