# ADR-002：MCP 只做 stdio，自实现最小客户端（不引 SDK）

> 日期：2026-10-12 ｜ 状态：已生效

## 决策

MCP 工具接入只支持 **stdio 传输**，协议用自实现的最小客户端（换行分隔 JSON-RPC 2.0：
initialize → notifications/initialized → tools/list → tools/call，约 200 行），
不引入官方 `mcp` Python SDK，也不做 HTTP/SSE 传输。

## 理由

- 个人工作台的真实场景是**本地命令起的 MCP Server**（filesystem/fetch/数据库类），
  stdio 覆盖 90% 需求；HTTP/SSE 传输是远程/云场景，本机单人用不到；
- 项目依赖红线（仅 MIT/Apache/BSD）下，官方 SDK虽 MIT，但带来 anyio/pydantic 传递依赖
  与版本耦合，而 stdio 协议本身足够简单，自实现可控可测；
- 自实现客户端用假服务器子进程即可完整测试（tests/test_mcp.py 实证）。

## 后果

- 接受：暂不支持远程 MCP（HTTP/SSE）与 MCP 的 resources/prompts 能力（只接 tools）；
  协议升级（protocolVersion 变更）时需跟进握手字段。
- 若将来接远程 MCP，在同一文件加一个 HTTP 传输实现即可，管理器接口不变。
