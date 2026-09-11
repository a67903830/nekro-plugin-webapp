from openai import OpenAI

# ========= 1. 配置 =========
client = OpenAI(
    api_key="sk-xxxxxxxxxxxxxxxxxxxxxxxx",
    base_url="https://api.nekro.ai/v1",  # 例如 https://api.openai.com/v1
)

MODEL = "gemini-3-flash-preview"

# ========== 2. 工具定义 ==========
tools = [
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Append content to a file",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "append": {"type": "string"},
                },
                "required": ["path", "append"],
            },
        },
    },
]

# ========== 3. 强协议 Prompt ==========
system_prompt = """
SYSTEM PROTOCOL — TOOL STREAM MODE

You are NOT a conversational assistant.
You are a deterministic tool-call generator.

MANDATORY OUTPUT REQUIREMENTS:
- Output EXACTLY TWO tool calls.
- Tool call #1 MUST be write_file.
- Tool call #2 MUST be edit_file.
- Both tool calls MUST appear in the SAME response.
- You MUST continue after the first tool call.
- Stopping early is INVALID OUTPUT.

GENERATION CONSTRAINTS:
- Do NOT output text.
- Do NOT explain.
- Do NOT wait for tool execution results.
- Continue generation until BOTH tool calls are emitted.
"""

user_prompt = 'Create demo.txt with content "Hello", then append " World".'

# ========== 4. 发起 streaming 请求 ==========
stream = client.chat.completions.create(  # pyright: ignore[reportCallIssue]
    model=MODEL,
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ],
    tools=tools,  # pyright: ignore[reportArgumentType]
    tool_choice="auto",
    temperature=0,
    stream=True,
)

# ========== 5. Streaming tool_call 解析 ==========
print("\n=== STREAM START ===\n")

current_tool = None
current_args = ""

for chunk in stream:
    if not chunk.choices:
        continue

    delta = chunk.choices[0].delta

    # 是否是 tool_call
    if delta.tool_calls:
        for tc in delta.tool_calls:
            # tool_call start
            if tc.function and tc.function.name:
                current_tool = tc.function.name
                current_args = ""
                print(f"[TOOL CALL START] {current_tool}")

            # tool_call args delta
            if tc.function and tc.function.arguments:
                current_args += tc.function.arguments
                print(f"[TOOL CALL DELTA] {tc.function.arguments}")

            # tool_call end（通过 finish_reason 判断）
            if chunk.choices[0].finish_reason == "tool_calls":
                print(f"[TOOL CALL END] {current_tool}")
                print(f"  args = {current_args}\n")

print("\n=== STREAM END ===\n")
