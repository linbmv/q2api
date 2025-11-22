# Amazon Q Account Feeder Service

账号投喂服务 - 让用户通过简单的 Web 界面投喂 Amazon Q 账号到主服务。

## ✨ 特性

- ✅ 独立部署，与主服务解耦
- ✅ 友好的 Web 界面
- ✅ URL 登录（设备授权）
- ✅ 手动投喂凭据
- ✅ 批量投喂账号
- ✅ 无需数据库（内存存储临时会话）

## 🚀 快速开始

### 方式一：Docker Compose（推荐）

确保主服务已启动，然后：

```bash
# 1. 复制环境变量配置
cp .env.example .env

# 2. 编辑 .env 文件
# 配置 API_SERVER 指向主服务地址

# 3. 启动服务
docker-compose up -d

# 4. 查看日志
docker-compose logs -f

# 5. 停止服务
docker-compose down
```

服务访问地址：http://localhost:8001/

### 方式二：本地部署

```bash
# 1. 安装 uv (如果未安装)
pip install uv

# 2. 创建虚拟环境并安装依赖
uv venv
uv pip install -r requirements.txt
# 3. 配置环境变量
cp .env.example .env
# 编辑 .env 文件，确保 API_SERVER 指向主服务

# 4. 启动服务
python app.py
```

使用 `python app.py` 直接部署或 `Docker` 部署在功能上没有区别，可根据您的环境偏好选择。

## ⚙️ 配置说明

### 环境变量

```bash
# 投喂服务端口（默认 8001）
FEEDER_PORT=8001

# 主服务地址（必须配置）
# Docker Compose 环境：http://q2api:8000
# 本地开发环境：http://localhost:8000
API_SERVER=http://localhost:8000

# HTTP代理设置（可选）
HTTP_PROXY=""

# 主服务管理员密码（如果主服务配置了 ADMIN_PASSWORD）
API_SERVER_PASSWORD=
```

**重要：** 确保 `API_SERVER` 配置正确，指向主服务地址！

## 📖 使用指南

### URL 登录（推荐）

1. 访问 http://localhost:8001/
2. 填写账号标签（可选）
3. 点击"🚀 开始登录"
4. 在打开的授权页面完成登录
5. 返回页面，点击"⏳ 等待授权并创建账号"
6. 等待最多 5 分钟，账号自动创建

### 手动投喂

如果已有账号凭据（clientId、clientSecret、refreshToken），可直接在"手动投喂账号"区域填写并提交。

### 批量投喂

准备 JSON 数组格式的账号列表：

```json
[
  {
    "label": "账号1",
    "clientId": "xxx",
    "clientSecret": "xxx",
    "refreshToken": "xxx"
  },
  {
    "label": "账号2",
    "clientId": "yyy",
    "clientSecret": "yyy",
    "refreshToken": "yyy"
  }
]
```

在"批量投喂账号"区域粘贴并提交。

## 🏗️ 技术架构

### 后端（app.py）
- **框架**: FastAPI
- **HTTP 客户端**: httpx
- **OIDC 授权**: 自实现（无第三方库依赖）
- **会话存储**: 内存字典（无需数据库）

### 前端（index.html）
- **技术栈**: 原生 HTML + CSS + JavaScript
- **样式**: 内联 CSS（深色主题）
- **交互**: 原生 Fetch API

## 📁 项目结构

```
account-feeder/
├── app.py                  # FastAPI 后端服务
├── index.html              # Web 前端界面
├── requirements.txt        # Python 依赖
├── .env.example            # 环境变量示例
├── .env                    # 环境变量配置（需自行创建）
├── docker-compose.yml      # Docker Compose 配置
├── Dockerfile              # Docker 镜像配置
└── README.md               # 本文件
```

## 📝 API 端点

### POST /auth/start
启动设备授权流程

**请求：**
```json
{
  "label": "账号标签（可选）",
  "enabled": true
}
```

**响应：**
```json
{
  "authId": "会话ID",
  "verificationUriComplete": "授权URL",
  "userCode": "用户代码",
  "expiresIn": 600,
  "interval": 1
}
```

### POST /auth/claim/{auth_id}
轮询并创建账号

**响应：**
```json
{
  "status": "completed",
  "account": {
    "id": "账号ID",
    "label": "账号标签",
    ...
  }
}
```

### POST /accounts/create
创建单个账号

**请求：**
```json
{
  "label": "账号标签",
  "clientId": "客户端ID",
  "clientSecret": "客户端密钥",
  "refreshToken": "刷新令牌",
  "accessToken": "访问令牌（可选）",
  "enabled": true
}
```

### POST /accounts/batch
批量创建账号

**请求：**
```json
{
  "accounts": [
    {
      "label": "账号1",
      "clientId": "xxx",
      "clientSecret": "xxx",
      "refreshToken": "xxx"
    }
  ]
}
```

### GET /health
健康检查

## ⚠️ 注意事项

- ⏱️ URL 登录有 5 分钟超时限制
- 🔗 确保主服务正常运行且可访问
- 🔒 建议在内网环境使用，避免暴露到公网
- 📊 会话数据存储在内存中，服务重启后丢失

## 🐛 故障排查

### 创建账号失败

**可能原因：**
- 主服务未运行或不可访问
- `API_SERVER` 配置错误
- 主服务的 `/v2/accounts` 接口不可用

**解决方法：**
1. 检查主服务是否正常运行
2. 验证 `API_SERVER` 配置是否正确
3. 查看主服务日志排查问题

### 授权超时

**可能原因：**
- 未在 5 分钟内完成授权
- 网络连接问题

**解决方法：**
1. 重新开始授权流程
2. 检查网络连接
3. 确保能访问 AWS OIDC 服务

## 🔒 安全建议

1. ✅ 仅在可信网络环境使用
2. ✅ 使用 HTTPS（通过反向代理）
3. ✅ 限制访问来源（防火墙规则）
4. ✅ 定期清理内存中的过期会话
5. ✅ 添加访问日志监控

## 📄 许可证

与主项目保持一致

## 🙏 致谢

本服务是 [q2api](https://github.com/CassiopeiaCode/q2api) 项目的一部分。