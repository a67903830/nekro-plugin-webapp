# WebApp 智能开发助手

> 🚀 **AI 驱动的 Web 应用开发工具** - 单 Agent 原生 Tool Call 架构，自动编译部署

## ✨ 功能特性

### ⚡ 异步工作架构

- **主对话不阻塞**：Agent 在后台独立工作，用户与 NekroAgent 主框架的交互完全不受影响
- **实时状态感知**：主 Agent 通过提示词注入实时查看任务工作进度
- **双向通信**：用户可以随时发送反馈补充，任务完成后自动通知
- **会话隔离**：每个会话的任务互相独立，互不干扰

### � Text-to-Tool Bridge 架构

- **纯文本协议**：LLM 输出文本流，通过标记解析执行操作
- **流式工具调用**：操作块完成后立即执行，支持错误时打断
- **迭代式开发**：自动编译验证，错误自动反馈修复

### 🚀 核心功能

- **AI 一键部署**：AI 自动将生成的代码编译并部署为在线网页
- **全球加速**：基于 Cloudflare Workers，享受全球 CDN 加速
- **实时编译**：本地 esbuild 编译，自动生成 Import Map
- **权限分离设计**：管理密钥和创建页面密钥分离，安全可控，便捷共享

---

## 🎯 运行原理

### 单 Agent 开发流程

当用户需要创建网页时，系统会派遣 **Developer Agent** 进行全流程处理：

```
用户：帮我创建一个计时器应用

AI：✅ 已创建任务 [Web_0001]
📝 计时器应用开发

Developer Agent 正在工作...
  📝 分析需求 → 💻 编写代码 → ✅ 编译验证 → 🚀 部署上线
```

### 任务状态查看

通过 `wa_ls` 命令可以实时查看任务状态：

```
🌐 WebApp 状态
━━━━━━━━━━━━━━━━━━━━━━━━

� 任务列表
  🔄 [Web_0001] 计时器应用开发
     🏃 编写代码 (45%) | 迭代 3/20

━━━━━━━━━━━━━━━━━━━━━━━━
💡 wa_help 查看命令帮助
```

### 任务详情

通过 `wa_info <id>` 查看任务详情：

```
📋 任务详情 [Web_0001]
━━━━━━━━━━━━━━━━━━━━━━━━

状态: � RUNNING
描述: 计时器应用开发

📁 项目文件 (3 个):
├─ � src
│  ├─ ⚛️ App.tsx
│  └─ 🎨 styles.css
└─ 📄 index.html
```

### 任务完成通知

当任务完成后，AI 会通知用户：

```
[系统] ✅ [WebDev Task Web_0001] (成果)
网页已部署完成！
🔗 预览链接: https://your-worker.pages.dev/abc12345

如需修改，请向 AI 发送反馈。
```

### 管理员命令

| 命令                | 说明                         |
| ------------------- | ---------------------------- |
| `wa_ls [-v]`        | 列出任务和项目状态           |
| `wa_info <id>`      | 查看任务详情                 |
| `wa_stop [id]`      | 取消/停止任务                |
| `wa_clear [id]`     | 清空项目文件                 |
| `wa_help`           | 显示帮助                     |

> 💡 所有命令支持 `-` 和 `_` 通配（如 `wa_ls` = `wa-ls` = `webapp_ls`）

---

## 📦 快速开始

### 第一步：部署 Worker

请查看完整的部署指南：

👉 **[部署文档（DEPLOYMENT.md）](https://github.com/KroMiose/nekro-plugin-webapp/blob/main/DEPLOYMENT.md)**

### 第二步：配置插件

1. 打开 NekroAgent 插件配置页面
2. 找到 **WebApp 智能开发助手** 插件
3. 填写基础配置：
   - **Worker URL**：你的 Worker 地址
   - **ACCESS_KEY**：访问密钥
   - **MODEL_GROUP**：开发模型组
4. 保存配置

### 第三步：创建访问密钥

1. 访问管理界面：[点击跳转](/plugins/KroMiose.nekro_plugin_webapp/)
2. 使用管理密钥登录
3. 在"密钥管理"中创建访问密钥
4. 将访问密钥填入插件配置

✅ **配置完成**！

---

## 🔐 密钥说明

### 密钥类型

**管理密钥（Admin Key）**：

- ✅ 拥有完全管理权限
- ❌ 不能用于创建页面
- ⚠️ 不要分享给其他人

**访问密钥（Access Key）**：

- ✅ 用于创建和托管新页面
- ✅ 可以安全分享给可信任的其他 NekroAgent 用户使用
- ❌ 不能管理系统或其他密钥

---

## 📚 相关文档

- [📖 部署指南（DEPLOYMENT.md）](https://github.com/KroMiose/nekro-plugin-webapp/blob/main/DEPLOYMENT.md)
- [💻 开发文档（DEVELOPMENT.md）](https://github.com/KroMiose/nekro-plugin-webapp/blob/main/DEVELOPMENT.md)
- [🌐 Cloudflare Workers 文档](https://developers.cloudflare.com/workers/)

---

## 📄 许可证

MIT License - 详见 [LICENSE](./LICENSE) 文件

---

## 🤝 贡献

欢迎贡献代码、报告问题或提出建议！

- 🐛 [报告 Bug](https://github.com/KroMiose/nekro-plugin-webapp/issues/new)
- 💡 [提出功能建议](https://github.com/KroMiose/nekro-plugin-webapp/issues/new)

---

**Made with ❤️ by NekroAI Team**

如果觉得这个插件有用，欢迎给个 ⭐ Star！
