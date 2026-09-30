"""Agent CLI 交互入口 — 启动一个持续对话的 Agent 会话。

通过 AgentAction 实例实现多轮对话，历史自动累积。
"""

from agent import AgentAction


def main():
    """Agent 对话主循环。

    创建一个 AgentAction 实例后进入 REPL 循环，
    接收用户输入并打印 Agent 的响应消息。
    输入 quit/exit 或 Ctrl+C/Ctrl+D 退出。
    """
    # 启动时创建单个 Agent 实例，保证整个会话的历史持续累积
    agent = AgentAction(debug=True)

    print("=== Agent 对话模式 ===")
    print("输入 quit/exit 退出。")

    while True:
        try:
            user_input = input("\n>>> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n会话结束。")
            break

        if not user_input:
            continue

        if user_input.lower() in {"quit", "exit"}:
            print("会话结束。")
            break

        # 调用 Agent 处理用户输入
        result = agent.run(user_input)
        print("\nAgent:")
        print(result.get("message", ""))


if __name__ == "__main__":
    main()
