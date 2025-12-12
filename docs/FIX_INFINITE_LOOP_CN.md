# 修复工具调用无限循环问题

## 问题描述

在使用多轮工具调用（Tool Use）对话时，AI 会重复调用相同的工具，进入无限循环。

### 用户报告的现象

```
用户: 帮我看一下 index.html 是否提交
AI: 好的我来帮你检查
*tool use: git diff
*tool use: git status  
*tool use: git log

AI: 用户说xxxx，好的我来帮你检查  ← 重复！
*tool use: git diff
*tool use: git status
*tool use: git log

AI: 用户说xxxx，我马上来检查  ← 又重复！
...无限循环...
```

### 抓包分析发现的问题

用户抓包发现：
1. 消息M（"你再查看一下index.html是否提交成功了"）一直在最后
2. **但是工具调用和结果却跑到了M的上面**
3. 多次tool_use和tool_result的顺序混乱
4. 整个对话历史被压缩成很少的消息块

## 根本原因

在 `claude_converter.py` 的 `merge_user_messages()` 函数中，**只使用了第一个消息的 `userInputMessageContext`**，导致后续消息的 `toolResults` 丢失。

### 问题代码

```python
def merge_user_messages(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    # ...
    for msg in messages:
        content = msg.get("content", "")
        if base_context is None:
            base_context = msg.get("userInputMessageContext", {})  # ❌ 只用第一个
        # ...
    
    result = {
        "content": "\n\n".join(all_contents),
        "userInputMessageContext": base_context or {},  # ❌ 后续消息的 toolResults 丢失
        # ...
    }
```

### 问题示例

当合并多个连续的USER消息时：

**输入**：
```python
[
  {content: "", userInputMessageContext: {toolResults: [t1]}},  # 第1个消息
  {content: "", userInputMessageContext: {toolResults: [t2]}},  # 第2个消息
  {content: "用户问题", userInputMessageContext: {}},           # 第3个消息
]
```

**旧代码输出**（错误）：
```python
{
  content: "用户问题",
  userInputMessageContext: {
    toolResults: [t1]  # ❌ 只有第1个消息的 toolResults，t2 丢失了！
  }
}
```

**后果**：
- AI 只能看到部分工具执行结果
- AI 认为某些工具还没执行，重复调用
- 进入无限循环

## 修复方案

修改 `merge_user_messages()` 函数，**收集并合并所有消息的 `toolResults`**。

### 修复后的代码

```python
def merge_user_messages(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge consecutive user messages, keeping only the last 2 messages' images.
    
    IMPORTANT: This function properly merges toolResults from all messages to prevent
    losing tool execution history, which would cause infinite loops.
    """
    if not messages:
        return {}
    
    all_contents = []
    base_context = None
    all_tool_results = []  # 收集所有消息的 toolResults
    
    for msg in messages:
        content = msg.get("content", "")
        msg_ctx = msg.get("userInputMessageContext", {})
        
        # 从第一个消息初始化 base_context
        if base_context is None:
            base_context = msg_ctx.copy() if msg_ctx else {}
            # 移除 toolResults，单独合并
            if "toolResults" in base_context:
                all_tool_results.extend(base_context.pop("toolResults"))
        else:
            # 从后续消息收集 toolResults
            if "toolResults" in msg_ctx:
                all_tool_results.extend(msg_ctx["toolResults"])
        
        if content:
            all_contents.append(content)
    
    result = {
        "content": "\n\n".join(all_contents),
        "userInputMessageContext": base_context or {},
        # ...
    }
    
    # ✅ 将合并的 toolResults 添加到结果
    if all_tool_results:
        result["userInputMessageContext"]["toolResults"] = all_tool_results
    
    return result
```

### 修复后的效果

**相同的输入，修复后的输出**（正确）：
```python
{
  content: "用户问题",
  userInputMessageContext: {
    toolResults: [t1, t2]  # ✅ 包含所有消息的 toolResults
  }
}
```

**优势**：
- ✅ 所有 toolResults 都被保留
- ✅ AI 可以看到完整的工具执行历史
- ✅ 消除无限循环
- ✅ 提高对话质量
            history.append({"userInputMessage": merged})
            pending_user_msgs = []
        history.append(item)
```

### 修复后的效果

**相同的输入，修复后的输出**（正确）：
```python
history = [
  {userInputMessage: "M: 检查文件"},
  {assistantResponseMessage: [tool_use...]},
  {userInputMessage: toolResults:[...]},           # ✅ 独立的tool_result
  {userInputMessage: "用户的跟进问题"},             # ✅ 独立的文本消息
]
```

**优势**：
- ✅ tool_result 消息保持独立
- ✅ 消息数量正确，不会丢失
- ✅ AI 可以看到完整的对话历史
- ✅ 消除了无限循环的根本原因

## 测试验证

### 测试1：标准工具调用流程

```python
输入: USER -> ASSISTANT(tool_use) -> USER(tool_result)
输出: 2条history + 1条current
结果: ✅ 通过
```

### 测试2：多轮工具调用

```python
输入: 
  USER -> ASSISTANT(tool_use) -> USER(tool_result) 
  -> ASSISTANT(tool_use) -> USER(tool_result)
输出: 4条history + 1条current
结果: ✅ 通过（每轮对话保持独立）
```

### 测试3：连续USER消息（包含tool_result）

```python
输入: 
  USER(M) -> ASSISTANT(tool_use) -> USER(tool_result) 
  -> USER(跟进问题) -> ASSISTANT
输出: 4条history + 1条current
  [0] USER: M
  [1] ASSISTANT: tool_use
  [2] USER: tool_result (独立)
  [3] USER: 跟进问题 (独立)
结果: ✅ 通过（tool_result和普通文本没有被合并）
```

## 相关改进

除了修复核心bug，还添加了以下改进：

### 1. 消息顺序验证

新增 `_validate_message_order()` 函数，验证：
- 首条消息必须是user
- 检测连续的相同角色消息（记录警告）
- 验证tool_result是否跟在tool_use之后

### 2. 增强的循环检测

改进 `_detect_tool_call_loop()` 函数：
- 检测完全相同的工具调用（名称+参数）
- 检测相同工具名的重复调用（即使参数不同）
- 记录警告日志

### 3. 调试模式

添加环境变量 `DEBUG_MESSAGE_CONVERSION`：
```bash
export DEBUG_MESSAGE_CONVERSION=true
```

启用后会输出详细的消息转换日志：
```
=== Message Conversion Debug ===
Input: 7 Claude messages
Output: 6 history messages + 1 current message
  History[0]: USER (toolResults: False)
  History[1]: ASSISTANT (toolUses: True)
  History[2]: USER (toolResults: True)
  ...
================================
```

## 使用建议

### 对于用户

1. **更新到最新版本**：此修复已合并到主分支
2. **启用调试模式**（可选）：设置 `DEBUG_MESSAGE_CONVERSION=true` 查看详细日志
3. **报告问题**：如果仍遇到循环，请提供完整的消息序列用于调试

### 对于开发者

1. **正确构建消息序列**：
   ```python
   # ✅ 正确
   messages = [
     {"role": "user", "content": "问题"},
     {"role": "assistant", "content": [tool_use...]},
     {"role": "user", "content": [tool_result...]},  # 独立的tool_result消息
   ]
   
   # ❌ 错误
   messages[0]["content"].append(tool_result)  # 不要把tool_result添加到其他消息中
   ```

2. **遵循Claude API规范**：
   - 消息必须 user-assistant 交替
   - tool_result 必须在独立的user消息中
   - tool_result 必须紧跟对应的tool_use

3. **实现轮次限制**：
   ```python
   MAX_ROUNDS = 5
   for round in range(MAX_ROUNDS):
       response = call_api(messages)
       if not has_tool_use(response):
           break
       # 执行工具并添加结果
   ```

## 相关资源

- [Claude API 工具使用文档](https://docs.anthropic.com/claude/docs/tool-use)
- [Issue讨论](https://github.com/CassiopeiaCode/q2api/issues)
- [完整排查指南](./TROUBLESHOOTING_INFINITE_LOOP.md)

## 总结

这个修复解决了工具调用无限循环的根本原因：**错误的消息合并**。通过确保tool_result消息保持独立，AI现在可以正确理解对话历史，从而避免重复调用工具。

修复影响：
- ✅ 解决无限循环问题
- ✅ 保持消息历史完整性
- ✅ 提高对话质量
- ✅ 减少不必要的API调用

---

**版本**: v1.0  
**日期**: 2025-12-08  
**修复PR**: [链接]
