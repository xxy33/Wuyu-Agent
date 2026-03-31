"""
Wuyu-Agent 统一命令行入口

用法:
    swagent chat [--model MODEL] [--domain DOMAIN]     # 交互式对话
    swagent serve [--host HOST] [--port PORT]           # 启动 Web 服务
    swagent detect [--mode MODE] [--input PATH]         # 遥感检测
    swagent run <workflow> [--input PATH]                # 运行工作流
    swagent skill list                                   # 列出可用 Skill
    swagent skill run <name> [--param KEY=VALUE]         # 执行 Skill

直接运行:
    python -m swagent.cli
"""

import argparse
import asyncio
import signal
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from swagent.utils.logger import get_logger

logger = get_logger(__name__)

# ====================== ANSI 颜色工具 ======================

class _Colors:
    """ANSI 颜色辅助类（仅在 tty 时启用颜色）"""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[32m"
    BLUE = "\033[34m"
    CYAN = "\033[36m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    DIM = "\033[2m"

    @classmethod
    def enabled(cls) -> bool:
        return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

    @classmethod
    def wrap(cls, text: str, color: str) -> str:
        if cls.enabled():
            return f"{color}{text}{cls.RESET}"
        return text

    @classmethod
    def green(cls, text: str) -> str:
        return cls.wrap(text, cls.GREEN)

    @classmethod
    def blue(cls, text: str) -> str:
        return cls.wrap(text, cls.BLUE)

    @classmethod
    def cyan(cls, text: str) -> str:
        return cls.wrap(text, cls.CYAN)

    @classmethod
    def yellow(cls, text: str) -> str:
        return cls.wrap(text, cls.YELLOW)

    @classmethod
    def red(cls, text: str) -> str:
        return cls.wrap(text, cls.RED)

    @classmethod
    def bold(cls, text: str) -> str:
        return cls.wrap(text, cls.BOLD)

    @classmethod
    def dim(cls, text: str) -> str:
        return cls.wrap(text, cls.DIM)


C = _Colors


# ====================== 辅助函数 ======================

def _find_project_root() -> Path:
    """向上查找项目根目录（含 pyproject.toml 或 swagent/ 目录）"""
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / "pyproject.toml").exists() or (parent / "swagent").is_dir():
            return parent
    return cwd


def _parse_key_value(items: Optional[List[str]]) -> Dict[str, str]:
    """将 KEY=VALUE 列表解析为字典"""
    result: Dict[str, str] = {}
    if not items:
        return result
    for item in items:
        if "=" not in item:
            print(C.red(f"无效参数格式（需要 KEY=VALUE）: {item}"))
            continue
        key, value = item.split("=", 1)
        result[key.strip()] = value.strip()
    return result


# ====================== Chat REPL ======================

SLASH_COMMANDS = {
    "/help":    "显示帮助信息",
    "/tools":   "列出可用工具",
    "/skills":  "列出可用 Skill",
    "/skill":   "激活 Skill（用法: /skill <name> [KEY=VALUE ...]）",
    "/clear":   "清空对话历史",
    "/history": "显示对话历史",
    "/quit":    "退出对话",
}


def _print_help() -> None:
    """打印斜杠命令帮助"""
    print(C.bold("\n可用命令:"))
    for cmd, desc in SLASH_COMMANDS.items():
        print(f"  {C.cyan(cmd):<24s} {desc}")
    print()


def _run_chat(args: argparse.Namespace) -> None:
    """交互式对话 REPL"""
    from swagent.skills.registry import SkillRegistry
    from swagent.skills.loader import SkillLoader

    domain = getattr(args, "domain", "all")
    model = getattr(args, "model", None)

    print(C.bold("╔══════════════════════════════════════╗"))
    print(C.bold("║     Wuyu-Agent 交互式对话终端        ║"))
    print(C.bold("╚══════════════════════════════════════╝"))
    print(C.dim(f"  领域: {domain}  模型: {model or '默认'}"))
    print(C.dim("  输入 /help 查看可用命令\n"))

    # 加载 Skill
    skill_registry = SkillRegistry()
    project_root = _find_project_root()
    skill_dirs = [
        project_root / "skills",
        project_root / "swagent" / "skills" / "definitions",
    ]
    skill_registry.discover_skills([d for d in skill_dirs if d.is_dir()])

    # 加载工具注册中心
    try:
        from swagent.tools import get_global_registry
        tool_registry = get_global_registry()
    except Exception:
        tool_registry = None

    history: List[Dict[str, str]] = []
    active_skill: Optional[str] = None

    # 优雅处理 Ctrl+C
    def _sigint_handler(signum: int, frame: Any) -> None:
        print(C.yellow("\n(按 Ctrl+C 再次退出，或输入 /quit)"))
        signal.signal(signal.SIGINT, signal.SIG_DFL)

    signal.signal(signal.SIGINT, _sigint_handler)

    while True:
        try:
            prompt_prefix = C.green("you> ") if not active_skill else C.cyan(f"[{active_skill}]> ")
            user_input = input(prompt_prefix).strip()
        except (EOFError, KeyboardInterrupt):
            print(C.dim("\n再见!"))
            break

        if not user_input:
            continue

        # 重新注册 SIGINT
        signal.signal(signal.SIGINT, _sigint_handler)

        # ---- 斜杠命令 ----
        if user_input.startswith("/"):
            parts = user_input.split()
            cmd = parts[0].lower()

            if cmd == "/quit" or cmd == "/exit":
                print(C.dim("再见!"))
                break

            elif cmd == "/help":
                _print_help()

            elif cmd == "/clear":
                history.clear()
                active_skill = None
                print(C.dim("对话历史已清空。"))

            elif cmd == "/history":
                if not history:
                    print(C.dim("暂无对话记录。"))
                else:
                    for msg in history:
                        role_color = C.green if msg["role"] == "user" else C.blue
                        print(f"{role_color(msg['role'])}: {msg['content'][:120]}")

            elif cmd == "/tools":
                if tool_registry is not None:
                    tools = tool_registry.list_tools() if hasattr(tool_registry, "list_tools") else []
                    if tools:
                        print(C.bold("\n可用工具:"))
                        for t in tools:
                            name = t.name if hasattr(t, "name") else str(t)
                            desc = t.description if hasattr(t, "description") else ""
                            print(f"  {C.blue(name):<30s} {desc}")
                    else:
                        print(C.dim("暂无已注册工具。"))
                else:
                    print(C.dim("工具注册中心未初始化。"))

            elif cmd == "/skills":
                skills = skill_registry.list_skills(
                    domain=domain if domain != "all" else None
                )
                if skills:
                    print(C.bold("\n可用 Skill:"))
                    for s in skills:
                        print(f"  {C.cyan(s.name):<30s} [{s.domain}] {s.description}")
                else:
                    print(C.dim("暂无可用 Skill。"))

            elif cmd == "/skill":
                if len(parts) < 2:
                    print(C.red("用法: /skill <name> [KEY=VALUE ...]"))
                else:
                    skill_name = parts[1]
                    skill = skill_registry.get_skill(skill_name)
                    if skill is None:
                        print(C.red(f"未找到 Skill: {skill_name}"))
                    else:
                        active_skill = skill_name
                        params = _parse_key_value(parts[2:])
                        rendered = skill_registry.render_prompt(skill_name, **params)
                        print(C.cyan(f"\n已激活 Skill: {skill_name}"))
                        print(C.dim(f"Prompt 已加载 ({len(rendered)} 字符)"))
                        # 将 Skill prompt 添加为系统指令
                        history.append({"role": "system", "content": rendered})

            else:
                print(C.red(f"未知命令: {cmd}  输入 /help 查看帮助"))

            continue

        # ---- 普通对话 ----
        history.append({"role": "user", "content": user_input})

        print(C.blue("agent> "), end="", flush=True)
        print(C.dim("(Agent 推理功能将在集成 LLM 后启用)"))
        print(C.dim("  提示: 当前为 CLI 框架模式，请先完成 LLM 集成。"))

        history.append({
            "role": "assistant",
            "content": "[CLI 框架模式 - 待集成 LLM]",
        })


# ====================== Serve ======================

def _run_serve(args: argparse.Namespace) -> None:
    """启动 Web 服务"""
    host = getattr(args, "host", "0.0.0.0")
    port = getattr(args, "port", 8080)

    try:
        import uvicorn
    except ImportError:
        print(C.red("错误: 需要安装 uvicorn。运行: pip install uvicorn"))
        sys.exit(1)

    print(C.bold("╔══════════════════════════════════════════════════════╗"))
    print(C.bold("║        Wuyu-Agent Web 服务                          ║"))
    print(C.bold("╠══════════════════════════════════════════════════════╣"))
    print(C.bold(f"║  服务地址: http://{host}:{port}"))
    print(C.bold(f"║  API 文档: http://{host}:{port}/docs"))
    print(C.bold("╚══════════════════════════════════════════════════════╝"))

    uvicorn.run("web.app:app", host=host, port=port, reload=getattr(args, "reload", False))


# ====================== Detect ======================

def _run_detect(args: argparse.Namespace) -> None:
    """遥感检测（委托给 waste_monitoring）"""
    # 构造 waste_monitoring 的命令行参数并调用
    detect_args = []
    if getattr(args, "mode", None):
        detect_args.extend(["--mode", args.mode])
    if getattr(args, "input", None):
        detect_args.extend(["--input", args.input])
    if getattr(args, "city", None):
        detect_args.extend(["--city", args.city])

    try:
        from swagent.waste_monitoring.__main__ import parse_args as wm_parse_args, main as wm_main
        print(C.blue("启动遥感检测..."))
        # 将参数传入 waste_monitoring
        old_argv = sys.argv
        sys.argv = ["swagent-detect"] + detect_args
        try:
            wm_main()
        finally:
            sys.argv = old_argv
    except ImportError:
        print(C.red("错误: waste_monitoring 模块不可用"))
        sys.exit(1)
    except Exception as exc:
        print(C.red(f"检测失败: {exc}"))
        sys.exit(1)


# ====================== Run Workflow ======================

def _run_workflow(args: argparse.Namespace) -> None:
    """运行 StateGraph 工作流"""
    workflow_name = args.workflow
    input_path = getattr(args, "input", None)

    try:
        from swagent.workflows.workflow_manager import WorkflowManager
    except ImportError:
        print(C.red("错误: 工作流模块不可用"))
        sys.exit(1)

    manager = WorkflowManager()
    available = list(manager._workflows.keys()) if hasattr(manager, "_workflows") else []

    if workflow_name not in available:
        print(C.red(f"未知工作流: {workflow_name}"))
        print(C.dim(f"可用工作流: {', '.join(available)}"))
        sys.exit(1)

    print(C.blue(f"运行工作流: {workflow_name}"))
    if input_path:
        print(C.dim(f"输入: {input_path}"))

    try:
        workflow = manager.create(workflow_name) if hasattr(manager, "create") else None
        if workflow is None:
            print(C.yellow("工作流创建接口待完善，请确认 WorkflowManager.create() 方法存在。"))
            return

        result = asyncio.run(workflow.run({"input_path": input_path}))
        print(C.green(f"工作流完成: {result}"))
    except Exception as exc:
        print(C.red(f"工作流执行失败: {exc}"))
        sys.exit(1)


# ====================== Skill 子命令 ======================

def _run_skill(args: argparse.Namespace) -> None:
    """Skill 子命令入口"""
    from swagent.skills.registry import SkillRegistry

    skill_registry = SkillRegistry()
    project_root = _find_project_root()
    skill_dirs = [
        project_root / "skills",
        project_root / "swagent" / "skills" / "definitions",
    ]
    skill_registry.discover_skills([d for d in skill_dirs if d.is_dir()])

    action = getattr(args, "skill_action", None)

    if action == "list":
        domain = getattr(args, "domain", None)
        skills = skill_registry.list_skills(domain=domain)
        if not skills:
            print(C.dim("暂无可用 Skill。"))
            return
        print(C.bold(f"\n{'名称':<25s} {'领域':<12s} {'描述'}"))
        print("-" * 70)
        for s in skills:
            print(f"  {C.cyan(s.name):<34s} {s.domain:<12s} {s.description}")
        print()

    elif action == "run":
        skill_name = args.name
        params = _parse_key_value(getattr(args, "param", None))
        skill = skill_registry.get_skill(skill_name)
        if skill is None:
            print(C.red(f"未找到 Skill: {skill_name}"))
            sys.exit(1)

        # 校验参数
        errors = skill.validate_params(params)
        if errors:
            print(C.red("参数校验失败:"))
            for err in errors:
                print(C.red(f"  - {err}"))
            sys.exit(1)

        rendered = skill_registry.render_prompt(skill_name, **params)
        tools_needed = skill_registry.get_required_tools(skill_name)

        print(C.bold(f"\n执行 Skill: {skill.name}"))
        print(C.dim(f"领域: {skill.domain}"))
        if tools_needed:
            print(C.blue(f"依赖工具: {', '.join(tools_needed)}"))
        if skill.steps:
            print(C.bold("\n执行步骤:"))
            for i, step in enumerate(skill.steps, 1):
                print(f"  {i}. {step}")
        print(C.bold("\n渲染后 Prompt:"))
        print(C.dim("-" * 50))
        print(rendered)
        print(C.dim("-" * 50))
        print(C.yellow("\n(完整执行需集成 LLM Agent，当前仅展示 Prompt 渲染结果)"))

    else:
        print(C.red("请指定操作: list 或 run"))
        print(C.dim("用法: swagent skill list | swagent skill run <name>"))


# ====================== 主入口 ======================

def build_parser() -> argparse.ArgumentParser:
    """构建命令行解析器"""
    parser = argparse.ArgumentParser(
        prog="swagent",
        description="Wuyu-Agent 统一命令行入口",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  swagent chat                          # 启动交互式对话
  swagent chat --domain waste           # 指定领域
  swagent serve --port 8080             # 启动 Web 服务
  swagent detect --mode test --input img.tif  # 遥感检测
  swagent run research --input data.json      # 运行工作流
  swagent skill list                    # 列出 Skill
  swagent skill run 排放计算报告 --param waste_type=生活垃圾
        """,
    )
    parser.add_argument(
        "--version", action="version",
        version="%(prog)s 0.1.0",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="启用详细日志",
    )

    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # ---- chat ----
    chat_parser = subparsers.add_parser("chat", help="交互式对话")
    chat_parser.add_argument("--model", type=str, default=None, help="LLM 模型名称")
    chat_parser.add_argument(
        "--domain", type=str, default="all",
        choices=["waste", "environment", "logistics", "all"],
        help="领域 (默认: all)",
    )
    chat_parser.set_defaults(func=_run_chat)

    # ---- serve ----
    serve_parser = subparsers.add_parser("serve", help="启动 Web 服务")
    serve_parser.add_argument("--host", type=str, default="0.0.0.0", help="服务地址 (默认: 0.0.0.0)")
    serve_parser.add_argument("--port", type=int, default=8080, help="服务端口 (默认: 8080)")
    serve_parser.add_argument("--reload", action="store_true", help="启用热重载")
    serve_parser.set_defaults(func=_run_serve)

    # ---- detect ----
    detect_parser = subparsers.add_parser("detect", help="遥感检测")
    detect_parser.add_argument("--mode", type=str, default="test", choices=["test", "prod"], help="运行模式")
    detect_parser.add_argument("--input", type=str, help="输入路径")
    detect_parser.add_argument("--city", type=str, help="城市名称")
    detect_parser.set_defaults(func=_run_detect)

    # ---- run ----
    run_parser = subparsers.add_parser("run", help="运行工作流")
    run_parser.add_argument("workflow", type=str, help="工作流名称")
    run_parser.add_argument("--input", type=str, help="输入文件路径")
    run_parser.set_defaults(func=_run_workflow)

    # ---- skill ----
    skill_parser = subparsers.add_parser("skill", help="Skill 管理")
    skill_sub = skill_parser.add_subparsers(dest="skill_action")

    skill_list_parser = skill_sub.add_parser("list", help="列出可用 Skill")
    skill_list_parser.add_argument("--domain", type=str, default=None, help="按领域过滤")
    skill_list_parser.set_defaults(func=_run_skill)

    skill_run_parser = skill_sub.add_parser("run", help="执行 Skill")
    skill_run_parser.add_argument("name", type=str, help="Skill 名称")
    skill_run_parser.add_argument("--param", "-p", action="append", help="参数 (KEY=VALUE)")
    skill_run_parser.set_defaults(func=_run_skill)

    return parser


def main() -> None:
    """CLI 主入口"""
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        sys.exit(0)

    try:
        func(args)
    except KeyboardInterrupt:
        print(C.dim("\n中断退出。"))
        sys.exit(130)
    except Exception as exc:
        print(C.red(f"\n错误: {exc}"))
        logger.exception("CLI 执行异常")
        sys.exit(1)


if __name__ == "__main__":
    main()
