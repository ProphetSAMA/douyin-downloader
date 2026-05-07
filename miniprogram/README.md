# Douyin Downloader Mini Program

微信小程序前端，调用后端 REST API：

- `GET /api/v1/health`
- `POST /api/v1/parse`
- `GET /api/v1/media/{media_id}`

页面流程：

1. 粘贴抖音视频链接或 App 分享文案。
2. 小程序自动调用后端解析接口。
3. 解析成功后展示视频预览。
4. 点击“保存到相册”，小程序通过后端媒体代理下载视频并保存本地。

## 本地开发

1. 启动后端：

```bash
python run.py --serve --serve-host 0.0.0.0 --serve-port 8000
```

Windows PowerShell 如果遇到 Rich 控制台符号编码错误，先执行：

```powershell
$env:PYTHONIOENCODING = "utf-8"
python run.py --serve --serve-host 0.0.0.0 --serve-port 8000
```

2. 按需修改 `config.js`：

```js
module.exports = {
  apiBaseUrl: "http://你的后端地址:8000"
};
```

3. 用微信开发者工具打开 `miniprogram/` 目录。

开发工具里 `project.config.json` 默认关闭了合法域名校验。真机和生产环境需要把 `apiBaseUrl` 换成 HTTPS 域名，并在微信公众平台配置 request 合法域名。
