#!/usr/bin/env python3
"""Synnovator 本地代码上传工具。

同一个工具提供彼此隔离的访问检查、SSH 绑定和提交推送阶段：

- ``check``：只读检查账号 SSH 访问与指定仓库读取权限；
- ``bind``：只处理 SSH 密钥生成、读取、公钥绑定和认证验证；
- ``push``：只处理仓库选择、安全扫描、提交和推送，认证失败时直接停止；
- ``run``：按“检查 -> 必要时绑定 -> 推送”编排上述独立阶段。

该脚本不依赖平台私有 API。没有插件/API 时，仓库列表与创建需要用户在网页完成，
然后把平台实际显示的 SSH 克隆地址提供给脚本。
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
import webbrowser
from typing import Iterable, Sequence

DEFAULT_HOST = "synnovator.com"
SETTINGS_URL = "https://www.synnovator.com/user/settings/keys"
CONFIG_NAME = "synnovator-submit.json"
MAX_CONTENT_SCAN = 2 * 1024 * 1024
WARN_SIZE = 20 * 1024 * 1024
HIGH_SIZE = 50 * 1024 * 1024
BLOCK_SIZE = 100 * 1024 * 1024

PUBLIC_KEY_PREFIXES = (
    "ssh-ed25519 ",
    "ssh-rsa ",
    "ecdsa-sha2-",
    "sk-ecdsa-sha2-",
    "sk-ssh-ed25519@openssh.com ",
)

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


class ToolError(RuntimeError):
    pass


def run(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    capture: bool = True,
    text: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        cp = subprocess.run(
            list(args),
            cwd=str(cwd) if cwd else None,
            check=False,
            capture_output=capture,
            text=text,
        )
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


def print_git_install_help() -> None:
    system = platform.system().lower()
    print("未检测到 Git。请先安装 Git，然后重新运行脚本。")
    if system == "windows":
        print("Windows：winget install --id Git.Git -e --source winget")
        print("备选：choco install git -y")
    elif system == "darwin":
        print("macOS：xcode-select --install")
        print("或：brew install git")
    else:
        print("Debian/Ubuntu：sudo apt-get update && sudo apt-get install -y git openssh-client")
        print("Fedora/RHEL：sudo dnf install -y git openssh-clients")
        print("Arch：sudo pacman -S --needed git openssh")
    print("中国区环境优先使用现有系统镜像或企业代理，不要关闭 SSL 校验。")


def ensure_git() -> None:
    if not shutil.which("git"):
        print_git_install_help()
        raise ToolError("Git 未安装")
    version = run(["git", "--version"]).stdout.strip()
    print(f"Git：{version}")


def is_git_repo(root: Path) -> bool:
    cp = run(["git", "rev-parse", "--is-inside-work-tree"], cwd=root, check=False)
    return cp.returncode == 0 and cp.stdout.strip() == "true"


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
        print(f"Git 身份：{name} <{email}>")
        return
    print("当前项目缺少 Git 提交身份。配置只写入当前仓库。")
    name = ask("提交显示名称")
    email = ask("提交邮箱")
    if not name or not email:
        raise ToolError("Git 用户名或邮箱为空")
    if not confirm_exact(f"将当前仓库身份设置为 {name} <{email}>。", "确认身份"):
        raise ToolError("用户取消身份配置")
    run(["git", "config", "user.name", name], cwd=root)
    run(["git", "config", "user.email", email], cwd=root)


def parse_ssh_host(remote: str) -> str:
    remote = remote.strip()
    if remote.startswith("ssh://"):
        without_scheme = remote[len("ssh://") :]
        host_part = without_scheme.split("/", 1)[0]
        if "@" in host_part:
            host_part = host_part.split("@", 1)[1]
        return host_part.split(":", 1)[0]
    if "@" in remote and ":" in remote:
        return remote.split("@", 1)[1].split(":", 1)[0]
    return DEFAULT_HOST


def test_ssh(host: str) -> tuple[bool, str]:
    if not shutil.which("ssh"):
        return False, "未检测到 ssh 命令"
    cp = run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-T", f"git@{host}"],
        check=False,
    )
    output = "\n".join(x for x in ((cp.stdout or "").strip(), (cp.stderr or "").strip()) if x)
    lower = output.lower()
    hard_failures = (
        "permission denied",
        "could not resolve hostname",
        "connection timed out",
        "operation timed out",
        "connection refused",
        "no route to host",
        "host key verification failed",
    )
    if any(item in lower for item in hard_failures):
        return False, output
    success_hints = ("authenticated", "welcome", "success", "shell access is disabled", "you've successfully")
    if cp.returncode == 0 or any(item in lower for item in success_hints):
        return True, output
    return False, output


def append_ssh_config(host: str, key_path: Path) -> None:
    ssh_dir = Path.home() / ".ssh"
    ssh_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    config = ssh_dir / "config"
    current = config.read_text(encoding="utf-8", errors="ignore") if config.exists() else ""
    exact_host = re.compile(rf"(?mi)^Host\s+{re.escape(host)}\s*$")
    if exact_host.search(current):
        print(f"SSH config 已有 Host {host}，不自动覆盖。请确认其 IdentityFile 指向正确密钥。")
        return
    block = (
        f"\nHost {host}\n"
        f"  HostName {host}\n"
        "  User git\n"
        f"  IdentityFile {key_path.as_posix()}\n"
        "  IdentitiesOnly yes\n"
        "  ServerAliveInterval 15\n"
    )
    config.write_text(current.rstrip() + "\n" + block, encoding="utf-8")
    if os.name != "nt":
        os.chmod(ssh_dir, 0o700)
        os.chmod(config, 0o600)
    print(f"已追加 SSH 配置：{config}")


def copy_to_clipboard(text: str) -> bool:
    """尝试复制文本；失败不阻止终端继续展示完整公钥。"""
    commands: list[list[str]] = []
    if os.name == "nt":
        commands = [["clip"]]
        if shutil.which("powershell.exe"):
            commands.append(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "$input | Set-Clipboard",
                ]
            )
    elif platform.system().lower() == "darwin":
        commands = [["pbcopy"]]
    else:
        commands = [["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]
    for command in commands:
        if not shutil.which(command[0]):
            continue
        try:
            subprocess.run(
                command,
                input=text,
                text=True,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except (OSError, subprocess.CalledProcessError):
            continue
    return False


def read_public_key(public_key_path: Path) -> str:
    """读取并验证 OpenSSH 公钥，拒绝读取私钥或异常多行内容。"""
    public_key_path = public_key_path.expanduser().resolve()
    if public_key_path.suffix.lower() != ".pub":
        raise ToolError(f"拒绝读取非 .pub 文件：{public_key_path}。该文件可能是私钥。")
    if not public_key_path.is_file():
        raise ToolError(f"未找到 SSH 公钥文件：{public_key_path}")

    try:
        raw = public_key_path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError) as exc:
        raise ToolError(f"无法读取 SSH 公钥文件：{public_key_path}：{exc}") from exc

    if "PRIVATE KEY" in raw:
        raise ToolError("检测到私钥内容，已停止展示。不得上传或输出私钥。")

    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ToolError("SSH 公钥格式异常：公钥必须是完整的一行。")

    public_key = lines[0]
    if not public_key.startswith(PUBLIC_KEY_PREFIXES):
        raise ToolError(
            "读取到的内容不是受支持的 SSH 公钥；应以 ssh-ed25519、ssh-rsa、"
            "ecdsa-sha2- 等密钥类型开头。"
        )

    fields = public_key.split()
    if len(fields) < 2 or not fields[1]:
        raise ToolError("SSH 公钥格式异常：缺少密钥主体。")
    return public_key


def get_public_key_fingerprint(public_key_path: Path) -> str | None:
    """返回 SHA256 指纹。指纹只用于核验，不能代替完整公钥。"""
    if not shutil.which("ssh-keygen"):
        return None
    cp = run(
        ["ssh-keygen", "-lf", str(public_key_path), "-E", "sha256"],
        check=False,
    )
    output = "\n".join(x for x in ((cp.stdout or "").strip(), (cp.stderr or "").strip()) if x)
    match = re.search(r"SHA256:[^\s]+", output)
    return match.group(0) if match else None


def show_public_key(public_key_path: Path) -> str:
    """在终端完整展示可复制公钥，并把指纹降级为辅助信息。"""
    public_key_path = public_key_path.expanduser().resolve()
    public_key = read_public_key(public_key_path)
    copied = copy_to_clipboard(public_key)
    fingerprint = get_public_key_fingerprint(public_key_path)

    print()
    print("=" * 72)
    print("SSH 公钥（请复制完整一行）")
    print("=" * 72)
    if copied:
        print("完整公钥已复制到系统剪贴板；仍请核对下面显示的内容。")
    else:
        print("未能访问系统剪贴板，请手动复制下面显示的完整一行。")
    print()
    print("只复制两条边界线之间的公钥内容，不要复制边界线：")
    print("----- SSH PUBLIC KEY BEGIN -----")
    print(public_key)
    print("----- SSH PUBLIC KEY END -------")
    print()
    print(f"公钥文件：{public_key_path}")
    if fingerprint:
        print()
        print("仅供核验的公钥指纹（不要粘贴到‘密钥内容’输入框）：")
        print(fingerprint)
    print()
    print("网页中粘贴的内容必须以 ssh-ed25519、ssh-rsa 或其他有效密钥类型开头。")
    print("不要粘贴以 SHA256: 开头的指纹。")
    print("不要读取、复制或上传不带 .pub 后缀的私钥文件。")
    print("=" * 72)
    print()
    return public_key


def classify_ssh_failure(output: str) -> str:
    lower = output.lower()
    if "permission denied" in lower or "publickey" in lower:
        return "auth"
    if "host key verification failed" in lower:
        return "hostkey"
    network_failures = (
        "could not resolve hostname",
        "connection timed out",
        "operation timed out",
        "connection refused",
        "no route to host",
        "network is unreachable",
    )
    if any(item in lower for item in network_failures):
        return "network"
    return "unknown"


def check_repository_read(remote: str, *, root: Path | None = None) -> tuple[bool, str]:
    """只读检查指定仓库；空仓库也会返回成功。"""
    cp = run(["git", "ls-remote", "--heads", remote], cwd=root, check=False)
    output = "\n".join(x for x in ((cp.stdout or "").strip(), (cp.stderr or "").strip()) if x)
    return cp.returncode == 0, output


def access_check(
    *,
    host: str,
    remote: str | None = None,
    root: Path | None = None,
    require_repository: bool = False,
) -> bool:
    """执行只读访问门禁，不生成密钥、不修改仓库、不提交、不推送。"""
    print("\n[访问检查阶段：只读]")
    ok, output = test_ssh(host)
    if not ok:
        if output:
            print("账号 SSH 访问检查结果：")
            print(output)
        reason = classify_ssh_failure(output)
        if reason == "auth":
            print("账号 SSH 认证未通过，需要单独执行绑定阶段。")
        elif reason == "hostkey":
            print("主机指纹尚未确认。需要人工核对指纹，不能关闭主机密钥校验。")
        elif reason == "network":
            print("平台 SSH 主机不可达，请先检查网络、DNS、代理或 SSH 端口。")
        else:
            print("账号 SSH 访问未通过，不能进入推送阶段。")
        return False

    print(f"账号 SSH 访问已通过：git@{host}")
    if remote:
        repo_ok, repo_output = check_repository_read(remote, root=root)
        if not repo_ok:
            print(f"仓库读取失败：{remote}")
            if repo_output:
                print(repo_output)
            return False
        print(f"仓库读取已通过：{remote}")
        return True

    if require_repository:
        print("当前没有可用于验证的仓库 SSH 地址。")
        print("命令行模式不能在没有平台 API 的情况下枚举账号仓库。请提供 --remote，")
        print("或由代码编辑 AI 工具通过已登录浏览器/平台插件读取仓库列表。")
        return False

    print("未提供仓库地址，本次仅确认账号 SSH 访问；尚未验证具体仓库读取权限。")
    return True


def bind_ssh(host: str, email: str) -> None:
    """只执行 SSH 绑定流程，不接触项目仓库、提交或推送。"""
    print("\n[SSH 绑定阶段：不提交、不推送]")
    ok, output = test_ssh(host)
    if ok:
        print(f"SSH 认证已通过：git@{host}，无需重复绑定。")
        return

    reason = classify_ssh_failure(output)
    if reason == "network":
        raise ToolError("SSH 主机当前不可达，请先检查网络、DNS、代理或平台 SSH 端口。\n" + output)
    if reason == "hostkey":
        print("首次连接需要人工核对主机指纹。不要关闭主机密钥校验。")
        if not confirm_exact(f"将以交互方式连接 git@{host}，请核对终端显示的指纹。", "核对指纹"):
            raise ToolError("用户取消主机指纹核对")
        run(["ssh", "-o", "StrictHostKeyChecking=ask", "-T", f"git@{host}"], check=False, capture=False)
        ok, output = test_ssh(host)
        if ok:
            print(f"SSH 认证已通过：git@{host}")
            return

    if output:
        print("绑定前认证检查结果：")
        print(output)

    key_path = Path.home() / ".ssh" / "id_ed25519_synnovator"
    pub_path = key_path.with_suffix(key_path.suffix + ".pub")
    if not key_path.exists() or not pub_path.exists():
        if not shutil.which("ssh-keygen"):
            raise ToolError("未检测到 ssh-keygen，请安装 OpenSSH 客户端")
        if not confirm_exact(f"将生成专用 SSH 密钥：{key_path}", "生成密钥"):
            raise ToolError("用户取消生成 SSH 密钥")
        key_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        run(["ssh-keygen", "-t", "ed25519", "-C", email, "-f", str(key_path)], capture=False)
    else:
        print(f"发现现有专用密钥：{key_path}")

    append_ssh_config(host, key_path)
    show_public_key(pub_path)
    print("绑定步骤：")
    print(f"  1. 打开：{SETTINGS_URL}")
    print("  2. ‘密钥名称’填写可识别名称，例如：比赛电脑-2026")
    print("  3. ‘密钥内容’粘贴上方以 ssh-ed25519 等类型开头的完整一行公钥")
    print("  4. 点击‘增加密钥’")
    print("  5. 确认网页中不是以 SHA256: 开头，也没有复制 BEGIN/END 边界线")
    try:
        webbrowser.open(SETTINGS_URL)
    except Exception:
        pass
    input("绑定完成后按 Enter 继续验证……")
    ok, output = test_ssh(host)
    if not ok:
        if output:
            print(output)
        raise ToolError("SSH 认证仍未通过。请检查公钥是否完整、SSH config 和平台账号权限。")
    print("SSH 绑定验证已通过。绑定阶段结束，尚未执行任何提交或推送。")

def load_config(root: Path) -> dict[str, str]:
    path = root / ".git" / CONFIG_NAME
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def save_config(root: Path, remote: str, mode: str) -> None:
    path = root / ".git" / CONFIG_NAME
    payload = {"remote": remote, "mode": mode, "updated_at": dt.datetime.now().isoformat(timespec="seconds")}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def get_existing_origin(root: Path) -> str:
    if not is_git_repo(root):
        return ""
    return git_output(root, "remote", "get-url", "origin", check=False)


def validate_ssh_remote(remote: str) -> str:
    value = remote.strip()
    if not value:
        raise ToolError("仓库地址为空")
    if not (value.startswith("git@") or value.startswith("ssh://")):
        raise ToolError("请提供平台页面实际显示的 SSH 克隆地址，而不是网页地址或 HTTPS 地址")
    return value


def current_checkout_info(root: Path) -> dict[str, str]:
    if not is_git_repo(root):
        return {}
    top = git_output(root, "rev-parse", "--show-toplevel", check=False)
    branch = git_output(root, "branch", "--show-current", check=False) or "(detached HEAD)"
    upstream = git_output(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}", check=False)
    origin = get_existing_origin(root)
    head = git_output(root, "log", "-1", "--pretty=%h %s", check=False)
    return {
        "top": top,
        "branch": branch,
        "upstream": upstream or "未设置",
        "origin": origin or "未设置",
        "head": head or "尚无提交",
    }


def confirm_existing_checkout(root: Path, origin: str) -> bool:
    info = current_checkout_info(root)
    if not info or not origin:
        return False
    print("\n检测到当前文件夹已经是带远端的 Git 仓库（通常来自 clone 或之前已绑定远端）：")
    print(f"  仓库根目录：{info['top']}")
    print(f"  origin：{origin}")
    print(f"  当前分支：{info['branch']}")
    print(f"  跟踪分支：{info['upstream']}")
    print(f"  最近提交：{info['head']}")
    print("继续后会把当前文件夹的变更同步提交到上述仓库的正式 main。")
    return confirm_exact("是否继续使用这个已 clone/已配置的仓库同步推送？", "继续同步")


def choose_remote(root: Path, cli_remote: str | None) -> str:
    """选择目标仓库；已配置 origin 时必须单独确认是否继续同步。"""
    config = load_config(root)
    existing = get_existing_origin(root)

    if existing:
        if confirm_existing_checkout(root, existing):
            if cli_remote and validate_ssh_remote(cli_remote) != existing:
                print("已确认继续同步当前仓库，因此忽略与 origin 不同的 --remote 参数。")
            return validate_ssh_remote(existing)
        print("用户未选择继续同步当前 origin，将改为选择其他仓库。")

    if cli_remote:
        candidate = validate_ssh_remote(cli_remote)
        print(f"命令行指定目标仓库：{candidate}")
        if confirm_exact("将使用该仓库作为新的推送目标。", "使用此仓库"):
            return candidate
        raise ToolError("用户取消使用 --remote 指定的仓库")

    recent = config.get("remote", "")
    if recent and recent != existing:
        print(f"检测到上次使用的仓库：{recent}")
        if confirm_exact("继续使用上次记录的仓库。", "使用此仓库"):
            return validate_ssh_remote(recent)

    print("没有平台 API 时，命令行脚本不能安全地枚举账号仓库或猜测创建接口。")
    print("请在平台选择已有仓库；不满意时在网页新建仓库，并复制页面实际显示的 SSH 克隆地址。")
    print("新建仓库时需要先确认仓库名和 private/public；默认建议 private。")
    remote = ask("目标仓库 SSH 地址")
    return validate_ssh_remote(remote)

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
    actual = get_existing_origin(root)
    if actual != remote:
        raise ToolError("origin 配置验证失败")


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
    cp = run(
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


def print_scan(root: Path, result: dict[str, object]) -> None:
    files = result["files"]
    print(f"待考虑提交文件：{len(files)} 个，合计约 {human_size(int(result['total_size']))}")
    large = result["large"]
    if large:
        print("较大文件：")
        for rel, size in large[:10]:
            label = "阻止" if size >= BLOCK_SIZE else ("高风险" if size >= HIGH_SIZE else "提示")
            print(f"  - [{label}] {rel}: {human_size(size)}")
    warnings = result["warnings"]
    if warnings:
        print("警告：")
        for item in warnings[:20]:
            print(f"  - {item}")
    blocked_names = result["blocked_names"]
    secrets = result["secrets"]
    if blocked_names:
        print("禁止上传的敏感文件名：")
        for item in blocked_names:
            print(f"  - {item}")
    if secrets:
        print("疑似凭证内容（匹配值已隐藏）：")
        for rel, line_no, kind in secrets:
            print(f"  - {rel}:{line_no} [{kind}]")


def enforce_scan(result: dict[str, object]) -> None:
    blocked_names = result["blocked_names"]
    secrets = result["secrets"]
    oversized = [(rel, size) for rel, size in result["large"] if size >= BLOCK_SIZE]
    if blocked_names or secrets or oversized:
        problems = []
        if blocked_names:
            problems.append("存在敏感文件名")
        if secrets:
            problems.append("存在疑似凭证内容")
        if oversized:
            problems.append("存在不小于 100 MiB 的文件")
        raise ToolError("安全扫描未通过：" + "、".join(problems) + "。请先移除、忽略或使用 Git LFS。")


def remote_main_oid(root: Path) -> str | None:
    cp = run(["git", "ls-remote", "--heads", "origin", "refs/heads/main"], cwd=root, check=False)
    if cp.returncode != 0:
        detail = "\n".join(x for x in ((cp.stdout or "").strip(), (cp.stderr or "").strip()) if x)
        raise ToolError("无法读取远端仓库。" + (f"\n{detail}" if detail else ""))
    output = (cp.stdout or "").strip()
    if not output:
        return None
    return output.split()[0]


def fetch_main(root: Path, exists: bool) -> None:
    if not exists:
        return
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
    if mode == "snapshot":
        default = "chore: publish snapshot " + dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    else:
        default = "chore: update submission" if has_head(root) else "chore: initial submission"
    message = ask("提交信息", default=default)
    run(["git", "commit", "-m", message], cwd=root, capture=False)


def is_fast_forward(root: Path) -> bool:
    if run(["git", "rev-parse", "--verify", "refs/remotes/origin/main"], cwd=root, check=False).returncode != 0:
        return False
    return run(["git", "merge-base", "--is-ancestor", "refs/remotes/origin/main", "HEAD"], cwd=root, check=False).returncode == 0


def archive_branch_name(root: Path, expected_oid: str) -> str:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    short = expected_oid[:7]
    base = f"archive/main-{stamp}"
    cp = run(["git", "ls-remote", "--heads", "origin", f"refs/heads/{base}"], cwd=root, check=False)
    return f"{base}-{short}" if (cp.stdout or "").strip() else base


def create_archive(root: Path, expected_oid: str) -> str:
    branch = archive_branch_name(root, expected_oid)
    run(["git", "push", "origin", f"{expected_oid}:refs/heads/{branch}"], cwd=root, capture=False)
    cp = run(["git", "ls-remote", "--heads", "origin", f"refs/heads/{branch}"], cwd=root)
    if not (cp.stdout or "").strip():
        raise ToolError("历史备份分支创建后验证失败")
    print(f"远端原 main 已保存为：{branch}")
    return branch


def push(root: Path, mode: str, expected_oid: str | None) -> str | None:
    archive: str | None = None
    current_oid = remote_main_oid(root)
    if current_oid != expected_oid:
        raise ToolError("远端 main 在确认后发生变化。已停止推送，请重新运行并重新确认。")

    if expected_oid is None:
        run(["git", "push", "-u", "origin", "HEAD:main"], cwd=root, capture=False)
        return None

    fetch_main(root, True)
    if mode == "incremental":
        if not is_fast_forward(root):
            raise ToolError("远端 main 不能快进到当前提交。请先整合远端历史，或重新选择 --mode snapshot。")
        run(["git", "push", "origin", "HEAD:main"], cwd=root, capture=False)
        return None

    archive = create_archive(root, expected_oid)
    if is_fast_forward(root):
        run(["git", "push", "origin", "HEAD:main"], cwd=root, capture=False)
    else:
        run(
            [
                "git",
                "push",
                f"--force-with-lease=refs/heads/main:{expected_oid}",
                "origin",
                "HEAD:main",
            ],
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


def repo_label(remote: str) -> str:
    value = remote.rstrip("/")
    tail = value.rsplit("/", 1)[-1]
    if ":" in tail and "@" in value:
        tail = value.rsplit(":", 1)[-1]
    return tail[:-4] if tail.endswith(".git") else tail


def resolve_email(root: Path | None, cli_email: str | None) -> str:
    if cli_email:
        return cli_email
    if root and is_git_repo(root):
        local = git_output(root, "config", "--get", "user.email", check=False)
        if local:
            return local
    global_email = run(["git", "config", "--global", "--get", "user.email"], check=False).stdout.strip()
    if global_email:
        return global_email
    email = ask("用于 SSH 公钥备注的邮箱")
    if not email:
        raise ToolError("邮箱为空")
    return email


def find_probe_remote(root: Path | None, cli_remote: str | None) -> str | None:
    if cli_remote:
        return validate_ssh_remote(cli_remote)
    if root and is_git_repo(root):
        origin = get_existing_origin(root)
        if origin:
            return origin
    return None


def push_workflow(root: Path, cli_remote: str | None, mode: str) -> None:
    """只执行提交推送阶段；认证失败时停止，不在此阶段生成或绑定密钥。"""
    print("\n[提交推送阶段：不会自动生成或绑定 SSH 密钥]")
    remote = choose_remote(root, cli_remote)
    host = parse_ssh_host(remote)
    if not access_check(host=host, remote=remote, root=root, require_repository=True):
        raise ToolError(
            "提交推送阶段的访问门禁未通过，未修改 origin、未提交、未推送。\n"
            "请单独运行：python scripts/synnovator_submit.py bind\n"
            "绑定完成后再运行 push。"
        )

    ensure_git_repo(root)
    ensure_identity(root)
    configure_origin(root, remote)
    ensure_gitignore(root)

    files = candidate_files(root)
    scan = scan_files(root, files)
    print_scan(root, scan)
    enforce_scan(scan)

    expected_oid = remote_main_oid(root)
    fetch_main(root, expected_oid is not None)
    backup_preview = (
        "无（远端 main 不存在）"
        if expected_oid is None
        else ("普通更新不创建" if mode == "incremental" else "archive/main-<时间戳>")
    )
    print("\n推送计划")
    print(f"  目标仓库：{repo_label(remote)}")
    print(f"  远端地址：{remote}")
    print("  目标分支：main")
    print(f"  发布模式：{'普通更新' if mode == 'incremental' else '快照替换'}")
    print(f"  远端 main：{expected_oid or '不存在'}")
    print(f"  历史备份：{backup_preview}")
    print(f"  文件数量：{len(files)}")
    print(f"  预计内容：{human_size(int(scan['total_size']))}")
    if scan["warnings"]:
        print("  风险：检测到依赖目录、构建产物或无法读取的文件，请查看上方警告。")
    if scan["large"]:
        print("  风险：包含较大文件，请确认仓库体积和平台限制。")
    print("  已阻止：.env、私钥、常见凭证文件和疑似 Token。")
    print("  注意：提交会包含 git add --all 可见的新增、修改和删除。")

    if mode == "snapshot" and expected_oid:
        expected_confirmation = repo_label(remote)
        ok = confirm_exact(
            "远端 main 将以当前项目快照为准；原 main 会先保存到历史分支。",
            expected_confirmation,
        )
    else:
        ok = confirm_exact("确认提交并推送以上内容到正式 main。", "确认推送")
    if not ok:
        raise ToolError("用户取消推送")

    commit_changes(root, mode)
    if not has_head(root):
        raise ToolError("当前仓库没有可推送的提交")
    archive = push(root, mode, expected_oid)
    oid = verify_push(root)
    save_config(root, remote, mode)

    subject = git_output(root, "log", "-1", "--pretty=%s")
    print("\n上传完成。")
    print(f"仓库：{repo_label(remote)}")
    print("分支：main")
    print(f"提交：{oid[:7]} {subject}")
    if archive:
        print(f"历史备份：{archive}")
    print("下一次普通更新：python scripts/synnovator_submit.py push --mode incremental")
    print("下一次快照替换：python scripts/synnovator_submit.py push --mode snapshot")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Synnovator 账号检查、SSH 绑定与安全代码推送工具")
    sub = parser.add_subparsers(dest="command")

    check_parser = sub.add_parser("check", help="只读检查账号 SSH 访问和指定仓库读取权限")
    check_parser.add_argument("--project", default=".", help="用于发现现有 origin 的项目目录")
    check_parser.add_argument("--remote", help="用于读取验证的仓库 SSH 地址")
    check_parser.add_argument("--host", default=DEFAULT_HOST, help="平台 SSH 主机")

    bind_parser = sub.add_parser("bind", help="只处理 SSH 密钥生成、公钥绑定和认证验证")
    bind_parser.add_argument("--project", default=".", help="仅用于读取本地 Git 邮箱，不修改项目")
    bind_parser.add_argument("--host", default=DEFAULT_HOST, help="平台 SSH 主机")
    bind_parser.add_argument("--email", help="SSH 公钥备注邮箱")

    push_parser = sub.add_parser("push", help="只处理仓库选择、安全扫描、提交和推送")
    push_parser.add_argument("--project", default=".", help="项目目录，默认当前目录")
    push_parser.add_argument("--remote", help="平台页面实际显示的 SSH 克隆地址")
    push_parser.add_argument("--mode", choices=("incremental", "snapshot"), default="incremental")

    run_parser = sub.add_parser("run", help="在一个工具中编排检查、必要时绑定、再推送")
    run_parser.add_argument("--project", default=".", help="项目目录，默认当前目录")
    run_parser.add_argument("--remote", help="平台页面实际显示的 SSH 克隆地址")
    run_parser.add_argument("--host", default=DEFAULT_HOST, help="没有 remote 时使用的平台 SSH 主机")
    run_parser.add_argument("--email", help="SSH 公钥备注邮箱")
    run_parser.add_argument("--mode", choices=("incremental", "snapshot"), default="incremental")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    raw_args = list(argv) if argv is not None else sys.argv[1:]
    if not raw_args or raw_args[0].startswith("-"):
        raw_args = ["run", *raw_args]
    args = parser.parse_args(raw_args)
    command = args.command

    project_arg = getattr(args, "project", ".")
    root = Path(project_arg).expanduser().resolve()
    if not root.is_dir():
        raise ToolError(f"项目目录不存在：{root}")

    print(f"工具：synnovator-code-submit；执行阶段：{command}")
    print(f"项目目录：{root}")
    ensure_git()

    if command == "check":
        probe = find_probe_remote(root, args.remote)
        host = parse_ssh_host(probe) if probe else args.host
        ok = access_check(host=host, remote=probe, root=root, require_repository=True)
        if not ok:
            raise ToolError("访问检查未通过。认证失败时请运行 bind；仓库读取失败时请检查地址和权限。")
        print("访问检查完成；没有执行绑定、提交或推送。")
        return 0

    if command == "bind":
        email = resolve_email(root, args.email)
        bind_ssh(args.host, email)
        return 0

    if command == "push":
        push_workflow(root, args.remote, args.mode)
        return 0

    # run：仍是同一个 Skill 工具，但三个阶段保持边界清晰。
    probe = find_probe_remote(root, args.remote)
    host = parse_ssh_host(probe) if probe else args.host
    account_ok = access_check(host=host, remote=probe, root=root, require_repository=False)
    if not account_ok:
        if not confirm_exact("访问检查失败。是否进入独立的 SSH 绑定阶段？", "开始绑定"):
            raise ToolError("用户取消绑定，未进入提交推送阶段")
        email = resolve_email(root, args.email)
        bind_ssh(host, email)
        if not access_check(host=host, remote=probe, root=root, require_repository=False):
            raise ToolError("绑定后账号访问仍未通过，未进入提交推送阶段")

    push_workflow(root, args.remote, args.mode)
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
