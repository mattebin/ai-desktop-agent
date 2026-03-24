from __future__ import annotations

import argparse

from core.agent import Agent
from core.config import load_settings
from core.local_api import DEFAULT_LOCAL_API_HOST, DEFAULT_LOCAL_API_PORT, serve_local_api
from core.safety import start_emergency_stop_listener


def main():
    parser = argparse.ArgumentParser(description="Run the safe local operator.")
    parser.add_argument("--api", action="store_true", help="Launch the local API server.")
    parser.add_argument("--api-host", default="", help="Override the local API host.")
    parser.add_argument("--api-port", type=int, default=-1, help="Override the local API port. Use 0 for an ephemeral local port.")
    parser.add_argument("--ui", action="store_true", help="Launch the local control UI.")
    parser.add_argument("--goal", default="", help="Run a goal directly without prompting.")
    args = parser.parse_args()

    settings = load_settings()

    if args.api:
        host = str(args.api_host).strip() or str(settings.get("local_api_host", DEFAULT_LOCAL_API_HOST)).strip() or DEFAULT_LOCAL_API_HOST
        port = int(args.api_port if args.api_port >= 0 else settings.get("local_api_port", DEFAULT_LOCAL_API_PORT) or DEFAULT_LOCAL_API_PORT)
        serve_local_api(host=host, port=port, settings=settings)
        return

    start_emergency_stop_listener()

    if args.ui:
        from control_ui import launch_control_ui

        launch_control_ui(settings=settings)
        return

    agent = Agent()
    goal = str(args.goal).strip() or input("Enter goal: ").strip()
    result = agent.run_task(goal)

    print("\n=== RESULT ===")
    print(result.get("message", result))


if __name__ == "__main__":
    main()

