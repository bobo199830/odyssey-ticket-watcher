# 《奥德赛》票务监控

每 10 分钟检查猫眼“中国电影博物馆”页面中的《奥德赛》场次，只在以下真实变化发生时通过 Server酱通知：

- `NEW_AVAILABLE`：新出现且可以购票的场次
- `RESTOCK`：已知的无票场次恢复购票

首次运行只建立基线，不发送变化通知。状态通过 GitHub Actions cache 跨运行保存。

## 必需设置

在仓库 **Settings → Secrets and variables → Actions → New repository secret** 中创建：

- 名称：`SERVERCHAN_SENDKEY`
- 值：你的 Server酱 SendKey

SendKey 不写入代码，也不会输出到日志。

## 手动运行

打开 **Actions → Odyssey ticket watcher → Run workflow**。默认执行真实检查；若需要验证 Server酱链路，可将 `send_test_notification` 设为 `true`，它只发送明确标注的手动测试消息，不会伪造票务变化。

数据源默认是猫眼的[中国电影博物馆影院页](https://www.maoyan.com/cinema/181)。可通过仓库变量 `TICKET_SOURCE_URL` 覆盖；变量值应填写纯 URL（例如 `https://www.maoyan.com/cinema/181`），不要填写 Markdown 链接。

## 访问频率与失败保护

定时任务每 10 分钟最多访问一次数据源，不做密集重试。若猫眼返回重定向、限流或页面结构异常，运行会失败并保留上一次有效状态，也不会发送票务变化通知。下一次定时运行会自然重试。
