"""Terminal chat, setup, and the Session 1 token check.

    python -m src            chat with the agent
    python -m src setup      choose a provider and paste an API key
    python -m src check      send 'hello' and count the tokens (add --offline for a rough estimate)
    python -m src --demo     try the chat with a fake model, no API key needed
"""
from __future__ import annotations

import argparse
import getpass
import sys

from .core.agent import FIRST_CALL_TARGET_TOKENS, Agent
from .core.agent_tools import AgentTools, enabled_tool_names
from .core.configure import (check_connection, finish_models, looks_like_key, model_prefix,
                             reset_models_if_invalid, suggest_models, valid_model)
from .core.providers import PROVIDER_INFO, PROVIDERS, ProviderError, get_provider
from .core.settings import APP_NAME, Settings, SettingsError
from .core.tokens import estimate_tokens
from .core.tools import ToolRegistry
from .core.usage import CapReached, UsageTracker
from .core.workspace import Workspace

HELP = """Commands:
  /usage   what you have used today and in this chat
  /reset   forget this chat and start fresh
  /help    show this list
  /quit    leave"""


def _ask(input_fn, label: str, current: str) -> str:
    answer = input_fn(f"{label} [{current}]: ").strip()
    return answer or current


def _ask_model(input_fn, out, label, current, provider, secrets) -> str:
    """Ask for a model name, refusing commands and pasted keys (they would be shown and saved by mistake)."""
    while True:
        answer = _ask(input_fn, label, current)
        if valid_model(answer, provider, secrets):
            return answer
        if looks_like_key(answer, provider, secrets):
            out("That looks like an API key. Do not paste your key here; it goes only in the hidden key question.")
        out(f"A model name starts with '{model_prefix(provider)}' and has no spaces. "
            "Press Enter to keep the suggested one.")
        if not valid_model(current, provider, secrets):
            current = ""


def run_setup(settings, input_fn=input, secret_fn=getpass.getpass, out=print, provider_factory=get_provider) -> None:
    out(f"{APP_NAME} setup. Press Enter to keep the value shown in [brackets].")
    for name in PROVIDERS:
        out(f"  {name}: {PROVIDER_INFO[name]['label']}")
    provider = _ask(input_fn, "Provider", settings.provider)
    while provider not in PROVIDERS:
        if provider.lower().startswith("python"):
            out("That looks like a command. Finish setup first, then run it afterwards.")
        out(f"Please type one of: {', '.join(PROVIDERS)}.")
        provider = _ask(input_fn, "Provider", settings.provider)
    settings.set("provider", provider)
    reset_models_if_invalid(settings, provider)  # new provider or a bad saved name: start from defaults
    if provider != "mock":
        out(PROVIDER_INFO[provider]["key_help"])
        key = "".join(ch for ch in secret_fn(
            "Paste your API key (it will not show as you type), or press Enter to keep the current one: ")
            if ch.isprintable()).strip()
        if key:
            settings.set_api_key(provider, key)
            out(f"Got your key ({len(key)} characters). It stays hidden.")
        elif settings.api_key(provider):
            out("Keeping the key you saved before.")
        else:
            out("No key was pasted. If pasting does nothing, right-click in the window to paste, then press Enter.")
    try:
        picked = suggest_models(settings, provider_factory)
        if picked:
            settings.set("models", {"main": picked["main"], "cheap": picked["cheap"]})
            out(f"Your key can use {picked['main']} (most free requests per day).")
            if picked["flash"]:
                out(f"For smarter answers but fewer free requests, type {picked['flash']} as the main model.")
    except ProviderError as exc:
        out(settings.redact(str(exc)))
    secrets = settings.secrets()
    settings.set("models", "main", _ask_model(input_fn, out, "Main model", settings.get("models", "main"), provider, secrets))
    settings.set("models", "cheap", _ask_model(input_fn, out, "Cheap model", settings.get("models", "cheap"), provider, secrets))
    finish_models(settings)
    settings.save()
    out("Saved. Your key is stored only in data/settings.json on this computer.")
    if provider != "mock" and settings.api_key(provider):
        if input_fn("Test the key now? [Y/n]: ").strip().lower() in ("", "y", "yes"):
            try:
                out("Connected. The model answered: " + check_connection(settings, provider_factory))
            except ProviderError as exc:
                out(settings.redact(str(exc)))


def ask_approval(request, input_fn=input, out=print) -> str:
    """The terminal's Approve or Deny: show exactly what will run and why, then ask."""
    out("")
    out("Pilot wants to run this command:")
    out(f"    {request.action}")
    out(f"Why: {request.reason}")
    if request.risks:
        out("Needs your OK because: " + "; ".join(request.risks) + ".")
    options = "[y] yes once" + (f", [a] always allow \"{request.rule}\"" if request.rule else "") + ", [N] no"
    answer = input_fn(f"Allow it? {options}: ").strip().lower()
    if answer in ("y", "yes"):
        return "once"
    if answer in ("a", "always") and request.rule:
        return "always"
    return "deny"


def _show_usage(agent, out) -> None:
    today = agent.usage.totals()
    session = agent.usage.session
    usd = f"about ${today['usd']:.4f}" + ("" if today["usd_complete"] else " (some calls have no price)")
    out(f"Today: {today['calls']} calls, {today['total_tokens']:,} tokens, {usd}.")
    out(f"This chat: {session['calls']} calls, "
        f"{session['input_tokens'] + session['output_tokens'] + session['cache_read_tokens'] + session['cache_write_tokens']:,} tokens.")


def _run_with_cap_prompt(agent, text, input_fn, out):
    try:
        return agent.run_task(text)
    except CapReached as exc:
        out(str(exc))
        if input_fn("Go past the limit for the rest of this chat? [y/N]: ").strip().lower() in ("y", "yes"):
            agent.cap_override = True
            return agent.run_task(text)
    return None


def chat(settings, agent, input_fn=input, out=print) -> None:
    out(f"{APP_NAME} is ready. Type /help for commands, /quit to leave.")
    while True:
        try:
            text = input_fn("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            out("")
            return
        if not text:
            continue
        if text.startswith("/"):
            command = text.split()[0].lower()
            if command == "/quit":
                return
            elif command == "/help":
                out(HELP)
            elif command == "/usage":
                _show_usage(agent, out)
            elif command == "/reset":
                agent.conversation.reset()
                out("Chat cleared.")
            else:
                out("I don't know that command. Type /help.")
            continue
        try:
            result = _run_with_cap_prompt(agent, text, input_fn, out)
        except ProviderError as exc:
            out(settings.redact(str(exc)))
            continue
        if result is None:
            continue
        out(f"{APP_NAME}: {result.text}")
        out(f"  ({result.calls} call{'s' if result.calls != 1 else ''}, {result.total_tokens:,} tokens)")


def run_check(settings, agent, offline: bool, out=print) -> int:
    system, messages, tools = agent.baseline_request()
    estimate = estimate_tokens(system) + estimate_tokens(messages) + estimate_tokens(tools)
    if offline:
        used, kind = estimate, "rough offline estimate (about 4 characters per token)"
    else:
        import time
        started = time.time()
        response = agent.provider.complete(model=settings.get("models", "main"), system=system,
                                           messages=messages, tools=tools, max_tokens=64)
        agent.usage.record(task="check: hello", model=response.model or settings.get("models", "main"),
                           response=response, started=started, ended=time.time())
        used = response.input_tokens + response.cache_read_tokens + response.cache_write_tokens
        kind = "measured by the model service"
    ok = used <= FIRST_CALL_TARGET_TOKENS
    out(f"A fresh 'hello' with no connectors on sends {used:,} input tokens ({kind}).")
    out(f"Target: {FIRST_CALL_TARGET_TOKENS:,} or less. Result: {'OK' if ok else 'TOO HIGH, fix before moving on'}.")
    return 0 if ok else 2


def run_daemon(settings=None, stop_event=None, out=print) -> int:
    """Headless always-on background mode: connectors, scheduler, web access, and 60s resource sampling."""
    import signal
    import threading
    import time
    from .ui.controller import Controller

    controller = Controller(settings)
    stop = stop_event or threading.Event()
    if stop_event is None:
        for sig_name in ("SIGINT", "SIGTERM"):
            sig = getattr(signal, sig_name, None)
            if sig is not None:
                try:
                    signal.signal(sig, lambda *_a: stop.set())
                except (ValueError, OSError):
                    pass
    controller.start_connectors()
    controller.start_scheduler()
    controller.start_web_if_enabled()
    controller.activity.record(summary="Pilot started in background mode.", kind="system", source="system")
    out(f"{APP_NAME} background service is running. Press Ctrl+C to stop.")
    try:
        while not stop.is_set():
            try:
                controller.sample_resources()
            except Exception:
                pass
            stop.wait(60.0)
    except KeyboardInterrupt:
        pass
    finally:
        controller.stop_connectors()
        controller.stop_scheduler()
        controller.stop_web()
        controller.activity.record(summary="Pilot background service stopped.", kind="system", source="system")
    return 0


def run_web(settings=None, host: str = "", port: int | None = None,
            tunnel: bool = False, stop_event=None, out=print) -> int:
    """Headless web server mode (supports --tunnel for $0 Cloudflare HTTPS or PORT/PILOT_WEB_PASSWORD for cloud)."""
    import os
    import signal
    import threading
    from .ui.controller import Controller

    controller = Controller(settings)
    cfg = controller.web_config()
    bind_host = host or os.environ.get("PILOT_WEB_HOST") or cfg["host"] or "127.0.0.1"
    raw_port = port if port is not None else os.environ.get("PORT") or cfg["port"] or 8765
    env_pw = os.environ.get("PILOT_WEB_PASSWORD", "").strip()
    err = controller.configure_web(enabled=True, host=bind_host, port=int(raw_port), new_password=env_pw)
    if err:
        out(f"Could not start web access: {err}")
        return 1

    stop = stop_event or threading.Event()
    if stop_event is None:
        for sig_name in ("SIGINT", "SIGTERM"):
            sig = getattr(signal, sig_name, None)
            if sig is not None:
                try:
                    signal.signal(sig, lambda *_a: stop.set())
                except (ValueError, OSError):
                    pass

    controller.start_connectors()
    controller.start_scheduler()
    out(f"{APP_NAME} web app running at: " + " | ".join(controller.web_config()["urls"]))
    if tunnel:
        out("Starting free Cloudflare HTTPS tunnel...")
        pub_url, t_err = controller.start_public_tunnel()
        if t_err:
            out(f"Public tunnel failed: {t_err}")
        else:
            out(f"Public HTTPS link (password-protected): {pub_url}")
    out("Press Ctrl+C to stop.")
    try:
        while not stop.is_set():
            try:
                controller.sample_resources()
            except Exception:
                pass
            stop.wait(60.0)
    except KeyboardInterrupt:
        pass
    finally:
        controller.stop_connectors()
        controller.stop_scheduler()
        controller.stop_web()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src", description=f"{APP_NAME}, a simple personal agent.")
    parser.add_argument("--demo", action="store_true", help="use a fake model, no API key needed")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("chat", help="chat with the agent (default)")
    sub.add_parser("window", help="open the desktop window")
    sub.add_parser("daemon", help="run quietly in the background (connectors, schedules, web access)")
    web_cmd = sub.add_parser("web", help="run the password-protected web app (add --tunnel for a free public HTTPS link)")
    web_cmd.add_argument("--host", default="", help="bind address (default 127.0.0.1, or 0.0.0.0 for LAN/cloud)")
    web_cmd.add_argument("--port", type=int, default=None, help="port number (default 8765 or $PORT)")
    web_cmd.add_argument("--tunnel", action="store_true", help="publish a free HTTPS link via Cloudflare Tunnel")
    sub.add_parser("setup", help="choose a provider and paste an API key")
    check = sub.add_parser("check", help="count the tokens in a fresh 'hello'")
    check.add_argument("--offline", action="store_true", help="estimate without calling the model")
    args = parser.parse_args(argv)

    try:
        settings = Settings.load()
    except SettingsError as exc:
        print(exc)
        return 1
    command = args.command or "chat"
    if command == "window":
        from .ui.app import run as run_window  # imported here so the terminal chat never needs tkinter
        run_window()
        return 0
    if command == "daemon":
        return run_daemon(settings)
    if command == "web":
        return run_web(settings, host=args.host, port=args.port, tunnel=args.tunnel)
    if command == "setup":
        run_setup(settings)
        return 0
    if args.demo:
        settings.set("provider", "mock")
    elif command == "chat" and settings.provider != "mock" and not settings.api_key():
        print("There is no API key yet, so let's set one up first.")
        run_setup(settings)
    try:
        provider = get_provider(settings)
    except ProviderError as exc:
        print(exc)
        return 1
    hands = AgentTools(settings, Workspace(settings), approve=ask_approval,
                       on_activity=lambda text: print(f"  ({text})"),
                       on_send_file=lambda s: print(f"  [File ready: {s.path}]"))
    agent = Agent(settings, provider, UsageTracker(settings),
                  registry=ToolRegistry(hands.tools(), enabled_tool_names(settings)))
    try:
        if command == "check":
            return run_check(settings, agent, offline=args.offline)
        chat(settings, agent)
    except ProviderError as exc:
        print(settings.redact(str(exc)))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
