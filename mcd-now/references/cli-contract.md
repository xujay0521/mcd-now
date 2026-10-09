# CLI 契约

## 命令

```text
doctor
preference add|update|list|remove|clear
coupon-check
remind
```

`coupon-check` 与 `remind` 必须传入 `--input`，只读取用户主动导出并脱敏的本地
JSON。CLI 和计划任务禁止连接麦当劳 MCP。

CLI 不提供 `order create`。定时或无人值守进程无法完成可靠的人机确认，下单必须由
加载本 Skill 且能够展示当前订单草稿的交互式宿主调用 `create-order`。

## 状态目录

默认使用 `~/.mcd-now/`，可通过 `MCD_SKILL_HOME` 覆盖：

- `preferences.json`：用户明确保存的偏好。
- `reminder-state.json`：券实例摘要及已发送阈值。

所有 JSON 使用 UTF-8、LF 和原子替换写入。

## 定时行为

- 首版提醒阈值为 72 小时和 24 小时。
- 可重复传入 `--remind-at DURATION` 覆盖默认阈值，例如 `3d` 和 `12h`。
- 同一券实例同一阈值只提醒一次。
- 没有新增提醒时不输出正文，退出码仍为 0。
- 定时模式只处理本地快照，不允许调用任何 MCP 工具。
- CLI 只输出文本或 JSON，不负责微信、邮件、短信等通知投递。

## MCP 安全边界

CLI 不读取或接收 MCP Token，也不建立 MCP 连接。实时查询只能由用户在支持 MCP 的
交互式宿主中主动发起。无人值守入口不得调用查询、领券、下单、取消订单、抽奖或
积分兑换工具。

## 退出码

| 退出码 | 含义 |
|---:|---|
| 0 | 成功，包括没有提醒 |
| 2 | CLI 参数错误 |
| 3 | 输入或状态数据无效 |
